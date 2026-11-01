"""Extractor router dynamically selecting between yt-dlp and gallery-dl."""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple
from app.services.facebook_downloader import (
    download_facebook_photos,
    is_facebook_story_url,
    is_facebook_url,
    is_facebook_video_url,
    resolve_facebook_url,
)
from app.services.gallerydl_downloader import download_with_gallerydl
from app.services.instagram_photo_downloader import (
    download_instagram_photos,
    is_instagram_url,
)
from app.services.media_inspector import MediaInspectionResult, MediaType, inspect_directory
from app.services.task_manager import DownloadTask
from app.services.tiktok_photo_downloader import (
    download_tiktok_media,
    download_tiktok_photos,
    is_tiktok_url,
    resolve_tiktok_url,
)
from app.services.twitter_downloader import download_twitter_media, extract_tweet_id, is_twitter_url
from app.services.video_splitter import compress_video_file, split_video_file
from app.services.ytdlp_downloader import (
    AuthenticationRequiredError,
    ContentNotFoundError,
    download_with_ytdlp,
    ExtractorError,
)
from app.utils.logging import get_logger
from curl_cffi import requests

logger = get_logger(__name__)


class ExtractorType(str, Enum):
    YTDLP = "yt-dlp"
    GALLERYDL = "gallery-dl"
    UNKNOWN = "unknown"


def is_googlevideo_url(url: str) -> bool:
    """Vérifie si l'URL pointe vers le CDN de flux vidéo Google/YouTube."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().split(":")[0]
        return domain == "googlevideo.com" or domain.endswith(".googlevideo.com")
    except Exception:
        return False


class ExtractorRouter:
    """Intelligently detects suitable extraction engine and manages download with fallback."""

    def __init__(
        self,
        max_file_size: int,
        download_timeout: int,
        ytdlp_bin: str = "yt-dlp",
        gallerydl_bin: str = "gallery-dl",
        ffmpeg_bin: Optional[str] = None,
        cookies_file: Optional[Path] = None,
        cookies_browser: Optional[str] = None,
    ) -> None:
        self.max_file_size = max_file_size
        self.download_timeout = download_timeout
        self.ytdlp_bin = ytdlp_bin
        self.gallerydl_bin = gallerydl_bin
        self.ffmpeg_bin = ffmpeg_bin
        self.cookies_file = cookies_file
        self.cookies_browser = cookies_browser

    def detect_extractors(self, url: str) -> List[ExtractorType]:
        """Inspect URL using native extractor matching in gallery-dl and yt-dlp and return valid engines."""
        lower = url.lower()

        # Fast path pour les domaines vidéo purs et Stories : évite de charger des extracteurs non supportés
        if is_googlevideo_url(url) or is_facebook_story_url(url) or any(
            d in lower for d in ("youtube.com", "youtu.be", "googlevideo.com", "vimeo.com", "dailymotion.com", "twitch.tv")
        ):
            return [ExtractorType.YTDLP]

        gallery_supported = False
        try:
            import gallery_dl.extractor  # type: ignore

            extractor = gallery_dl.extractor.find(url)
            if extractor is not None and getattr(extractor, "category", "") != "generic":
                gallery_supported = True
        except Exception as exc:
            logger.debug(f"gallery-dl detection error: {exc}")

        ytdlp_supported = False
        try:
            import yt_dlp.extractor  # type: ignore

            for ie_cls in yt_dlp.extractor.gen_extractor_classes():
                if ie_cls.suitable(url) and getattr(ie_cls, "IE_NAME", "").lower() != "generic":
                    ytdlp_supported = True
                    break
        except Exception as exc:
            logger.debug(f"yt-dlp detection error: {exc}")

        # If only one tool supports this platform, NEVER attempt the other as fallback!
        # (ex: YouTube/Vimeo are exclusively supported by yt-dlp, calling gallery-dl causes Unsupported URL errors)
        if ytdlp_supported and not gallery_supported:
            return [ExtractorType.YTDLP]
        if gallery_supported and not ytdlp_supported:
            return [ExtractorType.GALLERYDL]

        # If both match (e.g. Instagram, Reddit, Twitter, TikTok, Pinterest):
        if ytdlp_supported and gallery_supported:
            url_lower = url.lower()
            if any(hint in url_lower for hint in ["/reel/", "/video/", "watch?v=", "/shorts/", "/v/"]):
                return [ExtractorType.YTDLP, ExtractorType.GALLERYDL]
            if any(hint in url_lower for hint in ["/p/", "/album/", "/gallery/", "/art/", "/photo/", "/photos/", "/photo.php", "/image/", "/images/", "/posts/"]):
                return [ExtractorType.GALLERYDL, ExtractorType.YTDLP]
            if "pinterest." in url_lower or "pin.it" in url_lower:
                return [ExtractorType.GALLERYDL, ExtractorType.YTDLP]
            return [ExtractorType.YTDLP, ExtractorType.GALLERYDL]

        # Default fallback for direct media or generic links
        return [ExtractorType.YTDLP, ExtractorType.GALLERYDL]

    def detect_primary_extractor(self, url: str) -> ExtractorType:
        """Inspect URL using native extractor matching in gallery-dl and yt-dlp."""
        extractors = self.detect_extractors(url)
        return extractors[0] if extractors else ExtractorType.UNKNOWN

    def _split_oversized_videos(self, target_dir: Path) -> None:
        """Find oversized video files in target_dir and split them into parts <= max_file_size."""
        video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
        target_part_size = min(self.max_file_size, 48 * 1024 * 1024)
        for f in list(target_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in video_exts and f.stat().st_size > self.max_file_size:
                split_video_file(f, target_part_size=target_part_size, ffmpeg_bin=self.ffmpeg_bin)

    def _compress_oversized_videos(self, target_dir: Path) -> None:
        """Find oversized video files in target_dir and compress them to <= max_file_size."""
        video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
        target_size = min(self.max_file_size, 48 * 1024 * 1024)
        for f in list(target_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in video_exts and f.stat().st_size > self.max_file_size:
                compress_video_file(f, target_size_bytes=target_size, ffmpeg_bin=self.ffmpeg_bin)

    async def probe_media(self, url: str) -> Optional[dict]:
        """Probe media metadata (filesize, duration, title) quickly without downloading."""
        lower = url.lower()

        # Ignore les stories Facebook qui nécessitent une authentification
        if is_facebook_story_url(url):
            return None

        # Résolution éventuelle des liens de partage Facebook pour le probing
        if is_facebook_url(url):
            try:
                resolved_fb = await asyncio.to_thread(resolve_facebook_url, url)
                if resolved_fb:
                    url = resolved_fb
                    lower = url.lower()
            except Exception:
                pass

        # 1. Check Twitter via FxTwitter API
        if is_twitter_url(url):
            tweet_id = extract_tweet_id(url)
            if tweet_id:
                try:
                    def _probe_tw() -> Optional[dict]:
                        sess = requests.Session(impersonate="chrome124")
                        try:
                            r = sess.get(f"https://api.fxtwitter.com/status/{tweet_id}", timeout=5)
                            if r.status_code == 200:
                                tdata = r.json().get("tweet", {})
                                media = tdata.get("media") or {}
                                videos = media.get("videos") or []
                                if not videos and "all" in media:
                                    videos = [x for x in media["all"] if x.get("type") == "video"]
                                if videos:
                                    vid = videos[0]
                                    dur = float(vid.get("duration") or 0.0)
                                    variants = [
                                        v for v in vid.get("variants", [])
                                        if v.get("content_type") == "video/mp4"
                                    ]
                                    variants.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                                    best_br = variants[0].get("bitrate", 0) if variants else 0
                                    est_size = int((best_br / 8.0) * dur) if dur and best_br else 0
                                    return {
                                        "title": tdata.get("text", "Vidéo Twitter")[:40],
                                        "duration": dur,
                                        "estimated_size": est_size,
                                    }
                        finally:
                            sess.close()
                        return None

                    return await asyncio.wait_for(asyncio.to_thread(_probe_tw), timeout=6.0)
                except Exception as exc:
                    logger.debug(f"Twitter probe failed: {exc}")

        # 2. Check yt-dlp supported platforms (e.g. YouTube, Vimeo, Dailymotion, Facebook)
        if any(d in lower for d in ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "facebook.com", "fb.watch")) or (
            not is_tiktok_url(url) and not is_instagram_url(url)
        ):
            try:
                cmd = [
                    self.ytdlp_bin,
                    "--no-playlist",
                    "--simulate",
                    "--no-warnings",
                    "--socket-timeout",
                    "10",
                    "--print",
                    "%(duration)s;%(filesize,filesize_approx)s;%(tbr)s;%(title)s",
                    url,
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
                if proc.returncode == 0 and stdout_data:
                    lines = [l.strip() for l in stdout_data.decode("utf-8", errors="replace").splitlines() if l.strip()]
                    if lines:
                        out = lines[-1]
                        parts = out.split(";", 3)
                        if len(parts) >= 3:
                            dur_raw, size_raw, tbr_raw = parts[0].strip(), parts[1].strip(), parts[2].strip()
                            title = parts[3].strip() if len(parts) > 3 else "Vidéo"
                            duration = float(dur_raw) if dur_raw and dur_raw != "NA" else 0.0
                            size = int(size_raw) if size_raw and size_raw != "NA" else 0
                            if size == 0 and tbr_raw and tbr_raw != "NA" and duration > 0:
                                try:
                                    tbr_val = float(tbr_raw)
                                    size = int((tbr_val * 1000 / 8.0) * duration)
                                except (ValueError, TypeError):
                                    pass
                            return {
                                "title": title,
                                "duration": duration,
                                "estimated_size": size,
                            }
            except Exception as exc:
                logger.debug(f"yt-dlp probe failed for {url}: {exc}")

        return None

    async def execute_download(
        self,
        task: DownloadTask,
        target_dir: Path,
        strategy: str = "auto",
    ) -> Tuple[ExtractorType, MediaInspectionResult]:
        """Download media using the primary strategy, with automatic fallback if 0 files produced."""
        # Résolution des liens de partage raccourcis (ex: facebook.com/share/)
        if is_facebook_url(task.url):
            resolved = await asyncio.to_thread(resolve_facebook_url, task.url)
            if resolved != task.url:
                logger.info(f"[Task {task.task_id}] URL Facebook résolue: {task.url} -> {resolved}")
                task.url = resolved

        # Résolution des liens de partage raccourcis TikTok (ex: vt.tiktok.com, vm.tiktok.com)
        if is_tiktok_url(task.url):
            resolved_tt = await asyncio.to_thread(resolve_tiktok_url, task.url)
            if resolved_tt != task.url:
                logger.info(f"[Task {task.task_id}] URL TikTok résolue: {task.url} -> {resolved_tt}")
                task.url = resolved_tt

        # Détection immédiate des flux Google Video expirés
        if is_googlevideo_url(task.url):
            import time
            from urllib.parse import parse_qs, urlparse
            try:
                parsed = urlparse(task.url)
                qs = parse_qs(parsed.query)
                expire = qs.get("expire", [None])[0]
                if expire and float(expire) < time.time():
                    raise ExtractorError(
                        "Ce lien de flux Google Video a expiré. "
                        "Veuillez envoyer le lien YouTube standard de la vidéo (ex: https://www.youtube.com/watch?v=... ou https://youtu.be/...) à la place."
                    )
            except ExtractorError:
                raise
            except Exception:
                pass

        sequence = self.detect_extractors(task.url)
        primary = sequence[0]
        logger.info(f"[Task {task.task_id}] Primary extractor detected: {primary.value} (sequence: {[t.value for t in sequence]}) for {task.url}")

        # Tentative d'extraction directe des diaporamas / photos TikTok si l'URL est de TikTok
        if is_tiktok_url(task.url):
            try:
                extracted = await download_tiktok_photos(
                    task=task,
                    target_dir=target_dir,
                    timeout=self.download_timeout,
                )
                if extracted:
                    inspection = inspect_directory(target_dir, max_file_size=self.max_file_size)
                    if inspection.has_valid_media or inspection.has_oversized_media:
                        logger.info(f"[Task {task.task_id}] Diaporama photo TikTok extrait avec succès.")
                        return ExtractorType.GALLERYDL, inspection
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] L'extracteur TikTok photo a échoué: {exc}")

        last_error: Optional[Exception] = None
        executed_tool: ExtractorType = primary

        for tool in sequence:
            if task.cancel_event.is_set():
                break

            logger.info(f"[Task {task.task_id}] Attempting download with {tool.value}")
            executed_tool = tool

            try:
                if tool == ExtractorType.YTDLP:
                    await download_with_ytdlp(
                        task=task,
                        target_dir=target_dir,
                        max_file_size=self.max_file_size,
                        timeout=self.download_timeout,
                        bin_path=self.ytdlp_bin,
                        ffmpeg_bin=self.ffmpeg_bin,
                        cookies_file=self.cookies_file,
                        cookies_browser=self.cookies_browser,
                        allow_large=(strategy in ("split", "compress")),
                    )
                else:
                    await download_with_gallerydl(
                        task=task,
                        target_dir=target_dir,
                        timeout=self.download_timeout,
                        bin_path=self.gallerydl_bin,
                        cookies_file=self.cookies_file,
                        cookies_browser=self.cookies_browser,
                    )

                # Inspect directory to see if any media was produced
                result = inspect_directory(target_dir, max_file_size=self.max_file_size)
                if strategy == "compress":
                    self._compress_oversized_videos(target_dir)
                    result = inspect_directory(target_dir, max_file_size=self.max_file_size)
                elif strategy == "split" or result.has_oversized_media:
                    self._split_oversized_videos(target_dir)
                    result = inspect_directory(target_dir, max_file_size=self.max_file_size)

                if result.has_valid_media or result.has_oversized_media:
                    return executed_tool, result

                logger.info(f"[Task {task.task_id}] {tool.value} produced 0 media files, trying fallback...")

            except Exception as exc:
                logger.warning(f"[Task {task.task_id}] {tool.value} failed: {exc}")
                # Ne pas écraser une erreur spécifique d'authentification par une erreur générique subséquente
                if last_error is None or not isinstance(last_error, AuthenticationRequiredError):
                    last_error = exc

        # Tentative d'extraction spécialisée des photos/carrousels Instagram si aucun média n'a été produit
        if is_instagram_url(task.url) and not task.cancel_event.is_set():
            try:
                ig_photos = await download_instagram_photos(
                    task=task,
                    target_dir=target_dir,
                    timeout=self.download_timeout,
                    cookies_file=self.cookies_file,
                    cookies_browser=self.cookies_browser,
                )
                if ig_photos:
                    result = inspect_directory(target_dir, max_file_size=self.max_file_size)
                    if result.has_valid_media or result.has_oversized_media:
                        logger.info(f"[Task {task.task_id}] Carrousel photo Instagram extrait avec succès.")
                        return ExtractorType.GALLERYDL, result
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] Échec extracteur Instagram photo: {exc}")

        # Tentative d'extraction spécialisée des photos Facebook si aucun média n'a été produit
        # (NE JAMAIS appeler l'extracteur photo sur des URLs de vidéo/Reels ou si la vidéo dépassait la taille max)
        if (
            is_facebook_url(task.url)
            and not is_facebook_video_url(task.url)
            and not task.cancel_event.is_set()
            and not (last_error and "dépasse la limite maximale" in str(last_error).lower())
        ):
            try:
                fb_photos = await download_facebook_photos(
                    task=task,
                    target_dir=target_dir,
                    timeout=self.download_timeout,
                    cookies_file=self.cookies_file,
                )
                if fb_photos:
                    result = inspect_directory(target_dir, max_file_size=self.max_file_size)
                    if result.has_valid_media or result.has_oversized_media:
                        logger.info(f"[Task {task.task_id}] Médias Facebook extraits avec succès.")
                        extractor_used = (
                            ExtractorType.GALLERYDL
                            if any(i.media_type == MediaType.PHOTO for i in result.valid_items)
                            and not any(i.media_type == MediaType.VIDEO for i in result.valid_items)
                            else ExtractorType.YTDLP
                        )
                        return extractor_used, result
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] Échec extracteur Facebook photo: {exc}")

        # Tentative d'extraction spécialisée Twitter/X (contournement des restrictions de sensibilité/NSFW)
        if is_twitter_url(task.url) and not task.cancel_event.is_set():
            try:
                tw_ok = await download_twitter_media(
                    task=task,
                    target_dir=target_dir,
                    max_file_size=self.max_file_size,
                    timeout=self.download_timeout,
                )
                if tw_ok:
                    result = inspect_directory(target_dir, max_file_size=self.max_file_size)
                    if result.has_valid_media or result.has_oversized_media:
                        logger.info(f"[Task {task.task_id}] Média Twitter extrait avec succès via API de contournement.")
                        extractor_used = (
                            ExtractorType.GALLERYDL
                            if any(i.media_type == MediaType.PHOTO for i in result.valid_items)
                            and not any(i.media_type == MediaType.VIDEO for i in result.valid_items)
                            else ExtractorType.YTDLP
                        )
                        return extractor_used, result
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] Échec extracteur Twitter bypass: {exc}")

        # Tentative d'extraction spécialisée TikTok (contournement anti-bot/Cloudflare si échec yt-dlp & gallery-dl)
        if is_tiktok_url(task.url) and not task.cancel_event.is_set():
            try:
                tt_ok = await download_tiktok_media(
                    task=task,
                    target_dir=target_dir,
                    timeout=self.download_timeout,
                )
                if tt_ok:
                    result = inspect_directory(target_dir, max_file_size=self.max_file_size)
                    if result.has_valid_media or result.has_oversized_media:
                        logger.info(f"[Task {task.task_id}] Média TikTok extrait avec succès via API de contournement.")
                        extractor_used = (
                            ExtractorType.GALLERYDL
                            if any(i.media_type == MediaType.PHOTO for i in result.valid_items)
                            and not any(i.media_type == MediaType.VIDEO for i in result.valid_items)
                            else ExtractorType.YTDLP
                        )
                        return extractor_used, result
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] Échec extracteur TikTok bypass: {exc}")

        # If both attempts failed or produced no files:
        final_inspection = inspect_directory(target_dir, max_file_size=self.max_file_size)
        if strategy == "compress":
            self._compress_oversized_videos(target_dir)
            final_inspection = inspect_directory(target_dir, max_file_size=self.max_file_size)
        elif strategy == "split" or final_inspection.has_oversized_media:
            self._split_oversized_videos(target_dir)
            final_inspection = inspect_directory(target_dir, max_file_size=self.max_file_size)

        if final_inspection.has_valid_media or final_inspection.has_oversized_media:
            return executed_tool, final_inspection

        if last_error:
            err_str = str(last_error).lower()
            if (
                "no video could be found" in err_str
                or "no video formats found" in err_str
                or "aucun média vidéo" in err_str
            ):
                raise ContentNotFoundError(
                    "Aucun média téléchargeable trouvé dans ce tweet (publication textuelle uniquement)."
                )
            if "'unavailable'" in err_str or "unavailable" in err_str or "tombstone" in err_str:
                raise ContentNotFoundError(
                    "Ce tweet est indisponible ou a été supprimé de Twitter/X."
                )
            raise last_error

        raise ExtractorError("Aucun média n'a pu être extrait depuis cette URL.")

"""Extractor router dynamically selecting between yt-dlp and gallery-dl."""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple
from app.services.facebook_downloader import (
    download_facebook_media,
    download_facebook_photos,
    is_facebook_story_url,
    is_facebook_url,
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
from app.services.twitter_downloader import download_twitter_media, is_twitter_url
from app.services.ytdlp_downloader import (
    AuthenticationRequiredError,
    ContentNotFoundError,
    download_with_ytdlp,
    ExtractorError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractorType(str, Enum):
    YTDLP = "yt-dlp"
    GALLERYDL = "gallery-dl"
    UNKNOWN = "unknown"


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

            ies = yt_dlp.extractor.gen_extractors()
            for ie in ies:
                if ie.suitable(url) and ie.IE_NAME.lower() != "generic":
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

    async def execute_download(
        self,
        task: DownloadTask,
        target_dir: Path,
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

        # Tentative d'extraction directe des Stories Facebook via getmyfb
        if is_facebook_story_url(task.url):
            try:
                extracted = await download_facebook_media(
                    task=task,
                    target_dir=target_dir,
                    timeout=self.download_timeout,
                    cookies_file=self.cookies_file,
                )
                if extracted:
                    inspection = inspect_directory(target_dir, max_file_size=self.max_file_size)
                    if inspection.has_valid_media or inspection.has_oversized_media:
                        logger.info(f"[Task {task.task_id}] Story Facebook extraite directement avec succès via getmyfb.")
                        extractor_used = (
                            ExtractorType.GALLERYDL
                            if any(i.media_type == MediaType.PHOTO for i in inspection.valid_items)
                            and not any(i.media_type == MediaType.VIDEO for i in inspection.valid_items)
                            else ExtractorType.YTDLP
                        )
                        return extractor_used, inspection
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] Extraction directe story Facebook via getmyfb a échoué: {exc}")

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
                if result.has_valid_media or result.has_oversized_media:
                    return executed_tool, result

                logger.info(f"[Task {task.task_id}] {tool.value} produced 0 media files, trying fallback...")

            except Exception as exc:
                logger.warning(f"[Task {task.task_id}] {tool.value} failed: {exc}")
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
        if is_facebook_url(task.url) and not task.cancel_event.is_set():
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

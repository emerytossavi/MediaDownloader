"""yt-dlp asynchronous subprocess downloader."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
from typing import List, Optional
from app.services.task_manager import DownloadTask
from app.utils.filesystem import force_garbage_collection
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractorError(Exception):
    """Base error for media extraction failures."""


class ContentNotFoundError(ExtractorError):
    """Raised when the requested media does not exist or has been deleted."""


class AuthenticationRequiredError(ExtractorError):
    """Raised when the media requires account authentication or is private."""


class DownloadTimeoutError(ExtractorError):
    """Raised when the download exceeds the maximum allowed duration."""


class DownloadCancelledError(ExtractorError):
    """Raised when the download was explicitly cancelled by the user."""


async def download_with_ytdlp(
    task: DownloadTask,
    target_dir: Path,
    max_file_size: int,
    timeout: int,
    bin_path: str = "yt-dlp",
    ffmpeg_bin: Optional[str] = None,
    cookies_file: Optional[Path] = None,
    cookies_browser: Optional[str] = None,
) -> None:
    """Download video or audio media using yt-dlp via an isolated asynchronous subprocess."""
    max_size_mb = max(1, max_file_size // (1024 * 1024))
    
    # Safe output template with length restriction
    out_tmpl = str(target_dir / "%(title).80s-%(id)s.%(ext)s")

    v_budget = max(1, max_size_mb - 5)
    format_selector = (
        f"b[filesize<={max_size_mb}M]/b[filesize_approx<={max_size_mb}M]/"
        f"bestvideo[ext=mp4][filesize<={v_budget}M]+bestaudio[ext=m4a][filesize<=5M]/"
        f"bestvideo[filesize<={v_budget}M]+bestaudio[filesize<=5M]/"
        f"bestvideo[filesize_approx<={v_budget}M]+bestaudio[filesize_approx<=5M]/"
        f"bestvideo[height<=1080][filesize<={v_budget}M]+bestaudio/"
        f"bestvideo[height<=720][filesize<={v_budget}M]+bestaudio/"
        f"bestvideo[height<=480][filesize<={v_budget}M]+bestaudio/"
        f"bestvideo[height<=360][filesize<={v_budget}M]+bestaudio/"
        f"bestvideo[filesize<={max_size_mb}M]+bestaudio/"
        f"bestvideo[height<=720]+bestaudio/best[height<=720]/"
        f"bestvideo[height<=480]+bestaudio/best[height<=480]/"
        f"bestvideo+bestaudio/best"
    )

    cmd: List[str] = [
        bin_path,
        "--no-playlist",
        "--restrict-filenames",
        "-f",
        format_selector,
        "--max-filesize",
        f"{max_size_mb}M",
        "-o",
        out_tmpl,
        "--no-warnings",
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "--buffer-size",
        "16K",
        "--http-chunk-size",
        "1M",
        "--no-cache-dir",
    ]

    # Runtimes JS légers uniquement (quickjs, deno) pour éviter la surconsommation mémoire de Node.js (V8: 150-200 Mo)
    # Sur environnements PaaS / VPS contraints, Node.js provoque des crashs OOM (Killed)
    enable_node = os.getenv("ENABLE_NODE_JS_RUNTIME", "false").lower() in ("true", "1")
    js_candidates = ["quickjs", "deno"]
    if enable_node:
        js_candidates.append("node")

    for r_name in js_candidates:
        r_path = shutil.which(r_name)
        if r_path:
            cmd.extend(["--js-runtimes", f"{r_name}:{r_path}"])
            break

    if ffmpeg_bin:
        cmd.extend(["--ffmpeg-location", ffmpeg_bin])

    if cookies_file and Path(cookies_file).exists():
        cmd.extend(["--cookies", str(cookies_file)])
    elif cookies_browser:
        cmd.extend(["--cookies-from-browser", cookies_browser])

    # Add URL as the final positional argument
    cmd.append(task.url)

    logger.info(f"[Task {task.task_id}] Executing yt-dlp command: {' '.join(cmd[:5])} ...")

    force_garbage_collection()
    sub_env = os.environ.copy()
    sub_env["NODE_OPTIONS"] = "--max-old-space-size=32"
    sub_env["PYTHONUNBUFFERED"] = "1"

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=sub_env,
        )
        task.process = process

        # Run process with timeout and cancellation watch
        stdout_data, stderr_data = await asyncio.wait_for(
            process.communicate(),
            timeout=float(timeout),
        )
    except asyncio.TimeoutError as exc:
        if task.process:
            try:
                task.process.kill()
                await task.process.wait()
            except ProcessLookupError:
                pass
        logger.error(f"[Task {task.task_id}] yt-dlp timed out after {timeout}s")
        raise DownloadTimeoutError(f"Le téléchargement a dépassé le délai limite de {timeout}s.") from exc
    except asyncio.CancelledError as exc:
        if task.process:
            try:
                task.process.kill()
                await task.process.wait()
            except ProcessLookupError:
                pass
        raise DownloadCancelledError("Téléchargement annulé.") from exc
    finally:
        task.process = None
        force_garbage_collection()

    stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
    stdout_text = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""

    if task.cancel_event.is_set():
        raise DownloadCancelledError("Téléchargement annulé par l'utilisateur.")

    if process.returncode != 0:
        logger.warning(
            f"[Task {task.task_id}] yt-dlp failed (code {process.returncode}): {stderr_text[:500]}"
        )
        _classify_ytdlp_error(stderr_text, task.url)

    # Vérification anti-orphelin : si la vidéo a été avortée car > 50M mais que le flux audio a été téléchargé
    all_output = (stdout_text + "\n" + stderr_text).lower()
    video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts"}
    audio_exts = {".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus", ".aac"}

    downloaded_files = list(target_dir.iterdir()) if target_dir.exists() else []
    has_video = any(f.suffix.lower() in video_exts for f in downloaded_files)
    audio_files = [f for f in downloaded_files if f.suffix.lower() in audio_exts]

    if not has_video and "larger than max-filesize" in all_output:
        logger.warning(
            f"[Task {task.task_id}] Le flux vidéo a dépassé {max_size_mb}M et a été ignoré par yt-dlp. "
            f"Suppression du flux audio isolé."
        )
        for a in audio_files:
            try:
                a.unlink()
            except OSError:
                pass
        raise ExtractorError(f"Le fichier vidéo dépasse la limite maximale autorisée ({max_size_mb} Mo) pour Telegram.")

    if not downloaded_files:
        if process.returncode != 0:
            _classify_ytdlp_error(stderr_text, task.url)
        raise ContentNotFoundError("Aucun média vidéo téléchargeable trouvé à cette adresse.")


def _classify_ytdlp_error(stderr: str, url: str = "") -> None:
    """Analyze yt-dlp stderr output and raise descriptive exceptions."""
    lower = stderr.lower()
    url_lower = url.lower()

    if "googlevideo.com" in url_lower or "googlevideo.com" in lower:
        raise ExtractorError(
            "Ce lien est un flux technique temporaire YouTube (Google Video) protégé par IP et inaccessible hors de votre navigateur. "
            "Veuillez envoyer le lien YouTube standard de la vidéo (ex: https://www.youtube.com/watch?v=... ou https://youtu.be/...) à la place."
        )

    if (
        "this content is unreachable. use --cookies" in lower
        or "use --cookies for the authentication" in lower
        or "[instagram:story]" in lower
        or "instagram.com/stories" in lower
    ):
        raise AuthenticationRequiredError(
            "Cette Story Instagram nécessite une session connectée (cookies manquants ou expirés). "
            "Configurez COOKIES_FILE ou COOKIES_BROWSER pour télécharger les Stories."
        )

    if (
        "facebook.com/login" in lower
        or "facebook.com/stories" in lower
        or ("facebook.com" in lower and "login.php" in lower)
    ):
        raise AuthenticationRequiredError(
            "Cette Story ou publication Facebook nécessite un compte connecté. "
            "Configurez COOKIES_FILE ou COOKIES_BROWSER pour y accéder."
        )

    if any(
        phrase in lower
        for phrase in [
            "private video",
            "this video is private",
            "login required",
            "sign in",
            "members-only",
            "requires authentication",
        ]
    ):
        raise AuthenticationRequiredError(
            "Ce contenu est privé ou nécessite une authentification."
        )

    if (
        "no video could be found in this tweet" in lower
        or "no video formats found" in lower
    ):
        raise ContentNotFoundError(
            "Aucun média vidéo trouvé dans cette publication. "
            "Il s'agit potentiellement d'un tweet textuel ou d'images."
        )

    if any(
        phrase in lower
        for phrase in [
            "video unavailable",
            "does not exist",
            "not found",
            "404",
            "deleted",
            "has been removed",
            "unavailable",
            "tombstone",
        ]
    ):
        raise ContentNotFoundError("Le contenu demandé est indisponible, introuvable ou a été supprimé.")

    if "file is larger than max-filesize" in lower:
        raise ExtractorError(
            "Le fichier multimédia dépasse la limite maximale autorisée (50 MiB) et a été ignoré."
        )

    if (
        "unexpected response from webpage request" in lower
        or "the extractor is attempting impersonation" in lower
    ):
        raise ExtractorError(
            "La plateforme a restreint l'accès (protection anti-bot / impersonation requise). "
            "Veuillez réessayer ultérieurement."
        )

    # Generic extractor error with first relevant line
    lines = [line.strip() for line in stderr.splitlines() if "ERROR:" in line]
    error_detail = lines[0] if lines else "Échec de l'extraction multimédia."
    raise ExtractorError(error_detail)


class YtDlpDownloader:
    """Downloader implementation wrapping yt-dlp."""

    def __init__(
        self,
        max_file_size: int,
        download_timeout: int,
        bin_path: str = "yt-dlp",
        ffmpeg_bin: Optional[str] = None,
        cookies_file: Optional[Path] = None,
        cookies_browser: Optional[str] = None,
    ) -> None:
        self.max_file_size = max_file_size
        self.download_timeout = download_timeout
        self.bin_path = bin_path
        self.ffmpeg_bin = ffmpeg_bin
        self.cookies_file = cookies_file
        self.cookies_browser = cookies_browser

    async def download(self, task: DownloadTask, target_dir: Path) -> None:
        await download_with_ytdlp(
            task=task,
            target_dir=target_dir,
            max_file_size=self.max_file_size,
            timeout=self.download_timeout,
            bin_path=self.bin_path,
            ffmpeg_bin=self.ffmpeg_bin,
            cookies_file=self.cookies_file,
            cookies_browser=self.cookies_browser,
        )

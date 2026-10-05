"""yt-dlp asynchronous subprocess downloader."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional
from app.services.task_manager import DownloadTask
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
) -> None:
    """Download video or audio media using yt-dlp via an isolated asynchronous subprocess."""
    max_size_mb = max(1, max_file_size // (1024 * 1024))
    
    # Safe output template with length restriction
    out_tmpl = str(target_dir / "%(title).80s-%(id)s.%(ext)s")

    cmd: List[str] = [
        bin_path,
        "--no-playlist",
        "--restrict-filenames",
        # Attempt to prioritize MP4/compatible streams under the limit
        "-f",
        f"bestvideo[ext=mp4][filesize<={max_size_mb}M]+bestaudio[ext=m4a]/best[filesize<={max_size_mb}M]/bestvideo+bestaudio/best",
        "--max-filesize",
        f"{max_size_mb}M",
        "-o",
        out_tmpl,
        "--no-warnings",
        "--socket-timeout",
        "30",
        "--retries",
        "3",
    ]

    if ffmpeg_bin:
        cmd.extend(["--ffmpeg-location", ffmpeg_bin])

    if cookies_file and Path(cookies_file).exists():
        cmd.extend(["--cookies", str(cookies_file)])

    # Add URL as the final positional argument
    cmd.append(task.url)

    logger.info(f"[Task {task.task_id}] Executing yt-dlp command: {' '.join(cmd[:5])} ...")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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

    stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
    stdout_text = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""

    if task.cancel_event.is_set():
        raise DownloadCancelledError("Téléchargement annulé par l'utilisateur.")

    if process.returncode != 0:
        logger.warning(
            f"[Task {task.task_id}] yt-dlp failed (code {process.returncode}): {stderr_text[:500]}"
        )
        _classify_ytdlp_error(stderr_text)


def _classify_ytdlp_error(stderr: str) -> None:
    """Analyze yt-dlp stderr output and raise descriptive exceptions."""
    lower = stderr.lower()

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
        "this content is unreachable. use --cookies" in lower
        or "use --cookies for the authentication" in lower
        or "[instagram:story]" in lower
    ):
        raise AuthenticationRequiredError(
            "Les Stories Instagram nécessitent un compte connecté. "
            "Configurez un fichier de cookies (COOKIES_FILE) pour y accéder."
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
        ]
    ):
        raise ContentNotFoundError("Le contenu demandé est introuvable ou a été supprimé.")

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

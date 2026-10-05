"""gallery-dl asynchronous subprocess downloader."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List
from app.services.task_manager import DownloadTask
from app.services.ytdlp_downloader import (
    AuthenticationRequiredError,
    ContentNotFoundError,
    DownloadCancelledError,
    DownloadTimeoutError,
    ExtractorError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def download_with_gallerydl(
    task: DownloadTask,
    target_dir: Path,
    timeout: int,
    bin_path: str = "gallery-dl",
    cookies_file: Optional[Path] = None,
) -> None:
    """Download images/galleries using gallery-dl via an isolated asynchronous subprocess."""
    cmd: List[str] = [
        bin_path,
        "--destination",
        str(target_dir),
        "--filename",
        "{num:03d}_{filename}.{extension}",
        "--no-part",
        "--retries",
        "3",
        "--http-timeout",
        "30",
    ]

    if cookies_file and Path(cookies_file).exists():
        cmd.extend(["--cookies", str(cookies_file)])

    cmd.append(task.url)

    logger.info(f"[Task {task.task_id}] Executing gallery-dl command: {' '.join(cmd[:4])} ...")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task.process = process

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
        logger.error(f"[Task {task.task_id}] gallery-dl timed out after {timeout}s")
        raise DownloadTimeoutError(f"Le téléchargement gallery-dl a dépassé le délai de {timeout}s.") from exc
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
            f"[Task {task.task_id}] gallery-dl exited with code {process.returncode}: {stderr_text[:500]}"
        )
        _classify_gallerydl_error(stderr_text)


def _classify_gallerydl_error(stderr: str) -> None:
    """Analyze gallery-dl error output and raise specialized exceptions."""
    lower = stderr.lower()

    if "solving javascript challenge" in lower or "challenge" in lower:
        raise ExtractorError("La plateforme a bloqué la requête (protection anti-bot/Cloudflare).")

    if "http redirect to login page" in lower or "/accounts/login" in lower:
        raise AuthenticationRequiredError(
            "Instagram exige une connexion pour accéder à ce contenu. "
            "Configurez un fichier de cookies (COOKIES_FILE) pour débloquer cet accès."
        )

    if "requested user could not be found" in lower or "user could not be found" in lower:
        raise AuthenticationRequiredError(
            "Ce contenu (Story ou compte restreint) nécessite un compte connecté sur Instagram."
        )

    if any(phrase in lower for phrase in ["401", "unauthorized", "login", "private"]):
        raise AuthenticationRequiredError(
            "Ce contenu d'image est privé ou nécessite une connexion sur la plateforme."
        )

    if any(phrase in lower for phrase in ["404", "not found", "doesn't exist", "deleted"]):
        raise ContentNotFoundError("L'image ou l'album demandé est introuvable ou a été supprimé.")

    if "403" in lower:
        if any(w in lower for w in ["login", "private", "account"]):
            raise AuthenticationRequiredError(
                "Ce contenu d'image est privé ou nécessite une connexion sur la plateforme."
            )
        raise ExtractorError("Accès refusé par la plateforme (protection anti-bot ou restriction).")

    lines = [
        line.strip()
        for line in stderr.splitlines()
        if "[error]" in line.lower() or "error:" in line.lower()
    ]
    detail = lines[0] if lines else "Échec de l'extraction d'images."
    raise ExtractorError(detail)

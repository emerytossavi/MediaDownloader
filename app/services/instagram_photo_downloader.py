"""Specialized Instagram photo and carousel downloader using yt-dlp metadata and curl_cffi."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from curl_cffi import requests

from app.services.task_manager import DownloadTask
from app.utils.logging import get_logger

logger = get_logger(__name__)


def is_instagram_url(url: str) -> bool:
    """Vérifie si l'URL appartient à Instagram."""
    lower = url.lower()
    return "instagram.com" in lower or "instagr.am" in lower


async def download_instagram_photos(
    task: DownloadTask,
    target_dir: Path,
    timeout: int = 30,
    cookies_file: Optional[Path] = None,
) -> bool:
    """Extrait et télécharge les photos et carrousels d'un post Instagram."""
    cmd = [
        "yt-dlp",
        "-j",
        "--ignore-no-formats-error",
        "--no-warnings",
        "--socket-timeout",
        "20",
    ]
    if cookies_file and Path(cookies_file).exists():
        cmd.extend(["--cookies", str(cookies_file)])
    cmd.append(task.url)

    logger.info(f"[Task {task.task_id}] Extraction des photos Instagram via métadonnées...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_data, stderr_data = await asyncio.wait_for(
            proc.communicate(),
            timeout=float(timeout),
        )
    except Exception as exc:
        logger.debug(f"[Task {task.task_id}] Échec de récupération des métadonnées Instagram: {exc}")
        return False

    lines = [
        line.strip()
        for line in stdout_data.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    if not lines:
        return False

    def _download_all() -> int:
        sess = requests.Session(impersonate="chrome")
        downloaded = 0
        try:
            for idx, line in enumerate(lines, 1):
                if task.cancel_event.is_set():
                    break
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                thumb_url = data.get("thumbnail")
                if not thumb_url and data.get("thumbnails"):
                    thumb_url = data["thumbnails"][-1].get("url")

                if thumb_url:
                    try:
                        resp = sess.get(thumb_url, timeout=timeout)
                        if resp.status_code == 200 and resp.content:
                            item_id = data.get("id", f"{idx:03d}")
                            filename = f"{idx:03d}_instagram_{item_id}.jpg"
                            filepath = target_dir / filename
                            filepath.write_bytes(resp.content)
                            downloaded += 1
                    except Exception as err:
                        logger.debug(f"[Task {task.task_id}] Erreur téléchargement photo {idx}: {err}")
            return downloaded
        finally:
            sess.close()

    count = await asyncio.to_thread(_download_all)
    if count > 0:
        logger.info(f"[Task {task.task_id}] {count} photo(s) Instagram téléchargée(s) avec succès.")
        return True

    return False

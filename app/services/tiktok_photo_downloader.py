"""Specialized TikTok photo and slideshow downloader using curl_cffi for anti-bot bypass."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Optional

from curl_cffi import requests

from app.services.task_manager import DownloadTask
from app.utils.logging import get_logger

logger = get_logger(__name__)


def is_tiktok_url(url: str) -> bool:
    """Vérifie si l'URL correspond à une adresse TikTok."""
    lower = url.lower()
    return any(domain in lower for domain in ["tiktok.com", "douyin.com", "tiktokv.com"])


def _extract_tiktok_photos_sync(task: DownloadTask, target_dir: Path, timeout: int = 30) -> bool:
    """Télécharge de manière synchrone les images d'un post TikTok photo."""
    sess = requests.Session(impersonate="chrome")

    try:
        # Résolution des redirections (pour les liens courts comme vt.tiktok.com)
        resp = sess.get(task.url, allow_redirects=True, timeout=timeout)
        canonical_url = resp.url

        # Récupération de l'identifiant du post
        m = re.search(r"/(?:photo|video)/(\d+)", canonical_url)
        if not m:
            logger.debug(f"[Task {task.task_id}] Aucun ID de post trouvé dans l'URL résolue: {canonical_url}")
            return False

        post_id = m.group(1)

        # TikTok expose les données complètes de rehydration via le chemin /video/
        video_url = re.sub(r"/(?:photo|video)/\d+", f"/video/{post_id}", canonical_url)
        resp2 = sess.get(video_url, timeout=timeout)

        m_json = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', resp2.text)
        if not m_json:
            logger.debug(f"[Task {task.task_id}] Balise de rehydration introuvable pour {video_url}")
            return False

        data = json.loads(m_json.group(1))
        detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
        item = detail.get("itemInfo", {}).get("itemStruct", {})

        image_post = item.get("imagePost", {})
        images = image_post.get("images", [])

        if not images:
            logger.debug(f"[Task {task.task_id}] Le post TikTok #{post_id} ne contient pas de photos (vidéo standard).")
            return False

        logger.info(f"[Task {task.task_id}] Détection de {len(images)} photos pour le post TikTok #{post_id}")

        downloaded_count = 0
        for idx, img in enumerate(images, 1):
            if task.cancel_event.is_set():
                break

            url_list = img.get("imageURL", {}).get("urlList", [])
            if not url_list:
                continue

            img_url = url_list[0]
            img_resp = sess.get(img_url, timeout=timeout)
            if img_resp.status_code == 200 and img_resp.content:
                filename = f"{idx:03d}_tiktok_{post_id}.jpeg"
                filepath = target_dir / filename
                with open(filepath, "wb") as f:
                    f.write(img_resp.content)
                downloaded_count += 1

        # Optionnel : télécharger également la piste audio associée au diaporama
        music_url = item.get("music", {}).get("playUrl")
        if music_url and not task.cancel_event.is_set():
            try:
                audio_resp = sess.get(music_url, timeout=timeout)
                if audio_resp.status_code == 200 and audio_resp.content:
                    audio_path = target_dir / f"audio_tiktok_{post_id}.mp3"
                    with open(audio_path, "wb") as f:
                        f.write(audio_resp.content)
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] Impossible de récupérer l'audio du diaporama: {exc}")

        return downloaded_count > 0

    except Exception as exc:
        logger.warning(f"[Task {task.task_id}] Erreur lors de l'extraction des photos TikTok: {exc}")
        return False
    finally:
        sess.close()


async def download_tiktok_photos(task: DownloadTask, target_dir: Path, timeout: int = 30) -> bool:
    """Téléchargement asynchrone non-bloquant des photos TikTok."""
    return await asyncio.to_thread(_extract_tiktok_photos_sync, task, target_dir, timeout)

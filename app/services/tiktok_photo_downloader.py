"""Specialized TikTok photo, slideshow and video downloader using curl_cffi and bypass APIs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from curl_cffi import requests

from app.services.task_manager import DownloadTask
from app.utils.logging import get_logger
from app.utils.network import stream_download_to_file

logger = get_logger(__name__)


def is_tiktok_url(url: str) -> bool:
    """Vérifie si l'URL correspond à une adresse TikTok."""
    lower = url.lower()
    return any(domain in lower for domain in ["tiktok.com", "douyin.com", "tiktokv.com"])


def is_tiktok_short_url(url: str) -> bool:
    """Vérifie si l'URL est un lien court de partage TikTok."""
    lower = url.lower()
    return any(domain in lower for domain in ["vt.tiktok.com", "vm.tiktok.com", "/t/"])


def resolve_tiktok_url(url: str, timeout: int = 10) -> str:
    """Résout les redirections d'un lien court TikTok (ex: vt.tiktok.com, vm.tiktok.com)."""
    if not is_tiktok_short_url(url):
        return url
    try:
        resp = requests.head(url, impersonate="chrome", timeout=timeout, allow_redirects=True)
        if resp.url and resp.url != url:
            logger.info(f"URL TikTok résolue: {url} -> {resp.url}")
            return resp.url
    except Exception as exc:
        logger.debug(f"Erreur HEAD lors de la résolution de l'URL TikTok {url}: {exc}")
        try:
            resp = requests.get(url, impersonate="chrome", timeout=timeout, allow_redirects=True)
            if resp.url and resp.url != url:
                logger.info(f"URL TikTok résolue via GET: {url} -> {resp.url}")
                return resp.url
        except Exception as exc2:
            logger.debug(f"Erreur GET lors de la résolution de l'URL TikTok {url}: {exc2}")
    return url


def _fetch_tikwm_data(url: str, sess: requests.Session, timeout: int = 15) -> Optional[Dict[str, Any]]:
    """Interroge l'API TikWM pour extraire les métadonnées média sans restriction anti-bot."""
    endpoints = [
        f"https://www.tikwm.com/api/?url={url}",
        f"https://tikwm.com/api/?url={url}",
    ]
    for endpoint in endpoints:
        try:
            resp = sess.get(endpoint, timeout=timeout)
            if resp.status_code == 200:
                payload = resp.json()
                if payload.get("code") == 0 and payload.get("data"):
                    return payload["data"]
        except Exception as exc:
            logger.debug(f"Erreur API TikWM ({endpoint}): {exc}")
    return None


def _extract_tiktok_photos_sync(task: DownloadTask, target_dir: Path, timeout: int = 30) -> bool:
    """Télécharge de manière synchrone les images d'un post TikTok photo."""
    sess = requests.Session(impersonate="chrome")

    try:
        # Résolution de l'URL canonique si lien court
        canonical_url = resolve_tiktok_url(task.url, timeout=min(timeout, 10))
        m = re.search(r"/(?:photo|video)/(\d+)", canonical_url)
        post_id = m.group(1) if m else "media"

        # Stratégie 1 : API TikWM (contourne le JS challenge et fournit directement les photos HD)
        data = _fetch_tikwm_data(canonical_url, sess, timeout=min(timeout, 15))
        if data:
            images = data.get("images", [])
            if images:
                logger.info(f"[Task {task.task_id}] Détection de {len(images)} photos TikTok via TikWM.")
                downloaded_count = 0
                for idx, img_url in enumerate(images, 1):
                    if task.cancel_event.is_set():
                        break
                    try:
                        img_resp = sess.get(img_url, timeout=timeout)
                        if img_resp.status_code == 200 and img_resp.content:
                            filepath = target_dir / f"{idx:03d}_tiktok_{post_id}.jpeg"
                            filepath.write_bytes(img_resp.content)
                            downloaded_count += 1
                    except Exception as img_err:
                        logger.debug(f"[Task {task.task_id}] Échec photo #{idx}: {img_err}")

                music_url = data.get("music")
                if music_url and not task.cancel_event.is_set():
                    try:
                        audio_resp = sess.get(music_url, timeout=timeout)
                        if audio_resp.status_code == 200 and audio_resp.content:
                            (target_dir / f"audio_tiktok_{post_id}.mp3").write_bytes(audio_resp.content)
                    except Exception as aud_err:
                        logger.debug(f"[Task {task.task_id}] Échec audio: {aud_err}")

                if downloaded_count > 0:
                    return True
            else:
                logger.debug(f"[Task {task.task_id}] TikWM indique qu'il ne s'agit pas d'un diaporama photo.")
                return False

        # Stratégie 2 : Scraping HTML avec curl_cffi et rehydration JSON
        resp = sess.get(canonical_url, allow_redirects=True, timeout=timeout)
        m_json = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', resp.text)
        if m_json:
            try:
                raw_json = json.loads(m_json.group(1))
                detail = raw_json.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
                item = detail.get("itemInfo", {}).get("itemStruct", {})
                image_post = item.get("imagePost", {})
                images = image_post.get("images", [])

                if images:
                    logger.info(f"[Task {task.task_id}] Détection de {len(images)} photos TikTok via HTML rehydration.")
                    downloaded_count = 0
                    for idx, img in enumerate(images, 1):
                        if task.cancel_event.is_set():
                            break
                        url_list = img.get("imageURL", {}).get("urlList", [])
                        if not url_list:
                            continue
                        img_resp = sess.get(url_list[0], timeout=timeout)
                        if img_resp.status_code == 200 and img_resp.content:
                            filepath = target_dir / f"{idx:03d}_tiktok_{post_id}.jpeg"
                            filepath.write_bytes(img_resp.content)
                            downloaded_count += 1

                    music_url = item.get("music", {}).get("playUrl")
                    if music_url and not task.cancel_event.is_set():
                        try:
                            audio_resp = sess.get(music_url, timeout=timeout)
                            if audio_resp.status_code == 200 and audio_resp.content:
                                (target_dir / f"audio_tiktok_{post_id}.mp3").write_bytes(audio_resp.content)
                        except Exception:
                            pass

                    return downloaded_count > 0
            except Exception as json_err:
                logger.debug(f"[Task {task.task_id}] Erreur parsing rehydration JSON: {json_err}")

        return False

    except Exception as exc:
        logger.warning(f"[Task {task.task_id}] Erreur lors de l'extraction des photos TikTok: {exc}")
        return False
    finally:
        sess.close()


def _extract_tiktok_media_sync(task: DownloadTask, target_dir: Path, timeout: int = 30) -> bool:
    """Télécharge une vidéo ou des photos TikTok via l'API de contournement anti-bot (fallback universel)."""
    sess = requests.Session(impersonate="chrome")

    try:
        canonical_url = resolve_tiktok_url(task.url, timeout=min(timeout, 10))
        m = re.search(r"/(?:photo|video)/(\d+)", canonical_url)
        post_id = m.group(1) if m else "media"

        # 1. Tentative via TikWM API
        data = _fetch_tikwm_data(canonical_url, sess, timeout=min(timeout, 15))
        if data:
            images = data.get("images", [])
            if images:
                # C'est un diaporama photo
                downloaded = 0
                for idx, img_url in enumerate(images, 1):
                    if task.cancel_event.is_set():
                        break
                    try:
                        r = sess.get(img_url, timeout=timeout)
                        if r.status_code == 200 and r.content:
                            (target_dir / f"{idx:03d}_tiktok_{post_id}.jpeg").write_bytes(r.content)
                            downloaded += 1
                    except Exception:
                        pass
                if downloaded > 0:
                    music_url = data.get("music")
                    if music_url and not task.cancel_event.is_set():
                        try:
                            m_resp = sess.get(music_url, timeout=timeout)
                            if m_resp.status_code == 200 and m_resp.content:
                                (target_dir / f"audio_tiktok_{post_id}.mp3").write_bytes(m_resp.content)
                        except Exception:
                            pass
                    return True

            # C'est une vidéo : lien direct sans filigrane
            video_url = data.get("play") or data.get("wmplay")
            if video_url and not task.cancel_event.is_set():
                logger.info(f"[Task {task.task_id}] Téléchargement vidéo TikTok via TikWM...")
                video_path = target_dir / f"tiktok_{post_id}.mp4"
                if stream_download_to_file(sess, video_url, video_path, timeout=timeout, cancel_event=task.cancel_event):
                    logger.info(f"[Task {task.task_id}] Vidéo TikTok téléchargée avec succès.")
                    return True

        # 2. Tentative de secours via Lovetik API
        try:
            logger.info(f"[Task {task.task_id}] Tentative de secours TikTok via Lovetik API...")
            l_resp = sess.post(
                "https://lovetik.com/api/ajax/search",
                data={"query": canonical_url},
                timeout=min(timeout, 15),
            )
            if l_resp.status_code == 200:
                l_data = l_resp.json()
                links = l_data.get("links", [])
                for link in links:
                    dl_url = link.get("a")
                    if dl_url and not task.cancel_event.is_set():
                        video_path = target_dir / f"tiktok_{post_id}.mp4"
                        if stream_download_to_file(sess, dl_url, video_path, timeout=timeout, cancel_event=task.cancel_event):
                            logger.info(f"[Task {task.task_id}] Vidéo TikTok téléchargée via Lovetik.")
                            return True
        except Exception as l_err:
            logger.debug(f"[Task {task.task_id}] Lovetik API error: {l_err}")

        return False

    except Exception as exc:
        logger.warning(f"[Task {task.task_id}] Erreur lors du téléchargement de contournement TikTok: {exc}")
        return False
    finally:
        sess.close()


async def download_tiktok_photos(task: DownloadTask, target_dir: Path, timeout: int = 30) -> bool:
    """Téléchargement asynchrone non-bloquant des photos TikTok."""
    return await asyncio.to_thread(_extract_tiktok_photos_sync, task, target_dir, timeout)


async def download_tiktok_media(task: DownloadTask, target_dir: Path, timeout: int = 30) -> bool:
    """Téléchargement asynchrone de contournement TikTok (vidéos et photos)."""
    return await asyncio.to_thread(_extract_tiktok_media_sync, task, target_dir, timeout)

"""Specialized Facebook photo / album extractor using curl_cffi."""

from __future__ import annotations

import asyncio
import html
import re
import urllib.parse
from pathlib import Path
from typing import List, Optional

from curl_cffi import requests

from app.services.task_manager import DownloadTask
from app.utils.logging import get_logger
from app.utils.network import stream_download_to_file

logger = get_logger(__name__)

# Liste noire de domaines/URLs malveillants, publicitaires ou honeypots connus
BLOCKED_MEDIA_PATTERNS = [
    "getmyfb.com",
    "ssscdn.io/getmyfb",
]


def is_blocked_media_url(url: str) -> bool:
    """Vérifie si une URL de média provient d'un service honeypot ou publicitaire."""
    lower = url.lower()
    return any(p in lower for p in BLOCKED_MEDIA_PATTERNS)


def is_facebook_url(url: str) -> bool:
    """Check if the URL belongs to Facebook."""
    lower = url.lower()
    return any(domain in lower for domain in ["facebook.com", "fb.watch", "fb.com", "m.facebook.com"])


def is_facebook_story_url(url: str) -> bool:
    """Check if the URL belongs to Facebook Stories."""
    lower = url.lower()
    return is_facebook_url(url) and any(domain in lower for domain in ["/stories/", "story.php"])


def is_facebook_video_url(url: str) -> bool:
    """Check if the Facebook URL specifically targets a video or reel."""
    lower = url.lower()
    if not is_facebook_url(lower):
        return False
    video_markers = [
        "/reel/",
        "/reels/",
        "/videos/",
        "/watch",
        "fb.watch",
        "/share/v/",
        "/share/r/",
        "video.php",
    ]
    return any(m in lower for m in video_markers)


def resolve_facebook_url(url: str, timeout: int = 10) -> str:
    """Resolve redirect for Facebook share URLs (e.g. facebook.com/share/...)."""
    lower = url.lower()
    if "facebook.com/share" not in lower and "fb.watch" not in lower:
        return url
    try:
        # 1. Try without following redirects to grab direct location header if available
        resp = requests.get(url, impersonate="chrome124", timeout=timeout, allow_redirects=False)
        loc = resp.headers.get("location")
        if loc and "login.php" not in loc and "checkpoint" not in loc:
            logger.info(f"Resolved Facebook share URL via location header: {url} -> {loc}")
            return loc

        # 2. Try following redirects
        resp = requests.get(url, impersonate="chrome124", timeout=timeout, allow_redirects=True)
        if resp.url and resp.url != url:
            if "login.php" in resp.url or "checkpoint" in resp.url:
                # If redirected to login.php, extract target url from 'next' param
                parsed = urllib.parse.urlparse(resp.url)
                qs = urllib.parse.parse_qs(parsed.query)
                if "next" in qs and qs["next"]:
                    real_target = urllib.parse.unquote(qs["next"][0])
                    logger.info(f"Extracted real target from Facebook login redirect: {url} -> {real_target}")
                    return real_target
                return url
            logger.info(f"Resolved Facebook share URL: {url} -> {resp.url}")
            return resp.url
    except Exception as exc:
        logger.debug(f"Failed to resolve Facebook URL {url}: {exc}")
    return url


async def download_facebook_media(
    task: DownloadTask,
    target_dir: Path,
    timeout: int = 30,
    cookies_file: Optional[Path] = None,
) -> bool:
    """Extract and download public photos from a Facebook post using direct HTML inspection."""
    if is_facebook_video_url(task.url):
        logger.info(f"[Task {task.task_id}] URL Facebook identifiée comme vidéo : extraction de photos ignorée.")
        return False

    logger.info(f"[Task {task.task_id}] Tentative d'extraction des photos Facebook...")

    def _fetch() -> int:
        sess = requests.Session(impersonate="chrome124")
        try:
            if task.cancel_event.is_set():
                return 0

            # Extraction directe par inspection HTML pour les photos publiques
            try:
                target_url = task.url
                if "login.php" in target_url or "next=" in target_url:
                    parsed = urllib.parse.urlparse(target_url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "next" in qs and qs["next"]:
                        target_url = urllib.parse.unquote(qs["next"][0])

                resp = sess.get(target_url, timeout=timeout, allow_redirects=True)
                if resp.status_code != 200:
                    return 0
                txt = resp.text

                # Extraction og:image
                og_images = [
                    html.unescape(x)
                    for x in re.findall(r'<meta property=\"og:image\" content=\"(.*?)\"', txt)
                    if not is_blocked_media_url(x)
                ]

                # Extraction liens CDN scontent
                cdn_matches = re.findall(r'https:\\/\\/scontent[^\s"\',]+\.jpg[^\s"\',]*', txt)
                cdn_urls = [
                    html.unescape(u.replace(r"\/", "/"))
                    for u in cdn_matches
                    if not is_blocked_media_url(u)
                ]

                candidates: List[str] = []
                for u in og_images + cdn_urls:
                    if u not in candidates and "scontent" in u and not is_blocked_media_url(u):
                        candidates.append(u)

                if not candidates:
                    return 0

                downloaded = 0
                for idx, img_url in enumerate(candidates[:10], 1):
                    if task.cancel_event.is_set():
                        break
                    filepath = target_dir / f"{idx:03d}_facebook_photo.jpg"
                    if stream_download_to_file(sess, img_url, filepath, timeout=timeout, cancel_event=task.cancel_event):
                        downloaded += 1
                        if og_images and len(candidates) == 1:
                            break

                return downloaded
            except Exception as e:
                logger.debug(f"[Task {task.task_id}] Erreur extraction Facebook photo: {e}")
                return 0
        finally:
            sess.close()

    count = await asyncio.to_thread(_fetch)
    if count > 0:
        logger.info(f"[Task {task.task_id}] {count} média(s) Facebook téléchargé(s) avec succès.")
        return True
    return False


# Backward compatibility alias
download_facebook_photos = download_facebook_media

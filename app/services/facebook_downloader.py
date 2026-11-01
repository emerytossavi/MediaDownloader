"""Specialized Facebook photo / album extractor using curl_cffi."""

from __future__ import annotations

import asyncio
import base64
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


def is_facebook_url(url: str) -> bool:
    """Check if the URL belongs to Facebook."""
    lower = url.lower()
    return any(domain in lower for domain in ["facebook.com", "fb.watch", "fb.com", "m.facebook.com"])


def is_facebook_story_url(url: str) -> bool:
    """Check if the URL belongs to Facebook Stories."""
    lower = url.lower()
    return is_facebook_url(url) and any(domain in lower for domain in ["/stories/", "story.php"])


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
    """Extract and download media (stories, videos, photos) from a Facebook post using curl_cffi and bypass services."""
    logger.info(f"[Task {task.task_id}] Tentative d'extraction des médias Facebook...")

    def _fetch() -> int:
        sess = requests.Session(impersonate="chrome124")
        try:
            # 1. Attempt bypass extraction via getmyfb service (handles stories, reels, videos, photos)
            try:
                target_url = task.url
                if "login.php" in target_url or "next=" in target_url:
                    parsed = urllib.parse.urlparse(target_url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "next" in qs and qs["next"]:
                        target_url = urllib.parse.unquote(qs["next"][0])

                service_resp = sess.post(
                    "https://getmyfb.com/process",
                    data={"id": target_url},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=timeout,
                )
                if service_resp.status_code == 200:
                    stxt = service_resp.text
                    hd_links = re.findall(r'href="([^"]+)"[^>]*class="[^"]*hd-button', stxt)
                    sd_links = re.findall(r'href="([^"]+)"[^>]*class="[^"]*sd-button', stxt)
                    video_url = hd_links[0] if hd_links else (sd_links[0] if sd_links else None)
                    if video_url and not task.cancel_event.is_set():
                        vpath = target_dir / "001_facebook_video.mp4"
                        if stream_download_to_file(sess, video_url, vpath, timeout=timeout, cancel_event=task.cancel_event):
                            logger.info(f"[Task {task.task_id}] Vidéo/Story Facebook téléchargée via service de contournement.")
                            return 1

                    # Check for photo if no video found
                    img_matches = re.findall(r'class="results-item-image"\s+src="([^"]+)"', stxt)
                    if img_matches and not task.cancel_event.is_set():
                        img_src = img_matches[0]
                        if "/getmyfb/p/" in img_src:
                            b64_part = img_src.split("/getmyfb/p/")[-1]
                            b64_part += "=" * ((4 - len(b64_part) % 4) % 4)
                            try:
                                decoded_url = base64.b64decode(b64_part).decode("utf-8", "ignore")
                                if decoded_url.startswith("http"):
                                    img_src = decoded_url
                            except Exception:
                                pass
                        ipath = target_dir / "001_facebook_photo.jpg"
                        if stream_download_to_file(sess, img_src, ipath, timeout=timeout, cancel_event=task.cancel_event):
                            logger.info(f"[Task {task.task_id}] Image/Story Facebook téléchargée via service de contournement.")
                            return 1
            except Exception as service_err:
                logger.debug(f"[Task {task.task_id}] Service getmyfb indisponible: {service_err}")

            if task.cancel_event.is_set():
                return 0

            # 2. Fallback to direct HTML inspection for public photos
            try:
                resp = sess.get(task.url, timeout=timeout, allow_redirects=True)
                if resp.status_code != 200:
                    return 0
                txt = resp.text

                # Look for og:image
                og_images = [
                    html.unescape(x)
                    for x in re.findall(r'<meta property=\"og:image\" content=\"(.*?)\"', txt)
                ]

                # Look for scontent image links
                cdn_matches = re.findall(r'https:\\/\\/scontent[^\s"\',]+\.jpg[^\s"\',]*', txt)
                cdn_urls = [html.unescape(u.replace(r"\/", "/")) for u in cdn_matches]

                candidates: List[str] = []
                for u in og_images + cdn_urls:
                    if u not in candidates and "scontent" in u:
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

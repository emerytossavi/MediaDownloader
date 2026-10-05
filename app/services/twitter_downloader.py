"""Specialized Twitter/X media downloader bypassing sensitive/NSFW restrictions via FixTweet / VxTwitter APIs."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from curl_cffi import requests

from app.services.task_manager import DownloadTask
from app.utils.logging import get_logger

logger = get_logger(__name__)


def is_twitter_url(url: str) -> bool:
    """Check if the given URL is a Twitter/X URL."""
    lower = url.lower()
    return any(domain in lower for domain in ["twitter.com", "x.com", "t.co/"])


def extract_tweet_id(url: str) -> Optional[str]:
    """Extract numeric tweet ID from a Twitter/X URL."""
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None


async def download_twitter_media(
    task: DownloadTask,
    target_dir: Path,
    timeout: int = 30,
) -> bool:
    """Download videos or photos from Twitter/X using bypass APIs for sensitive/restricted tweets."""
    tweet_id = extract_tweet_id(task.url)
    if not tweet_id:
        return False

    logger.info(f"[Task {task.task_id}] Tentative de téléchargement Twitter via API de contournement (Tweet ID: {tweet_id})...")

    def _fetch_and_download() -> int:
        api_endpoints = [
            f"https://api.fxtwitter.com/status/{tweet_id}",
            f"https://api.vxtwitter.com/Twitter/status/{tweet_id}",
        ]

        tweet_data: Optional[Dict[str, Any]] = None
        for endpoint in api_endpoints:
            try:
                resp = requests.get(endpoint, impersonate="chrome124", timeout=10)
                if resp.status_code == 200:
                    payload = resp.json()
                    tweet_data = payload.get("tweet", payload)
                    if tweet_data:
                        break
            except Exception as exc:
                logger.debug(f"[Task {task.task_id}] Erreur endpoint {endpoint}: {exc}")

        if not tweet_data:
            return 0

        media = tweet_data.get("media") or {}
        videos = list(media.get("videos", [])) if media.get("videos") else []
        photos = list(media.get("photos", [])) if media.get("photos") else []

        # If no explicit media lists, try media.get('all')
        if not videos and not photos and "all" in media and media["all"]:
            for item in media["all"]:
                if item.get("type") == "video":
                    videos.append(item)
                elif item.get("type") == "photo":
                    photos.append(item)

        # If tweet is a Quote Tweet and has no media of its own, check quoted tweet
        if not videos and not photos and "quote" in tweet_data and isinstance(tweet_data["quote"], dict):
            quote_media = tweet_data["quote"].get("media") or {}
            quote_videos = quote_media.get("videos", [])
            quote_photos = quote_media.get("photos", [])
            if quote_videos:
                videos.extend(quote_videos)
            if quote_photos:
                photos.extend(quote_photos)
            if not videos and not photos and "all" in quote_media and quote_media["all"]:
                for item in quote_media["all"]:
                    if item.get("type") == "video":
                        videos.append(item)
                    elif item.get("type") == "photo":
                        photos.append(item)

        if not videos and not photos:
            logger.debug(f"[Task {task.task_id}] Aucun média dans les données du tweet {tweet_id}.")
            return 0

        downloaded_count = 0
        sess = requests.Session(impersonate="chrome124")

        try:
            # 1. Download Videos
            for idx, vid in enumerate(videos, 1):
                if task.cancel_event.is_set():
                    break

                # Pick highest bitrate MP4 variant
                variants = vid.get("variants", [])
                mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
                mp4_variants.sort(key=lambda x: x.get("bitrate", 0), reverse=True)

                best_video_url = mp4_variants[0]["url"] if mp4_variants else vid.get("url")
                if not best_video_url:
                    continue

                try:
                    r = sess.get(best_video_url, timeout=timeout)
                    if r.status_code == 200 and len(r.content) > 1024:
                        vid_id = str(vid.get("id", f"{idx:03d}"))
                        ext = "mp4"
                        clean_url = best_video_url.split("?")[0]
                        if "." in clean_url:
                            possible_ext = clean_url.split(".")[-1].lower()
                            if possible_ext in ["mp4", "mkv", "webm", "mov"]:
                                ext = possible_ext
                        filename = f"{idx:03d}_twitter_{vid_id}.{ext}"
                        (target_dir / filename).write_bytes(r.content)
                        downloaded_count += 1
                except Exception as err:
                    logger.debug(f"[Task {task.task_id}] Erreur téléchargement vidéo Twitter: {err}")

            # 2. Download Photos
            for idx, pic in enumerate(photos, 1 + len(videos)):
                if task.cancel_event.is_set():
                    break

                pic_url = pic.get("url")
                if not pic_url:
                    continue

                try:
                    r = sess.get(pic_url, timeout=timeout)
                    if r.status_code == 200 and len(r.content) > 1024:
                        pic_id = str(pic.get("id", f"{idx:03d}"))
                        ext = "jpg"
                        clean_url = pic_url.split("?")[0]
                        if "." in clean_url:
                            possible_ext = clean_url.split(".")[-1].lower()
                            if possible_ext in ["jpg", "jpeg", "png", "webp"]:
                                ext = possible_ext
                        filename = f"{idx:03d}_twitter_{pic_id}.{ext}"
                        (target_dir / filename).write_bytes(r.content)
                        downloaded_count += 1
                except Exception as err:
                    logger.debug(f"[Task {task.task_id}] Erreur téléchargement photo Twitter: {err}")

            return downloaded_count
        finally:
            sess.close()

    count = await asyncio.to_thread(_fetch_and_download)
    if count > 0:
        logger.info(f"[Task {task.task_id}] {count} média(s) Twitter téléchargé(s) via contournement avec succès.")
        return True

    return False

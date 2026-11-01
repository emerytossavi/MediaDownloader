"""Specialized Twitter/X media downloader bypassing sensitive/NSFW restrictions via FixTweet / VxTwitter APIs."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from curl_cffi import requests

from app.services.task_manager import DownloadTask
from app.utils.logging import get_logger
from app.utils.network import stream_download_to_file

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
    max_file_size: Optional[int] = None,
    timeout: int = 30,
) -> bool:
    """Download videos or photos from Twitter/X using bypass APIs for sensitive/restricted tweets.

    Downloads streams in chunks directly to disk and selects appropriate video qualities
    that respect `max_file_size`, preventing memory saturation and process termination.
    """
    tweet_id = extract_tweet_id(task.url)
    if not tweet_id:
        return False

    logger.info(
        f"[Task {task.task_id}] Tentative de téléchargement Twitter via API de contournement (Tweet ID: {tweet_id})..."
    )

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
            # 1. Download Videos with streaming and intelligent quality selection
            for idx, vid in enumerate(videos, 1):
                if task.cancel_event.is_set():
                    break

                variants = vid.get("variants", [])
                mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
                if not mp4_variants and vid.get("url"):
                    mp4_variants = [{"url": vid.get("url"), "bitrate": 0}]

                if not mp4_variants:
                    continue

                duration = float(vid.get("duration") or 0.0)

                # Intelligent variant selection based on estimated size vs max_file_size
                if max_file_size and duration > 0:
                    # Preferred: fits estimated size, sorted best quality first
                    fits = [
                        v
                        for v in mp4_variants
                        if (v.get("bitrate", 0) / 8.0) * duration <= max_file_size
                    ]
                    fits.sort(key=lambda x: x.get("bitrate", 0), reverse=True)

                    # Fallback: candidate variants sorted smallest first (actual size can be lower than peak bitrate)
                    oversized = [v for v in mp4_variants if v not in fits]
                    oversized.sort(key=lambda x: x.get("bitrate", 0))

                    candidate_variants = fits + oversized
                else:
                    candidate_variants = sorted(
                        mp4_variants, key=lambda x: x.get("bitrate", 0), reverse=True
                    )

                vid_id = str(vid.get("id", f"{idx:03d}"))
                video_downloaded = False

                for variant in candidate_variants:
                    if task.cancel_event.is_set():
                        break

                    best_video_url = variant.get("url")
                    if not best_video_url:
                        continue

                    ext = "mp4"
                    clean_url = best_video_url.split("?")[0]
                    if "." in clean_url:
                        possible_ext = clean_url.split(".")[-1].lower()
                        if possible_ext in ["mp4", "mkv", "webm", "mov"]:
                            ext = possible_ext
                    filename = f"{idx:03d}_twitter_{vid_id}.{ext}"
                    dest_path = target_dir / filename

                    success = stream_download_to_file(
                        session=sess,
                        url=best_video_url,
                        dest_path=dest_path,
                        max_size=max_file_size,
                        timeout=timeout,
                        cancel_event=task.cancel_event,
                    )
                    if success:
                        downloaded_count += 1
                        video_downloaded = True
                        break
                    else:
                        logger.debug(
                            f"[Task {task.task_id}] Variante {variant.get('bitrate')} bps ignorée (dépassement limite ou erreur)."
                        )

                if not video_downloaded:
                    logger.warning(
                        f"[Task {task.task_id}] Impossible de télécharger une variante valide pour la vidéo Twitter {vid_id}."
                    )

            # 2. Download Photos with streaming
            for idx, pic in enumerate(photos, 1 + len(videos)):
                if task.cancel_event.is_set():
                    break

                pic_url = pic.get("url")
                if not pic_url:
                    continue

                pic_id = str(pic.get("id", f"{idx:03d}"))
                ext = "jpg"
                clean_url = pic_url.split("?")[0]
                if "." in clean_url:
                    possible_ext = clean_url.split(".")[-1].lower()
                    if possible_ext in ["jpg", "jpeg", "png", "webp"]:
                        ext = possible_ext
                filename = f"{idx:03d}_twitter_{pic_id}.{ext}"
                dest_path = target_dir / filename

                success = stream_download_to_file(
                    session=sess,
                    url=pic_url,
                    dest_path=dest_path,
                    max_size=max_file_size,
                    timeout=timeout,
                    cancel_event=task.cancel_event,
                )
                if success:
                    downloaded_count += 1

            return downloaded_count
        finally:
            sess.close()

    count = await asyncio.to_thread(_fetch_and_download)
    if count > 0:
        logger.info(f"[Task {task.task_id}] {count} média(s) Twitter téléchargé(s) via contournement avec succès.")
        return True

    return False

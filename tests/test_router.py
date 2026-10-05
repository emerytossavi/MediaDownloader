"""Unit tests for the extractor router and fallback mechanisms."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.media_inspector import MediaInspectionResult, MediaItem, MediaType
from app.services.router import ExtractorRouter, ExtractorType
from app.services.task_manager import DownloadTask
from app.services.ytdlp_downloader import ExtractorError


def test_router_detection_video_platforms():
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    assert router.detect_primary_extractor("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == ExtractorType.YTDLP
    assert router.detect_primary_extractor("https://vimeo.com/123456") == ExtractorType.YTDLP


def test_router_detection_gallery_platforms():
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    assert router.detect_primary_extractor("https://danbooru.donmai.us/posts/123456") == ExtractorType.GALLERYDL


@pytest.mark.asyncio
async def test_router_execute_download_with_fallback(tmp_path: Path):
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    task = DownloadTask(task_id="t1", user_id=1, chat_id=1, url="https://example.com/item")

    # Mock download_with_ytdlp to fail, and download_with_gallerydl to produce a file
    async def fake_ytdlp(*args, **kwargs):
        raise ExtractorError("yt-dlp failed to extract")

    async def fake_gallerydl(*args, **kwargs):
        # Simulate creating an image file
        target_dir = kwargs.get("target_dir") or args[1]
        (target_dir / "001_photo.jpg").write_bytes(b"image data")

    with patch("app.services.router.download_with_ytdlp", side_effect=fake_ytdlp):
        with patch("app.services.router.download_with_gallerydl", side_effect=fake_gallerydl):
            tool, inspection = await router.execute_download(task, tmp_path)
            assert tool == ExtractorType.GALLERYDL
            assert inspection.has_valid_media is True
            assert inspection.valid_items[0].filename == "001_photo.jpg"

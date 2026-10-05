"""Unit tests for gallerydl_downloader and ytdlp_downloader error classification and arguments."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from app.services.gallerydl_downloader import _classify_gallerydl_error, download_with_gallerydl
from app.services.task_manager import DownloadTask
from app.services.ytdlp_downloader import (
    AuthenticationRequiredError,
    ContentNotFoundError,
    ExtractorError,
    _classify_ytdlp_error,
)


def test_classify_gallerydl_errors():
    with pytest.raises(AuthenticationRequiredError):
        _classify_gallerydl_error("HTTP 401 Unauthorized: private post")

    with pytest.raises(ContentNotFoundError):
        _classify_gallerydl_error("HTTP 404 Not Found: deleted")

    with pytest.raises(ExtractorError) as exc_info:
        _classify_gallerydl_error("[error] Failed to parse JSON metadata")
    assert "Failed to parse JSON" in str(exc_info.value)


def test_classify_ytdlp_errors():
    with pytest.raises(AuthenticationRequiredError):
        _classify_ytdlp_error("ERROR: This video is private and requires login")

    with pytest.raises(ContentNotFoundError):
        _classify_ytdlp_error("ERROR: Video unavailable or has been removed")

    with pytest.raises(ExtractorError) as exc_info:
        _classify_ytdlp_error("ERROR: [TikTok] Unexpected response from webpage request")
    assert "anti-bot" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_gallerydl_command_args(tmp_path: Path):
    task = DownloadTask(
        task_id="test-task",
        user_id=123,
        chat_id=123,
        url="https://example.com/image.jpg",
    )

    captured_cmd = []

    async def fake_subprocess(*args, **kwargs):
        captured_cmd.extend(args)
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"")
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        await download_with_gallerydl(task, tmp_path, timeout=30)

    # Verify that --http-timeout is used and NOT --timeout
    assert "--http-timeout" in captured_cmd
    assert "--timeout" not in captured_cmd


def test_is_tiktok_url():
    from app.services.tiktok_photo_downloader import is_tiktok_url
    assert is_tiktok_url("https://vt.tiktok.com/ZSb93AEpS/") is True
    assert is_tiktok_url("https://www.tiktok.com/@user/photo/12345") is True
    assert is_tiktok_url("https://www.instagram.com/p/123") is False


def test_is_instagram_url():
    from app.services.instagram_photo_downloader import is_instagram_url
    assert is_instagram_url("https://www.instagram.com/p/DeDclIvDWAD/") is True
    assert is_instagram_url("https://instagr.am/reel/123") is True
    assert is_instagram_url("https://vt.tiktok.com/123") is False

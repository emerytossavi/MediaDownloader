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

    with pytest.raises(ExtractorError) as exc_info:
        _classify_ytdlp_error(
            "ERROR: [generic] videoplayback: HTTP Error 403: Forbidden",
            url="https://rr3---sn-4g5lznlr.googlevideo.com/videoplayback?expire=123",
        )
    assert "Google Video" in str(exc_info.value)
    assert "YouTube" in str(exc_info.value)


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
    from app.services.tiktok_photo_downloader import is_tiktok_short_url, is_tiktok_url
    assert is_tiktok_url("https://vt.tiktok.com/ZSb93AEpS/") is True
    assert is_tiktok_url("https://www.tiktok.com/@user/photo/12345") is True
    assert is_tiktok_url("https://www.instagram.com/p/123") is False

    assert is_tiktok_short_url("https://vt.tiktok.com/ZSbx3CSDH/") is True
    assert is_tiktok_short_url("https://vm.tiktok.com/ZSbx3CSDH/") is True
    assert is_tiktok_short_url("https://www.tiktok.com/t/ZSbx3CSDH/") is True
    assert is_tiktok_short_url("https://www.tiktok.com/@user/video/123") is False


@pytest.mark.asyncio
async def test_download_tiktok_photos_tikwm(tmp_path: Path):
    from unittest.mock import MagicMock
    from app.services.tiktok_photo_downloader import download_tiktok_photos

    task = DownloadTask(
        task_id="test-tt-photos",
        user_id=123,
        chat_id=123,
        url="https://vt.tiktok.com/ZSbx3CSDH/",
    )

    mock_api_resp = MagicMock()
    mock_api_resp.status_code = 200
    mock_api_resp.json.return_value = {
        "code": 0,
        "msg": "success",
        "data": {
            "title": "Test Photo Post",
            "images": [
                "https://example.com/img1.jpeg",
                "https://example.com/img2.jpeg",
            ],
            "music": "https://example.com/audio.mp3",
        },
    }

    mock_img_resp = MagicMock()
    mock_img_resp.status_code = 200
    mock_img_resp.content = b"fake_jpeg_content"

    mock_sess = MagicMock()
    mock_sess.get.side_effect = [mock_api_resp, mock_img_resp, mock_img_resp, mock_img_resp]

    with patch("curl_cffi.requests.Session", return_value=mock_sess), \
         patch("app.services.tiktok_photo_downloader.resolve_tiktok_url", return_value=task.url):
        ok = await download_tiktok_photos(task, tmp_path, timeout=10)

    assert ok is True
    jpeg_files = list(tmp_path.glob("*.jpeg"))
    assert len(jpeg_files) == 2


@pytest.mark.asyncio
async def test_download_tiktok_media_video_tikwm(tmp_path: Path):
    from unittest.mock import MagicMock
    from app.services.tiktok_photo_downloader import download_tiktok_media

    task = DownloadTask(
        task_id="test-tt-video",
        user_id=123,
        chat_id=123,
        url="https://vt.tiktok.com/ZSbx3VA1S/",
    )

    mock_api_resp = MagicMock()
    mock_api_resp.status_code = 200
    mock_api_resp.json.return_value = {
        "code": 0,
        "msg": "success",
        "data": {
            "title": "Test Video Post",
            "images": [],
            "play": "https://example.com/video.mp4",
        },
    }

    mock_vid_resp = MagicMock()
    mock_vid_resp.status_code = 200
    mock_vid_resp.content = b"fake_video_mp4_bytes"

    mock_sess = MagicMock()
    mock_sess.get.side_effect = [mock_api_resp, mock_vid_resp]

    with patch("curl_cffi.requests.Session", return_value=mock_sess), \
         patch("app.services.tiktok_photo_downloader.resolve_tiktok_url", return_value=task.url):
        ok = await download_tiktok_media(task, tmp_path, timeout=10)

    assert ok is True
    mp4_files = list(tmp_path.glob("*.mp4"))
    assert len(mp4_files) == 1


def test_is_instagram_url():
    from app.services.instagram_photo_downloader import is_instagram_url
    assert is_instagram_url("https://www.instagram.com/p/DeDclIvDWAD/") is True
    assert is_instagram_url("https://instagr.am/reel/123") is True
    assert is_instagram_url("https://vt.tiktok.com/123") is False


@pytest.mark.asyncio
async def test_download_instagram_photos_success(tmp_path: Path):
    import json
    from unittest.mock import MagicMock
    from app.services.instagram_photo_downloader import download_instagram_photos

    task = DownloadTask(
        task_id="test-ig",
        user_id=123,
        chat_id=123,
        url="https://www.instagram.com/p/DeDclIvDWAD/",
    )

    metadata_line1 = json.dumps({"id": "photo_1", "thumbnail": "https://example.com/pic1.jpg"})
    metadata_line2 = json.dumps({"id": "photo_2", "thumbnails": [{"url": "https://example.com/pic2.jpg"}]})
    fake_stdout = f"{metadata_line1}\n{metadata_line2}\n".encode()

    proc = AsyncMock()
    proc.communicate.return_value = (fake_stdout, b"")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake_jpeg_data"

    mock_sess = MagicMock()
    mock_sess.get.return_value = mock_resp

    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("curl_cffi.requests.Session", return_value=mock_sess):
        result = await download_instagram_photos(task, tmp_path, timeout=10)

    assert result is True
    jpg_files = list(tmp_path.glob("*.jpg"))
    assert len(jpg_files) == 2


@pytest.mark.asyncio
async def test_download_instagram_photos_empty_metadata_returns_false(tmp_path: Path):
    from app.services.instagram_photo_downloader import download_instagram_photos

    task = DownloadTask(
        task_id="test-ig-empty",
        user_id=123,
        chat_id=123,
        url="https://www.instagram.com/p/EmptyPost/",
    )

    proc = AsyncMock()
    proc.communicate.return_value = (b"", b"")

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await download_instagram_photos(task, tmp_path, timeout=10)

    assert result is False



def test_is_facebook_url():
    from app.services.facebook_downloader import is_facebook_story_url, is_facebook_url, resolve_facebook_url
    assert is_facebook_url("https://www.facebook.com/share/19WZ9BuC2F/") is True
    assert is_facebook_url("https://fb.watch/xyz/") is True
    assert is_facebook_url("https://www.instagram.com/p/123") is False

    assert is_facebook_story_url("https://www.facebook.com/stories/110425051397756/12345/") is True
    assert is_facebook_story_url("https://www.facebook.com/reel/12345") is False

    # URLs not matching share pattern should return immediately
    assert resolve_facebook_url("https://www.facebook.com/user/posts/123") == "https://www.facebook.com/user/posts/123"


def test_is_twitter_url():
    from app.services.twitter_downloader import is_twitter_url, extract_tweet_id
    assert is_twitter_url("https://x.com/prashan20020275/status/2106769227411607893") is True
    assert is_twitter_url("https://twitter.com/user/status/12345") is True
    assert is_twitter_url("https://instagram.com/p/123") is False

    assert extract_tweet_id("https://x.com/user/status/2106769227411607893?s=20") == "2106769227411607893"
    assert extract_tweet_id("https://x.com/home") is None
@pytest.mark.asyncio
async def test_download_twitter_quote_media(tmp_path: Path):
    from unittest.mock import MagicMock
    from app.services.twitter_downloader import download_twitter_media

    task = DownloadTask(
        task_id="test-tw-quote",
        user_id=123,
        chat_id=123,
        url="https://x.com/user/status/2106697357652005019",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "tweet": {
            "media": None,
            "quote": {
                "media": {
                    "photos": [
                        {"id": "photo1", "url": "https://pbs.twimg.com/media/pic1.jpg"},
                        {"id": "photo2", "url": "https://pbs.twimg.com/media/pic2.jpg"},
                    ]
                }
            },
        }
    }

    mock_img_resp = MagicMock()
    mock_img_resp.status_code = 200
    mock_img_resp.content = b"fake_image_bytes_" * 100

    mock_sess = MagicMock()
    mock_sess.get.return_value = mock_img_resp

    with patch("curl_cffi.requests.get", return_value=mock_resp), \
         patch("curl_cffi.requests.Session", return_value=mock_sess):
        success = await download_twitter_media(task, tmp_path, timeout=10)

    assert success is True
    files = list(tmp_path.glob("*.jpg"))
    assert len(files) == 2


def test_resolve_facebook_url_login_redirect():
    from unittest.mock import MagicMock
    from app.services.facebook_downloader import resolve_facebook_url

    login_url = "https://www.facebook.com/login.php?next=https%3A%2F%2Fwww.facebook.com%2Fstories%2F123%2F456%2F%3Fview_single%3D1&rdid=abc"
    mock_resp = MagicMock()
    mock_resp.headers = {}
    mock_resp.url = login_url

    with patch("curl_cffi.requests.get", return_value=mock_resp):
        resolved = resolve_facebook_url("https://www.facebook.com/share/12345/")

    assert resolved == "https://www.facebook.com/stories/123/456/?view_single=1"


@pytest.mark.asyncio
async def test_download_facebook_story_bypass(tmp_path: Path):
    from unittest.mock import MagicMock
    from app.services.facebook_downloader import download_facebook_media

    task = DownloadTask(
        task_id="test-fb-story",
        user_id=123,
        chat_id=123,
        url="https://www.facebook.com/share/1C53jcvcfs/",
    )

    mock_service_resp = MagicMock()
    mock_service_resp.status_code = 200
    mock_service_resp.text = '<a href="https://example.com/video.mp4" class="bxmfunk-button hd-button">HD</a>'

    mock_vid_resp = MagicMock()
    mock_vid_resp.status_code = 200
    mock_vid_resp.content = b"fake_video_mp4_content_" * 100

    mock_sess = MagicMock()
    mock_sess.post.return_value = mock_service_resp
    mock_sess.get.return_value = mock_vid_resp

    with patch("curl_cffi.requests.Session", return_value=mock_sess):
        success = await download_facebook_media(task, tmp_path, timeout=10)

    assert success is True
    files = list(tmp_path.glob("*.mp4"))
    assert len(files) == 1


@pytest.mark.asyncio
async def test_download_twitter_video_quality_selection(tmp_path: Path):
    from unittest.mock import MagicMock
    from app.services.twitter_downloader import download_twitter_media

    task = DownloadTask(
        task_id="test-tw-video",
        user_id=123,
        chat_id=123,
        url="https://x.com/user/status/2116150843737309329",
    )

    # 1080p is ~1GB (exceeds 50MB limit), 320p is ~30MB (fits within 50MB limit)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "tweet": {
            "media": {
                "videos": [
                    {
                        "id": "vid123",
                        "duration": 800.0,
                        "variants": [
                            {
                                "content_type": "video/mp4",
                                "bitrate": 10_000_000,
                                "url": "https://video.twimg.com/vid_1080p.mp4",
                            },
                            {
                                "content_type": "video/mp4",
                                "bitrate": 300_000,
                                "url": "https://video.twimg.com/vid_320p.mp4",
                            },
                        ],
                    }
                ]
            }
        }
    }

    mock_sess = MagicMock()

    def mock_get(url, stream=False, timeout=10):
        m = MagicMock()
        m.status_code = 200
        if "vid_1080p" in url:
            m.headers = {"content-length": str(1000 * 1024 * 1024)}
            m.content = b"huge_content" * 1000
        else:
            m.headers = {"content-length": str(30 * 1024 * 1024)}
            m.iter_content.return_value = [b"valid_video_bytes" * 100]
        return m

    mock_sess.get.side_effect = mock_get

    with patch("curl_cffi.requests.get", return_value=mock_resp), \
         patch("curl_cffi.requests.Session", return_value=mock_sess):
        success = await download_twitter_media(
            task=task,
            target_dir=tmp_path,
            max_file_size=50 * 1024 * 1024,
            timeout=10,
        )

    assert success is True
    files = list(tmp_path.glob("*.mp4"))
    assert len(files) == 1
    assert "vid123" in files[0].name



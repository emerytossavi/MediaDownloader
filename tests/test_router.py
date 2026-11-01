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
    assert router.detect_extractors("https://youtu.be/56lkofpjOAs") == [ExtractorType.YTDLP]
    assert router.detect_extractors("https://rr3---sn-4g5lznlr.googlevideo.com/videoplayback?expire=123") == [ExtractorType.YTDLP]
    assert router.detect_extractors("https://www.facebook.com/stories/104289841714539/UzpfSVNDOjQ3MzExOTcyMDcxNjQxMTk=/?view_single=1") == [ExtractorType.YTDLP]


def test_is_googlevideo_url():
    from app.services.router import is_googlevideo_url
    assert is_googlevideo_url("https://rr3---sn-4g5lznlr.googlevideo.com/videoplayback?expire=123") is True
    assert is_googlevideo_url("https://googlevideo.com/videoplayback") is True
    assert is_googlevideo_url("https://youtube.com/watch?v=123") is False


@pytest.mark.asyncio
async def test_router_youtube_never_calls_gallerydl(tmp_path: Path):
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    task = DownloadTask(task_id="t_yt", user_id=1, chat_id=1, url="https://youtu.be/56lkofpjOAs")

    async def fake_ytdlp(*args, **kwargs):
        raise ExtractorError("Le fichier vidéo dépasse la limite maximale autorisée (50 Mo) pour Telegram.")

    async def fake_gallerydl(*args, **kwargs):
        pytest.fail("gallery-dl should never be called on YouTube URLs")

    with patch("app.services.router.download_with_ytdlp", side_effect=fake_ytdlp), \
         patch("app.services.router.download_with_gallerydl", side_effect=fake_gallerydl):
        with pytest.raises(ExtractorError) as exc_info:
            await router.execute_download(task, tmp_path)
        assert "dépasse la limite maximale" in str(exc_info.value)


@pytest.mark.asyncio
async def test_router_facebook_story_never_calls_gallerydl(tmp_path: Path):
    from app.services.ytdlp_downloader import AuthenticationRequiredError
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    task = DownloadTask(
        task_id="t_fb_story",
        user_id=1,
        chat_id=1,
        url="https://www.facebook.com/stories/104289841714539/UzpfSVNDOjQ3MzExOTcyMDcxNjQxMTk=/?view_single=1",
    )

    async def fake_ytdlp(*args, **kwargs):
        raise AuthenticationRequiredError("Cette Story Facebook nécessite un compte connecté (cookies manquants ou expirés).")

    async def fake_gallerydl(*args, **kwargs):
        pytest.fail("gallery-dl should never be called on Facebook Story URLs")

    with patch("app.services.router.download_with_ytdlp", side_effect=fake_ytdlp), \
         patch("app.services.router.download_with_gallerydl", side_effect=fake_gallerydl):
        with pytest.raises(AuthenticationRequiredError) as exc_info:
            await router.execute_download(task, tmp_path)
        assert "Story Facebook" in str(exc_info.value)
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    # Expiration in the future
    task = DownloadTask(
        task_id="t_gv",
        user_id=1,
        chat_id=1,
        url="https://rr3---sn-4g5lznlr.googlevideo.com/videoplayback?expire=9999999999",
    )

    async def fake_ytdlp(*args, **kwargs):
        raise ExtractorError("HTTP Error 403: Forbidden")

    async def fake_gallerydl(*args, **kwargs):
        pytest.fail("gallery-dl should never be called on googlevideo URLs")

    with patch("app.services.router.download_with_ytdlp", side_effect=fake_ytdlp), \
         patch("app.services.router.download_with_gallerydl", side_effect=fake_gallerydl):
        with pytest.raises(ExtractorError) as exc_info:
            await router.execute_download(task, tmp_path)
        assert "HTTP Error 403: Forbidden" in str(exc_info.value)


@pytest.mark.asyncio
async def test_router_expired_googlevideo_fast_fail(tmp_path: Path):
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    # Expiration in the past (1000000000 = year 2001)
    task = DownloadTask(
        task_id="t_gv_exp",
        user_id=1,
        chat_id=1,
        url="https://rr3---sn-4g5lznlr.googlevideo.com/videoplayback?expire=1000000000",
    )

    with pytest.raises(ExtractorError) as exc_info:
        await router.execute_download(task, tmp_path)
    assert "Google Video a expiré" in str(exc_info.value)



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


@pytest.mark.asyncio
async def test_router_tiktok_fallback(tmp_path: Path):
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    task = DownloadTask(task_id="t_tt", user_id=1, chat_id=1, url="https://vt.tiktok.com/ZSbx3VA1S/")

    async def fake_ytdlp(*args, **kwargs):
        raise ExtractorError("TikTok challenge block")

    async def fake_gallerydl(*args, **kwargs):
        raise ExtractorError("La plateforme a bloqué la requête (protection anti-bot/Cloudflare).")

    async def fake_tiktok_media(*args, **kwargs):
        target_dir = kwargs.get("target_dir") or args[1]
        (target_dir / "tiktok_123.mp4").write_bytes(b"mp4 content" * 100)
        return True

    with patch("app.services.router.download_with_ytdlp", side_effect=fake_ytdlp), \
         patch("app.services.router.download_with_gallerydl", side_effect=fake_gallerydl), \
         patch("app.services.router.download_tiktok_photos", return_value=False), \
         patch("app.services.router.download_tiktok_media", side_effect=fake_tiktok_media):
        tool, inspection = await router.execute_download(task, tmp_path)
        assert tool == ExtractorType.YTDLP
        assert inspection.has_valid_media is True
        assert inspection.valid_items[0].filename == "tiktok_123.mp4"


def test_is_facebook_video_url():
    from app.services.facebook_downloader import is_facebook_video_url
    assert is_facebook_video_url("https://www.facebook.com/share/v/1ESATe4yFX/") is True
    assert is_facebook_video_url("https://www.facebook.com/share/r/12345/") is True
    assert is_facebook_video_url("https://www.facebook.com/reel/2211281456080141/") is True
    assert is_facebook_video_url("https://www.facebook.com/watch/?v=123456789") is True
    assert is_facebook_video_url("https://fb.watch/xyz123/") is True
    assert is_facebook_video_url("https://www.facebook.com/username/videos/123456789/") is True
    assert is_facebook_video_url("https://www.facebook.com/photo.php?fbid=123") is False
    assert is_facebook_video_url("https://www.facebook.com/username/posts/123") is False
    assert is_facebook_video_url("https://youtube.com/watch?v=123") is False


@pytest.mark.asyncio
async def test_router_facebook_video_never_falls_back_to_photos(tmp_path: Path):
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    task = DownloadTask(task_id="t_fb_vid", user_id=1, chat_id=1, url="https://www.facebook.com/share/v/1ESATe4yFX/")

    async def fake_ytdlp(*args, **kwargs):
        raise ExtractorError("Le fichier vidéo dépasse la limite maximale autorisée (50 Mo) pour Telegram.")

    async def fake_fb_photos(*args, **kwargs):
        pytest.fail("download_facebook_photos should NEVER be called on a Facebook video URL!")

    with patch("app.services.router.download_with_ytdlp", side_effect=fake_ytdlp), \
         patch("app.services.router.download_facebook_photos", side_effect=fake_fb_photos), \
         patch("app.services.router.resolve_facebook_url", return_value="https://www.facebook.com/reel/2211281456080141/"):
        with pytest.raises(ExtractorError) as exc_info:
            await router.execute_download(task, tmp_path)
        assert "dépasse la limite maximale" in str(exc_info.value)


@pytest.mark.asyncio
async def test_probe_media_facebook_video():
    router = ExtractorRouter(max_file_size=50 * 1024 * 1024, download_timeout=60)
    url = "https://www.facebook.com/share/v/1ESATe4yFX/"

    fake_stdout = b"331.65;NA;1926.857;Titre Test Facebook\n"
    proc = AsyncMock()
    proc.communicate.return_value = (fake_stdout, b"")
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("app.services.router.resolve_facebook_url", return_value="https://www.facebook.com/reel/2211281456080141/"):
        probe_info = await router.probe_media(url)

    assert probe_info is not None
    assert probe_info["duration"] == 331.65
    assert probe_info["title"] == "Titre Test Facebook"
    # estimated size = 1926.857 * 1000 / 8 * 331.65 =~ 79,879,907
    assert probe_info["estimated_size"] > 70 * 1024 * 1024



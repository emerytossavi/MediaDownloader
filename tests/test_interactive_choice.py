"""Tests for large media interactive choices and 30-second fallback to splitting."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import CallbackQuery, Update, User

from app.bot.handlers import BotHandlers
from app.config import AppConfig
from app.services.media_inspector import MediaInspectionResult, MediaItem, MediaType
from app.services.router import ExtractorRouter, ExtractorType
from app.services.task_manager import DownloadTask, TaskManager


@pytest.fixture
def mock_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        telegram_bot_token="TEST_BOT_TOKEN_123",
        max_file_size=50 * 1024 * 1024,
        max_concurrent_downloads=2,
        max_concurrent_downloads_per_user=1,
        download_timeout=30,
        temp_dir=tmp_path / "temp",
        log_level="INFO",
        ytdlp_bin="yt-dlp",
        gallerydl_bin="gallery-dl",
        ffmpeg_bin="ffmpeg",
    )


@pytest.mark.asyncio
async def test_choice_callback_sets_choice(mock_config):
    task_manager = TaskManager(max_concurrent=2, max_per_user=1)
    router = MagicMock(spec=ExtractorRouter)
    handlers = BotHandlers(config=mock_config, task_manager=task_manager, router=router)

    task = await task_manager.create_task(user_id=123, chat_id=456, url="https://example.com/video")

    update = MagicMock(spec=Update)
    query = MagicMock(spec=CallbackQuery)
    query.data = f"choice:compress:{task.task_id}"
    query.from_user = User(id=123, first_name="Test", is_bot=False)
    query.answer = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    await handlers.choice_callback(update, context)

    assert task.user_choice_event.is_set()
    assert task.user_choice == "compress"
    query.answer.assert_called_once()


@pytest.mark.asyncio
async def test_interactive_choice_timeout_defaults_to_split(mock_config, tmp_path):
    task_manager = TaskManager(max_concurrent=2, max_per_user=1)
    router = MagicMock(spec=ExtractorRouter)
    # Probe reports a 120MB video (large)
    router.probe_media = AsyncMock(return_value={"estimated_size": 120 * 1024 * 1024, "duration": 800, "title": "Large Movie"})
    
    dummy_item = MediaItem(
        path=tmp_path / "dummy.mp4",
        filename="dummy.mp4",
        size_bytes=1000,
        media_type=MediaType.VIDEO,
        is_oversized=False,
    )
    router.execute_download = AsyncMock(return_value=(ExtractorType.YTDLP, MediaInspectionResult(items=[dummy_item], valid_items=[dummy_item], oversized_items=[])))

    handlers = BotHandlers(config=mock_config, task_manager=task_manager, router=router)

    task = await task_manager.create_task(user_id=123, chat_id=456, url="https://example.com/large_video")
    notifier = MagicMock()
    notifier.update = AsyncMock()

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    # Fast-forward timeout: replace asyncio.wait_for with simulated TimeoutError
    orig_wait_for = asyncio.wait_for

    async def fake_wait_for(fut, timeout):
        if timeout == 30.0:
            raise asyncio.TimeoutError()
        return await orig_wait_for(fut, timeout)

    with patch("asyncio.wait_for", side_effect=fake_wait_for), \
         patch("app.bot.handlers.MediaSender.send_media_result", new_callable=AsyncMock) as mock_sender:
        mock_sender.return_value = 1
        await handlers._process_workflow(task, notifier, context)

    # Strategy passed to execute_download should be "split"
    router.execute_download.assert_called_once()
    _, kwargs = router.execute_download.call_args
    assert kwargs.get("strategy") == "split"


@pytest.mark.asyncio
async def test_interactive_choice_user_picks_compress(mock_config, tmp_path):
    task_manager = TaskManager(max_concurrent=2, max_per_user=1)
    router = MagicMock(spec=ExtractorRouter)
    router.probe_media = AsyncMock(return_value={"estimated_size": 120 * 1024 * 1024, "duration": 800, "title": "Large Movie"})

    dummy_item = MediaItem(
        path=tmp_path / "dummy.mp4",
        filename="dummy.mp4",
        size_bytes=1000,
        media_type=MediaType.VIDEO,
        is_oversized=False,
    )
    router.execute_download = AsyncMock(return_value=(ExtractorType.YTDLP, MediaInspectionResult(items=[dummy_item], valid_items=[dummy_item], oversized_items=[])))

    handlers = BotHandlers(config=mock_config, task_manager=task_manager, router=router)
    task = await task_manager.create_task(user_id=123, chat_id=456, url="https://example.com/large_video")
    notifier = MagicMock()
    notifier.update = AsyncMock()

    context = MagicMock()

    # User clicks "compress" immediately
    async def fake_wait_for(fut, timeout):
        task.set_choice("compress")
        return True

    with patch("asyncio.wait_for", side_effect=fake_wait_for), \
         patch("app.bot.handlers.MediaSender.send_media_result", new_callable=AsyncMock) as mock_sender:
        mock_sender.return_value = 1
        await handlers._process_workflow(task, notifier, context)

    router.execute_download.assert_called_once()
    _, kwargs = router.execute_download.call_args
    assert kwargs.get("strategy") == "compress"

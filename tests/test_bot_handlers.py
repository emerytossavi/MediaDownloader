"""Unit tests for bot commands and message handling."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Message, Update, User, Chat

from app.bot.handlers import BotHandlers
from app.config import AppConfig
from app.services.router import ExtractorRouter
from app.services.task_manager import TaskManager


@pytest.fixture
def mock_config(tmp_path: Path):
    return AppConfig(
        telegram_bot_token="123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ12345678",
        max_file_size=52428800,
        max_concurrent_downloads=2,
        max_concurrent_downloads_per_user=1,
        download_timeout=60,
        temp_dir=tmp_path / "temp",
        log_level="INFO",
        ytdlp_bin="yt-dlp",
        gallerydl_bin="gallery-dl",
        ffmpeg_bin=None,
    )


@pytest.mark.asyncio
async def test_start_command(mock_config):
    task_manager = TaskManager()
    router = ExtractorRouter(mock_config.max_file_size, mock_config.download_timeout)
    handlers = BotHandlers(mock_config, task_manager, router)

    update = MagicMock(spec=Update)
    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.message = message

    context = MagicMock()
    await handlers.start_command(update, context)

    message.reply_text.assert_called_once()
    assert "Bienvenue sur Media Downloader Bot" in message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_help_command(mock_config):
    task_manager = TaskManager()
    router = ExtractorRouter(mock_config.max_file_size, mock_config.download_timeout)
    handlers = BotHandlers(mock_config, task_manager, router)

    update = MagicMock(spec=Update)
    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.message = message

    context = MagicMock()
    await handlers.help_command(update, context)

    message.reply_text.assert_called_once()
    assert "Guide d'utilisation" in message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_no_url_message(mock_config):
    task_manager = TaskManager()
    router = ExtractorRouter(mock_config.max_file_size, mock_config.download_timeout)
    handlers = BotHandlers(mock_config, task_manager, router)

    update = MagicMock(spec=Update)
    message = MagicMock(spec=Message)
    message.text = "Bonjour, comment ça va ?"
    message.reply_text = AsyncMock()
    update.message = message
    update.effective_user = MagicMock(id=10)
    update.effective_chat = MagicMock(id=20)

    context = MagicMock()
    await handlers.handle_url_message(update, context)

    message.reply_text.assert_called_once()
    assert "Aucune URL valide trouvée" in message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cancel_callback_active_task(mock_config):
    task_manager = TaskManager()
    router = ExtractorRouter(mock_config.max_file_size, mock_config.download_timeout)
    handlers = BotHandlers(mock_config, task_manager, router)

    task = await task_manager.create_task(user_id=10, chat_id=20, url="https://example.com/video.mp4")

    update = MagicMock(spec=Update)
    query = MagicMock()
    query.data = f"cancel:{task.task_id}"
    query.from_user = MagicMock(id=10)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    await handlers.cancel_callback(update, context)

    assert task.cancel_event.is_set() is True
    query.answer.assert_called_once()
    assert "annulation" in query.answer.call_args[0][0].lower()
    query.edit_message_text.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_callback_other_user_forbidden(mock_config):
    task_manager = TaskManager()
    router = ExtractorRouter(mock_config.max_file_size, mock_config.download_timeout)
    handlers = BotHandlers(mock_config, task_manager, router)

    task = await task_manager.create_task(user_id=10, chat_id=20, url="https://example.com/video.mp4")

    update = MagicMock(spec=Update)
    query = MagicMock()
    query.data = f"cancel:{task.task_id}"
    query.from_user = MagicMock(id=999)  # Different user
    query.answer = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    await handlers.cancel_callback(update, context)

    assert task.cancel_event.is_set() is False
    query.answer.assert_called_once()
    assert "autre utilisateur" in query.answer.call_args[0][0]



@pytest.mark.asyncio
async def test_cancel_callback_finished_task(mock_config):
    task_manager = TaskManager()
    router = ExtractorRouter(mock_config.max_file_size, mock_config.download_timeout)
    handlers = BotHandlers(mock_config, task_manager, router)

    update = MagicMock(spec=Update)
    query = MagicMock()
    query.data = "cancel:nonexistent-task"
    query.answer = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    await handlers.cancel_callback(update, context)

    query.answer.assert_called_once()
    assert "terminée" in query.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_handle_url_message_task_limit_exceeded(mock_config):
    task_manager = TaskManager(max_per_user=1)
    router = ExtractorRouter(mock_config.max_file_size, mock_config.download_timeout)
    handlers = BotHandlers(mock_config, task_manager, router)

    # First task fills the user quota
    await task_manager.create_task(user_id=10, chat_id=20, url="https://example.com/first.mp4")

    update = MagicMock(spec=Update)
    message = MagicMock(spec=Message)
    message.text = "https://example.com/second.mp4"
    status_msg = MagicMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    message.reply_text = AsyncMock(return_value=status_msg)
    update.message = message
    update.effective_user = MagicMock(id=10)
    update.effective_chat = MagicMock(id=20)

    context = MagicMock()
    with patch("app.bot.handlers.validate_url_safety", return_value="https://example.com/second.mp4"):
        await handlers.handle_url_message(update, context)

    status_msg.edit_text.assert_called_once()
    assert "déjà un téléchargement en cours" in status_msg.edit_text.call_args[0][0]



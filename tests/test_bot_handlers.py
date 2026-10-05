"""Unit tests for bot commands and message handling."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
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

"""Unit tests for UserTracker and startup notifications."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram import Message, Update, User

from app.bot.handlers import BotHandlers
from app.config import AppConfig
from app.services.router import ExtractorRouter
from app.services.task_manager import TaskManager
from app.services.user_tracker import UserTracker


@pytest.fixture
def test_users_file(tmp_path: Path) -> Path:
    return tmp_path / "test_users.json"


@pytest.fixture
def mock_app_config(tmp_path: Path, test_users_file: Path) -> AppConfig:
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
        users_file=test_users_file,
    )


@pytest.mark.asyncio
async def test_user_tracker_initialization(test_users_file: Path):
    tracker = UserTracker(file_path=test_users_file)
    assert test_users_file.exists()
    users = await tracker.get_all_users()
    assert users == []


@pytest.mark.asyncio
async def test_user_tracker_record_and_update_user(test_users_file: Path):
    tracker = UserTracker(file_path=test_users_file)

    mock_user = MagicMock(spec=User)
    mock_user.id = 12345
    mock_user.is_bot = False
    mock_user.username = "alice"
    mock_user.first_name = "Alice"
    mock_user.last_name = "Smith"
    mock_user.language_code = "fr"

    rec1 = await tracker.record_user(mock_user)
    assert rec1 is not None
    assert rec1["user_id"] == 12345
    assert rec1["username"] == "alice"
    assert rec1["total_interactions"] == 1
    assert rec1["notifications_actives"] is True

    # Deuxième interaction : mise à jour
    rec2 = await tracker.record_user(mock_user)
    assert rec2["total_interactions"] == 2
    assert rec2["derniere_interaction"] >= rec1["premiere_interaction"]


@pytest.mark.asyncio
async def test_user_tracker_ignore_bot(test_users_file: Path):
    tracker = UserTracker(file_path=test_users_file)

    mock_bot = MagicMock(spec=User)
    mock_bot.id = 99999
    mock_bot.is_bot = True

    rec = await tracker.record_user(mock_bot)
    assert rec is None

    users = await tracker.get_all_users()
    assert users == []


@pytest.mark.asyncio
async def test_user_tracker_seed_from_download_history(tmp_path: Path):
    history_file = tmp_path / "downloads_history.json"
    history_data = {
        "aboutis": {
            "twitter": [
                {"user_id": 1001, "date": "2026-10-01T10:00:00+00:00"},
                {"user_id": 1002, "date": "2026-10-01T11:00:00+00:00"},
            ]
        },
        "non_aboutis": {
            "facebook": [
                {"user_id": 1001, "date": "2026-10-02T12:00:00+00:00"},
            ]
        },
    }
    history_file.write_text(json.dumps(history_data), encoding="utf-8")

    users_file = tmp_path / "seeded_users.json"
    tracker = UserTracker(file_path=users_file, seed_history_file=history_file)

    users = await tracker.get_all_users()
    assert len(users) == 2
    user_ids = await tracker.get_active_user_ids()
    assert 1001 in user_ids
    assert 1002 in user_ids


@pytest.mark.asyncio
async def test_user_tracker_notify_users_on_startup(test_users_file: Path):
    tracker = UserTracker(file_path=test_users_file)

    u1 = MagicMock(spec=User, id=101, is_bot=False, username="u1", first_name="A", last_name=None, language_code="fr")
    u2 = MagicMock(spec=User, id=102, is_bot=False, username="u2", first_name="B", last_name=None, language_code="en")
    await tracker.record_user(u1)
    await tracker.record_user(u2)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    stats = await tracker.notify_users_on_startup(mock_bot, message="Hey, du nouveau toi ?", rate_limit_delay=0.0)
    assert stats["total"] == 2
    assert stats["sent"] == 2
    assert stats["failed"] == 0
    assert mock_bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_user_tracker_notify_startup_forbidden(test_users_file: Path):
    from telegram.error import Forbidden

    tracker = UserTracker(file_path=test_users_file)
    u1 = MagicMock(spec=User, id=201, is_bot=False, username="blocked", first_name="Blocked", last_name=None, language_code="fr")
    await tracker.record_user(u1)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Forbidden("Forbidden: bot was blocked by the user"))

    stats = await tracker.notify_users_on_startup(mock_bot, message="Hey, du nouveau toi ?", rate_limit_delay=0.0)
    assert stats["total"] == 1
    assert stats["sent"] == 0
    assert stats["failed"] == 1

    # L'utilisateur doit maintenant être marqué inactif
    active_ids = await tracker.get_active_user_ids()
    assert 201 not in active_ids


@pytest.mark.asyncio
async def test_bot_handlers_tracks_user(mock_app_config: AppConfig, test_users_file: Path):
    task_manager = TaskManager()
    router = ExtractorRouter(mock_app_config.max_file_size, mock_app_config.download_timeout)
    tracker = UserTracker(file_path=test_users_file)
    handlers = BotHandlers(mock_app_config, task_manager, router, user_tracker=tracker)

    update = MagicMock(spec=Update)
    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.message = message
    update.effective_user = MagicMock(id=555, is_bot=False, username="tester", first_name="Bob", last_name="Dylan", language_code="fr")

    context = MagicMock()
    await handlers.start_command(update, context)

    users = await tracker.get_all_users()
    assert len(users) == 1
    assert users[0]["user_id"] == 555
    assert users[0]["username"] == "tester"

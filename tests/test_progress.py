"""Unit tests for ThrottledProgressNotifier."""

import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import Message
from telegram.error import TelegramError

from app.bot.progress import ThrottledProgressNotifier


@pytest.fixture
def mock_message():
    msg = MagicMock(spec=Message)
    msg.edit_text = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_progress_update_first_call_succeeds(mock_message):
    notifier = ThrottledProgressNotifier(mock_message, task_id="task-123", min_interval=2.0)

    await notifier.update("Téléchargement...")

    mock_message.edit_text.assert_called_once()
    assert mock_message.edit_text.call_args[1]["text"] == "Téléchargement..."
    reply_markup = mock_message.edit_text.call_args[1]["reply_markup"]
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].callback_data == "cancel:task-123"


@pytest.mark.asyncio
async def test_progress_update_throttles_within_min_interval(mock_message):
    notifier = ThrottledProgressNotifier(mock_message, task_id="task-123", min_interval=5.0)

    # First update goes through
    await notifier.update("Étape 1")
    assert mock_message.edit_text.call_count == 1

    # Second update immediately after should be throttled
    await notifier.update("Étape 2")
    assert mock_message.edit_text.call_count == 1


@pytest.mark.asyncio
async def test_progress_update_force_flag_bypasses_min_interval(mock_message):
    notifier = ThrottledProgressNotifier(mock_message, task_id="task-123", min_interval=5.0)

    await notifier.update("Étape 1")
    assert mock_message.edit_text.call_count == 1

    # Force update bypasses the 5s interval
    await notifier.update("Étape 2", force=True)
    assert mock_message.edit_text.call_count == 2
    assert mock_message.edit_text.call_args[1]["text"] == "Étape 2"


@pytest.mark.asyncio
async def test_progress_update_ignores_identical_text_unless_forced(mock_message):
    notifier = ThrottledProgressNotifier(mock_message, task_id="task-123", min_interval=0.1)

    await notifier.update("Même texte")
    assert mock_message.edit_text.call_count == 1

    # Simulate elapsed time
    with patch("time.monotonic", side_effect=[100.0, 200.0, 300.0]):
        await notifier.update("Même texte")
        assert mock_message.edit_text.call_count == 1  # Still ignored because text is identical

        # But if force=True, it should proceed
        await notifier.update("Même texte", force=True)
        assert mock_message.edit_text.call_count == 2


@pytest.mark.asyncio
async def test_progress_update_without_cancel_button_omits_markup(mock_message):
    notifier = ThrottledProgressNotifier(mock_message, task_id="task-123", min_interval=1.0)

    await notifier.update("Terminé !", show_cancel=False)

    mock_message.edit_text.assert_called_once()
    assert mock_message.edit_text.call_args[1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_progress_update_catches_message_not_modified_gracefully(mock_message):
    mock_message.edit_text.side_effect = TelegramError("Bad Request: message is not modified")
    notifier = ThrottledProgressNotifier(mock_message, task_id="task-123", min_interval=1.0)

    # Should not raise exception
    await notifier.update("Nouveau statut", force=True)
    mock_message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_progress_update_catches_general_telegram_error_gracefully(mock_message):
    mock_message.edit_text.side_effect = TelegramError("Network timeout")
    notifier = ThrottledProgressNotifier(mock_message, task_id="task-123", min_interval=1.0)

    # Should not raise exception
    await notifier.update("Nouveau statut", force=True)
    mock_message.edit_text.assert_called_once()

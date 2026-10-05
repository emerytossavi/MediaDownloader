"""Throttled progress update manager for Telegram messages."""

from __future__ import annotations

import asyncio
import time
from typing import Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import TelegramError
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ThrottledProgressNotifier:
    """Edits a status message on Telegram at controlled intervals to prevent 429 flood errors."""

    def __init__(
        self,
        message: Message,
        task_id: str,
        min_interval: float = 2.5,
    ) -> None:
        self.message = message
        self.task_id = task_id
        self.min_interval = min_interval
        self._last_update_time: float = 0.0
        self._last_text: str = ""
        self._lock = asyncio.Lock()

    def _build_markup(self, show_cancel: bool) -> Optional[InlineKeyboardMarkup]:
        if not show_cancel:
            return None
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🛑 Annuler", callback_data=f"cancel:{self.task_id}")]]
        )

    async def update(
        self,
        text: str,
        force: bool = False,
        show_cancel: bool = True,
    ) -> None:
        """Update the progress message text if interval elapsed or forced."""
        async with self._lock:
            now = time.monotonic()
            if not force and (now - self._last_update_time < self.min_interval):
                return
            if text == self._last_text and not force:
                return

            reply_markup = self._build_markup(show_cancel)

            try:
                await self.message.edit_text(
                    text=text,
                    reply_markup=reply_markup,
                )
                self._last_update_time = now
                self._last_text = text
            except TelegramError as exc:
                # Catch message not modified or message deleted gracefully
                if "Message is not modified" in str(exc):
                    pass
                else:
                    logger.debug(f"Could not edit progress message: {exc}")

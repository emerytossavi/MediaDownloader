"""Media sending service respecting Telegram API limits and media groupings."""

from __future__ import annotations

from typing import List
from telegram import Bot, InputMediaPhoto, InputMediaVideo
from telegram.error import TelegramError
from app.services.media_inspector import MediaInspectionResult, MediaItem, MediaType
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Telegram media group limit: maximum 10 items per group
MAX_MEDIA_GROUP_SIZE = 10


class MediaSender:
    """Dispatches downloaded media to Telegram chats with streaming and error handling."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_media_result(
        self,
        chat_id: int,
        result: MediaInspectionResult,
    ) -> int:
        """Send all valid media items to the specified chat. Returns count of successfully sent items."""
        if not result.has_valid_media:
            return 0

        # Separate items by media type
        photos = [item for item in result.valid_items if item.media_type == MediaType.PHOTO]
        videos = [item for item in result.valid_items if item.media_type == MediaType.VIDEO]
        audios = [item for item in result.valid_items if item.media_type == MediaType.AUDIO]
        documents = [
            item
            for item in result.valid_items
            if item.media_type not in {MediaType.PHOTO, MediaType.VIDEO, MediaType.AUDIO}
        ]

        sent_count = 0

        # 1. Send Photos (group into albums if multiple)
        if len(photos) > 1:
            sent_count += await self._send_photo_albums(chat_id, photos)
        elif len(photos) == 1:
            if await self._send_single_photo(chat_id, photos[0]):
                sent_count += 1

        # 2. Send Videos
        for video in videos:
            if await self._send_single_video(chat_id, video):
                sent_count += 1

        # 3. Send Audios
        for audio in audios:
            if await self._send_single_audio(chat_id, audio):
                sent_count += 1

        # 4. Send Documents
        for item in documents:
            if await self._send_document(chat_id, item):
                sent_count += 1

        return sent_count

    async def _send_single_photo(self, chat_id: int, item: MediaItem) -> bool:
        try:
            with open(item.path, "rb") as f:
                await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=f"📷 {item.filename}",
                )
            return True
        except TelegramError as exc:
            logger.warning(f"Failed to send single photo {item.filename}, fallback to document: {exc}")
            return await self._send_document(chat_id, item)

    async def _send_photo_albums(self, chat_id: int, photos: List[MediaItem]) -> int:
        """Chunk photos into groups of at most 10 and send as media groups."""
        sent = 0
        for i in range(0, len(photos), MAX_MEDIA_GROUP_SIZE):
            chunk = photos[i : i + MAX_MEDIA_GROUP_SIZE]
            if len(chunk) == 1:
                if await self._send_single_photo(chat_id, chunk[0]):
                    sent += 1
                continue

            try:
                # Open all files in chunk and construct InputMediaPhoto
                file_handles = [open(item.path, "rb") for item in chunk]
                try:
                    media_group = [
                        InputMediaPhoto(media=fh, caption=f"📷 {item.filename}" if idx == 0 else None)
                        for idx, (item, fh) in enumerate(zip(chunk, file_handles))
                    ]
                    await self.bot.send_media_group(chat_id=chat_id, media=media_group)
                    sent += len(chunk)
                finally:
                    for fh in file_handles:
                        fh.close()
            except TelegramError as exc:
                logger.warning(f"send_media_group failed: {exc}. Falling back to individual sends.")
                for item in chunk:
                    if await self._send_single_photo(chat_id, item):
                        sent += 1

        return sent

    async def _send_single_video(self, chat_id: int, item: MediaItem) -> bool:
        try:
            with open(item.path, "rb") as f:
                await self.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=f"🎥 {item.filename}",
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                )
            return True
        except TelegramError as exc:
            logger.warning(f"send_video failed for {item.filename} ({exc}). Falling back to document.")
            return await self._send_document(chat_id, item)

    async def _send_single_audio(self, chat_id: int, item: MediaItem) -> bool:
        try:
            with open(item.path, "rb") as f:
                await self.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    caption=f"🎵 {item.filename}",
                    read_timeout=120,
                    write_timeout=120,
                )
            return True
        except TelegramError as exc:
            logger.warning(f"send_audio failed for {item.filename} ({exc}). Falling back to document.")
            return await self._send_document(chat_id, item)

    async def _send_document(self, chat_id: int, item: MediaItem) -> bool:
        try:
            with open(item.path, "rb") as f:
                await self.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    caption=f"📄 {item.filename}",
                    read_timeout=120,
                    write_timeout=120,
                )
            return True
        except TelegramError as exc:
            logger.error(f"Failed to send document {item.filename}: {exc}")
            return False

"""Unit tests for MediaSender service."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from typing import List, Optional
import pytest
from telegram import Bot
from telegram.error import TelegramError

from app.bot.sender import MAX_MEDIA_GROUP_SIZE, MediaSender
from app.services.media_inspector import MediaInspectionResult, MediaItem, MediaType


@pytest.fixture
def mock_bot():
    bot = MagicMock(spec=Bot)
    bot.send_photo = AsyncMock()
    bot.send_media_group = AsyncMock()
    bot.send_video = AsyncMock()
    bot.send_audio = AsyncMock()
    bot.send_document = AsyncMock()
    return bot


def _create_media_item(
    tmp_path: Path,
    filename: str,
    media_type: MediaType,
    size_bytes: int = 1024,
    is_oversized: bool = False,
) -> MediaItem:
    file_path = tmp_path / filename
    file_path.write_bytes(b"x" * size_bytes)
    return MediaItem(
        path=file_path,
        filename=filename,
        size_bytes=size_bytes,
        media_type=media_type,
        is_oversized=is_oversized,
    )


def _make_result(valid_items: Optional[List[MediaItem]] = None, oversized_items: Optional[List[MediaItem]] = None) -> MediaInspectionResult:
    valid = valid_items or []
    oversized = oversized_items or []
    return MediaInspectionResult(items=valid + oversized, valid_items=valid, oversized_items=oversized)


@pytest.mark.asyncio
async def test_send_media_result_when_no_valid_media_returns_zero(mock_bot):
    sender = MediaSender(mock_bot)
    result = _make_result(valid_items=[], oversized_items=[])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 0
    mock_bot.send_photo.assert_not_called()
    mock_bot.send_media_group.assert_not_called()
    mock_bot.send_video.assert_not_called()


@pytest.mark.asyncio
async def test_send_single_photo_success(tmp_path: Path, mock_bot):
    sender = MediaSender(mock_bot)
    photo_item = _create_media_item(tmp_path, "test.jpg", MediaType.PHOTO)
    result = _make_result(valid_items=[photo_item])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 1
    mock_bot.send_photo.assert_called_once()
    assert mock_bot.send_photo.call_args[1]["chat_id"] == 123
    assert "test.jpg" in mock_bot.send_photo.call_args[1]["caption"]


@pytest.mark.asyncio
async def test_send_single_photo_when_telegram_error_falls_back_to_document(tmp_path: Path, mock_bot):
    mock_bot.send_photo.side_effect = TelegramError("Photo format invalid")
    sender = MediaSender(mock_bot)
    photo_item = _create_media_item(tmp_path, "corrupted.jpg", MediaType.PHOTO)
    result = _make_result(valid_items=[photo_item])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 1
    mock_bot.send_photo.assert_called_once()
    mock_bot.send_document.assert_called_once()
    assert "corrupted.jpg" in mock_bot.send_document.call_args[1]["caption"]


@pytest.mark.asyncio
async def test_send_photo_albums_chunks_large_groups_by_ten(tmp_path: Path, mock_bot):
    sender = MediaSender(mock_bot)
    # 15 photos: should result in 2 media groups (10 + 5)
    photos = [_create_media_item(tmp_path, f"photo_{i:02d}.jpg", MediaType.PHOTO) for i in range(15)]
    result = _make_result(valid_items=photos)

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 15
    assert mock_bot.send_media_group.call_count == 2
    first_call_media = mock_bot.send_media_group.call_args_list[0][1]["media"]
    second_call_media = mock_bot.send_media_group.call_args_list[1][1]["media"]
    assert len(first_call_media) == MAX_MEDIA_GROUP_SIZE
    assert len(second_call_media) == 5


@pytest.mark.asyncio
async def test_send_photo_albums_when_media_group_fails_falls_back_to_individual_sends(tmp_path: Path, mock_bot):
    mock_bot.send_media_group.side_effect = TelegramError("Failed to send media group")
    sender = MediaSender(mock_bot)
    photos = [_create_media_item(tmp_path, f"photo_{i:02d}.jpg", MediaType.PHOTO) for i in range(3)]
    result = _make_result(valid_items=photos)

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 3
    mock_bot.send_media_group.assert_called_once()
    assert mock_bot.send_photo.call_count == 3


@pytest.mark.asyncio
async def test_send_single_video_success(tmp_path: Path, mock_bot):
    sender = MediaSender(mock_bot)
    video_item = _create_media_item(tmp_path, "video.mp4", MediaType.VIDEO)
    result = _make_result(valid_items=[video_item])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 1
    mock_bot.send_video.assert_called_once()
    assert mock_bot.send_video.call_args[1]["chat_id"] == 123
    assert mock_bot.send_video.call_args[1]["supports_streaming"] is True


@pytest.mark.asyncio
async def test_send_single_video_when_telegram_error_falls_back_to_document(tmp_path: Path, mock_bot):
    mock_bot.send_video.side_effect = TelegramError("Failed to parse video")
    sender = MediaSender(mock_bot)
    video_item = _create_media_item(tmp_path, "broken.mp4", MediaType.VIDEO)
    result = _make_result(valid_items=[video_item])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 1
    mock_bot.send_video.assert_called_once()
    mock_bot.send_document.assert_called_once()


@pytest.mark.asyncio
async def test_send_single_audio_success(tmp_path: Path, mock_bot):
    sender = MediaSender(mock_bot)
    audio_item = _create_media_item(tmp_path, "song.mp3", MediaType.AUDIO)
    result = _make_result(valid_items=[audio_item])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 1
    mock_bot.send_audio.assert_called_once()


@pytest.mark.asyncio
async def test_send_single_audio_when_telegram_error_falls_back_to_document(tmp_path: Path, mock_bot):
    mock_bot.send_audio.side_effect = TelegramError("Audio codec error")
    sender = MediaSender(mock_bot)
    audio_item = _create_media_item(tmp_path, "song.ogg", MediaType.AUDIO)
    result = _make_result(valid_items=[audio_item])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 1
    mock_bot.send_audio.assert_called_once()
    mock_bot.send_document.assert_called_once()


@pytest.mark.asyncio
async def test_send_document_when_telegram_error_returns_zero(tmp_path: Path, mock_bot):
    mock_bot.send_document.side_effect = TelegramError("Document rejected")
    sender = MediaSender(mock_bot)
    doc_item = _create_media_item(tmp_path, "data.zip", MediaType.DOCUMENT)
    result = _make_result(valid_items=[doc_item])

    sent_count = await sender.send_media_result(chat_id=123, result=result)

    assert sent_count == 0
    mock_bot.send_document.assert_called_once()

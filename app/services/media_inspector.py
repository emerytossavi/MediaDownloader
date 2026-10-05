"""Media inspection and categorization module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import mimetypes
import os
from pathlib import Path
from typing import List
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Temporary / incomplete file extensions to ignore
IGNORED_EXTENSIONS = {
    ".part",
    ".ytdl",
    ".aria2",
    ".tmp",
    ".temp",
    ".ds_store",
    ".description",
    ".info.json",
    ".vtt",
    ".srt",
}

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus", ".aac"}


class MediaType(str, Enum):
    VIDEO = "VIDEO"
    PHOTO = "PHOTO"
    AUDIO = "AUDIO"
    DOCUMENT = "DOCUMENT"


@dataclass(frozen=True)
class MediaItem:
    path: Path
    filename: str
    size_bytes: int
    media_type: MediaType
    is_oversized: bool

    @property
    def human_size(self) -> str:
        """Format file size in MiB/KiB."""
        size = float(self.size_bytes)
        for unit in ["B", "KiB", "MiB", "GiB"]:
            if size < 1024.0 or unit == "GiB":
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{self.size_bytes} B"


@dataclass(frozen=True)
class MediaInspectionResult:
    items: List[MediaItem]
    valid_items: List[MediaItem]
    oversized_items: List[MediaItem]

    @property
    def has_valid_media(self) -> bool:
        return len(self.valid_items) > 0

    @property
    def has_oversized_media(self) -> bool:
        return len(self.oversized_items) > 0


def determine_media_type(path: Path) -> MediaType:
    """Determine media category from file extension and MIME type."""
    ext = path.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    if ext in IMAGE_EXTENSIONS:
        return MediaType.PHOTO
    if ext in AUDIO_EXTENSIONS:
        return MediaType.AUDIO

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        if mime_type.startswith("video/"):
            return MediaType.VIDEO
        if mime_type.startswith("image/"):
            return MediaType.PHOTO
        if mime_type.startswith("audio/"):
            return MediaType.AUDIO

    return MediaType.DOCUMENT


def inspect_directory(job_dir: Path, max_file_size: int) -> MediaInspectionResult:
    """Scan a completed job directory and classify all downloaded media files."""
    if not job_dir.exists() or not job_dir.is_dir():
        return MediaInspectionResult(items=[], valid_items=[], oversized_items=[])

    all_files: List[Path] = []
    for root, _, files in os.walk(job_dir):
        for f in files:
            p = Path(root) / f
            ext = p.suffix.lower()
            if ext in IGNORED_EXTENSIONS or f.startswith("."):
                continue
            all_files.append(p)

    # Sort files naturally by filename to preserve carousel/album order
    all_files.sort(key=lambda p: p.name)

    items: List[MediaItem] = []
    valid_items: List[MediaItem] = []
    oversized_items: List[MediaItem] = []

    for file_path in all_files:
        try:
            size_bytes = file_path.stat().st_size
            if size_bytes == 0:
                logger.debug(f"Skipping empty file: {file_path}")
                continue

            media_type = determine_media_type(file_path)
            is_oversized = size_bytes > max_file_size

            item = MediaItem(
                path=file_path,
                filename=file_path.name,
                size_bytes=size_bytes,
                media_type=media_type,
                is_oversized=is_oversized,
            )
            items.append(item)
            if is_oversized:
                oversized_items.append(item)
                logger.warning(
                    f"File '{item.filename}' ({item.human_size}) exceeds limit of {max_file_size} bytes"
                )
            else:
                valid_items.append(item)

        except (OSError, FileNotFoundError) as exc:
            logger.warning(f"Could not inspect file {file_path}: {exc}")

    return MediaInspectionResult(
        items=items,
        valid_items=valid_items,
        oversized_items=oversized_items,
    )

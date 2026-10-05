"""Extractor router dynamically selecting between yt-dlp and gallery-dl."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional, Tuple
from app.services.gallerydl_downloader import download_with_gallerydl
from app.services.media_inspector import MediaInspectionResult, inspect_directory
from app.services.task_manager import DownloadTask
from app.services.ytdlp_downloader import download_with_ytdlp, ExtractorError
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractorType(str, Enum):
    YTDLP = "yt-dlp"
    GALLERYDL = "gallery-dl"
    UNKNOWN = "unknown"


class ExtractorRouter:
    """Intelligently detects suitable extraction engine and manages download with fallback."""

    def __init__(
        self,
        max_file_size: int,
        download_timeout: int,
        ytdlp_bin: str = "yt-dlp",
        gallerydl_bin: str = "gallery-dl",
        ffmpeg_bin: Optional[str] = None,
    ) -> None:
        self.max_file_size = max_file_size
        self.download_timeout = download_timeout
        self.ytdlp_bin = ytdlp_bin
        self.gallerydl_bin = gallerydl_bin
        self.ffmpeg_bin = ffmpeg_bin

    def detect_primary_extractor(self, url: str) -> ExtractorType:
        """Inspect URL using native extractor matching in gallery-dl and yt-dlp."""
        gallery_supported = False
        try:
            import gallery_dl.extractor  # type: ignore

            extractor = gallery_dl.extractor.find(url)
            if extractor is not None and getattr(extractor, "category", "") != "generic":
                gallery_supported = True
        except Exception as exc:
            logger.debug(f"gallery-dl detection error: {exc}")

        ytdlp_supported = False
        try:
            import yt_dlp.extractor  # type: ignore

            ies = yt_dlp.extractor.gen_extractors()
            for ie in ies:
                if ie.suitable(url) and ie.IE_NAME.lower() != "generic":
                    ytdlp_supported = True
                    break
        except Exception as exc:
            logger.debug(f"yt-dlp detection error: {exc}")

        # Routing heuristic:
        # 1. If only one is supported, pick that one
        if gallery_supported and not ytdlp_supported:
            return ExtractorType.GALLERYDL
        if ytdlp_supported and not gallery_supported:
            return ExtractorType.YTDLP

        # 2. If both match (e.g. Instagram, Reddit, Twitter), prioritize yt-dlp for video or gallery-dl based on URL
        url_lower = url.lower()
        if any(hint in url_lower for hint in ["/reel/", "/video/", "watch?v=", "/shorts/", "/v/"]):
            return ExtractorType.YTDLP
        if any(hint in url_lower for hint in ["/p/", "/album/", "/gallery/", "/art/"]):
            return ExtractorType.GALLERYDL

        # Default preference if both match: yt-dlp first (it extracts both video and audio)
        if ytdlp_supported:
            return ExtractorType.YTDLP
        if gallery_supported:
            return ExtractorType.GALLERYDL

        return ExtractorType.UNKNOWN

    async def execute_download(
        self,
        task: DownloadTask,
        target_dir: Path,
    ) -> Tuple[ExtractorType, MediaInspectionResult]:
        """Download media using the primary strategy, with automatic fallback if 0 files produced."""
        primary = self.detect_primary_extractor(task.url)
        logger.info(f"[Task {task.task_id}] Primary extractor detected: {primary.value} for {task.url}")

        if primary == ExtractorType.UNKNOWN:
            # Try yt-dlp as a last attempt before giving up (handles many direct media links)
            primary = ExtractorType.YTDLP

        # Plan execution sequence: primary first, secondary as fallback
        sequence = (
            [ExtractorType.YTDLP, ExtractorType.GALLERYDL]
            if primary == ExtractorType.YTDLP
            else [ExtractorType.GALLERYDL, ExtractorType.YTDLP]
        )

        last_error: Optional[Exception] = None
        executed_tool: ExtractorType = primary

        for tool in sequence:
            if task.cancel_event.is_set():
                break

            logger.info(f"[Task {task.task_id}] Attempting download with {tool.value}")
            executed_tool = tool

            try:
                if tool == ExtractorType.YTDLP:
                    await download_with_ytdlp(
                        task=task,
                        target_dir=target_dir,
                        max_file_size=self.max_file_size,
                        timeout=self.download_timeout,
                        bin_path=self.ytdlp_bin,
                        ffmpeg_bin=self.ffmpeg_bin,
                    )
                else:
                    await download_with_gallerydl(
                        task=task,
                        target_dir=target_dir,
                        timeout=self.download_timeout,
                        bin_path=self.gallerydl_bin,
                    )

                # Inspect directory to see if any media was produced
                result = inspect_directory(target_dir, max_file_size=self.max_file_size)
                if result.has_valid_media or result.has_oversized_media:
                    return executed_tool, result

                logger.info(f"[Task {task.task_id}] {tool.value} produced 0 media files, trying fallback...")

            except Exception as exc:
                logger.warning(f"[Task {task.task_id}] {tool.value} failed: {exc}")
                last_error = exc

        # If both attempts failed or produced no files:
        final_inspection = inspect_directory(target_dir, max_file_size=self.max_file_size)
        if final_inspection.has_valid_media or final_inspection.has_oversized_media:
            return executed_tool, final_inspection

        if last_error:
            raise last_error

        raise ExtractorError("Aucun média n'a pu être extrait depuis cette URL.")

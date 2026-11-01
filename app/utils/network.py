"""Network utilities for safe streaming downloads without excessive memory usage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)

CHUNK_SIZE: int = 64 * 1024  # 64 KiB per chunk


def stream_download_to_file(
    session: Any,
    url: str,
    dest_path: Path,
    max_size: Optional[int] = None,
    timeout: int = 30,
    cancel_event: Optional[Any] = None,
    min_bytes: int = 1,
) -> bool:
    """Download a remote file in chunks directly to disk, avoiding buffering in RAM.

    Args:
        session: An active HTTP session (e.g. curl_cffi.requests.Session or requests.Session).
        url: Remote URL to download.
        dest_path: Final destination path on disk.
        max_size: Maximum allowed file size in bytes (aborts if exceeded).
        timeout: Request timeout in seconds.
        cancel_event: Optional asyncio or threading Event to abort download if requested.
        min_bytes: Minimum bytes required to consider the download valid.

    Returns:
        True if the file was downloaded successfully and is valid; False otherwise.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = dest_path.with_suffix(dest_path.suffix + ".part")

    try:
        resp = session.get(url, stream=True, timeout=timeout)
        if resp.status_code != 200:
            logger.debug(f"HTTP {resp.status_code} when downloading {url}")
            return False

        # Pre-check via Content-Length header if available
        headers = getattr(resp, "headers", None)
        if headers and max_size is not None:
            cl_val = headers.get("content-length") or headers.get("Content-Length")
            if cl_val:
                try:
                    content_length = int(cl_val)
                    if content_length > max_size:
                        logger.warning(
                            f"Content-Length ({content_length} bytes) exceeds maximum limit ({max_size} bytes): {url}"
                        )
                        return False
                except ValueError:
                    pass

        total_written = 0
        with open(temp_file, "wb") as f:
            read_stream = False
            if hasattr(resp, "iter_content") and callable(resp.iter_content):
                try:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        read_stream = True
                        if cancel_event and cancel_event.is_set():
                            temp_file.unlink(missing_ok=True)
                            return False
                        if chunk:
                            total_written += len(chunk)
                            if max_size is not None and total_written > max_size:
                                logger.warning(
                                    f"Streaming download exceeded maximum limit ({max_size} bytes) for {url}"
                                )
                                temp_file.unlink(missing_ok=True)
                                return False
                            f.write(chunk)
                except Exception as stream_err:
                    logger.debug(f"iter_content error for {url}: {stream_err}")

            # Fallback to resp.content if iter_content produced nothing (e.g. in test mocks)
            if not read_stream and hasattr(resp, "content") and resp.content:
                if cancel_event and cancel_event.is_set():
                    temp_file.unlink(missing_ok=True)
                    return False
                content_bytes = resp.content
                total_written = len(content_bytes)
                if max_size is not None and total_written > max_size:
                    logger.warning(f"Payload size ({total_written} bytes) exceeds maximum limit ({max_size} bytes)")
                    temp_file.unlink(missing_ok=True)
                    return False
                f.write(content_bytes)

        if total_written >= min_bytes:
            temp_file.replace(dest_path)
            return True

        temp_file.unlink(missing_ok=True)
        return False

    except Exception as exc:
        temp_file.unlink(missing_ok=True)
        logger.debug(f"Exception during streaming download of {url}: {exc}")
        return False

"""Filesystem helper utilities for task isolation, cleanup, and path safety."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Pattern for sanitizing filenames: remove control characters and dangerous delimiters
_INVALID_FILENAME_CHARS = re.compile(r'[\x00-\x1f/\\:\*\?"<>\|]')


def ensure_directory(path: Path) -> Path:
    """Ensure a directory exists and has write permissions."""
    path.mkdir(parents=True, exist_ok=True)
    if not os.access(path, os.W_OK):
        raise PermissionError(f"Directory is not writable: {path}")
    return path


def create_job_directory(base_dir: Path, job_id: str) -> Path:
    """Create a private isolated workspace directory for a single task."""
    ensure_directory(base_dir)
    safe_job_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", job_id)
    job_dir = (base_dir / f"job_{safe_job_id}").resolve()

    # Verify job directory is strictly inside base_dir
    if not job_dir.is_relative_to(base_dir.resolve()):
        raise ValueError(f"Job path traversal attempt detected: {job_dir}")

    job_dir.mkdir(parents=True, exist_ok=False)
    return job_dir


def cleanup_job_directory(job_dir: Optional[Path]) -> None:
    """Safely and recursively remove a task's temporary directory."""
    if job_dir is None:
        return
    try:
        resolved = job_dir.resolve()
        if resolved.exists() and resolved.is_dir():
            shutil.rmtree(resolved, ignore_errors=True)
            logger.debug(f"Cleaned up temporary workspace: {resolved}")
    except Exception as exc:
        logger.warning(f"Error while cleaning directory {job_dir}: {exc}")


def cleanup_abandoned_jobs(base_dir: Path) -> int:
    """Clean up any leftover job directories from prior crashed/aborted bot sessions."""
    if not base_dir.exists() or not base_dir.is_dir():
        return 0

    removed_count = 0
    try:
        for entry in base_dir.iterdir():
            if entry.is_dir() and entry.name.startswith("job_"):
                try:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed_count += 1
                except Exception as exc:
                    logger.warning(f"Failed to remove abandoned directory {entry}: {exc}")
    except Exception as exc:
        logger.warning(f"Failed to iterate abandoned directories in {base_dir}: {exc}")

    if removed_count > 0:
        logger.info(f"Cleaned up {removed_count} abandoned job directory(ies) in {base_dir}")
    return removed_count


def check_disk_space(path: Path, min_free_bytes: int = 100 * 1024 * 1024) -> bool:
    """Verify that the partition holding `path` has at least `min_free_bytes` free space."""
    try:
        target = path if path.exists() else path.parent
        usage = shutil.disk_usage(target)
        return usage.free >= min_free_bytes
    except Exception as exc:
        logger.warning(f"Could not determine disk usage for {path}: {exc}")
        return True  # Optimistic fallback if disk_usage fails


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitize a filename to avoid path traversal, forbidden characters, or extreme lengths."""
    # Strip any directory path components first
    name = Path(name).name
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name)
    cleaned = cleaned.replace("..", "_")
    cleaned = re.sub(r"_+", "_", cleaned).strip("._ ")
    if not cleaned:
        cleaned = "downloaded_file"
    if len(cleaned) > max_length:
        base, sep, ext = cleaned.rpartition(".")
        if sep and len(ext) <= 10:
            truncated_base = base[: max_length - len(ext) - 1].rstrip("._ ")
            cleaned = f"{truncated_base}.{ext}"
        else:
            cleaned = cleaned[:max_length].rstrip("._ ")
    return cleaned


def safe_resolve_child(parent_dir: Path, child_name: str | Path) -> Path:
    """Resolve a child file or subdirectory and ensure it does not escape `parent_dir`."""
    parent_resolved = parent_dir.resolve()
    child_resolved = (parent_resolved / child_name).resolve()
    if not child_resolved.is_relative_to(parent_resolved):
        raise ValueError(
            f"Path traversal detected: {child_name} escapes parent {parent_resolved}"
        )
    return child_resolved


def force_garbage_collection() -> None:
    """Force Python garbage collection and trim unmapped glibc heap memory on Linux."""
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

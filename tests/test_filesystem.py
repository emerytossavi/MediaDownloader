"""Unit tests for filesystem isolation, path traversal checks, and cleanup."""

import os
from pathlib import Path
import pytest

from app.utils.filesystem import (
    check_disk_space,
    cleanup_abandoned_jobs,
    cleanup_job_directory,
    create_job_directory,
    safe_resolve_child,
    sanitize_filename,
)


def test_create_and_cleanup_job_directory(tmp_path: Path):
    base_dir = tmp_path / "workspaces"
    job_id = "test-job-123"

    job_dir = create_job_directory(base_dir, job_id)
    assert job_dir.exists()
    assert job_dir.is_dir()
    assert job_dir.name == "job_test-job-123"

    # Create dummy file inside
    (job_dir / "sample.mp4").write_text("dummy video content")
    assert (job_dir / "sample.mp4").exists()

    # Cleanup
    cleanup_job_directory(job_dir)
    assert not job_dir.exists()


def test_cleanup_abandoned_jobs(tmp_path: Path):
    base_dir = tmp_path / "abandoned_test"
    base_dir.mkdir(parents=True, exist_ok=True)

    # Create abandoned directories and normal files
    (base_dir / "job_abc").mkdir()
    (base_dir / "job_xyz").mkdir()
    (base_dir / "other_folder").mkdir()
    (base_dir / "keep_me.txt").write_text("keep")

    removed = cleanup_abandoned_jobs(base_dir)
    assert removed == 2
    assert not (base_dir / "job_abc").exists()
    assert not (base_dir / "job_xyz").exists()
    assert (base_dir / "other_folder").exists()
    assert (base_dir / "keep_me.txt").exists()


def test_safe_resolve_child(tmp_path: Path):
    parent = tmp_path / "parent"
    parent.mkdir()

    safe = safe_resolve_child(parent, "sub/file.txt")
    assert safe.is_relative_to(parent)

    with pytest.raises(ValueError, match="Path traversal detected"):
        safe_resolve_child(parent, "../escape.txt")


def test_sanitize_filename():
    assert sanitize_filename("normal_video.mp4") == "normal_video.mp4"
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("bad:name*with?chars.jpg") == "bad_name_with_chars.jpg"

    # Very long filename should be truncated preserving extension
    long_name = "a" * 300 + ".mp4"
    sanitized = sanitize_filename(long_name, max_length=50)
    assert len(sanitized) == 50
    assert sanitized.endswith(".mp4")


def test_check_disk_space(tmp_path: Path):
    # Free space check with 1 byte should succeed
    assert check_disk_space(tmp_path, min_free_bytes=1) is True

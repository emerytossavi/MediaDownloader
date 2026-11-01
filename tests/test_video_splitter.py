"""Unit tests for the video splitter utility."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.video_splitter import (
    find_ffmpeg_binary,
    get_video_duration,
    split_video_file,
)


def test_find_ffmpeg_binary(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/ffmpeg" if cmd == "ffmpeg" else None)
    found = find_ffmpeg_binary()
    assert found == "/usr/bin/ffmpeg"


def test_find_ffmpeg_binary_custom():
    found = find_ffmpeg_binary("/custom/path/ffmpeg")
    assert found == "/custom/path/ffmpeg"


def test_get_video_duration_parsing(tmp_path: Path):
    dummy_file = tmp_path / "dummy.mp4"
    dummy_file.write_bytes(b"dummy")

    mock_proc = MagicMock()
    mock_proc.stderr = "Input #0... Duration: 00:15:30.50, start: 0.000000, bitrate: 1200 kb/s"

    with patch("subprocess.run", return_value=mock_proc):
        dur = get_video_duration(dummy_file, ffmpeg_bin="ffmpeg")

    assert dur == 15 * 60 + 30.5


def test_split_video_file_under_size(tmp_path: Path):
    dummy_file = tmp_path / "small.mp4"
    dummy_file.write_bytes(b"small_content")

    parts = split_video_file(dummy_file, target_part_size=1000)
    assert parts == [dummy_file]
    assert dummy_file.exists()


def test_split_video_file_success(tmp_path: Path):
    oversized = tmp_path / "large_movie.mp4"
    # 100 bytes file, target is 40 bytes -> 3 parts
    oversized.write_bytes(b"x" * 100)

    def fake_subprocess_run(cmd, *args, **kwargs):
        # If duration probe:
        if "-i" in cmd and "-ss" not in cmd:
            m = MagicMock()
            m.stderr = "Duration: 00:03:00.00,"
            return m
        # If segment command:
        out_file = Path(cmd[-1])
        out_file.write_bytes(b"part_content")
        m = MagicMock()
        m.returncode = 0
        return m

    with patch("app.services.video_splitter.find_ffmpeg_binary", return_value="ffmpeg"), \
         patch("subprocess.run", side_effect=fake_subprocess_run):
        parts = split_video_file(oversized, target_part_size=40, ffmpeg_bin="ffmpeg")

    assert len(parts) == 3
    assert not oversized.exists()
    for p in parts:
        assert p.exists()
        assert "_part" in p.name

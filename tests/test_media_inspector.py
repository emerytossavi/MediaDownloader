"""Unit tests for media inspection, categorization, and 50 MiB sizing limits."""

from pathlib import Path
from app.services.media_inspector import (
    MediaType,
    determine_media_type,
    inspect_directory,
)


def test_determine_media_type():
    assert determine_media_type(Path("video.mp4")) == MediaType.VIDEO
    assert determine_media_type(Path("clip.mkv")) == MediaType.VIDEO
    assert determine_media_type(Path("photo.jpg")) == MediaType.PHOTO
    assert determine_media_type(Path("picture.png")) == MediaType.PHOTO
    assert determine_media_type(Path("song.mp3")) == MediaType.AUDIO
    assert determine_media_type(Path("archive.zip")) == MediaType.DOCUMENT


def test_inspect_directory(tmp_path: Path):
    job_dir = tmp_path / "job_inspect"
    job_dir.mkdir()

    # 1. Valid video (100 KiB)
    v_path = job_dir / "valid_video.mp4"
    v_path.write_bytes(b"0" * (100 * 1024))

    # 2. Oversized video (2 MiB when limit is 1 MiB)
    big_path = job_dir / "huge_video.mp4"
    big_path.write_bytes(b"0" * (2 * 1024 * 1024))

    # 3. Valid photo (50 KiB)
    p_path = job_dir / "photo.jpg"
    p_path.write_bytes(b"0" * (50 * 1024))

    # 4. Ignored temporary files
    (job_dir / "video.mp4.part").write_bytes(b"0" * 100)
    (job_dir / "video.ytdl").write_bytes(b"0" * 100)
    (job_dir / ".DS_Store").write_bytes(b"0" * 100)

    # Set limit to 1 MiB (1,048,576 bytes)
    result = inspect_directory(job_dir, max_file_size=1024 * 1024)

    assert len(result.items) == 3
    assert len(result.valid_items) == 2
    assert len(result.oversized_items) == 1

    assert result.has_valid_media is True
    assert result.has_oversized_media is True

    oversized = result.oversized_items[0]
    assert oversized.filename == "huge_video.mp4"
    assert oversized.is_oversized is True
    assert "2.0 MiB" in oversized.human_size

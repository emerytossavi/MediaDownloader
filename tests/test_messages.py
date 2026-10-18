"""Unit tests for bot messages formatting functions."""

from pathlib import Path
from app.bot.messages import format_completion_summary, format_oversized_warning
from app.services.media_inspector import MediaItem, MediaType


def _make_item(name: str, size_bytes: int) -> MediaItem:
    return MediaItem(
        path=Path(f"/tmp/{name}"),
        filename=name,
        size_bytes=size_bytes,
        media_type=MediaType.VIDEO,
        is_oversized=True,
    )



def test_format_oversized_warning_with_few_items():
    items = [
        _make_item("movie1.mp4", 60 * 1024 * 1024),
        _make_item("movie2.mp4", 75 * 1024 * 1024),
    ]

    msg = format_oversized_warning(items, max_size_mb=50)

    assert "2 fichier(s) dépassent la limite de 50 MiB" in msg
    assert "• `movie1.mp4`" in msg
    assert "• `movie2.mp4`" in msg
    assert "autre(s)" not in msg


def test_format_oversized_warning_with_more_than_five_items():
    items = [_make_item(f"clip_{i}.mp4", 60 * 1024 * 1024) for i in range(8)]

    msg = format_oversized_warning(items, max_size_mb=50)

    assert "8 fichier(s) dépassent la limite de 50 MiB" in msg
    assert "• `clip_0.mp4`" in msg
    assert "• `clip_4.mp4`" in msg
    assert "• ... et 3 autre(s)" in msg


def test_format_completion_summary_without_oversized():
    msg = format_completion_summary(valid_count=3, oversized_count=0)

    assert "3 média(s) envoyé(s)" in msg
    assert "ignoré(s)" not in msg


def test_format_completion_summary_with_oversized():
    msg = format_completion_summary(valid_count=2, oversized_count=1)

    assert "2 média(s) envoyé(s)" in msg
    assert "1 fichier(s) ignoré(s) car > 50 MiB" in msg

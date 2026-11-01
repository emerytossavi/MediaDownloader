"""Tests for app.utils.network streaming download utility."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.utils.network import stream_download_to_file


def test_stream_download_success(tmp_path: Path):
    dest = tmp_path / "test_file.mp4"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": "2048"}
    mock_resp.iter_content.return_value = [b"a" * 1024, b"b" * 1024]

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    result = stream_download_to_file(
        session=mock_session,
        url="https://example.com/video.mp4",
        dest_path=dest,
        max_size=5000,
    )

    assert result is True
    assert dest.exists()
    assert dest.stat().st_size == 2048
    assert not dest.with_suffix(".mp4.part").exists()


def test_stream_download_http_error(tmp_path: Path):
    dest = tmp_path / "test_file.mp4"
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    result = stream_download_to_file(
        session=mock_session,
        url="https://example.com/notfound.mp4",
        dest_path=dest,
    )

    assert result is False
    assert not dest.exists()


def test_stream_download_precheck_content_length_exceeded(tmp_path: Path):
    dest = tmp_path / "test_file.mp4"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": "100000000"}  # 100 MB

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    result = stream_download_to_file(
        session=mock_session,
        url="https://example.com/huge.mp4",
        dest_path=dest,
        max_size=50 * 1024 * 1024,  # 50 MB
    )

    assert result is False
    assert not dest.exists()
    assert not dest.with_suffix(".mp4.part").exists()


def test_stream_download_streaming_size_exceeded(tmp_path: Path):
    dest = tmp_path / "test_file.mp4"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}  # No Content-Length header
    # 3 chunks of 2000 bytes = 6000 bytes > max_size (4000)
    mock_resp.iter_content.return_value = [b"x" * 2000, b"y" * 2000, b"z" * 2000]

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    result = stream_download_to_file(
        session=mock_session,
        url="https://example.com/stream.mp4",
        dest_path=dest,
        max_size=4000,
    )

    assert result is False
    assert not dest.exists()
    assert not dest.with_suffix(".mp4.part").exists()


def test_stream_download_cancellation(tmp_path: Path):
    dest = tmp_path / "test_file.mp4"
    cancel_event = asyncio.Event()
    cancel_event.set()  # Already cancelled

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"x" * 2000]

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    result = stream_download_to_file(
        session=mock_session,
        url="https://example.com/stream.mp4",
        dest_path=dest,
        cancel_event=cancel_event,
    )

    assert result is False
    assert not dest.exists()

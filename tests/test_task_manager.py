"""Unit tests for task manager, concurrency controls, and cancellation."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from app.services.task_manager import (
    DownloadTask,
    TaskLimitExceededError,
    TaskManager,
    TaskStatus,
)


@pytest.mark.asyncio
async def test_task_lifecycle():
    tm = TaskManager(max_concurrent=2, max_per_user=1)
    task = await tm.create_task(user_id=123, chat_id=456, url="https://example.com/video")

    assert task.status == TaskStatus.QUEUED
    assert task.user_id == 123
    assert task.duration_seconds is None

    job_dir = Path("/tmp/dummy")
    task.mark_downloading(job_dir)
    assert task.status == TaskStatus.DOWNLOADING
    assert task.job_dir == job_dir
    assert task.started_at is not None

    task.mark_processing()
    assert task.status == TaskStatus.PROCESSING

    task.mark_uploading()
    assert task.status == TaskStatus.UPLOADING

    task.mark_completed()
    assert task.status == TaskStatus.COMPLETED
    assert task.completed_at is not None
    assert task.duration_seconds is not None

    await tm.finalize_task(task.task_id)


@pytest.mark.asyncio
async def test_task_per_user_limit():
    tm = TaskManager(max_concurrent=5, max_per_user=1)
    task1 = await tm.create_task(user_id=999, chat_id=111, url="https://example.com/1")

    # Second task for same user should be blocked
    with pytest.raises(TaskLimitExceededError, match="déjà 1 téléchargement"):
        await tm.create_task(user_id=999, chat_id=111, url="https://example.com/2")

    # Another user is allowed
    task_other = await tm.create_task(user_id=888, chat_id=222, url="https://example.com/2")
    assert task_other.user_id == 888

    # Finalize task1 -> user 999 can now create a new task
    await tm.finalize_task(task1.task_id)
    task2 = await tm.create_task(user_id=999, chat_id=111, url="https://example.com/3")
    assert task2.task_id != task1.task_id


@pytest.mark.asyncio
async def test_task_cancellation():
    tm = TaskManager(max_concurrent=2, max_per_user=1)
    task = await tm.create_task(user_id=123, chat_id=456, url="https://example.com/video")

    # Mock an active subprocess
    mock_process = MagicMock()
    mock_process.terminate = MagicMock()
    task.process = mock_process

    cancelled = await tm.cancel_task(task.task_id)
    assert cancelled is True
    assert task.status == TaskStatus.CANCELLED
    assert task.cancel_event.is_set()
    mock_process.terminate.assert_called_once()

    # Repeated cancellation returns False
    second_cancel = await tm.cancel_task(task.task_id)
    assert second_cancel is False

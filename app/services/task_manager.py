"""Task manager handling lifecycle, states, concurrency limits, and cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Set
import uuid

from app.utils.logging import get_logger

logger = get_logger(__name__)


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskLimitExceededError(Exception):
    """Raised when an user exceeds their maximum active concurrent tasks."""


@dataclass
class DownloadTask:
    task_id: str
    user_id: int
    chat_id: int
    url: str
    status: TaskStatus = TaskStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    job_dir: Optional[Path] = None
    error_message: Optional[str] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    process: Optional[asyncio.subprocess.Process] = None
    user_choice_event: asyncio.Event = field(default_factory=asyncio.Event)
    user_choice: Optional[str] = None

    def set_choice(self, choice: str) -> None:
        self.user_choice = choice
        self.user_choice_event.set()

    @property
    def duration_seconds(self) -> Optional[float]:
        """Compute task execution duration in seconds if started."""
        if not self.started_at:
            return None
        end_time = self.completed_at or datetime.now(timezone.utc)
        return (end_time - self.started_at).total_seconds()

    def mark_downloading(self, job_dir: Path) -> None:
        self.status = TaskStatus.DOWNLOADING
        self.started_at = datetime.now(timezone.utc)
        self.job_dir = job_dir

    def mark_processing(self) -> None:
        self.status = TaskStatus.PROCESSING

    def mark_uploading(self) -> None:
        self.status = TaskStatus.UPLOADING

    def mark_completed(self) -> None:
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)
        self.error_message = error

    def mark_cancelled(self) -> None:
        self.status = TaskStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc)


class TaskManager:
    """Manages active tasks, global concurrency, per-user limits, and cancellation."""

    def __init__(self, max_concurrent: int = 2, max_per_user: int = 1) -> None:
        self.max_concurrent = max_concurrent
        self.max_per_user = max_per_user
        self._global_semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._tasks: Dict[str, DownloadTask] = {}
        self._user_active_tasks: Dict[int, Set[str]] = {}

    async def create_task(self, user_id: int, chat_id: int, url: str) -> DownloadTask:
        """Create and register a new task if user has not reached their active limit."""
        async with self._lock:
            active_ids = self._user_active_tasks.setdefault(user_id, set())
            if len(active_ids) >= self.max_per_user:
                raise TaskLimitExceededError(
                    f"Vous avez déjà {len(active_ids)} téléchargement(s) en cours. "
                    f"Veuillez attendre la fin ou annuler la tâche précédente."
                )

            task_id = str(uuid.uuid4())
            task = DownloadTask(
                task_id=task_id,
                user_id=user_id,
                chat_id=chat_id,
                url=url,
            )
            self._tasks[task_id] = task
            active_ids.add(task_id)
            logger.info(f"Registered new task {task_id} for user {user_id}")
            return task

    async def get_task(self, task_id: str) -> Optional[DownloadTask]:
        async with self._lock:
            return self._tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or queued task and terminate any linked subprocess."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                return False

            task.cancel_event.set()
            task.mark_cancelled()

            # Terminate active process if currently executing
            if task.process is not None:
                try:
                    task.process.terminate()
                    logger.info(f"Sent SIGTERM to process of task {task_id}")
                except ProcessLookupError:
                    pass

            # Cleanup user active mapping
            active_ids = self._user_active_tasks.get(task.user_id, set())
            active_ids.discard(task_id)

            logger.info(f"Task {task_id} marked as CANCELLED")
            return True

    async def finalize_task(self, task_id: str) -> None:
        """Unregister active tracking when a task finishes (success, failure, or cancellation)."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                active_ids = self._user_active_tasks.get(task.user_id, set())
                active_ids.discard(task_id)
                if not active_ids and task.user_id in self._user_active_tasks:
                    del self._user_active_tasks[task.user_id]
                logger.debug(f"Finalized task tracking for {task_id}")

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """Return the global concurrency semaphore."""
        return self._global_semaphore

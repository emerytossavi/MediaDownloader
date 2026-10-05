"""Telegram message, command, and callback query handlers."""

from __future__ import annotations

import asyncio
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.messages import (
    ERR_ALREADY_RUNNING,
    ERR_AUTH_REQUIRED,
    ERR_CONTENT_NOT_FOUND,
    ERR_DISK_FULL,
    ERR_INVALID_URL,
    ERR_NO_MEDIA,
    ERR_NO_URL_FOUND,
    ERR_SSRF_BLOCKED,
    ERR_TIMEOUT,
    ERR_UNEXPECTED,
    HELP_MESSAGE,
    START_MESSAGE,
    STATUS_ANALYZING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    STATUS_UPLOADING,
    format_completion_summary,
    format_oversized_warning,
)
from app.bot.progress import ThrottledProgressNotifier
from app.bot.sender import MediaSender
from app.config import AppConfig
from app.security.url_validator import (
    SSRFSecurityError,
    URLValidationError,
    extract_url_from_text,
    validate_url_safety,
)
from app.services.media_inspector import MediaInspectionResult
from app.services.router import ExtractorRouter
from app.services.task_manager import DownloadTask, TaskLimitExceededError, TaskManager
from app.services.ytdlp_downloader import (
    AuthenticationRequiredError,
    ContentNotFoundError,
    DownloadCancelledError,
    DownloadTimeoutError,
    ExtractorError,
)
from app.utils.filesystem import (
    check_disk_space,
    cleanup_job_directory,
    create_job_directory,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


class BotHandlers:
    """Encapsulates all telegram application routes and handlers."""

    def __init__(
        self,
        config: AppConfig,
        task_manager: TaskManager,
        router: ExtractorRouter,
    ) -> None:
        self.config = config
        self.task_manager = task_manager
        self.router = router

    def register(self, application: Application) -> None:
        """Register all dispatch handlers on the python-telegram-bot application."""
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CallbackQueryHandler(self.cancel_callback, pattern=r"^cancel:"))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url_message)
        )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(START_MESSAGE, parse_mode="Markdown")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(HELP_MESSAGE, parse_mode="Markdown")

    async def cancel_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        task_id = query.data.split(":", 1)[1]
        task = await self.task_manager.get_task(task_id)

        if not task:
            await query.answer("Cette tâche est déjà terminée ou n'existe plus.")
            return

        # Ensure only the user who started the task can cancel it
        if query.from_user and query.from_user.id != task.user_id:
            await query.answer("Vous ne pouvez pas annuler la tâche d'un autre utilisateur.", show_alert=True)
            return

        cancelled = await self.task_manager.cancel_task(task_id)
        if cancelled:
            await query.answer("Téléchargement en cours d'annulation...")
            if query.message:
                try:
                    await query.edit_message_text(STATUS_CANCELLED)
                except Exception:
                    pass
        else:
            await query.answer("La tâche est déjà terminée.")

    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id if update.effective_chat else 0

        # 1. Extract URL
        raw_url = extract_url_from_text(update.message.text)
        if not raw_url:
            await update.message.reply_text(ERR_NO_URL_FOUND)
            return

        status_msg = await update.message.reply_text(STATUS_ANALYZING)

        # 2. Validate URL safety & SSRF
        try:
            validated_url = await validate_url_safety(raw_url, resolve_dns=True)
        except SSRFSecurityError as exc:
            logger.warning(f"SSRF blocked for user {user_id}: {exc}")
            await status_msg.edit_text(ERR_SSRF_BLOCKED)
            return
        except URLValidationError as exc:
            logger.info(f"Invalid URL from user {user_id}: {exc}")
            await status_msg.edit_text(ERR_INVALID_URL.format(detail=str(exc)))
            return

        # 3. Create Task
        try:
            task = await self.task_manager.create_task(
                user_id=user_id,
                chat_id=chat_id,
                url=validated_url,
            )
        except TaskLimitExceededError:
            await status_msg.edit_text(ERR_ALREADY_RUNNING)
            return

        notifier = ThrottledProgressNotifier(status_msg, task.task_id)

        # 4. Spawn background processing to keep Telegram polling responsive
        asyncio.create_task(
            self._process_workflow(
                task=task,
                notifier=notifier,
                context=context,
            )
        )

    async def _process_workflow(
        self,
        task: DownloadTask,
        notifier: ThrottledProgressNotifier,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Isolated download, inspection, upload, and cleanup pipeline for a task."""
        job_dir = None
        max_size_mb = self.config.max_file_size // (1024 * 1024)

        try:
            if not check_disk_space(self.config.temp_dir):
                task.mark_failed(ERR_DISK_FULL)
                await notifier.update(ERR_DISK_FULL, force=True, show_cancel=False)
                return

            # Update status to Queued
            await notifier.update(STATUS_QUEUED, force=True, show_cancel=True)

            async with self.task_manager.semaphore:
                if task.cancel_event.is_set():
                    raise DownloadCancelledError()

                job_dir = create_job_directory(self.config.temp_dir, task.task_id)
                task.mark_downloading(job_dir)
                await notifier.update(STATUS_DOWNLOADING, force=True, show_cancel=True)

                # Download media
                executed_tool, inspection = await self.router.execute_download(task, job_dir)

                if task.cancel_event.is_set():
                    raise DownloadCancelledError()

                task.mark_processing()
                await notifier.update(STATUS_PROCESSING, show_cancel=True)

                if not inspection.has_valid_media and not inspection.has_oversized_media:
                    task.mark_failed(ERR_NO_MEDIA)
                    await notifier.update(ERR_NO_MEDIA, force=True, show_cancel=False)
                    return

                # Send media
                task.mark_uploading()
                await notifier.update(STATUS_UPLOADING, show_cancel=False)

                sender = MediaSender(context.bot)
                sent_count = await sender.send_media_result(task.chat_id, inspection)

                # Notify user of oversized items if any were rejected
                if inspection.has_oversized_media:
                    warning_text = format_oversized_warning(inspection.oversized_items, max_size_mb)
                    await context.bot.send_message(chat_id=task.chat_id, text=warning_text, parse_mode="Markdown")

                task.mark_completed()
                summary = format_completion_summary(sent_count, len(inspection.oversized_items))
                await notifier.update(summary, force=True, show_cancel=False)

        except DownloadCancelledError:
            task.mark_cancelled()
            await notifier.update(STATUS_CANCELLED, force=True, show_cancel=False)
        except AuthenticationRequiredError:
            task.mark_failed(ERR_AUTH_REQUIRED)
            await notifier.update(ERR_AUTH_REQUIRED, force=True, show_cancel=False)
        except ContentNotFoundError:
            task.mark_failed(ERR_CONTENT_NOT_FOUND)
            await notifier.update(ERR_CONTENT_NOT_FOUND, force=True, show_cancel=False)
        except DownloadTimeoutError:
            task.mark_failed(ERR_TIMEOUT)
            await notifier.update(ERR_TIMEOUT, force=True, show_cancel=False)
        except ExtractorError as exc:
            task.mark_failed(str(exc))
            await notifier.update(f"⚠️ {str(exc)}", force=True, show_cancel=False)
        except Exception as exc:
            logger.exception(f"[Task {task.task_id}] Unhandled error: {exc}")
            task.mark_failed(str(exc))
            await notifier.update(ERR_UNEXPECTED, force=True, show_cancel=False)
        finally:
            cleanup_job_directory(job_dir)
            await self.task_manager.finalize_task(task.task_id)

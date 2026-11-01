"""Telegram message, command, and callback query handlers."""

from __future__ import annotations

import asyncio
from typing import Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from app.services.download_tracker import DownloadTracker
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
        tracker: Optional[DownloadTracker] = None,
    ) -> None:
        self.config = config
        self.task_manager = task_manager
        self.router = router
        self.tracker = tracker or DownloadTracker(config.history_file)

    def register(self, application: Application) -> None:
        """Register all dispatch handlers on the python-telegram-bot application."""
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CallbackQueryHandler(self.cancel_callback, pattern=r"^cancel:"))
        application.add_handler(CallbackQueryHandler(self.choice_callback, pattern=r"^choice:"))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url_message)
        )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(START_MESSAGE, parse_mode="Markdown")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(HELP_MESSAGE, parse_mode="Markdown")

    async def choice_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        parts = query.data.split(":", 2)
        if len(parts) != 3:
            return

        action, task_id = parts[1], parts[2]
        task = await self.task_manager.get_task(task_id)

        if not task:
            await query.answer("Cette tâche est déjà terminée ou n'existe plus.")
            return

        if query.from_user and query.from_user.id != task.user_id:
            await query.answer("Vous ne pouvez pas modifier le choix d'un autre utilisateur.", show_alert=True)
            return

        task.set_choice(action)
        if action == "split":
            await query.answer("✂️ Option choisie : Scinder en plusieurs parties (HD).")
        else:
            await query.answer("🗜️ Option choisie : Compresser en 1 seul fichier.")

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
            await self.tracker.record_failure(
                url=raw_url,
                user_id=user_id,
                reason=f"Sécurité : URL bloquée (SSRF) - {exc}",
                error_type="SSRFSecurityError",
                extractor="validator",
            )
            await status_msg.edit_text(ERR_SSRF_BLOCKED)
            return
        except URLValidationError as exc:
            logger.info(f"Invalid URL from user {user_id}: {exc}")
            await self.tracker.record_failure(
                url=raw_url,
                user_id=user_id,
                reason=f"URL invalide : {exc}",
                error_type="URLValidationError",
                extractor="validator",
            )
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
            await self.tracker.record_failure(
                url=validated_url,
                user_id=user_id,
                reason="Limite de tâches simultanées dépassée pour cet utilisateur.",
                error_type="TaskLimitExceededError",
                extractor="task_manager",
            )
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
        executed_tool = None
        max_size_mb = self.config.max_file_size // (1024 * 1024)

        try:
            if not check_disk_space(self.config.temp_dir):
                task.mark_failed(ERR_DISK_FULL)
                await self.tracker.record_failure(
                    task=task,
                    reason="Espace disque temporaire insuffisant.",
                    error_type="DiskFullError",
                    extractor="system",
                )
                await notifier.update(ERR_DISK_FULL, force=True, show_cancel=False)
                return

            # Update status to Queued
            await notifier.update(STATUS_QUEUED, force=True, show_cancel=True)

            user_strategy = "auto"
            try:
                probe_info = await self.router.probe_media(task.url)
            except Exception as probe_err:
                logger.debug(f"Media probe error for {task.url}: {probe_err}")
                probe_info = None

            if probe_info and not task.cancel_event.is_set():
                est_size = probe_info.get("estimated_size") or 0
                dur = probe_info.get("duration") or 0
                title = probe_info.get("title") or "Vidéo"

                if est_size > self.config.max_file_size or (dur > 600 and est_size == 0):
                    dur_str = f"{int(dur // 60)}m {int(dur % 60):02d}s" if dur else "Inconnue"
                    size_mb = est_size // (1024 * 1024) if est_size else "> 50"

                    prompt_text = (
                        f"📹 *Média volumineux détecté*\n\n"
                        f"• Titre : {title}\n"
                        f"• Durée : {dur_str}\n"
                        f"• Poids estimé en haute qualité : ~{size_mb} Mo\n\n"
                        f"_La limite d'envoi par fichier sur Telegram est de 50 Mo._\n\n"
                        f"Que souhaitez-vous faire ?\n"
                        f"• *Scinder* : Découpe en plusieurs parties de < 50 Mo en qualité originale.\n"
                        f"• *Compresser* : Réduit la qualité pour tenir dans 1 seul fichier.\n\n"
                        f"⏱ _Sans réponse sous 30 secondes, le média sera automatiquement scindé._"
                    )

                    markup = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✂️ Scinder (Haute qualité)", callback_data=f"choice:split:{task.task_id}"),
                            InlineKeyboardButton("🗜️ Compresser (1 seul fichier)", callback_data=f"choice:compress:{task.task_id}"),
                        ],
                        [
                            InlineKeyboardButton("🛑 Annuler", callback_data=f"cancel:{task.task_id}"),
                        ],
                    ])

                    await notifier.update(prompt_text, force=True, show_cancel=False, custom_markup=markup, parse_mode="Markdown")

                    try:
                        await asyncio.wait_for(task.user_choice_event.wait(), timeout=30.0)
                        user_strategy = task.user_choice or "split"
                    except asyncio.TimeoutError:
                        logger.info(f"[Task {task.task_id}] Délai de 30s écoulé sans réponse : scission automatique choisie par défaut.")
                        user_strategy = "split"
                        try:
                            await notifier.update("⏱ *Délai de 30s écoulé* : Scission automatique sélectionnée.", force=True, show_cancel=True, parse_mode="Markdown")
                        except Exception:
                            pass

                    if user_strategy == "cancel" or task.cancel_event.is_set():
                        raise DownloadCancelledError()

            async with self.task_manager.semaphore:
                if task.cancel_event.is_set():
                    raise DownloadCancelledError()

                job_dir = create_job_directory(self.config.temp_dir, task.task_id)
                task.mark_downloading(job_dir)
                await notifier.update(STATUS_DOWNLOADING, force=True, show_cancel=True)

                # Download media
                executed_tool, inspection = await self.router.execute_download(
                    task=task,
                    target_dir=job_dir,
                    strategy=user_strategy,
                )

                if task.cancel_event.is_set():
                    raise DownloadCancelledError()

                task.mark_processing()
                await notifier.update(STATUS_PROCESSING, show_cancel=True)

                if not inspection.has_valid_media and not inspection.has_oversized_media:
                    task.mark_failed(ERR_NO_MEDIA)
                    await self.tracker.record_failure(
                        task=task,
                        reason="Aucun média téléchargeable n'a été trouvé à cette adresse.",
                        error_type="NoMediaError",
                        extractor=executed_tool.value if executed_tool else "inconnu",
                    )
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
                await self.tracker.record_success(
                    task=task,
                    extractor=executed_tool.value if executed_tool else "inconnu",
                    files_count=sent_count,
                )
                summary = format_completion_summary(sent_count, len(inspection.oversized_items))
                await notifier.update(summary, force=True, show_cancel=False)

        except DownloadCancelledError:
            task.mark_cancelled()
            await self.tracker.record_failure(
                task=task,
                reason="Téléchargement annulé par l'utilisateur.",
                error_type="DownloadCancelledError",
                extractor=executed_tool.value if executed_tool else "inconnu",
            )
            await notifier.update(STATUS_CANCELLED, force=True, show_cancel=False)
        except AuthenticationRequiredError as exc:
            msg = str(exc).strip() if str(exc).strip() else ERR_AUTH_REQUIRED
            task.mark_failed(msg)
            await self.tracker.record_failure(
                task=task,
                reason=msg,
                error_type="AuthenticationRequiredError",
                extractor=executed_tool.value if executed_tool else "inconnu",
            )
            await notifier.update(f"🔒 {msg}" if not msg.startswith("🔒") else msg, force=True, show_cancel=False)
        except ContentNotFoundError as exc:
            msg = str(exc).strip() if str(exc).strip() else ERR_CONTENT_NOT_FOUND
            task.mark_failed(msg)
            await self.tracker.record_failure(
                task=task,
                reason=msg,
                error_type="ContentNotFoundError",
                extractor=executed_tool.value if executed_tool else "inconnu",
            )
            await notifier.update(f"🔍 {msg}" if not msg.startswith("🔍") else msg, force=True, show_cancel=False)
        except DownloadTimeoutError:
            task.mark_failed(ERR_TIMEOUT)
            await self.tracker.record_failure(
                task=task,
                reason=f"Délai d'attente dépassé ({self.config.download_timeout}s).",
                error_type="DownloadTimeoutError",
                extractor=executed_tool.value if executed_tool else "inconnu",
            )
            await notifier.update(ERR_TIMEOUT, force=True, show_cancel=False)
        except ExtractorError as exc:
            task.mark_failed(str(exc))
            await self.tracker.record_failure(
                task=task,
                reason=str(exc),
                error_type="ExtractorError",
                extractor=executed_tool.value if executed_tool else "inconnu",
            )
            await notifier.update(f"⚠️ {str(exc)}", force=True, show_cancel=False)
        except Exception as exc:
            logger.exception(f"[Task {task.task_id}] Unhandled error: {exc}")
            task.mark_failed(str(exc))
            await self.tracker.record_failure(
                task=task,
                reason=str(exc),
                error_type=exc.__class__.__name__,
                extractor=executed_tool.value if executed_tool else "inconnu",
            )
            await notifier.update(ERR_UNEXPECTED, force=True, show_cancel=False)
        finally:
            cleanup_job_directory(job_dir)
            await self.task_manager.finalize_task(task.task_id)

"""Application entrypoint for Media Downloader Telegram Bot."""

from __future__ import annotations

import sys
from telegram.ext import ApplicationBuilder

from app.bot.handlers import BotHandlers
from app.config import AppConfig, ConfigError
from app.services.download_tracker import DownloadTracker
from app.services.router import ExtractorRouter
from app.services.task_manager import TaskManager
from app.services.user_tracker import UserTracker
from app.utils.filesystem import cleanup_abandoned_jobs, ensure_directory
from app.utils.logging import get_logger, setup_logging

logger = get_logger("app.main")


def main() -> None:
    """Bootstrap and start the Telegram Media Downloader Bot."""
    try:
        config = AppConfig.load()
    except ConfigError as exc:
        print(f"FATAL: Configuration error - {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(config.log_level)
    logger.info("Initializing Media Downloader Bot...")
    logger.info(f"Configuration: max_file_size={config.max_file_size} bytes, max_concurrent={config.max_concurrent_downloads}")

    # Ensure workspace directory exists and clean up leftovers from previous sessions
    ensure_directory(config.temp_dir)
    cleanup_abandoned_jobs(config.temp_dir)

    # Initialize domain services
    task_manager = TaskManager(
        max_concurrent=config.max_concurrent_downloads,
        max_per_user=config.max_concurrent_downloads_per_user,
    )

    router = ExtractorRouter(
        max_file_size=config.max_file_size,
        download_timeout=config.download_timeout,
        ytdlp_bin=config.ytdlp_bin,
        gallerydl_bin=config.gallerydl_bin,
        ffmpeg_bin=config.ffmpeg_bin,
        cookies_file=config.cookies_file,
        cookies_browser=config.cookies_browser,
    )

    # Tracker for tracking downloads by category (completed vs failed with reasons)
    tracker = DownloadTracker(file_path=config.history_file)

    # Tracker pour enregistrer les utilisateurs et diffuser les notifications au lancement
    user_tracker = UserTracker(
        file_path=config.users_file,
        seed_history_file=config.history_file,
    )

    async def on_startup(application) -> None:
        if config.notify_on_startup:
            logger.info("Vérification des notifications de démarrage...")
            stats = await user_tracker.notify_users_on_startup(
                bot=application.bot,
                message=config.startup_notification_message,
            )
            logger.info(
                f"Notification de démarrage : {stats['sent']} envoyée(s), "
                f"{stats['failed']} échec(s) sur {stats['total']} utilisateur(s)."
            )

    # Build python-telegram-bot application
    app = ApplicationBuilder().token(config.telegram_bot_token).post_init(on_startup).build()

    handlers = BotHandlers(
        config=config,
        task_manager=task_manager,
        router=router,
        tracker=tracker,
        user_tracker=user_tracker,
    )
    handlers.register(app)

    logger.info("Bot handlers registered. Starting polling...")

    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution interrupted by signal.")
    finally:
        logger.info("Performing final workspace cleanup...")
        cleanup_abandoned_jobs(config.temp_dir)
        logger.info("Media Downloader Bot shutdown complete.")


if __name__ == "__main__":
    main()

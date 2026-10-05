"""Configuration management for Media Downloader Bot."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Default Telegram limit: 50 MiB (in bytes)
DEFAULT_MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 52,428,800 bytes
DEFAULT_CONCURRENT_DOWNLOADS: int = 2
DEFAULT_CONCURRENT_PER_USER: int = 1
DEFAULT_DOWNLOAD_TIMEOUT: int = 300
DEFAULT_TEMP_DIR: str = "/tmp/telegram-downloader"
DEFAULT_HISTORY_FILE: str = "downloads_history.json"
DEFAULT_LOG_LEVEL: str = "INFO"


class ConfigError(ValueError):
    """Raised when application configuration is missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    telegram_bot_token: str
    max_file_size: int
    max_concurrent_downloads: int
    max_concurrent_downloads_per_user: int
    download_timeout: int
    temp_dir: Path
    log_level: str
    ytdlp_bin: str
    gallerydl_bin: str
    ffmpeg_bin: Optional[str]
    history_file: Path = Path(DEFAULT_HISTORY_FILE)
    cookies_file: Optional[Path] = None
    cookies_browser: Optional[str] = None

    @classmethod
    def load(cls, env_file: Optional[Path | str] = None) -> AppConfig:
        """Load configuration from environment variables and optional .env file."""
        if env_file:
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            load_dotenv(override=False)

        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token or token == "your_telegram_bot_token_here":
            raise ConfigError(
                "TELEGRAM_BOT_TOKEN is mandatory and must not be empty or the placeholder value. "
                "Set it in your .env file or environment."
            )

        max_file_size = cls._parse_int(
            "MAX_FILE_SIZE", os.getenv("MAX_FILE_SIZE"), DEFAULT_MAX_FILE_SIZE, min_val=1024
        )
        max_concurrent = cls._parse_int(
            "MAX_CONCURRENT_DOWNLOADS",
            os.getenv("MAX_CONCURRENT_DOWNLOADS"),
            DEFAULT_CONCURRENT_DOWNLOADS,
            min_val=1,
        )
        max_concurrent_per_user = cls._parse_int(
            "MAX_CONCURRENT_DOWNLOADS_PER_USER",
            os.getenv("MAX_CONCURRENT_DOWNLOADS_PER_USER"),
            DEFAULT_CONCURRENT_PER_USER,
            min_val=1,
        )
        if max_concurrent_per_user > max_concurrent:
            raise ConfigError(
                f"MAX_CONCURRENT_DOWNLOADS_PER_USER ({max_concurrent_per_user}) "
                f"cannot exceed MAX_CONCURRENT_DOWNLOADS ({max_concurrent})."
            )

        timeout = cls._parse_int(
            "DOWNLOAD_TIMEOUT", os.getenv("DOWNLOAD_TIMEOUT"), DEFAULT_DOWNLOAD_TIMEOUT, min_val=10
        )

        raw_temp_dir = os.getenv("TEMP_DIR", DEFAULT_TEMP_DIR).strip()
        temp_dir = Path(raw_temp_dir).expanduser().resolve()

        log_level = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if log_level not in valid_levels:
            raise ConfigError(
                f"Invalid LOG_LEVEL '{log_level}'. Must be one of: {', '.join(sorted(valid_levels))}."
            )

        # External binaries
        ytdlp_bin = os.getenv("YTDLP_BIN", "yt-dlp").strip()
        gallerydl_bin = os.getenv("GALLERYDL_BIN", "gallery-dl").strip()
        ffmpeg_bin = os.getenv("FFMPEG_BIN")
        if ffmpeg_bin:
            ffmpeg_bin = ffmpeg_bin.strip()
        else:
            ffmpeg_bin = shutil.which("ffmpeg")

        history_file_str = os.getenv("DOWNLOAD_HISTORY_FILE", DEFAULT_HISTORY_FILE).strip()
        history_file = Path(history_file_str)

        cookies_str = os.getenv("COOKIES_FILE")
        cookies_file = Path(cookies_str).resolve() if cookies_str and cookies_str.strip() else None

        cookies_browser = os.getenv("COOKIES_BROWSER")
        cookies_browser = cookies_browser.strip() if cookies_browser and cookies_browser.strip() else None

        return cls(
            telegram_bot_token=token,
            max_file_size=max_file_size,
            max_concurrent_downloads=max_concurrent,
            max_concurrent_downloads_per_user=max_concurrent_per_user,
            download_timeout=timeout,
            temp_dir=temp_dir,
            log_level=log_level,
            ytdlp_bin=ytdlp_bin,
            gallerydl_bin=gallerydl_bin,
            ffmpeg_bin=ffmpeg_bin,
            history_file=history_file,
            cookies_file=cookies_file,
            cookies_browser=cookies_browser,
        )

    @staticmethod
    def _parse_int(var_name: str, value: Optional[str], default: int, min_val: int = 1) -> int:
        if value is None or not value.strip():
            return default
        try:
            parsed = int(value.strip())
            if parsed < min_val:
                raise ConfigError(f"{var_name} must be >= {min_val}, got {parsed}")
            return parsed
        except ValueError as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"{var_name} must be a valid integer, got '{value}'") from exc

"""Logging configuration with secret redaction for Media Downloader Bot."""

from __future__ import annotations

import logging
import re
import sys
from typing import Optional

# Regex pattern to match Telegram Bot Tokens (e.g. 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ)
_TELEGRAM_TOKEN_REGEX = re.compile(r"\b\d{8,10}:[a-zA-Z0-9_-]{35}\b")
_BEARER_TOKEN_REGEX = re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE)
_PASSWORD_REGEX = re.compile(r"(password|pwd|secret|token)\s*=\s*['\"]?[^\s'\"]+", re.IGNORECASE)


class RedactingFormatter(logging.Formatter):
    """Logging formatter that strips or masks sensitive tokens and secrets."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        redacted = _TELEGRAM_TOKEN_REGEX.sub("[REDACTED_TELEGRAM_TOKEN]", original)
        redacted = _BEARER_TOKEN_REGEX.sub(r"\1[REDACTED_BEARER]", redacted)
        redacted = _PASSWORD_REGEX.sub(r"\1=[REDACTED]", redacted)
        return redacted


def setup_logging(log_level: str = "INFO") -> None:
    """Configure the root logger with redacting formatter and stream handler."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = RedactingFormatter(
        fmt="%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers on re-initialization
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Suppress verbose HTTP polling logs from httpx/httpcore/telegram
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Obtain a logger instance for a given module name."""
    return logging.getLogger(name)

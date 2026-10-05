"""Unit tests for configuration loading and validation."""

import os
import pytest
from pathlib import Path
from app.config import AppConfig, ConfigError


def test_config_load_valid(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ12345678")
    monkeypatch.setenv("MAX_FILE_SIZE", "10485760")
    monkeypatch.setenv("MAX_CONCURRENT_DOWNLOADS", "3")
    monkeypatch.setenv("MAX_CONCURRENT_DOWNLOADS_PER_USER", "2")
    monkeypatch.setenv("DOWNLOAD_TIMEOUT", "120")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "custom_temp"))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    config = AppConfig.load()
    assert config.telegram_bot_token == "123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ12345678"
    assert config.max_file_size == 10485760
    assert config.max_concurrent_downloads == 3
    assert config.max_concurrent_downloads_per_user == 2
    assert config.download_timeout == 120
    assert config.temp_dir == (tmp_path / "custom_temp").resolve()
    assert config.log_level == "DEBUG"


def test_config_missing_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is mandatory"):
        AppConfig.load()


def test_config_placeholder_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token_here")
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is mandatory"):
        AppConfig.load()


def test_config_invalid_user_concurrency(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token_123")
    monkeypatch.setenv("MAX_CONCURRENT_DOWNLOADS", "2")
    monkeypatch.setenv("MAX_CONCURRENT_DOWNLOADS_PER_USER", "5")
    with pytest.raises(ConfigError, match="cannot exceed MAX_CONCURRENT_DOWNLOADS"):
        AppConfig.load()


def test_config_invalid_log_level(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token_123")
    monkeypatch.setenv("LOG_LEVEL", "SUPER_VERBOSE")
    with pytest.raises(ConfigError, match="Invalid LOG_LEVEL"):
        AppConfig.load()


def test_config_invalid_int(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token_123")
    monkeypatch.setenv("MAX_FILE_SIZE", "not_a_number")
    with pytest.raises(ConfigError, match="must be a valid integer"):
        AppConfig.load()

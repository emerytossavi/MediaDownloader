# ==============================================================================
# Production Dockerfile for Media Downloader Telegram Bot
# ==============================================================================
FROM python:3.12-slim-bookworm AS base

# Install system dependencies: ffmpeg for media merging/muxing, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged user for security isolation
RUN useradd -m -u 1000 -s /bin/bash appuser

WORKDIR /home/appuser/app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create temporary download directory with proper permissions
RUN mkdir -p /tmp/telegram-downloader && chown -R appuser:appuser /tmp/telegram-downloader

# Copy application source code
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser pyproject.toml .

# Switch to non-root user
USER appuser

# Healthcheck to verify Python process responsiveness
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD python3 -c "import app.config" || exit 1

# Start bot application
CMD ["python3", "-m", "app.main"]

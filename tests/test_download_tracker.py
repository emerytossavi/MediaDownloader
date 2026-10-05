"""Tests unitaires pour le service de suivi et sauvegarde des téléchargements."""

import asyncio
from pathlib import Path
import pytest

from app.services.download_tracker import DownloadTracker, detect_url_category
from app.services.task_manager import DownloadTask


def test_detect_url_category():
    assert detect_url_category("https://vt.tiktok.com/ZSb97rvFw/") == "tiktok"
    assert detect_url_category("https://www.tiktok.com/@user/video/12345") == "tiktok"
    assert detect_url_category("https://www.instagram.com/reel/C12345/") == "instagram"
    assert detect_url_category("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert detect_url_category("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detect_url_category("https://x.com/user/status/12345") == "twitter_x"
    assert detect_url_category("https://twitter.com/user/status/12345") == "twitter_x"
    assert detect_url_category("https://www.reddit.com/r/funny/comments/123") == "reddit"
    assert detect_url_category("https://pin.it/abcde") == "pinterest"
    assert detect_url_category("https://example.com/media/file.mp4") == "example"
    assert detect_url_category("") == "inconnu"


@pytest.mark.asyncio
async def test_record_success_and_failure(tmp_path: Path):
    history_file = tmp_path / "downloads_history.json"
    tracker = DownloadTracker(history_file)

    task_success = DownloadTask(
        task_id="task-success-1",
        user_id=12345,
        chat_id=12345,
        url="https://vt.tiktok.com/ZSb97rvFw/",
    )
    task_success.mark_downloading(tmp_path)
    task_success.mark_completed()

    await tracker.record_success(
        task=task_success,
        extractor="yt-dlp",
        files_count=1,
    )

    task_failed = DownloadTask(
        task_id="task-failed-1",
        user_id=12345,
        chat_id=12345,
        url="https://www.instagram.com/p/invalid/",
    )
    task_failed.mark_downloading(tmp_path)
    task_failed.mark_failed("Post introuvable ou supprimé.")

    await tracker.record_failure(
        task=task_failed,
        reason="Post introuvable ou supprimé.",
        error_type="ContentNotFoundError",
        extractor="gallery-dl",
    )

    # Also test manual record failure for an URL blocked before task creation
    await tracker.record_failure(
        url="http://127.0.0.1/malicious",
        user_id=99999,
        reason="Sécurité : URL bloquée (SSRF)",
        error_type="SSRFSecurityError",
        extractor="validator",
    )

    history = await tracker.get_history()

    # Check stats
    stats = history["statistiques"]
    assert stats["total"] == 3
    assert stats["aboutis"] == 1
    assert stats["non_aboutis"] == 2

    # Check categorization
    assert "tiktok" in history["aboutis"]
    assert len(history["aboutis"]["tiktok"]) == 1
    entry_success = history["aboutis"]["tiktok"][0]
    assert entry_success["url"] == "https://vt.tiktok.com/ZSb97rvFw/"
    assert entry_success["task_id"] == "task-success-1"
    assert entry_success["extracteur"] == "yt-dlp"
    assert entry_success["fichiers_envoyes"] == 1

    assert "instagram" in history["non_aboutis"]
    assert len(history["non_aboutis"]["instagram"]) == 1
    entry_failed = history["non_aboutis"]["instagram"][0]
    assert entry_failed["url"] == "https://www.instagram.com/p/invalid/"
    assert entry_failed["raison"] == "Post introuvable ou supprimé."
    assert entry_failed["type_erreur"] == "ContentNotFoundError"
    assert entry_failed["extracteur"] == "gallery-dl"

    # SSRF recorded under loopback/inconnu or similar
    assert any("SSRF" in item["raison"] for cat in history["non_aboutis"].values() for item in cat)


@pytest.mark.asyncio
async def test_concurrent_writes(tmp_path: Path):
    history_file = tmp_path / "downloads_history_concurrent.json"
    tracker = DownloadTracker(history_file)

    async def add_item(i: int):
        if i % 2 == 0:
            await tracker.record_success(
                url=f"https://www.youtube.com/watch?v=video_{i}",
                user_id=i,
                task_id=f"task-{i}",
                extractor="yt-dlp",
            )
        else:
            await tracker.record_failure(
                url=f"https://vt.tiktok.com/fail_{i}",
                user_id=i,
                task_id=f"task-{i}",
                reason=f"Erreur test {i}",
                error_type="ExtractorError",
                extractor="yt-dlp",
            )

    await asyncio.gather(*[add_item(i) for i in range(20)])

    history = await tracker.get_history()
    stats = history["statistiques"]
    assert stats["total"] == 20
    assert stats["aboutis"] == 10
    assert stats["non_aboutis"] == 10
    assert len(history["aboutis"]["youtube"]) == 10
    assert len(history["non_aboutis"]["tiktok"]) == 10

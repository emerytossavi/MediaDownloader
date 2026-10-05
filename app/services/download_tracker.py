"""Service de suivi et de sauvegarde des téléchargements (aboutis et non-aboutis)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.services.task_manager import DownloadTask
from app.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_HISTORY_FILE = "downloads_history.json"


def _matches_domain(domain: str, targets: List[str]) -> bool:
    for target in targets:
        if domain == target or domain.endswith("." + target):
            return True
    return False


def detect_url_category(url: str) -> str:
    """Détecte la catégorie de la plateforme à partir de l'URL."""
    if not url:
        return "inconnu"

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().split(":")[0]
    except Exception:
        return "inconnu"

    # Plateformes courantes avec correspondance exacte ou sous-domaine
    if _matches_domain(domain, ["tiktok.com", "douyin.com"]):
        return "tiktok"
    if _matches_domain(domain, ["instagram.com", "instagr.am"]):
        return "instagram"
    if _matches_domain(domain, ["youtube.com", "youtu.be"]):
        return "youtube"
    if _matches_domain(domain, ["twitter.com", "x.com", "t.co"]):
        return "twitter_x"
    if _matches_domain(domain, ["reddit.com", "redd.it"]):
        return "reddit"
    if _matches_domain(domain, ["facebook.com", "fb.watch", "fb.com"]):
        return "facebook"
    if _matches_domain(domain, ["pinterest.com", "pin.it"]):
        return "pinterest"
    if _matches_domain(domain, ["threads.net"]):
        return "threads"
    if _matches_domain(domain, ["twitch.tv"]):
        return "twitch"
    if _matches_domain(domain, ["soundcloud.com"]):
        return "soundcloud"
    if _matches_domain(domain, ["vimeo.com"]):
        return "vimeo"
    if _matches_domain(domain, ["dailymotion.com", "dai.ly"]):
        return "dailymotion"

    # Détection générique basée sur le nom de domaine
    clean_domain = domain.split(":")[0]
    parts = clean_domain.split(".")
    if len(parts) >= 2:
        return parts[-2]

    return "autre"


class DownloadTracker:
    """Gère l'enregistrement catégorisé des téléchargements dans un fichier JSON."""

    def __init__(self, file_path: Path | str = DEFAULT_HISTORY_FILE) -> None:
        self.file_path = Path(file_path).resolve()
        self._lock = asyncio.Lock()
        self._ensure_file_initialized()

    def _ensure_file_initialized(self) -> None:
        """Initialise le fichier JSON s'il n'existe pas encore."""
        if not self.file_path.exists():
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                initial_data = self._create_initial_structure()
                self._sync_write(initial_data)
                logger.info(f"Fichier de suivi initialisé : {self.file_path}")
            except Exception as exc:
                logger.error(f"Erreur lors de l'initialisation du fichier de suivi {self.file_path}: {exc}")

    def _create_initial_structure(self) -> Dict[str, Any]:
        """Crée la structure de base du fichier de suivi."""
        return {
            "derniere_mise_a_jour": datetime.now(timezone.utc).isoformat(),
            "statistiques": {
                "total": 0,
                "aboutis": 0,
                "non_aboutis": 0,
            },
            "aboutis": {},
            "non_aboutis": {},
        }

    def _sync_read(self) -> Dict[str, Any]:
        """Lecture synchrone sécurisée du fichier JSON."""
        if not self.file_path.exists():
            return self._create_initial_structure()

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return self._create_initial_structure()
                # Assure les clés de base
                data.setdefault("derniere_mise_a_jour", datetime.now(timezone.utc).isoformat())
                data.setdefault("statistiques", {"total": 0, "aboutis": 0, "non_aboutis": 0})
                data.setdefault("aboutis", {})
                data.setdefault("non_aboutis", {})
                return data
        except Exception as exc:
            logger.warning(f"Fichier de suivi corrompu ou illisible ({exc}), réinitialisation.")
            return self._create_initial_structure()

    def _sync_write(self, data: Dict[str, Any]) -> None:
        """Écriture atomique pour éviter toute corruption de données."""
        temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, self.file_path)

    async def record_success(
        self,
        task: Optional[DownloadTask] = None,
        *,
        url: Optional[str] = None,
        user_id: Optional[int] = None,
        task_id: Optional[str] = None,
        extractor: str = "unknown",
        files_count: int = 1,
        duration_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Enregistre un téléchargement ayant abouti dans sa catégorie."""
        actual_url = (task.url if task else url) or ""
        actual_user_id = task.user_id if task else (user_id or 0)
        actual_task_id = task.task_id if task else (task_id or "")
        actual_duration = task.duration_seconds if task else duration_seconds

        category = detect_url_category(actual_url)
        now_iso = datetime.now(timezone.utc).isoformat()

        entry = {
            "url": actual_url,
            "categorie": category,
            "task_id": actual_task_id,
            "user_id": actual_user_id,
            "extracteur": extractor,
            "fichiers_envoyes": files_count,
            "duree_secondes": round(actual_duration, 2) if actual_duration is not None else None,
            "date": now_iso,
        }

        async with self._lock:
            data = await asyncio.to_thread(self._sync_read)
            category_list: List[Dict[str, Any]] = data["aboutis"].setdefault(category, [])
            category_list.append(entry)

            data["statistiques"]["aboutis"] = sum(len(items) for items in data["aboutis"].values())
            data["statistiques"]["total"] = (
                data["statistiques"]["aboutis"] + data["statistiques"]["non_aboutis"]
            )
            data["derniere_mise_a_jour"] = now_iso

            await asyncio.to_thread(self._sync_write, data)

        logger.info(f"Téléchargement abouti enregistré [{category}] pour {actual_url}")
        return entry

    async def record_failure(
        self,
        task: Optional[DownloadTask] = None,
        *,
        url: Optional[str] = None,
        user_id: Optional[int] = None,
        task_id: Optional[str] = None,
        reason: str,
        error_type: str = "ExtractorError",
        extractor: str = "unknown",
        duration_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Enregistre un téléchargement n'ayant pas abouti avec la raison."""
        actual_url = (task.url if task else url) or ""
        actual_user_id = task.user_id if task else (user_id or 0)
        actual_task_id = task.task_id if task else (task_id or "")
        actual_duration = task.duration_seconds if task else duration_seconds

        category = detect_url_category(actual_url)
        now_iso = datetime.now(timezone.utc).isoformat()

        entry = {
            "url": actual_url,
            "categorie": category,
            "task_id": actual_task_id,
            "user_id": actual_user_id,
            "extracteur": extractor,
            "raison": reason,
            "type_erreur": error_type,
            "duree_secondes": round(actual_duration, 2) if actual_duration is not None else None,
            "date": now_iso,
        }

        async with self._lock:
            data = await asyncio.to_thread(self._sync_read)
            category_list: List[Dict[str, Any]] = data["non_aboutis"].setdefault(category, [])
            category_list.append(entry)

            data["statistiques"]["non_aboutis"] = sum(len(items) for items in data["non_aboutis"].values())
            data["statistiques"]["total"] = (
                data["statistiques"]["aboutis"] + data["statistiques"]["non_aboutis"]
            )
            data["derniere_mise_a_jour"] = now_iso

            await asyncio.to_thread(self._sync_write, data)

        logger.info(f"Téléchargement non-abouti enregistré [{category}] pour {actual_url} (Raison: {reason})")
        return entry

    async def get_history(self) -> Dict[str, Any]:
        """Retourne l'intégralité du suivi."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_read)

"""Service de suivi et de persistance des utilisateurs avec notifications de démarrage."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_USERS_FILE = "users_history.json"
DEFAULT_STARTUP_MESSAGE = "Hey, du nouveau toi ? 😊\nLe bot est en ligne et prêt pour tes téléchargements !"


class UserTracker:
    """Gère l'enregistrement des utilisateurs et l'envoi de notifications au démarrage."""

    def __init__(
        self,
        file_path: Path | str = DEFAULT_USERS_FILE,
        seed_history_file: Optional[Path | str] = None,
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.seed_history_file = Path(seed_history_file).resolve() if seed_history_file else None
        self._lock = asyncio.Lock()
        self._ensure_file_initialized()

    def _ensure_file_initialized(self) -> None:
        """Initialise le fichier JSON s'il n'existe pas encore, en important les utilisateurs existants."""
        if not self.file_path.exists():
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                initial_data = self._create_initial_structure()

                # Amorçage depuis l'historique des téléchargements existants si disponible
                if self.seed_history_file and self.seed_history_file.exists():
                    self._seed_from_download_history(initial_data, self.seed_history_file)

                self._sync_write(initial_data)
                logger.info(f"Fichier d'historique utilisateurs initialisé : {self.file_path}")
            except Exception as exc:
                logger.error(f"Erreur lors de l'initialisation du fichier utilisateurs {self.file_path}: {exc}")

    def _create_initial_structure(self) -> Dict[str, Any]:
        """Crée la structure de base du fichier des utilisateurs."""
        return {
            "derniere_mise_a_jour": datetime.now(timezone.utc).isoformat(),
            "total_utilisateurs": 0,
            "utilisateurs": {},
        }

    def _seed_from_download_history(self, target_data: Dict[str, Any], history_file: Path) -> None:
        """Importe les IDs d'utilisateurs connus depuis l'historique des téléchargements."""
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                hist_data = json.load(f)

            found_users: Dict[int, Dict[str, Any]] = {}
            for section in ("aboutis", "non_aboutis"):
                items_by_cat = hist_data.get(section, {})
                for cat_items in items_by_cat.values():
                    if isinstance(cat_items, list):
                        for item in cat_items:
                            uid = item.get("user_id")
                            if isinstance(uid, int) and uid > 0:
                                date_str = item.get("date")
                                if uid not in found_users:
                                    found_users[uid] = {
                                        "user_id": uid,
                                        "username": None,
                                        "first_name": None,
                                        "last_name": None,
                                        "language_code": None,
                                        "premiere_interaction": date_str or datetime.now(timezone.utc).isoformat(),
                                        "derniere_interaction": date_str or datetime.now(timezone.utc).isoformat(),
                                        "total_interactions": 1,
                                        "notifications_actives": True,
                                    }
                                else:
                                    found_users[uid]["total_interactions"] += 1
                                    if date_str and date_str > found_users[uid]["derniere_interaction"]:
                                        found_users[uid]["derniere_interaction"] = date_str

            for uid, udata in found_users.items():
                target_data["utilisateurs"][str(uid)] = udata

            target_data["total_utilisateurs"] = len(target_data["utilisateurs"])
            logger.info(f"Amorçage de {len(found_users)} utilisateur(s) depuis {history_file}")
        except Exception as exc:
            logger.debug(f"Impossible d'amorcer depuis {history_file}: {exc}")

    def _sync_read(self) -> Dict[str, Any]:
        """Lecture synchrone sécurisée du fichier JSON."""
        if not self.file_path.exists():
            return self._create_initial_structure()

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return self._create_initial_structure()
                data.setdefault("derniere_mise_a_jour", datetime.now(timezone.utc).isoformat())
                data.setdefault("total_utilisateurs", 0)
                data.setdefault("utilisateurs", {})
                return data
        except Exception as exc:
            logger.warning(f"Fichier utilisateurs corrompu ({exc}), réinitialisation.")
            return self._create_initial_structure()

    def _sync_write(self, data: Dict[str, Any]) -> None:
        """Écriture atomique pour éviter toute corruption de données."""
        data["total_utilisateurs"] = len(data.get("utilisateurs", {}))
        data["derniere_mise_a_jour"] = datetime.now(timezone.utc).isoformat()
        temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, self.file_path)

    async def record_user(
        self,
        user: Any,
        *,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Enregistre ou met à jour les informations d'un utilisateur."""
        user_id = getattr(user, "id", None) if not isinstance(user, int) else user
        if user_id is None or not isinstance(user_id, int) or user_id <= 0:
            return None

        # Ignore les bots
        if getattr(user, "is_bot", False) is True:
            return None

        extracted_username = getattr(user, "username", None) or username
        extracted_first_name = getattr(user, "first_name", None) or first_name
        extracted_last_name = getattr(user, "last_name", None) or last_name
        extracted_lang = getattr(user, "language_code", None) or language_code

        now_iso = datetime.now(timezone.utc).isoformat()
        str_uid = str(user_id)

        async with self._lock:
            data = await asyncio.to_thread(self._sync_read)
            users_map = data.setdefault("utilisateurs", {})

            if str_uid not in users_map:
                user_record = {
                    "user_id": user_id,
                    "username": extracted_username,
                    "first_name": extracted_first_name,
                    "last_name": extracted_last_name,
                    "language_code": extracted_lang,
                    "premiere_interaction": now_iso,
                    "derniere_interaction": now_iso,
                    "total_interactions": 1,
                    "notifications_actives": True,
                }
                users_map[str_uid] = user_record
                logger.info(f"Nouvel utilisateur enregistré : {user_id} (@{extracted_username or 'sans_pseudo'})")
            else:
                user_record = users_map[str_uid]
                user_record["total_interactions"] = user_record.get("total_interactions", 0) + 1
                user_record["derniere_interaction"] = now_iso
                if extracted_username is not None:
                    user_record["username"] = extracted_username
                if extracted_first_name is not None:
                    user_record["first_name"] = extracted_first_name
                if extracted_last_name is not None:
                    user_record["last_name"] = extracted_last_name
                if extracted_lang is not None:
                    user_record["language_code"] = extracted_lang

            await asyncio.to_thread(self._sync_write, data)
            return user_record

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """Renvoie tous les utilisateurs enregistrés."""
        async with self._lock:
            data = await asyncio.to_thread(self._sync_read)
            return list(data.get("utilisateurs", {}).values())

    async def get_active_user_ids(self) -> List[int]:
        """Renvoie la liste des identifiants des utilisateurs ayant les notifications actives."""
        async with self._lock:
            data = await asyncio.to_thread(self._sync_read)
            return [
                u["user_id"]
                for u in data.get("utilisateurs", {}).values()
                if u.get("notifications_actives", True) and isinstance(u.get("user_id"), int)
            ]

    async def mark_user_unreachable(self, user_id: int) -> None:
        """Désactive les notifications pour un utilisateur (bot bloqué ou chat supprimé)."""
        str_uid = str(user_id)
        async with self._lock:
            data = await asyncio.to_thread(self._sync_read)
            if str_uid in data.get("utilisateurs", {}):
                data["utilisateurs"][str_uid]["notifications_actives"] = False
                await asyncio.to_thread(self._sync_write, data)
                logger.info(f"Utilisateur {user_id} marqué comme injoignable (notifications désactivées).")

    async def notify_users_on_startup(
        self,
        bot: Any,
        message: str = DEFAULT_STARTUP_MESSAGE,
        rate_limit_delay: float = 0.05,
    ) -> Dict[str, int]:
        """Diffuse le message de notification de démarrage à tous les utilisateurs enregistrés."""
        user_ids = await self.get_active_user_ids()
        total = len(user_ids)
        sent = 0
        failed = 0

        if not total:
            logger.info("Aucun utilisateur enregistré pour la notification de démarrage.")
            return {"total": 0, "sent": 0, "failed": 0}

        logger.info(f"Diffusion de la notification de démarrage à {total} utilisateur(s)...")

        for uid in user_ids:
            try:
                await bot.send_message(chat_id=uid, text=message)
                sent += 1
            except Exception as exc:
                failed += 1
                exc_str = str(exc).lower()
                # Si l'utilisateur a bloqué le bot ou supprimé le chat
                if "forbidden" in exc_str or "bot was blocked" in exc_str or "chat not found" in exc_str:
                    logger.debug(f"Utilisateur {uid} a bloqué le bot : {exc}")
                    await self.mark_user_unreachable(uid)
                else:
                    logger.warning(f"Échec notification de démarrage pour l'utilisateur {uid}: {exc}")

            if rate_limit_delay > 0:
                await asyncio.sleep(rate_limit_delay)

        return {"total": total, "sent": sent, "failed": failed}

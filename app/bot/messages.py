"""User-facing messages and internationalization templates (French)."""

from __future__ import annotations

from typing import List
from app.services.media_inspector import MediaItem

START_MESSAGE = (
    "👋 **Bienvenue sur Media Downloader Bot !**\n\n"
    "Envoyez-moi simplement un lien (YouTube, Instagram, TikTok, Twitter/X, Reddit, etc.) "
    "et je récupérerai automatiquement les vidéos, images ou albums disponibles.\n\n"
    "📌 **Fonctionnalités :**\n"
    "• Prise en charge automatique des vidéos et images\n"
    "• Détection intelligente des carrousels / albums\n"
    "• Limite Telegram : jusqu'à 50 MiB par fichier\n"
    "• Isolation et suppression automatique des fichiers temporaires\n\n"
    "Tapez /help pour afficher les commandes et règles de sécurité."
)

HELP_MESSAGE = (
    "📖 **Guide d'utilisation**\n\n"
    "1. **Télécharger un média** : Envoyez directement un lien valide dans cette discussion.\n"
    "2. **Annuler** : Cliquez sur le bouton *Annuler* sous le message de progression.\n\n"
    "⚠️ **Règles et limites :**\n"
    "• **Taille max** : 50 MiB par fichier (limite stricte imposée par Telegram).\n"
    "• **Sécurité** : Les adresses locales, IP privées et schémas non-HTTP sont strictement rejetés.\n"
    "• **Contenus protégés** : Les comptes privés, abonnements payants ou contenus nécessitant une connexion ne sont pas supportés.\n"
    "• **Concurrence** : Un seul téléchargement actif à la fois par utilisateur.\n\n"
    "💡 *Conseil : Si une vidéo dépasse 50 MiB, vous serez prévenu et les médias compatibles restants vous seront envoyés.*"
)

STATUS_ANALYZING = "🔎 Analyse et validation de l'URL..."
STATUS_QUEUED = "⏳ En attente dans la file d'attente..."
STATUS_DOWNLOADING = "⬇️ Téléchargement du contenu en cours..."
STATUS_PROCESSING = "📦 Inspection et traitement des médias..."
STATUS_UPLOADING = "📤 Envoi des fichiers vers Telegram..."
STATUS_COMPLETED = "✅ Téléchargement et envoi terminés !"
STATUS_CANCELLED = "🛑 Téléchargement annulé."

ERR_NO_URL_FOUND = "❌ Aucune URL valide trouvée dans votre message. Veuillez envoyer un lien commençant par http:// ou https://."
ERR_INVALID_URL = "❌ URL invalide : {detail}"
ERR_SSRF_BLOCKED = "🛡️ Sécurité : Cette adresse pointe vers un réseau local ou une destination non autorisée."
ERR_ALREADY_RUNNING = "⏳ Vous avez déjà un téléchargement en cours. Veuillez patienter ou l'annuler avant d'en lancer un nouveau."
ERR_CONTENT_NOT_FOUND = "🔍 Le contenu demandé est introuvable ou a été supprimé de la plateforme."
ERR_AUTH_REQUIRED = "🔒 Ce contenu est privé ou nécessite une authentification sur la plateforme d'origine."
ERR_TIMEOUT = "⏱️ Le téléchargement a pris trop de temps et a été interrompu."
ERR_NO_MEDIA = "⚠️ Aucun média téléchargeable n'a été trouvé à cette adresse."
ERR_DISK_FULL = "⚠️ Espace disque temporairement insuffisant sur le serveur."
ERR_UNEXPECTED = "❌ Une erreur inattendue est survenue lors du traitement."


def format_oversized_warning(items: List[MediaItem], max_size_mb: int) -> str:
    """Format warning message for files exceeding the 50 MiB limit."""
    lines = [f"⚠️ **{len(items)} fichier(s) dépassent la limite de {max_size_mb} MiB et n'ont pas pu être envoyés :**"]
    for item in items[:5]:
        lines.append(f"• `{item.filename}` ({item.human_size})")
    if len(items) > 5:
        lines.append(f"• ... et {len(items) - 5} autre(s)")
    return "\n".join(lines)


def format_completion_summary(valid_count: int, oversized_count: int) -> str:
    """Format final completion summary."""
    parts = [f"✅ **Opération terminée !**\n• {valid_count} média(s) envoyé(s)."]
    if oversized_count > 0:
        parts.append(f"• {oversized_count} fichier(s) ignoré(s) car > 50 MiB.")
    return "\n".join(parts)

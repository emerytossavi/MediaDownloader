# Media Downloader Bot 🤖📦

Bot Telegram haute performance, asynchrone et sécurisé conçu en Python 3.12+ permettant de télécharger et recevoir automatiquement des vidéos, photos, carrousels et albums issus d'une multitude de plateformes Internet (YouTube, Instagram, TikTok, Twitter/X, Reddit, Vimeo, Pixiv, etc.) grâce au couplage dynamique de `yt-dlp` et `gallery-dl`.

---

## 📑 Sommaire

1. [Fonctionnalités principales](#-fonctionnalités-principales)
2. [Architecture logicielle](#-architecture-logicielle)
3. [Prérequis](#-prérequis)
4. [Installation](#-installation)
5. [Création du bot Telegram](#-création-du-bot-telegram)
6. [Configuration (.env)](#-configuration-env)
7. [Installation de FFmpeg](#-installation-de-ffmpeg)
8. [Lancement](#-lancement)
9. [Utilisation et Commandes](#-utilisation-et-commandes)
10. [Sécurité et SSRF](#-sécurité-et-ssrf)
11. [Limitations connues](#-limitations-connues)
12. [Tests automatisés](#-tests-automatisés)
13. [Déploiement Docker](#-déploiement-docker)
14. [Dépannage (FAQ)](#-dépannage-faq)

---

## 🚀 Fonctionnalités principales

- **Routage dynamique intelligent** : Analyse l'URL et sélectionne automatiquement le meilleur extracteur (`yt-dlp` pour les vidéos/streams ou `gallery-dl` pour les galeries, carrousels et images) sans liste codée en dur.
- **Mécanisme de repli (Fallback)** : Si le premier extracteur ne produit aucun média, le système tente automatiquement le second avant de statuer.
- **Limite stricte de 50 MiB** : Respect rigoureux du plafond de l'API standard Telegram. Les fichiers volumineux sont détectés en amont ou lors de l'inspection post-téléchargement et signalés clairement à l'utilisateur, tout en envoyant les médias compatibles restants.
- **Sécurité et protection anti-SSRF** :
  - Seuls les schémas `http` et `https` sont autorisés.
  - Résolution DNS préventive bloquant les adresses `localhost`, boucles locales (`127.0.0.0/8`, `::1`), réseaux privés (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), adresses de lien local (`169.254.0.0/16`), broadcast et multicast.
  - Aucune injection de commande : processus lancés exclusivement avec des arguments sous forme de listes via `asyncio.create_subprocess_exec()` (`shell=True` strictement proscrit).
  - Assainissement strict des noms de fichiers contre les traversées de répertoires (`../`).
- **Gestion de la concurrence et files d'attente** :
  - Sémaphore global pour limiter le nombre de téléchargements concurrents sur le serveur (`MAX_CONCURRENT_DOWNLOADS`).
  - Plafond par utilisateur (`MAX_CONCURRENT_DOWNLOADS_PER_USER`) pour éviter les monopoles de ressources.
- **Annulation en direct** : Bouton inline *🛑 Annuler* permettant d'interrompre immédiatement un téléchargement en cours et de tuer le sous-processus sans fuite mémoire.
- **Isolation et nettoyage total des fichiers temporaires** :
  - Chaque tâche s'exécute dans un sous-répertoire dédié `/tmp/telegram-downloader/job_<uuid>`.
  - Nettoyage garanti via bloc `finally` (succès, échec, annulation, timeout).
  - Nettoyage automatique des répertoires orphelins au démarrage du bot.
- **Protection anti-flood Telegram** : Limitation du rythme d'édition des messages de statut (minimum 2.5 secondes entre chaque mise à jour).

---

## 🏛 Architecture logicielle

```
MediaDownloaderBot/
├── app/
│   ├── __init__.py
│   ├── main.py                  # Point d'entrée, cycle de vie et initialisation
│   ├── config.py                # Chargement et validation stricte de l'environnement
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers.py          # Handlers /start, /help, messages et callbacks
│   │   ├── messages.py          # Gabarits textuels et internationalisation (FR)
│   │   ├── progress.py          # Notification d'état avec throttling anti-429
│   │   └── sender.py            # Envoi des médias (vidéos, photos, albums groupés)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── task_manager.py      # Machine à états, sémaphores et gestion des annulations
│   │   ├── router.py            # Routeur d'extracteurs yt-dlp / gallery-dl avec fallback
│   │   ├── ytdlp_downloader.py  # Pilotage asynchrone sécurisé de yt-dlp
│   │   ├── gallerydl_downloader.py # Pilotage asynchrone sécurisé de gallery-dl
│   │   ├── download_tracker.py  # Sauvegarde et suivi catégorisé (aboutis / échecs avec raison)
│   │   └── media_inspector.py   # Inspection, typage et filtrage de taille (< 50 MiB)
│   ├── security/
│   │   ├── __init__.py
│   │   └── url_validator.py     # Validation syntaxique, filtrage ports et anti-SSRF
│   └── utils/
│       ├── __init__.py
│       ├── filesystem.py        # Isolation, sanitization de chemins et nettoyage
│       └── logging.py           # Logs structurés avec masquage automatique des secrets
├── tests/                       # Suite de tests unitaires et d'intégration (pytest)
├── .env.example                 # Modèle de variables d'environnement
├── .gitignore
├── requirements.txt
├── pyproject.toml
├── Dockerfile                   # Image Debian multi-stage optimisée avec FFmpeg
└── docker-compose.yml
```

---

## 📦 Prérequis

- **Python 3.12 ou supérieur**
- **FFmpeg** (fortement recommandé pour permettre à `yt-dlp` de fusionner les flux vidéo et audio HD)
- Un compte Telegram et un token de bot fourni par `@BotFather`

---

## 🔧 Installation

### 1. Cloner ou naviguer dans le projet

```bash
cd /Users/emery.toss/Downloads/Workspace/apps/MediaDownloaderBot
```

### 2. Créer et activer l'environnement virtuel

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Installer les dépendances Python

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 🤖 Création du bot Telegram

1. Ouvrez Telegram et lancez une conversation avec [@BotFather](https://t.me/BotFather).
2. Envoyez la commande `/newbot`.
3. Donnez un nom d'affichage à votre bot (ex: `Mon Super Downloader`).
4. Choisissez un identifiant se terminant par `bot` (ex: `super_media_dl_bot`).
5. `@BotFather` vous délivre alors un jeton d'authentification API (Token) au format `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`.
6. Conservez précieusement ce jeton pour l'étape suivante.

---

## ⚙️ Configuration (.env)

Copiez le fichier d'exemple `.env.example` vers `.env` :

```bash
cp .env.example .env
```

Éditez le fichier `.env` avec vos paramètres :

```ini
# Jeton API Telegram obligatoire
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ

# Taille maximale par fichier envoyé à Telegram (50 MiB en octets)
MAX_FILE_SIZE=52428800

# Téléchargements simultanés globaux sur la machine
MAX_CONCURRENT_DOWNLOADS=2

# Téléchargements simultanés par utilisateur
MAX_CONCURRENT_DOWNLOADS_PER_USER=1

# Délai maximum par téléchargement en secondes
DOWNLOAD_TIMEOUT=300

# Répertoire de travail pour isoler les tâches
TEMP_DIR=/tmp/telegram-downloader

# Niveau de logs (DEBUG, INFO, WARNING, ERROR)
LOG_LEVEL=INFO
```

---

## 🎬 Installation de FFmpeg

FFmpeg est nécessaire pour assembler les flux vidéo et audio haute définition téléchargés séparément :

- **macOS** (avec Homebrew) :
  ```bash
  brew install ffmpeg
  ```
- **Ubuntu / Debian** :
  ```bash
  sudo apt-get update && sudo apt-get install -y ffmpeg
  ```
- **Arch Linux** :
  ```bash
  sudo pacman -S ffmpeg
  ```

---

## ▶️ Lancement

### Lancement standard

```bash
source .venv/bin/activate
python3 -m app.main
```

Le bot affichera dans la console :
```
2026-10-04 23:45:00 [INFO   ] [app.main] Initializing Media Downloader Bot...
2026-10-04 23:45:00 [INFO   ] [app.main] Configuration: max_file_size=52428800 bytes, max_concurrent=2
2026-10-04 23:45:00 [INFO   ] [app.main] Bot handlers registered. Starting polling...
```

---

## 📱 Utilisation et Commandes

| Commande | Action |
|---|---|
| `/start` | Présente le bot, ses règles et son mode d'emploi. |
| `/help` | Affiche l'aide détaillée, les limites de taille et les consignes de sécurité. |
| *Envoi d'un lien* | Démarre automatiquement le téléchargement du média. |
| *Bouton "Annuler"* | Interrompt immédiatement le traitement en cours et nettoie l'espace disque. |

---

## 🛡️ Sécurité et SSRF

1. **Validation stricte des URL** : Seuls les protocoles `http://` et `https://` sont acceptés. Les préfixes `file://`, `ftp://`, `gopher://`, `javascript:`, etc. sont systématiquement rejetés.
2. **Mitigation SSRF** :
   - Vérification des IP directes (privées, loopback, link-local, multicast).
   - Résolution DNS préventive asynchrone : si le nom de domaine résout vers une adresse IP locale ou privée (ex: `10.0.0.1`, `127.0.0.1`, `169.254.169.254`), la requête est stoppée avant tout contact réseau.
   - Ports restreints aux flux web conventionnels (`80`, `443`, `8080`, `8443`).
3. **Exécution des commandes** :
   - Strictement aucun usage de `shell=True` ou de chaînes de caractères brutes concaténées.
   - Arguments séparés transmis à `asyncio.create_subprocess_exec()`.
4. **Masquage des données sensibles** :
   - Un `RedactingFormatter` personnalisé intercepte les logs et masque les tokens Telegram, cookies et mots de passe.

---

## ⚠️ Limitations connues

1. **Plafond Telegram Bot API (50 MiB)** : L'API standard de Telegram limite l'envoi de fichiers par des bots à **50 MiB**. Tout fichier individuel excédant cette taille après extraction est ignoré, et l'utilisateur en est explicitement informé.
2. **Albums (Media Groups)** : Telegram accepte un maximum de **10 médias par album**. Le bot découpe automatiquement les carrousels volumineux en paquets de 10.
3. **Contenus privés ou sous authentification** : Conformément aux règles d'accès des plateformes, le bot ne tente pas de contourner les paywalls, restrictions géographiques ou comptes privés (Instagram privé, abonnements Patreon/OnlyFans, etc.).
4. **Restrictions de débit des plateformes** : Certaines plateformes (YouTube, Instagram) peuvent appliquer des captchas ou limiter temporairement l'adresse IP du serveur en cas d'appels trop rapprochés.

---

## 🧪 Tests automatisés

La suite de tests automatisée couvre la validation des URL, la sécurité anti-SSRF, la gestion des tâches, le routeur d'extracteurs, l'inspection des médias et le respect de la limite des 50 MiB :

```bash
source .venv/bin/activate
pytest -v
```

Exemple de sortie :
```
============================== 31 passed in 1.64s ==============================
```

---

## 🐳 Déploiement Docker

Un fichier `Dockerfile` léger sous Debian Bookworm avec FFmpeg préinstallé et un utilisateur non-root est inclus :

### 1. Construire et démarrer avec Docker Compose

```bash
# Vérifiez que votre fichier .env contient votre TELEGRAM_BOT_TOKEN
docker compose up -d --build
```

### 2. Consulter les logs

```bash
docker compose logs -f
```

### 3. Arrêter le conteneur

```bash
docker compose down
```

---

## ❓ Dépannage (FAQ)

- **Erreur : `TELEGRAM_BOT_TOKEN is mandatory`** :
  Vérifiez que le fichier `.env` existe bien à la racine du projet et que la variable `TELEGRAM_BOT_TOKEN` contient votre token valide.
- **Erreur : `ffmpeg not found`** :
  Assurez-vous que le binaire `ffmpeg` est installé et présent dans votre variable d'environnement `$PATH`, ou renseignez son chemin absolu via la variable `FFMPEG_BIN=/chemin/vers/ffmpeg` dans `.env`.
- **Pourquoi certaines vidéos Instagram/TikTok échouent ?** :
  Les algorithmes et signatures des plateformes évoluent régulièrement. Mettez régulièrement à jour `yt-dlp` et `gallery-dl` via `pip install --upgrade yt-dlp gallery-dl`.

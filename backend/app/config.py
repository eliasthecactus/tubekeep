"""Paths and environment configuration."""
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", "/downloads")).resolve()
STATIC_DIR = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))
BUNDLED_YTDLP = Path(os.environ.get("BUNDLED_YTDLP", "/opt/yt-dlp/yt-dlp"))

DB_PATH = CONFIG_DIR / "tubekeep.db"
BIN_DIR = CONFIG_DIR / "bin"
TMP_DIR = CONFIG_DIR / "tmp"
LOG_DIR = CONFIG_DIR / "logs"
JOB_LOG_DIR = LOG_DIR / "jobs"
SECRET_FILE = CONFIG_DIR / "secret.key"

ADMIN_USER = os.environ.get("ADMIN_USER", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
APP_SECRET = os.environ.get("APP_SECRET", "").strip()
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "dev"
# Set to 1 to disable the background yt-dlp/account/proxy loops (tests).
DISABLE_BACKGROUND = os.environ.get("TUBEKEEP_DISABLE_BACKGROUND", "") == "1"

AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".webm", ".aac", ".wav", ".mka"}


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, BIN_DIR, TMP_DIR, LOG_DIR, JOB_LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

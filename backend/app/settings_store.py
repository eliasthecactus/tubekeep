"""Key/value app settings with defaults."""
from typing import Any

from . import crypto, db

DEFAULTS: dict[str, Any] = {
    # download
    "audio_format": "mp3",          # mp3 | m4a | opus | flac | best
    "audio_quality": "0",           # 0 (best) .. 10, or e.g. 320K
    "filename_template": "%(channel)s - %(title)s",
    "embed_thumbnail": True,
    "square_cover": False,
    "artist_from_channel": True,    # artist = YT Music artist, falls back to channel
    "sponsorblock_music_offtopic": False,
    "extra_args": "",
    "concurrency": 2,
    "max_parallel_jobs": 3,         # playlist syncs / transfers running at the same time
    "max_attempts": 3,
    "sleep_interval": 0,            # seconds between downloads (min)
    "max_sleep_interval": 0,
    "sleep_requests": 0,
    "limit_rate": "",               # e.g. 2M
    "default_folder_root": "",      # empty = DOWNLOADS_DIR
    "default_schedule": "0 */6 * * *",
    # proxies
    "proxy_strategy": "none",       # none | round_robin | random
    "proxy_cooldown_minutes": 15,
    "proxy_max_consecutive_fails": 5,
    "proxy_check_hours": 6,
    # yt-dlp
    "ytdlp_channel": "stable",      # stable | nightly
    "ytdlp_auto_update": True,
    "ytdlp_check_hours": 6,
    # accounts
    "account_check_hours": 24,
    "default_account_id": None,
    # ai
    "openrouter_model": "anthropic/claude-haiku-5.5",
    "openrouter_key_enc": "",
    "ai_batch_size": 25,
    # notifications
    "notify_webhook_url": "",
    # transfers between music services
    "public_url": "",               # e.g. https://music.example.com (for OAuth redirects)
    "spotify_client_id": "",
    "apple_team_id": "",
    "apple_key_id": "",
    "apple_private_key_enc": "",
    "match_auto_threshold": 0.82,
    "match_review_threshold": 0.6,
}

SECRET_KEYS = {"openrouter_key_enc", "apple_private_key_enc"}


def get_all() -> dict[str, Any]:
    out = dict(DEFAULTS)
    for r in db.rows("SELECT key, value FROM settings"):
        if r["key"] in DEFAULTS:
            out[r["key"]] = db.jload(r["value"], DEFAULTS[r["key"]])
    return out


def get(key: str) -> Any:
    r = db.row("SELECT value FROM settings WHERE key = ?", [key])
    if r is None:
        return DEFAULTS.get(key)
    return db.jload(r["value"], DEFAULTS.get(key))


def set_many(values: dict[str, Any]) -> None:
    with db.tx() as c:
        for k, v in values.items():
            if k not in DEFAULTS:
                continue
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [k, db.jdump(v)],
            )


def public() -> dict[str, Any]:
    s = get_all()
    for k in SECRET_KEYS:
        s[k.removesuffix("_enc") + "_set"] = bool(s.pop(k))
    return s


def openrouter_key() -> str:
    enc = get("openrouter_key_enc")
    return crypto.decrypt(enc) if enc else ""

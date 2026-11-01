"""Folder markers: notice when a playlist folder disappears (drive not mounted) instead of downloading everything again.

Every playlist folder gets a small hidden file. When a folder Tubekeep already used has lost its marker, the folder is
"offline": syncs, downloads and file checks of that playlist stop until the marker is back or the user decides what
to do.
"""
import asyncio
import json
import os
from pathlib import Path

from . import db, notify
from .events import bus

MARKER = ".tubekeep"
loop: asyncio.AbstractEventLoop | None = None  # set by the job manager, checks may run in worker threads


def marker_path(folder: str) -> Path:
    return Path(folder) / MARKER


def write_marker(pl: dict, folder: str | None = None) -> None:
    folder = folder or pl["folder"]
    p = marker_path(folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "about": "Tubekeep uses this file to notice when this folder is missing (for example an unmounted drive). "
                 "Keep it.",
        "playlist_id": pl["id"], "created_at": db.now()}, indent=2) + "\n")
    _set(pl["id"], marker_seen=1, storage_offline_at=None)


def _set(pid: int, **fields) -> None:
    db.update("playlists", pid, fields)


def _bootstrap_ok(pl: dict) -> bool:
    """Playlists from before markers existed: trust the folder if any downloaded file is there (or none exist yet)."""
    done = db.rows("SELECT file_path FROM items WHERE playlist_id = ? AND status = 'done' AND file_path IS NOT NULL",
                   [pl["id"]])
    return not done or any(os.path.exists(r["file_path"]) for r in done)


def check(pl: dict) -> bool:
    """True when the folder can be used. Writes the marker for new folders, flags the playlist when it's gone."""
    if not pl.get("folder"):
        return True
    if marker_path(pl["folder"]).exists():
        if not pl.get("marker_seen") or pl.get("storage_offline_at"):
            was_offline = bool(pl.get("storage_offline_at"))
            _set(pl["id"], marker_seen=1, storage_offline_at=None)
            if was_offline:
                bus.publish("playlist", {"id": pl["id"]})
        return True
    if not pl.get("marker_seen") and _bootstrap_ok(pl):
        try:
            write_marker(pl)
        except OSError:
            pass  # read-only or missing folder: the normal folder checks report it
        return True
    if not pl.get("storage_offline_at"):
        _set(pl["id"], storage_offline_at=db.now())
        bus.publish("playlist", {"id": pl["id"]})
        _notify(pl)
    return False


def offline_message(pl: dict) -> str:
    return (f"The folder {pl['folder']} is missing its Tubekeep marker, so the drive is probably not mounted. "
            "Nothing was downloaded or marked as missing. Mount the drive and check again, or choose how to continue "
            "on the playlist page.")


def _notify(pl: dict) -> None:
    if loop is None or loop.is_closed():
        return
    msg = f"{pl['title'] or pl['url']}: {offline_message(pl)}"
    loop.call_soon_threadsafe(lambda: loop.create_task(notify.send("Tubekeep: folder missing", msg)))

"""Audio files in a playlist's folder that aren't on its YouTube playlist (any more).

Two kinds: files of tracks that left the playlist and were kept, and files Tubekeep never linked to a track
(dropped in by hand, left over from an older setup). Files a current track of any playlist uses are never listed,
so playlists that share or nest folders don't show each other's tracks.
"""
import os
import re
from pathlib import Path

from . import db, library, tags


def _in_use() -> set[str]:
    return {r["file_path"] for r in db.rows(
        "SELECT file_path FROM items WHERE file_path IS NOT NULL AND removed_at IS NULL")}


def files(pl: dict) -> list[dict]:
    folder = pl.get("folder")
    if not folder or not os.path.isdir(folder):
        return []
    used = _in_use()
    removed = {r["file_path"]: r for r in db.rows(
        "SELECT i.id, i.file_path, i.removed_at, i.meta, v.title, v.channel FROM items i "
        "JOIN videos v ON v.id = i.video_id WHERE i.playlist_id = ? AND i.removed_at IS NOT NULL "
        "AND i.file_path IS NOT NULL", [pl["id"]])}
    root = Path(folder)
    out = []
    for p in library.list_audio(folder):
        path = str(p)
        if path in used:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        it = removed.get(path)
        meta = db.jload(it["meta"], {}) if it else None
        if meta is None:
            try:
                meta = tags.read(p)
            except Exception:
                meta = {}
        vid = None
        if not it:
            m = re.search(r"\[([\w-]{11})\]", p.stem)
            vid = m.group(1) if m else (tags.youtube_id(meta) if meta else None)
        out.append({
            "path": path,
            "name": str(p.relative_to(root)),
            "size": st.st_size,
            "mtime": st.st_mtime,
            "title": (meta or {}).get("title") or (it and it["title"]) or p.stem,
            "artist": (meta or {}).get("artist") or (it and it["channel"]) or None,
            "item_id": it and it["id"],
            "removed_at": it and it["removed_at"],
            "video_id": vid,
        })
    out.sort(key=lambda f: f["name"].lower())
    return out


def delete(pl: dict, paths: list[str]) -> int:
    """Deletes files of this list only (never an arbitrary path), and forgets removed tracks that lose their file."""
    allowed = {f["path"]: f for f in files(pl)}
    deleted = 0
    for path in paths:
        f = allowed.get(path)
        if not f:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            continue
        deleted += 1
        if f["item_id"]:
            db.execute("DELETE FROM items WHERE id = ? AND removed_at IS NOT NULL", [f["item_id"]])
    return deleted

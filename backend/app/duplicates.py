"""Duplicates across the library.

Three kinds, found from what Tubekeep already knows (no network):
- video:  the same YouTube video in several playlists
- song:   the same song as different videos (official video vs. topic audio, re-uploads), by cleaned title, artist
          and length; live, remix, cover etc. versions are never the same song
- repeat: a video that was added more than once to one YouTube playlist (counted during sync)

Removed and ignored tracks don't count. Groups the user marked as "not a duplicate" are hidden by their member set,
so a group shows up again when a new copy joins it.
"""
import hashlib
import os
import re

from rapidfuzz import fuzz

from . import db, playlist_edit
from .jobs import item_event
from .matching import NOISE, clean_title, norm, versions

MAX_LENGTH_DIFF = 10  # seconds; official videos with long intros are a different file anyway
_cache: tuple[tuple, list[dict]] | None = None


def _rows() -> list[dict]:
    return db.rows(
        "SELECT i.id, i.playlist_id, i.video_id, i.status, i.file_path, i.file_size, i.first_seen_at, i.copies, "
        "i.meta, i.meta_override, i.position, v.title, v.channel, v.duration, v.thumbnail, v.yt_artist, v.yt_track, "
        "p.title AS playlist_title, p.url AS playlist_url FROM items i JOIN videos v ON v.id = i.video_id "
        "JOIN playlists p ON p.id = i.playlist_id WHERE i.removed_at IS NULL AND i.status != 'ignored'")


def _song(r: dict) -> dict:
    src = playlist_edit.source_of(r)
    title, artist = src["title"], ", ".join(src["artists"])
    if " - " in title:  # "Artist - Song (Official Video)": file tags of music videos often look like this
        left, _, right = title.partition(" - ")
        if not artist or fuzz.token_set_ratio(norm(left), norm(artist)) >= 80:
            title, artist = right.strip(), artist or left.strip()
    raw = f"{r.get('title') or ''} {src['title']}"
    parts = [a for a in re.split(r",|;|&| x | feat\. | ft\. ", artist) if _compact(a)]
    return {"title": title, "artist": artist, "key": clean_title(title) if src["known"] else "",
            "artists": [_compact(a) for a in parts], "artists_n": [norm(a) for a in parts], "raw_n": norm(raw),
            "versions": versions(raw)}


def _compact(s: str) -> str:
    """'Daft Punk', 'Daft Punk - Topic', 'DaftPunkVEVO' all become 'daftpunk'."""
    c = norm(s).replace(" ", "")
    for suffix in ("topic", "vevo", "official", "music"):
        if c.endswith(suffix) and len(c) > len(suffix) + 2:
            c = c[: -len(suffix)]
    return c


def _same_artist(a: dict, b: dict) -> bool:
    for x in a["artists"]:
        for y in b["artists"]:
            # exact, or a small typo in a longer name; "artist 12" vs "artist 123" is not the same artist
            if x == y or (min(len(x), len(y)) >= 6 and fuzz.ratio(x, y) >= 92
                          and not any(ch.isdigit() for ch in x + y)):
                return True
    # the artist is in the other video's title ("Artist - Song" uploads on someone else's channel), as whole words
    return any(_in(x, b["raw_n"]) for x in a["artists_n"]) or any(_in(y, a["raw_n"]) for y in b["artists_n"])


def _in(name: str, text: str) -> bool:
    return len(name) >= 3 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def same_song(a: dict, b: dict, da: float | None, db_: float | None) -> bool:
    if not a["key"] or a["key"] != b["key"] or len(a["key"]) < 2 or a["versions"] != b["versions"]:
        return False
    if not a["artists"] or not b["artists"] or not _same_artist(a, b):
        return False
    return not (da and db_ and abs(da - db_) > MAX_LENGTH_DIFF)


def _is_song_upload(r: dict) -> bool:
    return bool(r.get("yt_track")) or (r.get("channel") or "").endswith(" - Topic")


def _keeper(items: list[dict]) -> dict:
    """Downloaded first, then a clean song upload, then the oldest."""
    return max(items, key=lambda r: (r["status"] == "done", _is_song_upload(r), -(r["first_seen_at"] or 0)))


def _display(title: str) -> str:
    return re.sub(r"\s+", " ", NOISE.sub(" ", title or "")).strip(" -") or title


def _key(ids: list[int]) -> str:
    return hashlib.sha1(",".join(map(str, sorted(ids))).encode()).hexdigest()[:16]


def find() -> list[dict]:
    """Cached until any track or playlist changes (filtering in the UI asks again on every keystroke)."""
    global _cache
    # a fingerprint of everything the result depends on; not every write path bumps items.updated_at
    state = tuple(db.row(
        "SELECT COUNT(*), MAX(id), MAX(updated_at), SUM(removed_at IS NULL), SUM(status = 'ignored'), "
        "SUM(status = 'done'), SUM(copies), SUM(COALESCE(file_size, 0)), SUM(LENGTH(COALESCE(file_path, ''))), "
        "SUM(LENGTH(COALESCE(meta, '')) + LENGTH(COALESCE(meta_override, ''))) FROM items").values()) + \
        tuple(db.row("SELECT MAX(updated_at) FROM videos").values()) + \
        tuple(db.row("SELECT COUNT(*), GROUP_CONCAT(COALESCE(title, url)) FROM playlists").values())
    if _cache and _cache[0] == state:
        return _cache[1]
    groups = _find()
    _cache = (state, groups)
    return groups


def _find() -> list[dict]:
    rows = _rows()
    parent = {r["id"]: r["id"] for r in rows}

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(a: int, b: int) -> None:
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[rb] = ra

    by_video: dict[str, int] = {}
    songs: dict[int, dict] = {}
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        if r["video_id"] in by_video:
            join(by_video[r["video_id"]], r["id"])
        else:
            by_video[r["video_id"]] = r["id"]
        s = songs[r["id"]] = _song(r)
        if s["key"]:
            by_key.setdefault(s["key"], []).append(r)
    for bucket in by_key.values():
        if len(bucket) < 2 or len(bucket) > 200:  # a huge bucket is a generic title ("intro"), not one song
            continue
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if a["video_id"] != b["video_id"] and root(a["id"]) != root(b["id"]) \
                        and same_song(songs[a["id"]], songs[b["id"]], a["duration"], b["duration"]):
                    join(a["id"], b["id"])

    members: dict[int, list[dict]] = {}
    for r in rows:
        members.setdefault(root(r["id"]), []).append(r)
    groups = []
    for items in members.values():
        repeated = [r for r in items if (r["copies"] or 1) > 1]
        if len(items) < 2 and not repeated:
            continue
        keep = _keeper(items)
        kind = "song" if len({r["video_id"] for r in items}) > 1 else "video" if len(items) > 1 else "repeat"
        paths = {keep["file_path"]} if keep["file_path"] else set()
        wasted = 0
        for r in items:
            if r is not keep and r["file_path"] and r["file_path"] not in paths:
                paths.add(r["file_path"])
                wasted += r["file_size"] or 0
        named = next((r for r in items if _is_song_upload(r)), keep)  # topic uploads carry the clean title
        s = songs[named["id"]]
        shared = {}
        for r in items:
            if r["file_path"]:
                shared[r["file_path"]] = shared.get(r["file_path"], 0) + 1
        out_items = []
        for r in sorted(items, key=lambda r: (r is not keep, (r["playlist_title"] or "").lower())):
            si = songs[r["id"]]
            out_items.append({
                "id": r["id"], "playlist_id": r["playlist_id"],
                "playlist_title": r["playlist_title"] or r["playlist_url"], "video_id": r["video_id"],
                "title": si["title"] or r["title"] or r["video_id"], "artist": si["artist"] or r["channel"],
                "video_title": r["title"], "channel": r["channel"], "duration": r["duration"],
                "thumbnail": r["thumbnail"], "status": r["status"], "file_size": r["file_size"],
                "file_name": os.path.basename(r["file_path"]) if r["file_path"] else None,
                "shared_file": bool(r["file_path"]) and shared[r["file_path"]] > 1,
                "copies": r["copies"] or 1, "position": r["position"]})
        groups.append({"key": _key([r["id"] for r in items]) if len(items) > 1 else f"r{items[0]['id']}",
                       "kind": kind, "repeat": bool(repeated),
                       "title": _display(s["title"] or keep["title"] or keep["video_id"]),
                       "artist": s["artist"] or keep["channel"], "keep_id": keep["id"], "items": out_items,
                       "extra": len(items) - 1 + sum((r["copies"] or 1) - 1 for r in items),
                       "wasted_bytes": wasted})
    groups.sort(key=lambda g: (g["title"] or "").lower())
    return groups


def _is(g: dict, kind: str) -> bool:
    return g["repeat"] if kind == "repeat" else g["kind"] == kind


def listing(kind: str | None = None, playlist_id: int | None = None, q: str | None = None,
            dismissed: bool = False) -> dict:
    hidden = {r["key"] for r in db.rows("SELECT key FROM duplicate_dismissed")}
    all_groups = find()
    visible = [g for g in all_groups if (g["key"] in hidden) == dismissed]
    active = [g for g in all_groups if g["key"] not in hidden]
    summary = {"groups": len(active), "extra": sum(g["extra"] for g in active),
               "wasted_bytes": sum(g["wasted_bytes"] for g in active),
               "dismissed": len(all_groups) - len(active),
               "kinds": {k: sum(1 for g in active if _is(g, k)) for k in ("video", "song", "repeat")}}
    if kind:
        visible = [g for g in visible if _is(g, kind)]
    if playlist_id:
        visible = [g for g in visible if any(i["playlist_id"] == playlist_id for i in g["items"])]
    if q:
        n = norm(q)
        visible = [g for g in visible if any(n in norm(f"{i['title']} {i['artist']} {i['video_title']}")
                                             for i in g["items"])]
    pids = {i["playlist_id"] for g in visible for i in g["items"]}
    busy = playlist_edit.syncing()
    playlists = {}
    if pids:
        marks = ",".join("?" for _ in pids)
        for p in db.rows(f"SELECT id, title, url, writable, access_reason, on_removed FROM playlists "
                         f"WHERE id IN ({marks})", list(pids)):
            playlists[p["id"]] = {"title": p["title"] or p["url"],
                                  "writable": None if p["writable"] is None else bool(p["writable"]),
                                  "reason": p["access_reason"], "files_follow": p["on_removed"] == "delete",
                                  "syncing": p["id"] in busy}
    return {"summary": summary, "groups": visible, "playlists": playlists}


def dismiss(key: str, undo: bool = False) -> None:
    if undo:
        db.execute("DELETE FROM duplicate_dismissed WHERE key = ?", [key])
    else:
        db.execute("INSERT OR IGNORE INTO duplicate_dismissed (key, created_at) VALUES (?, ?)", [key, db.now()])


async def apply(item_ids: list[int], action: str, delete_files: bool = False,
                extra_copy_ids: list[int] | None = None) -> dict:
    """What happens to the copies the user doesn't keep.
    youtube: remove them from their YouTube playlists (and extra_copy_ids: only their repeated entries)
    file:    delete their files, Tubekeep ignores them so they don't download again; YouTube unchanged
    ignore:  Tubekeep ignores them, files and YouTube unchanged"""
    out = {"removed": 0, "still_listed": 0, "deleted_files": 0, "freed_bytes": 0, "ignored": 0, "playlists": 0,
           "skipped": []}
    if action == "youtube":
        for ids, extra in ((item_ids, False), (extra_copy_ids or [], True)):
            if not ids:
                continue
            r = await playlist_edit.remove_many(ids, delete_files, extra_copies=extra)
            for k in ("removed", "still_listed", "deleted_files", "playlists"):
                out[k] += r[k]
            out["skipped"] += r["skipped"]
        return out
    if action not in ("file", "ignore"):
        raise ValueError("Unknown action")
    if not item_ids:
        return out
    marks = ",".join("?" for _ in item_ids)
    for r in db.rows(f"SELECT id, file_path, file_size FROM items WHERE id IN ({marks})", item_ids):
        fields = {"status": "ignored", "updated_at": db.now()}
        if action == "file" and r["file_path"]:
            if playlist_edit.delete_item_file(r):
                out["deleted_files"] += 1
                out["freed_bytes"] += r["file_size"] or 0
            elif not db.scalar("SELECT COUNT(*) FROM items WHERE file_path = ? AND id != ? AND removed_at IS NULL",
                               [r["file_path"], r["id"]]):
                out["skipped"].append({"item_id": r["id"], "tracks": 1, "title": os.path.basename(r["file_path"]),
                                       "reason": "The file could not be deleted"})
                continue
            # a file another copy still uses stays, only this copy lets go of it
            fields.update(file_path=None, file_size=None)
        db.update("items", r["id"], fields)
        out["ignored"] += 1
        item_event(r["id"])
    return out

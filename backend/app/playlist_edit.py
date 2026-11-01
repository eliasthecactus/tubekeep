"""Changes to the real YouTube playlist: remove tracks, find and add alternatives for dead videos.

Everything goes through YouTube Music (ytmusicapi) with the cookies of the playlist's account. Only playlists
that account can edit (its own, or collaborative ones) are writable.
"""
import os
import re
import time

from . import accounts, db
from .jobs import PLACEHOLDER_TITLES, item_event, manager
from .matching import score
from .providers.base import ProviderError
from .providers.youtube import YouTubeMusic

ACCESS_TTL = 6 * 3600
ENTRIES_TTL = 300
_entries: dict[str, tuple[float, list[dict]]] = {}


class EditError(Exception):
    pass


def playlist(pid: int) -> dict:
    pl = db.row("SELECT * FROM playlists WHERE id = ?", [pid])
    if not pl:
        raise EditError("Playlist not found")
    return pl


def yt_playlist_id(pl: dict) -> str | None:
    if pl.get("yt_id"):
        return pl["yt_id"]
    m = re.search(r"list=([\w-]+)", pl["url"] or "")
    return m.group(1) if m else None


def client(pl: dict) -> YouTubeMusic:
    if not pl["account_id"] or not accounts.get(pl["account_id"]):
        raise EditError("Link a YouTube account to this playlist to change it")
    return YouTubeMusic({"account_id": pl["account_id"]}, {})


async def _entries_of(pl: dict, yt: YouTubeMusic, fresh: bool = False) -> list[dict]:
    """All entries of the YouTube playlist with their setVideoId (needed to remove or move them)."""
    pid = yt_playlist_id(pl)
    hit = _entries.get(pid)
    if hit and not fresh and time.time() - hit[0] < ENTRIES_TTL:
        return hit[1]
    data = await yt._call(lambda c: c.get_playlist(pid, limit=None))
    tracks = data.get("tracks") or []
    _entries[pid] = (time.time(), tracks)
    return tracks


# ------------------------------------------------------------------ access

def _special(pid: str | None) -> str | None:
    if not pid:
        return "This isn't a playlist URL"
    if pid in ("LL", "LM", "WL") or pid.startswith(("RD", "UU", "OL")):
        return "Liked videos, Watch later, mixes, uploads and albums can't be changed from Tubekeep"
    return None


async def access(pid: int, refresh: bool = False) -> dict:
    pl = playlist(pid)
    if not refresh and pl.get("access_checked_at") and time.time() - pl["access_checked_at"] < ACCESS_TTL:
        return {"writable": bool(pl["writable"]), "reason": pl["access_reason"]}
    writable, reason = False, _special(yt_playlist_id(pl))
    if not reason and not pl["account_id"]:
        reason = "Link a YouTube account to this playlist to change it"
    if not reason:
        try:
            yt = client(pl)
            ytid = yt_playlist_id(pl)
            data = await yt._call(lambda c: c.get_playlist(ytid, limit=100))
            writable = bool(data.get("owned")) or any(t.get("setVideoId") for t in data.get("tracks") or [])
            reason = None if writable else "The linked account can't edit this playlist"
        except (ProviderError, EditError) as e:
            reason = str(e)
    db.update("playlists", pid, {"writable": int(writable), "access_reason": reason, "access_checked_at": time.time()})
    return {"writable": writable, "reason": reason}


async def _require_writable(pl: dict) -> YouTubeMusic:
    a = await access(pl["id"])
    if not a["writable"]:
        raise EditError(a["reason"] or "This playlist can't be changed")
    return client(pl)


# ------------------------------------------------------------------ remove

def delete_item_file(item: dict) -> bool:
    path = item.get("file_path")
    if not path:
        return False
    # never delete a file another playlist still uses
    if db.scalar("SELECT COUNT(*) FROM items WHERE file_path = ? AND id != ? AND removed_at IS NULL",
                 [path, item["id"]]):
        return False
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _mark_removed(item: dict, delete_file: bool) -> bool:
    now = db.now()
    fields = {"removed_at": now, "updated_at": now}
    deleted = False
    if delete_file and item.get("file_path"):
        deleted = delete_item_file(item)
        if deleted:
            fields.update(status="deleted", file_path=None, file_size=None)
    db.update("items", item["id"], fields)
    item_event(item["id"])
    return deleted


async def remove(pid: int, item_ids: list[int], delete_files: bool) -> dict:
    pl = playlist(pid)
    yt = await _require_writable(pl)
    marks = ",".join("?" for _ in item_ids)
    items = db.rows(f"SELECT * FROM items WHERE playlist_id = ? AND id IN ({marks})", [pid, *item_ids])
    if not items:
        raise EditError("No tracks selected")
    entries = await _entries_of(pl, yt, fresh=True)
    wanted = {it["video_id"] for it in items}
    videos = [{"videoId": e["videoId"], "setVideoId": e["setVideoId"]} for e in entries
              if e.get("videoId") in wanted and e.get("setVideoId")]
    if videos:
        ytid = yt_playlist_id(pl)
        for i in range(0, len(videos), 100):
            res = await yt._call(lambda c, b=videos[i:i + 100]: c.remove_playlist_items(ytid, b))
            if isinstance(res, dict) or (isinstance(res, str) and "SUCCEEDED" not in res):
                raise EditError(f"YouTube did not remove the tracks: {str(res)[:200]}")
    _entries.pop(yt_playlist_id(pl), None)
    # a playlist that deletes files of removed tracks would do it on the next sync anyway
    delete_files = delete_files or pl["on_removed"] == "delete"
    deleted = sum(_mark_removed(it, delete_files) for it in items)
    return {"removed": len(videos), "already_gone": len(items) - len({v["videoId"] for v in videos}),
            "deleted_files": deleted}


async def remove_extra_copies(pid: int, item_ids: list[int]) -> dict:
    """A video added several times to one YouTube playlist: keep its first entry, remove the others."""
    pl = playlist(pid)
    yt = await _require_writable(pl)
    marks = ",".join("?" for _ in item_ids)
    items = db.rows(f"SELECT * FROM items WHERE playlist_id = ? AND id IN ({marks})", [pid, *item_ids])
    entries = await _entries_of(pl, yt, fresh=True)
    extra, firsts = [], set()
    wanted = {it["video_id"] for it in items}
    for e in entries:
        vid = e.get("videoId")
        if vid in wanted and e.get("setVideoId"):
            if vid in firsts:
                extra.append({"videoId": vid, "setVideoId": e["setVideoId"]})
            firsts.add(vid)
    if extra:
        ytid = yt_playlist_id(pl)
        for i in range(0, len(extra), 100):
            res = await yt._call(lambda c, b=extra[i:i + 100]: c.remove_playlist_items(ytid, b))
            if isinstance(res, dict) or (isinstance(res, str) and "SUCCEEDED" not in res):
                raise EditError(f"YouTube did not remove the tracks: {str(res)[:200]}")
        _entries.pop(ytid, None)
    for it in items:
        db.update("items", it["id"], {"copies": 1, "updated_at": db.now()})
    return {"removed": len(extra)}


# ------------------------------------------------------------------ several playlists at once (library, duplicates)

def syncing() -> set[int]:
    return {j["playlist_id"] for j in manager.live() if j["type"] == "sync"}


async def groups(item_ids: list[int]) -> list[dict]:
    """The tracks grouped per playlist, with whether that playlist can be changed on YouTube."""
    if not item_ids:
        return []
    marks = ",".join("?" for _ in item_ids)
    rows = db.rows(f"SELECT i.id, i.playlist_id, p.title, p.url, p.on_removed FROM items i "
                   f"JOIN playlists p ON p.id = i.playlist_id WHERE i.id IN ({marks}) ORDER BY i.playlist_id",
                   item_ids)
    out: dict[int, dict] = {}
    for r in rows:
        g = out.setdefault(r["playlist_id"], {"playlist_id": r["playlist_id"], "title": r["title"] or r["url"],
                                              "files_follow": r["on_removed"] == "delete", "item_ids": []})
        g["item_ids"].append(r["id"])
    busy = syncing()
    for g in out.values():
        try:
            a = await access(g["playlist_id"])
            g["writable"], g["reason"] = a["writable"], a["reason"]
        except (EditError, ProviderError) as e:
            g["writable"], g["reason"] = False, str(e)
        g["syncing"] = g["playlist_id"] in busy
    return sorted(out.values(), key=lambda g: (not g["writable"], (g["title"] or "").lower()))


async def remove_many(item_ids: list[int], delete_files: bool, extra_copies: bool = False) -> dict:
    """Removes tracks of several playlists from YouTube (or, with extra_copies, only their repeated entries).
    Read-only or syncing playlists are skipped and reported, not failed."""
    out = {"removed": 0, "already_gone": 0, "deleted_files": 0, "playlists": 0, "skipped": []}
    for g in await groups(item_ids):
        reason = None
        if not g["writable"]:
            reason = g["reason"] or "Read-only"
        elif g["playlist_id"] in syncing():
            reason = "Wait until the sync of this playlist is finished"
        else:
            try:
                if extra_copies:
                    r = await remove_extra_copies(g["playlist_id"], g["item_ids"])
                else:
                    r = await remove(g["playlist_id"], g["item_ids"], delete_files)
            except (EditError, ProviderError) as e:
                reason = str(e)
            else:
                out["playlists"] += 1
                for k in ("removed", "already_gone", "deleted_files"):
                    out[k] += r.get(k, 0)
        if reason:
            out["skipped"].append({"playlist_id": g["playlist_id"], "title": g["title"],
                                   "tracks": len(g["item_ids"]), "reason": reason})
    return out


# ------------------------------------------------------------------ alternatives

def _clean_channel(c: str | None) -> str:
    return re.sub(r"\s*-\s*Topic$", "", c or "").strip()


def source_of(item: dict, entry: dict | None = None) -> dict:
    """What the track was, from the best source we have: edited tags, file tags, YouTube metadata, YT Music."""
    meta = db.jload(item.get("meta"), {}) or {}
    over = db.jload(item.get("meta_override"), {}) or {}
    vtitle = None if item.get("title") in PLACEHOLDER_TITLES else item.get("title")
    title = over.get("title") or meta.get("title") or item.get("yt_track") or vtitle
    artist = over.get("artist") or meta.get("artist") or item.get("yt_artist") or _clean_channel(item.get("channel"))
    if entry and entry.get("title") and entry["title"] not in PLACEHOLDER_TITLES:
        title = title or entry["title"]
        artist = artist or ", ".join(a["name"] for a in entry.get("artists") or [] if a.get("name"))
    artists = [a.strip() for a in re.split(r",|;|&| feat\. | ft\. ", artist or "") if a.strip()]
    if title and not artists and " - " in title:  # "Artist - Song" video titles
        a, _, title = title.partition(" - ")
        artists = [a.strip()]
    return {"id": item["video_id"], "title": title or "", "artists": artists, "album": meta.get("album") or None,
            "duration": item.get("duration"), "isrc": None, "known": bool(title)}


def _candidate(t: dict, src: dict, in_playlist: set[str]) -> dict:
    return {"video_id": t["id"], "title": t["title"], "artists": t["artists"], "album": t["album"],
            "duration": t["duration"], "thumbnail": t["extra"].get("thumbnail"),
            "kind": t["extra"].get("resultType") or "song", "url": f"https://music.youtube.com/watch?v={t['id']}",
            "score": score(src, t) if src["title"] else None, "in_playlist": t["id"] in in_playlist}


async def alternatives(pid: int, item_id: int, query: str | None = None) -> dict:
    pl = playlist(pid)
    yt = await _require_writable(pl)
    item = db.row("SELECT i.*, v.title, v.channel, v.duration, v.yt_artist, v.yt_track, v.thumbnail "
                  "FROM items i JOIN videos v ON v.id = i.video_id WHERE i.id = ? AND i.playlist_id = ?",
                  [item_id, pid])
    if not item:
        raise EditError("Track not found")
    src = source_of(item)
    if not src["known"]:
        entries = await _entries_of(pl, yt)
        src = source_of(item, next((e for e in entries if e.get("videoId") == item["video_id"]), None))
    q = (query or "").strip() or f"{' '.join(src['artists'][:2])} {src['title']}".strip()
    if not q:
        return {"source": src, "query": "", "candidates": []}
    found = await yt.search(q, limit=10)
    in_playlist = {r["video_id"] for r in db.rows("SELECT video_id FROM items WHERE playlist_id = ? "
                                                    "AND removed_at IS NULL", [pid])}
    seen, cands = {item["video_id"]}, []
    for t in found:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        cands.append(_candidate(t, src, in_playlist))
    if src["title"]:
        cands.sort(key=lambda c: c["score"] or 0, reverse=True)
    return {"source": src, "query": q, "candidates": cands[:8]}


async def apply_alternative(pid: int, item_id: int, cand: dict, replace: bool, delete_file: bool) -> dict:
    """Adds the chosen video to the YouTube playlist (at the old track's spot when replacing) and to Tubekeep,
    where it is an ordinary track from now on."""
    pl = playlist(pid)
    yt = await _require_writable(pl)
    old = db.row("SELECT * FROM items WHERE id = ? AND playlist_id = ?", [item_id, pid])
    if not old:
        raise EditError("Track not found")
    vid = cand.get("video_id") or ""
    if not re.fullmatch(r"[\w-]{11}", vid):
        raise EditError("Not a YouTube video id")
    ytid = yt_playlist_id(pl)
    entries = await _entries_of(pl, yt, fresh=True)
    old_entry = next((e for e in entries if e.get("videoId") == old["video_id"] and e.get("setVideoId")), None)
    already = any(e.get("videoId") == vid for e in entries)
    new_set = None
    if not already:
        res = await yt._call(lambda c: c.add_playlist_items(ytid, [vid], duplicates=True))
        if not isinstance(res, dict) or "SUCCEEDED" not in str(res.get("status")):
            raise EditError(f"YouTube did not add the track: {str(res)[:200]}")
        new_set = next((r.get("setVideoId") for r in res.get("playlistEditResults") or [] if r), None)
    if replace:
        if new_set and old_entry:
            await yt._call(lambda c: c.edit_playlist(ytid, moveItem=(new_set, old_entry["setVideoId"])))
        if old_entry:
            await yt._call(lambda c: c.remove_playlist_items(
                ytid, [{"videoId": old_entry["videoId"], "setVideoId": old_entry["setVideoId"]}]))
    _entries.pop(ytid, None)

    now = db.now()
    title = cand.get("title") or None
    artists = ", ".join(a for a in cand.get("artists") or [] if a) or None
    song = cand.get("kind") == "song"  # songs carry clean artist/title, videos only a channel
    db.execute("INSERT INTO videos (id, title, channel, duration, thumbnail, yt_artist, yt_track, updated_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
               "title = COALESCE(videos.title, excluded.title), duration = COALESCE(videos.duration, excluded.duration), "
               "thumbnail = COALESCE(videos.thumbnail, excluded.thumbnail)",
               [vid, title, artists, cand.get("duration"), cand.get("thumbnail"),
                artists if song else None, title if song else None, now])
    position = old["position"] if replace else (db.scalar("SELECT MAX(position) FROM items WHERE playlist_id = ?",
                                                          [pid]) or 0) + 1
    existing = db.row("SELECT * FROM items WHERE playlist_id = ? AND video_id = ?", [pid, vid])
    if existing:
        status = "pending" if existing["status"] in ("deleted", "unavailable", "failed") else existing["status"]
        db.update("items", existing["id"], {"removed_at": None, "position": position, "status": status,
                                            "updated_at": now})
        new_id = existing["id"]
    else:
        new_id = db.insert("items", {"playlist_id": pid, "video_id": vid, "position": position, "status": "pending",
                                     "first_seen_at": now, "updated_at": now})
    deleted = _mark_removed(old, delete_file or pl["on_removed"] == "delete") if replace else False
    item_event(new_id)
    if db.scalar("SELECT status FROM items WHERE id = ?", [new_id]) == "pending":
        manager.enqueue("download", pid, {"item_ids": [new_id]})
    return {"item_id": new_id, "replaced": replace, "deleted_file": deleted}

import os
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, db, library, playlist_edit, scheduler, settings_store, storage
from ..jobs import manager
from ..providers.base import ProviderError

router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])

STATUSES = ["pending", "queued", "downloading", "done", "failed", "unavailable", "missing", "ignored", "deleted"]
OPTION_KEYS = {"audio_format", "audio_quality", "filename_template", "embed_thumbnail", "square_cover",
               "artist_from_channel", "sponsorblock_music_offtopic", "extra_args"}


class PlaylistIn(BaseModel):
    url: str | None = None
    urls: list[str] | None = None
    folder: str | None = None
    account_id: int | None = None
    proxy_mode: str = "default"
    proxy_id: int | None = None
    schedule: str | None = None
    enabled: bool = True
    on_removed: str = "keep"
    redownload_missing: bool = True
    options: dict[str, Any] = {}
    sync_now: bool = True


class PlaylistPatch(BaseModel):
    url: str | None = None
    title: str | None = None
    folder: str | None = None
    account_id: int | None = None
    proxy_mode: str | None = None
    proxy_id: int | None = None
    schedule: str | None = None
    enabled: bool | None = None
    on_removed: str | None = None
    redownload_missing: bool | None = None
    options: dict[str, Any] | None = None
    move_files: bool = False


def _clean_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url):
        if re.fullmatch(r"(PL|LL|WL|UU|OL|FL|RD)[\w-]*", url):
            return f"https://www.youtube.com/playlist?list={url}"
        raise HTTPException(400, f"Not a URL: {url}")
    return url


def _counts(playlist_id: int | None = None) -> dict:
    where, params = ("WHERE playlist_id = ? AND removed_at IS NULL", [playlist_id]) if playlist_id \
        else ("WHERE removed_at IS NULL", [])
    out = {s: 0 for s in STATUSES}
    for r in db.rows(f"SELECT status, COUNT(*) n FROM items {where} GROUP BY status", params):
        out[r["status"]] = r["n"]
    out["removed"] = db.scalar("SELECT COUNT(*) FROM items WHERE removed_at IS NOT NULL" +
                               (" AND playlist_id = ?" if playlist_id else ""), [playlist_id] if playlist_id else [])
    return out


def _public(p: dict) -> dict:
    p = dict(p)
    p["options"] = db.jload(p["options"], {}) or {}
    p["enabled"] = bool(p["enabled"])
    p["redownload_missing"] = bool(p["redownload_missing"])
    return p


@router.get("/playlists")
def list_playlists():
    pls = db.rows("SELECT p.*, a.label AS account_label, a.status AS account_status FROM playlists p "
                  "LEFT JOIN accounts a ON a.id = p.account_id ORDER BY COALESCE(p.title, p.url) COLLATE NOCASE")
    counts: dict[int, dict] = {}
    for r in db.rows("SELECT playlist_id, status, COUNT(*) n, SUM(COALESCE(file_size, 0)) b FROM items "
                     "WHERE removed_at IS NULL GROUP BY playlist_id, status"):
        c = counts.setdefault(r["playlist_id"], {s: 0 for s in STATUSES} | {"bytes": 0})
        c[r["status"]] = r["n"]
        c["bytes"] += r["b"] or 0
    running = {j["playlist_id"]: j for j in manager.live()}
    out = []
    for p in pls:
        d = _public(p)
        d["counts"] = counts.get(p["id"], {s: 0 for s in STATUSES} | {"bytes": 0})
        d["job"] = running.get(p["id"])
        out.append(d)
    return out


@router.post("/playlists")
def add_playlists(body: PlaylistIn):
    urls = [u for u in ([body.url] if body.url else []) + (body.urls or []) if u and u.strip()]
    if not urls:
        raise HTTPException(400, "Add at least one URL")
    if body.folder and len(urls) > 1:
        raise HTTPException(400, "A custom folder only works when adding one playlist")
    schedule = scheduler.normalize(body.schedule if body.schedule is not None
                                   else settings_store.get("default_schedule") or "")
    folder = library.check_folder(body.folder) if body.folder else None
    created, skipped = [], []
    for u in urls:
        url = _clean_url(u)
        if db.row("SELECT id FROM playlists WHERE url = ?", [url]):
            skipped.append(url)
            continue
        pid = db.insert("playlists", {
            "url": url, "folder": folder, "account_id": body.account_id, "proxy_mode": body.proxy_mode,
            "proxy_id": body.proxy_id, "schedule": schedule, "enabled": int(body.enabled),
            "options": db.jdump({k: v for k, v in body.options.items() if k in OPTION_KEYS}),
            "on_removed": body.on_removed, "redownload_missing": int(body.redownload_missing),
            "next_run_at": scheduler.next_run(schedule), "created_at": db.now()})
        created.append(pid)
        if body.sync_now:
            manager.enqueue("sync", pid)
    return {"created": created, "skipped": skipped}


@router.post("/playlists/import-text")
def import_text(body: dict):
    """Paste the old playlists.txt (one URL per line)."""
    lines = [ln.strip() for ln in (body.get("text") or "").splitlines()]
    urls = [ln for ln in lines if ln and not ln.startswith("#")]
    return add_playlists(PlaylistIn(urls=urls, sync_now=bool(body.get("sync_now", True)),
                                    account_id=body.get("account_id")))


@router.get("/playlists/{pid}")
def get_playlist(pid: int):
    p = db.row("SELECT p.*, a.label AS account_label, a.status AS account_status FROM playlists p "
               "LEFT JOIN accounts a ON a.id = p.account_id WHERE p.id = ?", [pid])
    if not p:
        raise HTTPException(404, "Playlist not found")
    d = _public(p)
    d["counts"] = _counts(pid)
    d["bytes"] = db.scalar("SELECT SUM(COALESCE(file_size,0)) FROM items WHERE playlist_id = ? AND status='done'",
                           [pid]) or 0
    d["job"] = next((j for j in manager.live() if j["playlist_id"] == pid), None)
    return d


@router.patch("/playlists/{pid}")
def patch_playlist(pid: int, body: PlaylistPatch):
    p = db.row("SELECT * FROM playlists WHERE id = ?", [pid])
    if not p:
        raise HTTPException(404, "Playlist not found")
    if any(j["playlist_id"] == pid for j in manager.live()) and (body.folder or body.move_files):
        raise HTTPException(409, "Wait until the running job of this playlist is finished")
    data = body.model_dump(exclude_unset=True)
    move = data.pop("move_files", False)
    fields: dict = {}
    for k in ("url", "title", "account_id", "proxy_mode", "proxy_id", "on_removed"):
        if k in data:
            fields[k] = _clean_url(data[k]) if k == "url" else data[k]
    for k in ("enabled", "redownload_missing"):
        if k in data:
            fields[k] = int(bool(data[k]))
    if "options" in data:
        fields["options"] = db.jdump({k: v for k, v in (data["options"] or {}).items() if k in OPTION_KEYS})
    if "schedule" in data:
        fields["schedule"] = scheduler.normalize(data["schedule"] or "")
        fields["next_run_at"] = scheduler.next_run(fields["schedule"])
    if "folder" in data and data["folder"] and data["folder"] != p["folder"]:
        new = library.check_folder(data["folder"])
        fields["folder"] = new
        if move and p["folder"]:
            _move_files(pid, p["folder"], new)
    db.update("playlists", pid, fields)
    if fields.get("folder"):
        storage.write_marker(db.row("SELECT * FROM playlists WHERE id = ?", [pid]))
    return get_playlist(pid)


def _move_files(pid: int, old: str, new: str) -> None:
    for it in db.rows("SELECT id, file_path FROM items WHERE playlist_id = ? AND file_path IS NOT NULL", [pid]):
        src = it["file_path"]
        if not src or not os.path.exists(src):
            continue
        try:
            rel = os.path.relpath(src, old)
            if rel.startswith(".."):
                rel = Path(src).name
            dst = Path(new) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                continue
            shutil.move(src, dst)
            db.update("items", it["id"], {"file_path": str(dst)})
        except OSError:
            continue


@router.delete("/playlists/{pid}")
def delete_playlist(pid: int, delete_files: bool = False):
    p = db.row("SELECT * FROM playlists WHERE id = ?", [pid])
    if not p:
        raise HTTPException(404, "Playlist not found")
    for j in manager.live():
        if j["playlist_id"] == pid:
            manager.cancel(j["id"])
    deleted = 0
    if delete_files:
        for it in db.rows("SELECT file_path FROM items WHERE playlist_id = ? AND file_path IS NOT NULL", [pid]):
            # never delete a file another playlist also references
            if db.scalar("SELECT COUNT(*) FROM items WHERE file_path = ? AND playlist_id != ?", [it["file_path"], pid]):
                continue
            try:
                os.remove(it["file_path"])
                deleted += 1
            except OSError:
                pass
        if p["folder"] and os.path.isdir(p["folder"]):
            try:
                storage.marker_path(p["folder"]).unlink(missing_ok=True)
                os.rmdir(p["folder"])  # only if empty
            except OSError:
                pass
    db.execute("DELETE FROM playlists WHERE id = ?", [pid])
    db.execute("DELETE FROM videos WHERE id NOT IN (SELECT video_id FROM items)")
    return {"ok": True, "deleted_files": deleted}


@router.post("/playlists/{pid}/sync")
def sync_playlist(pid: int):
    if not db.row("SELECT id FROM playlists WHERE id = ?", [pid]):
        raise HTTPException(404, "Playlist not found")
    return manager.enqueue("sync", pid)


@router.post("/playlists/sync-all")
def sync_all():
    jobs = [manager.enqueue("sync", p["id"]) for p in db.rows("SELECT id FROM playlists WHERE enabled = 1")]
    return {"jobs": [j["id"] for j in jobs]}


@router.post("/playlists/{pid}/verify")
def verify_playlist(pid: int):
    return manager.enqueue("verify", pid)


@router.post("/verify-all")
def verify_all():
    return manager.enqueue("verify", None, title="Verify files of all playlists")


# ------------------------------------------------------------------ items

ITEM_SELECT = (
    "SELECT i.id, i.playlist_id, i.video_id, i.position, i.status, i.file_path, i.file_size, i.error, "
    "i.error_kind, i.attempts, i.first_seen_at, i.removed_at, i.downloaded_at, i.meta, i.meta_override, "
    "v.title, v.channel, v.duration, v.thumbnail, v.upload_date, v.yt_artist, v.yt_album, v.yt_track, "
    "p.title AS playlist_title FROM items i JOIN videos v ON v.id = i.video_id "
    "JOIN playlists p ON p.id = i.playlist_id ")


def item_public(r: dict) -> dict:
    r = dict(r)
    r["meta"] = db.jload(r["meta"])
    r["meta_override"] = db.jload(r["meta_override"], {})
    r["file_name"] = Path(r["file_path"]).name if r.get("file_path") else None
    return r


def query_items(playlist_id: int | None, status: str | None, q: str | None, removed: str | None,
                offset: int, limit: int, sort: str = "position") -> dict:
    where, params = [], []
    if playlist_id:
        where.append("i.playlist_id = ?")
        params.append(playlist_id)
    if status:
        sts = [s for s in status.split(",") if s in STATUSES]
        if sts:
            where.append(f"i.status IN ({','.join('?' for _ in sts)})")
            params += sts
    if removed == "only":
        where.append("i.removed_at IS NOT NULL")
    elif removed != "include":
        where.append("i.removed_at IS NULL")
    if q:
        where.append("(v.title LIKE ? OR v.channel LIKE ? OR i.meta LIKE ? OR i.video_id = ? OR i.file_path LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like, q, like]
    w = (" WHERE " + " AND ".join(where)) if where else ""
    order = {"position": "p.id, i.position", "title": "v.title COLLATE NOCASE", "added": "i.first_seen_at DESC",
             "downloaded": "i.downloaded_at DESC", "status": "i.status, i.position"}.get(sort, "i.position")
    total = db.scalar("SELECT COUNT(*) FROM items i JOIN videos v ON v.id = i.video_id "
                      "JOIN playlists p ON p.id = i.playlist_id" + w, params)
    rows = db.rows(ITEM_SELECT + w + f" ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"total": total, "items": [item_public(r) for r in rows]}


@router.get("/playlists/{pid}/items")
def playlist_items(pid: int, status: str | None = None, q: str | None = None, removed: str | None = None,
                   offset: int = 0, limit: int = 200, sort: str = "position"):
    return query_items(pid, status, q, removed, offset, min(limit, 2000), sort)


@router.get("/items")
def all_items(playlist_id: int | None = None, status: str | None = None, q: str | None = None,
              removed: str | None = None, offset: int = 0, limit: int = 200, sort: str = "position"):
    return query_items(playlist_id, status, q, removed, offset, min(limit, 2000), sort)


@router.get("/items/ids")
def item_ids(playlist_id: int | None = None, status: str | None = None, q: str | None = None,
             removed: str | None = None):
    """All ids matching a filter, for 'select all' in the UI."""
    res = query_items(playlist_id, status, q, removed, 0, 100000)
    return [i["id"] for i in res["items"]]


@router.post("/items/download")
def download_items(body: dict):
    ids = [int(i) for i in body.get("item_ids", [])]
    if not ids:
        raise HTTPException(400, "No items selected")
    marks = ",".join("?" for _ in ids)
    by_pl: dict[int, list[int]] = {}
    for r in db.rows(f"SELECT id, playlist_id FROM items WHERE id IN ({marks})", ids):
        by_pl.setdefault(r["playlist_id"], []).append(r["id"])
    jobs = [manager.enqueue("download", pid, {"item_ids": its, "force": bool(body.get("force"))},
                            title=None) for pid, its in by_pl.items()]
    return {"jobs": [j["id"] for j in jobs]}


@router.post("/items/action")
def item_action(body: dict):
    ids = [int(i) for i in body.get("item_ids", [])]
    action = body.get("action")
    if not ids:
        raise HTTPException(400, "No items selected")
    marks = ",".join("?" for _ in ids)
    if action == "ignore":
        db.execute(f"UPDATE items SET status = 'ignored' WHERE id IN ({marks}) AND status != 'done'", ids)
    elif action == "unignore":
        db.execute(f"UPDATE items SET status = 'pending' WHERE id IN ({marks}) AND status = 'ignored'", ids)
    elif action == "reset":
        db.execute(f"UPDATE items SET status = 'pending', attempts = 0, error = NULL, error_kind = NULL "
                   f"WHERE id IN ({marks}) AND status IN ('failed','unavailable','missing')", ids)
    elif action == "delete_file":
        for r in db.rows(f"SELECT id, file_path FROM items WHERE id IN ({marks})", ids):
            if r["file_path"] and os.path.exists(r["file_path"]):
                try:
                    os.remove(r["file_path"])
                except OSError:
                    pass
            db.update("items", r["id"], {"status": "ignored", "file_path": None, "file_size": None})
    else:
        raise HTTPException(400, "Unknown action")
    return {"ok": True}


# ------------------------------------------------------------------ changes to the YouTube playlist

class RemoveIn(BaseModel):
    item_ids: list[int]
    delete_files: bool = False


class AlternativesIn(BaseModel):
    query: str | None = None


class AlternativeIn(BaseModel):
    candidate: dict[str, Any]
    replace: bool = False
    delete_file: bool = False


async def _edit(pid: int, coro_fn, check_job: bool = True):
    if check_job and any(j["playlist_id"] == pid and j["type"] == "sync" for j in manager.live()):
        raise HTTPException(409, "Wait until the sync of this playlist is finished")
    try:
        return await coro_fn()
    except playlist_edit.EditError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e))
    except ProviderError as e:  # incl. expired cookies; never 401, that would log the user out of Tubekeep
        raise HTTPException(502, str(e))


@router.get("/playlists/{pid}/access")
async def playlist_access(pid: int, refresh: bool = False):
    return await _edit(pid, lambda: playlist_edit.access(pid, refresh), check_job=False)


@router.post("/playlists/{pid}/remove")
async def remove_from_youtube(pid: int, body: RemoveIn):
    if not body.item_ids:
        raise HTTPException(400, "No tracks selected")
    return await _edit(pid, lambda: playlist_edit.remove(pid, body.item_ids, body.delete_files))


@router.post("/playlists/{pid}/items/{item_id}/alternatives")
async def find_alternatives(pid: int, item_id: int, body: AlternativesIn):
    return await _edit(pid, lambda: playlist_edit.alternatives(pid, item_id, body.query), check_job=False)


@router.post("/playlists/{pid}/items/{item_id}/alternative")
async def use_alternative(pid: int, item_id: int, body: AlternativeIn):
    return await _edit(pid, lambda: playlist_edit.apply_alternative(pid, item_id, body.candidate, body.replace,
                                                                    body.delete_file))


# ------------------------------------------------------------------ folder marker missing

class StorageIn(BaseModel):
    action: str  # check | redownload | keep_missing
    playlist_ids: list[int] | None = None


def _storage_action(pl: dict, action: str) -> dict:
    if action == "check":
        ok = storage.check(pl)
        if ok:
            manager.enqueue("sync", pl["id"])
        return {"id": pl["id"], "offline": not ok}
    if action not in ("redownload", "keep_missing"):
        raise HTTPException(400, "Unknown action")
    try:
        storage.write_marker(pl)
    except OSError as e:
        raise HTTPException(400, f"Cannot write to {pl['folder']}: {e}")
    if action == "redownload":
        db.execute("UPDATE items SET status = 'pending', error = NULL, attempts = 0 "
                   "WHERE playlist_id = ? AND status = 'missing'", [pl["id"]])
        manager.enqueue("sync", pl["id"], {"redownload": True})
    else:
        manager.enqueue("verify", pl["id"], {"redownload": False})
    return {"id": pl["id"], "offline": False}


@router.post("/playlists/{pid}/storage")
def playlist_storage(pid: int, body: StorageIn):
    pl = db.row("SELECT * FROM playlists WHERE id = ?", [pid])
    if not pl:
        raise HTTPException(404, "Playlist not found")
    return _storage_action(pl, body.action)


@router.post("/storage/offline")
def all_offline_storage(body: StorageIn):
    """The same choice for several (default: all) playlists whose folder is missing."""
    pls = db.rows("SELECT * FROM playlists WHERE storage_offline_at IS NOT NULL")
    if body.playlist_ids is not None:
        pls = [p for p in pls if p["id"] in set(body.playlist_ids)]
    return {"results": [_storage_action(p, body.action) for p in pls]}

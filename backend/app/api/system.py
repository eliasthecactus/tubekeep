import asyncio
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .. import accounts, auth, config, crypto, db, proxies, scheduler, settings_store, ytdlp
from ..events import bus
from ..jobs import manager
from .playlists import STATUSES

router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


# ------------------------------------------------------------------ jobs

@router.get("/jobs")
def list_jobs(limit: int = 50, offset: int = 0, status: str | None = None, playlist_id: int | None = None,
              type: str | None = None):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if playlist_id:
        where.append("playlist_id = ?")
        params.append(playlist_id)
    if type:
        where.append("type = ?")
        params.append(type)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.rows(f"SELECT * FROM jobs{w} ORDER BY id DESC LIMIT ? OFFSET ?", [*params, min(limit, 500), offset])
    for r in rows:
        r["stats"] = db.jload(r["stats"], {})
        r["params"] = db.jload(r["params"], {})
    return {"total": db.scalar(f"SELECT COUNT(*) FROM jobs{w}", params), "jobs": rows}


@router.get("/jobs/live")
def live_jobs():
    return manager.live()


@router.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
def job_log(job_id: int, tail: int = 2000):
    p = config.JOB_LOG_DIR / f"{job_id}.log"
    if not p.exists():
        return ""
    lines = p.read_text(errors="replace").splitlines()
    return "\n".join(lines[-tail:])


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    if not manager.cancel(job_id):
        raise HTTPException(404, "Job is not running")
    return {"ok": True}


@router.delete("/jobs")
def clear_jobs():
    old = db.rows("SELECT id FROM jobs WHERE status NOT IN ('queued','running')")
    for r in old:
        (config.JOB_LOG_DIR / f"{r['id']}.log").unlink(missing_ok=True)
    db.execute("DELETE FROM jobs WHERE status NOT IN ('queued','running')")
    return {"deleted": len(old)}


# ------------------------------------------------------------------ accounts

class AccountIn(BaseModel):
    label: str
    cookies: str
    proxy_id: int | None = None


class AccountPatch(BaseModel):
    label: str | None = None
    cookies: str | None = None
    proxy_id: int | None = None


@router.get("/accounts")
def list_accounts():
    rows = db.rows("SELECT a.*, (SELECT COUNT(*) FROM playlists p WHERE p.account_id = a.id) AS playlist_count "
                   "FROM accounts a ORDER BY a.label")
    return [accounts.public(r) for r in rows]


@router.post("/accounts")
async def add_account(body: AccountIn):
    try:
        info = accounts.validate_cookies(body.cookies)
    except ValueError as e:
        raise HTTPException(400, str(e))
    now = db.now()
    aid = db.insert("accounts", {"label": body.label.strip() or "YouTube account",
                                 "cookies_enc": crypto.encrypt(info["text"]), "proxy_id": body.proxy_id,
                                 "cookies_updated_at": now, "created_at": now})
    test = await accounts.test(aid)
    return {"id": aid, "cookie_count": info["cookie_count"], "test": test}


@router.patch("/accounts/{aid}")
async def patch_account(aid: int, body: AccountPatch):
    if not accounts.get(aid):
        raise HTTPException(404, "Account not found")
    data = body.model_dump(exclude_unset=True)
    fields = {}
    if "label" in data:
        fields["label"] = data["label"].strip()
    if "proxy_id" in data:
        fields["proxy_id"] = data["proxy_id"]
    if data.get("cookies"):
        try:
            info = accounts.validate_cookies(data["cookies"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        fields.update(cookies_enc=crypto.encrypt(info["text"]), cookies_updated_at=db.now(), status="unknown")
    db.update("accounts", aid, fields)
    test = await accounts.test(aid) if data.get("cookies") else None
    return {"ok": True, "test": test}


@router.delete("/accounts/{aid}")
def delete_account(aid: int):
    db.execute("DELETE FROM accounts WHERE id = ?", [aid])
    return {"ok": True}


@router.post("/accounts/{aid}/test")
async def test_account(aid: int):
    try:
        return await accounts.test(aid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/accounts/{aid}/playlists")
async def account_playlists(aid: int):
    try:
        lists = await accounts.list_playlists(aid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    have = {r["url"] for r in db.rows("SELECT url FROM playlists")}
    for p in lists:
        p["added"] = p["url"] in have
    return lists


# ------------------------------------------------------------------ proxies

class ProxyIn(BaseModel):
    label: str = ""
    url: str | None = None
    text: str | None = None   # bulk: one URL per line


class ProxyPatch(BaseModel):
    label: str | None = None
    url: str | None = None
    enabled: bool | None = None


@router.get("/proxies")
def list_proxies():
    return [proxies.public(r) for r in db.rows("SELECT * FROM proxies ORDER BY id")]


@router.post("/proxies")
def add_proxies(body: ProxyIn):
    urls = [body.url] if body.url else [ln.strip() for ln in (body.text or "").splitlines()
                                        if ln.strip() and not ln.strip().startswith("#")]
    if not urls:
        raise HTTPException(400, "Add at least one proxy URL")
    ids = []
    for u in urls:
        try:
            u = proxies.validate(u)
        except ValueError as e:
            raise HTTPException(400, f"{e}: {proxies.mask(u)}")
        label = (body.label or "").strip() if len(urls) == 1 else ""
        ids.append(db.insert("proxies", {"label": label or urlsplit(u).hostname, "url_enc": crypto.encrypt(u),
                                         "created_at": db.now()}))
    return {"created": ids}


@router.patch("/proxies/{pid}")
def patch_proxy(pid: int, body: ProxyPatch):
    data = body.model_dump(exclude_unset=True)
    fields = {}
    if "label" in data:
        fields["label"] = data["label"]
    if "enabled" in data:
        fields["enabled"] = int(bool(data["enabled"]))
        if data["enabled"]:
            fields.update(consecutive_fails=0, cooldown_until=None)
    if data.get("url"):
        try:
            fields["url_enc"] = crypto.encrypt(proxies.validate(data["url"]))
        except ValueError as e:
            raise HTTPException(400, str(e))
    db.update("proxies", pid, fields)
    return {"ok": True}


@router.delete("/proxies/{pid}")
def delete_proxy(pid: int):
    db.execute("DELETE FROM proxies WHERE id = ?", [pid])
    return {"ok": True}


@router.post("/proxies/{pid}/test")
async def test_proxy(pid: int):
    try:
        return await proxies.test(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/proxies/{pid}/reset")
def reset_proxy(pid: int):
    db.execute("UPDATE proxies SET cooldown_until = NULL, consecutive_fails = 0, enabled = 1 WHERE id = ?", [pid])
    return {"ok": True}


# ------------------------------------------------------------------ settings & system

@router.get("/settings")
def get_settings():
    return settings_store.public()


@router.put("/settings")
async def put_settings(body: dict):
    values = {k: v for k, v in body.items() if k in settings_store.DEFAULTS and k not in settings_store.SECRET_KEYS}
    for enc_key in settings_store.SECRET_KEYS:
        plain = enc_key.removesuffix("_enc")
        if plain in body:
            key = (body.get(plain) or "").strip()
            if key and plain == "apple_private_key" and "PRIVATE KEY" not in key:
                raise HTTPException(400, "The Apple key must be the content of the .p8 file (BEGIN PRIVATE KEY)")
            values[enc_key] = crypto.encrypt(key) if key else ""
    if values.get("public_url"):
        values["public_url"] = values["public_url"].strip().rstrip("/")
        if not values["public_url"].startswith(("http://", "https://")):
            raise HTTPException(400, "Public URL must start with http:// or https://")
    if "default_schedule" in values:
        try:
            values["default_schedule"] = scheduler.normalize(values["default_schedule"] or "")
        except ValueError as e:
            raise HTTPException(400, str(e))
    if values.get("default_folder_root"):
        p = Path(values["default_folder_root"])
        if not p.is_absolute():
            raise HTTPException(400, "Default folder must be an absolute path")
    for k in ("concurrency", "max_attempts"):
        if k in values:
            try:
                values[k] = max(1, min(16, int(values[k])))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number")
    if values.get("extra_args"):
        import shlex
        try:
            shlex.split(values["extra_args"])
        except ValueError as e:
            raise HTTPException(400, f"extra args: {e}")
    settings_store.set_many(values)
    await manager.slots.poke()
    return settings_store.public()


_tool_versions: dict = {}


async def _tool_version(cmd: list[str]) -> str | None:
    key = cmd[0]
    if key in _tool_versions:
        return _tool_versions[key]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
        v = out.decode(errors="replace").splitlines()[0].strip() if out else None
    except Exception:
        v = None
    _tool_versions[key] = v
    return v


def _disk(path: str) -> dict | None:
    try:
        u = shutil.disk_usage(path)
        return {"path": path, "total": u.total, "used": u.used, "free": u.free}
    except OSError:
        return None


@router.get("/system")
async def system():
    roots = {str(config.DOWNLOADS_DIR)}
    for r in db.rows("SELECT DISTINCT folder FROM playlists WHERE folder IS NOT NULL"):
        p = Path(r["folder"])
        while not p.exists() and p.parent != p:
            p = p.parent
        roots.add(str(p))
    disks, seen_dev = [], set()
    for r in sorted(roots):
        try:
            dev = os.stat(r).st_dev
        except OSError:
            continue
        if dev in seen_dev:
            continue
        seen_dev.add(dev)
        d = _disk(r)
        if d:
            disks.append(d)
    return {
        "ytdlp": ytdlp.state, "ytdlp_running": ytdlp.active_count(),
        "ffmpeg": await _tool_version(["ffmpeg", "-version"]),
        "deno": await _tool_version(["deno", "--version"]),
        "config_dir": str(config.CONFIG_DIR), "downloads_dir": str(config.DOWNLOADS_DIR),
        "disks": disks, "time": time.time(),
    }


@router.post("/system/ytdlp/update")
async def update_ytdlp(force: bool = False):
    try:
        return await ytdlp.update(force=force, channel=settings_store.get("ytdlp_channel"))
    except Exception as e:
        raise HTTPException(502, f"Update failed: {e}")


@router.get("/dashboard")
def dashboard():
    counts = {s: 0 for s in STATUSES}
    for r in db.rows("SELECT status, COUNT(*) n FROM items WHERE removed_at IS NULL GROUP BY status"):
        counts[r["status"]] = r["n"]
    return {
        "counts": counts,
        "playlists": db.scalar("SELECT COUNT(*) FROM playlists"),
        "bytes": db.scalar("SELECT SUM(COALESCE(file_size,0)) FROM items WHERE status = 'done'") or 0,
        "recent_failures": [dict(r) for r in db.rows(
            "SELECT i.id, i.playlist_id, i.error, i.error_kind, i.updated_at, v.title, p.title AS playlist_title "
            "FROM items i JOIN videos v ON v.id = i.video_id JOIN playlists p ON p.id = i.playlist_id "
            "WHERE i.status = 'failed' ORDER BY i.updated_at DESC LIMIT 8")],
        "recent_downloads": [dict(r) for r in db.rows(
            "SELECT i.id, i.playlist_id, i.downloaded_at, v.title, v.channel, p.title AS playlist_title "
            "FROM items i JOIN videos v ON v.id = i.video_id JOIN playlists p ON p.id = i.playlist_id "
            "WHERE i.status = 'done' AND i.downloaded_at IS NOT NULL ORDER BY i.downloaded_at DESC LIMIT 8")],
        "next_runs": db.rows("SELECT id, title, url, next_run_at FROM playlists WHERE enabled = 1 AND "
                             "next_run_at IS NOT NULL ORDER BY next_run_at LIMIT 5"),
        "accounts_expired": db.rows("SELECT id, label FROM accounts WHERE status = 'expired'"),
        "proxies": {"total": db.scalar("SELECT COUNT(*) FROM proxies"),
                    "usable": db.scalar("SELECT COUNT(*) FROM proxies WHERE enabled = 1 AND "
                                        "(cooldown_until IS NULL OR cooldown_until < ?)", [time.time()])},
        "last_jobs": db.rows("SELECT id, title, status, finished_at, error FROM jobs WHERE status NOT IN "
                             "('queued','running') ORDER BY id DESC LIMIT 6"),
        "ytdlp": ytdlp.state,
    }


@router.get("/fs")
def browse(path: str | None = None):
    """Folder picker. Lists sub folders of a path inside the container."""
    p = Path(path or config.DOWNLOADS_DIR)
    if not p.is_absolute():
        raise HTTPException(400, "Absolute path required")
    if not p.is_dir():
        raise HTTPException(404, "Not a folder")
    try:
        dirs = sorted([d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")],
                      key=str.casefold)
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs[:1000],
            "writable": os.access(p, os.W_OK)}


@router.get("/events")
async def events(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), 15)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

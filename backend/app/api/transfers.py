import secrets
import time
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import accounts, auth, db, matching, providers, scheduler, settings_store
from ..jobs import manager
from ..providers import LIKED, PROVIDERS, AuthError, ProviderError
from ..providers import spotify as sp
from ..providers import tidal as td
from ..providers.apple import developer_token

router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])
_spotify_states: dict[str, dict] = {}


def _err(e: Exception) -> HTTPException:
    if isinstance(e, AuthError):
        return HTTPException(400, f"Login expired: {e}")
    return HTTPException(400, str(e))


def public_base(request: Request) -> str:
    base = (settings_store.get("public_url") or "").strip().rstrip("/")
    if base:
        return base
    return str(request.base_url).rstrip("/")


def spotify_redirect(request: Request) -> str:
    base = public_base(request)
    p = urlsplit(base)
    if p.hostname == "localhost":  # Spotify only accepts the loopback IP, not "localhost"
        base = base.replace("://localhost", "://127.0.0.1", 1)
    return base + "/api/connections/spotify/callback"


def conn_public(c: dict) -> dict:
    out = {k: v for k, v in c.items() if k != "credentials_enc"}
    cls = PROVIDERS.get(c["provider"])
    out["provider_label"] = cls.label if cls else c["provider"]
    out["can_remove"] = bool(cls and cls.can_remove)
    return out


# ------------------------------------------------------------------ providers & connections

@router.get("/providers")
def provider_info(request: Request):
    s = settings_store.get_all()
    return {
        "youtube": {"label": "YouTube Music", "ready": True, "accounts": len(db.rows("SELECT id FROM accounts"))},
        "spotify": {"label": "Spotify", "ready": bool(s["spotify_client_id"]), "redirect_uri": spotify_redirect(request),
                    "client_id": s["spotify_client_id"]},
        "apple": {"label": "Apple Music", "ready": bool(s["apple_team_id"] and s["apple_key_id"]
                                                       and settings_store.get("apple_private_key_enc")),
                  "team_id": s["apple_team_id"], "key_id": s["apple_key_id"]},
        "tidal": {"label": "TIDAL", "ready": True},
    }


@router.get("/connections")
def list_connections():
    return [conn_public(c) for c in db.rows("SELECT * FROM connections ORDER BY provider, label")]


@router.delete("/connections/{cid}")
def delete_connection(cid: int):
    db.execute("DELETE FROM connections WHERE id = ?", [cid])
    return {"ok": True}


@router.post("/connections/{cid}/test")
async def test_connection(cid: int):
    try:
        p = providers.load(cid)
        name = await p.whoami()
        providers.save_creds(p)
        providers.mark(cid, "valid")
        return {"ok": True, "name": name}
    except AuthError as e:
        providers.mark(cid, "expired", str(e))
        return {"ok": False, "error": str(e)}
    except ProviderError as e:
        providers.mark(cid, "unknown", str(e))
        return {"ok": False, "error": str(e)}


@router.get("/connections/{cid}/playlists")
async def connection_playlists(cid: int):
    try:
        p = providers.load(cid)
        lists = await p.list_playlists()
        providers.save_creds(p)
        for x in lists:
            x["url"] = p.playlist_url(x["id"])
        return lists
    except (ProviderError, AuthError) as e:
        if isinstance(e, AuthError):
            providers.mark(cid, "expired", str(e))
        raise _err(e)


class YouTubeConn(BaseModel):
    account_id: int
    label: str | None = None


@router.post("/connections/youtube")
async def connect_youtube(body: YouTubeConn):
    acc = accounts.get(body.account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    cid = providers.create_connection("youtube", body.label or acc["label"], {}, account_id=acc["id"])
    res = await test_connection(cid)
    if not res["ok"]:
        db.execute("DELETE FROM connections WHERE id = ?", [cid])
        raise HTTPException(400, f"YouTube Music did not accept the account cookies: {res['error']}")
    if not body.label:
        db.update("connections", cid, {"label": f"{res['name']}"})
    return {"id": cid, "name": res["name"]}


# --- Spotify (PKCE)

@router.get("/connections/spotify/authorize")
def spotify_authorize(request: Request, label: str = ""):
    client_id = settings_store.get("spotify_client_id")
    if not client_id:
        raise HTTPException(400, "Add your Spotify Client ID first")
    verifier, challenge = sp.pkce_pair()
    state = secrets.token_urlsafe(24)
    redirect = spotify_redirect(request)
    _spotify_states[state] = {"verifier": verifier, "redirect": redirect, "label": label, "t": time.time()}
    for k in [k for k, v in _spotify_states.items() if v["t"] < time.time() - 900]:
        _spotify_states.pop(k, None)
    return {"url": sp.authorize_url(client_id, redirect, state, challenge), "redirect_uri": redirect}


@router.get("/connections/spotify/callback", include_in_schema=False)
async def spotify_callback(state: str = "", code: str = "", error: str = ""):
    st = _spotify_states.pop(state, None)
    if error or not st or not code:
        msg = error or "Login expired, try again"
        return RedirectResponse(f"/transfers?error={quote('Spotify: ' + msg)}")
    try:
        creds = await sp.exchange_code(settings_store.get("spotify_client_id"), code, st["verifier"], st["redirect"])
        cid = providers.create_connection("spotify", st["label"] or "Spotify", creds)
        p = providers.load(cid)
        name = await p.whoami()
        p.creds_changed = True  # whoami stored the user id
        providers.save_creds(p)
        if not st["label"]:
            db.update("connections", cid, {"label": name})
    except Exception as e:
        return RedirectResponse(f"/transfers?error={quote('Spotify: ' + str(e)[:200])}")
    return RedirectResponse("/transfers?connected=spotify")


# --- Apple Music (MusicKit JS in the browser gets the user token)

@router.get("/connections/apple/developer-token")
def apple_dev_token():
    try:
        return {"token": developer_token()}
    except ProviderError as e:
        raise HTTPException(400, str(e))


class AppleConn(BaseModel):
    music_user_token: str
    storefront: str | None = None
    label: str | None = None


@router.post("/connections/apple")
async def connect_apple(body: AppleConn):
    cid = providers.create_connection("apple", body.label or "Apple Music",
                                      {"music_user_token": body.music_user_token, "storefront": body.storefront or "us"})
    res = await test_connection(cid)
    if not res["ok"]:
        db.execute("DELETE FROM connections WHERE id = ?", [cid])
        raise HTTPException(400, f"Apple Music did not accept the token: {res['error']}")
    return {"id": cid, "name": res["name"]}


# --- TIDAL (device login)

@router.post("/connections/tidal/start")
async def tidal_start(body: dict):
    import asyncio
    try:
        return await asyncio.to_thread(td.start_login, (body or {}).get("label") or "")
    except Exception as e:
        raise HTTPException(502, f"TIDAL login could not start: {e}")


@router.get("/connections/tidal/status/{flow_id}")
async def tidal_status(flow_id: str):
    f = td.FLOWS.get(flow_id)
    if not f:
        raise HTTPException(404, "Login flow expired, start again")
    if f.get("result"):
        return f["result"]
    fut = f["future"]
    if not fut.done():
        return {"status": "pending"}
    try:
        fut.result()
        s = f["session"]
        if not s.check_login():
            raise RuntimeError("login was not completed")
        creds = td.creds_of(s)
        name = getattr(s.user, "username", None) or getattr(s.user, "email", None) or "TIDAL"
        cid = providers.create_connection("tidal", f["label"] or name, creds)
        f["result"] = {"status": "done", "id": cid, "name": name}
    except Exception as e:
        f["result"] = {"status": "error", "error": str(e)[:300]}
    return f["result"]


# ------------------------------------------------------------------ transfers

class TransferIn(BaseModel):
    name: str | None = None
    a_conn_id: int
    a_playlist_id: str
    a_playlist_name: str | None = None
    b_conn_id: int
    b_playlist_id: str | None = None
    b_playlist_name: str | None = None
    keep_synced: bool = False
    two_way: bool = False
    sync_removals: bool = False
    add_uncertain: bool = False
    download: bool = False
    schedule: str = "0 */6 * * *"
    run_now: bool = True


class TransferPatch(BaseModel):
    name: str | None = None
    keep_synced: bool | None = None
    two_way: bool | None = None
    sync_removals: bool | None = None
    add_uncertain: bool | None = None
    download: bool | None = None
    schedule: str | None = None
    enabled: bool | None = None


ITEM_STATUSES = ["new", "matched", "added", "synced", "review", "not_found", "pending_add", "skipped", "gone", "removed"]


def _transfer_public(t: dict) -> dict:
    t = dict(t)
    t["stats"] = db.jload(t["stats"], {})
    for k in ("keep_synced", "two_way", "sync_removals", "add_uncertain", "download", "enabled"):
        t[k] = bool(t[k])
    conns = {c["id"]: c for c in db.rows("SELECT id, provider, label, status FROM connections WHERE id IN (?, ?)",
                                         [t["a_conn_id"], t["b_conn_id"]])}
    for side in ("a", "b"):
        c = conns.get(t[f"{side}_conn_id"]) or {}
        cls = PROVIDERS.get(c.get("provider", ""))
        t[f"{side}_provider"] = c.get("provider")
        t[f"{side}_provider_label"] = cls.label if cls else None
        t[f"{side}_conn_label"] = c.get("label")
        t[f"{side}_conn_status"] = c.get("status")
        pid = t[f"{side}_playlist_id"]
        try:
            t[f"{side}_url"] = cls(c, {}).playlist_url(pid) if cls and pid else None
        except Exception:
            t[f"{side}_url"] = None
    counts: dict = {"a": {}, "b": {}}
    for r in db.rows("SELECT side, status, COUNT(*) n FROM transfer_items WHERE transfer_id = ? AND present = 1 "
                     "GROUP BY side, status", [t["id"]]):
        counts[r["side"]][r["status"]] = r["n"]
    t["counts"] = counts
    t["job"] = next((j for j in manager.live() if j.get("transfer_id") == t["id"]), None)
    return t


@router.get("/transfers")
def list_transfers():
    return [_transfer_public(t) for t in db.rows("SELECT * FROM transfers ORDER BY name COLLATE NOCASE")]


@router.post("/transfers")
async def create_transfer(body: TransferIn):
    for cid in (body.a_conn_id, body.b_conn_id):
        if not db.row("SELECT id FROM connections WHERE id = ?", [cid]):
            raise HTTPException(404, "Connection not found")
    a = providers.load(body.a_conn_id)
    b = providers.load(body.b_conn_id)
    a_pid = a.parse_playlist_id(body.a_playlist_id)
    b_pid = b.parse_playlist_id(body.b_playlist_id) if body.b_playlist_id else None
    if body.a_conn_id == body.b_conn_id and a_pid == b_pid:
        raise HTTPException(400, "Source and target are the same playlist")
    if body.two_way and not body.keep_synced:
        raise HTTPException(400, "Two-way only makes sense together with 'keep in sync'")
    a_name = body.a_playlist_name
    if not a_name:
        try:
            a_name = await a.playlist_name(a_pid)
        except Exception as e:
            raise HTTPException(400, f"Cannot read the source playlist: {e}")
    schedule = scheduler.normalize(body.schedule) if body.keep_synced else ""
    tid = db.insert("transfers", {
        "name": body.name or f"{a_name or a_pid} → {b.label}", "a_conn_id": body.a_conn_id, "a_playlist_id": a_pid,
        "a_playlist_name": a_name, "b_conn_id": body.b_conn_id, "b_playlist_id": b_pid,
        "b_playlist_name": body.b_playlist_name or (None if b_pid else a_name),
        "keep_synced": int(body.keep_synced), "two_way": int(body.two_way), "sync_removals": int(body.sync_removals),
        "add_uncertain": int(body.add_uncertain), "download": int(body.download), "schedule": schedule,
        "next_run_at": scheduler.next_run(schedule) if schedule else None, "created_at": db.now()})
    if body.run_now:
        manager.enqueue("transfer", None, transfer_id=tid)
    return _transfer_public(db.row("SELECT * FROM transfers WHERE id = ?", [tid]))


@router.get("/transfers/{tid}")
def get_transfer(tid: int):
    t = db.row("SELECT * FROM transfers WHERE id = ?", [tid])
    if not t:
        raise HTTPException(404, "Transfer not found")
    return _transfer_public(t)


@router.patch("/transfers/{tid}")
def patch_transfer(tid: int, body: TransferPatch):
    t = db.row("SELECT * FROM transfers WHERE id = ?", [tid])
    if not t:
        raise HTTPException(404, "Transfer not found")
    data = body.model_dump(exclude_unset=True)
    fields = {}
    if "name" in data and data["name"]:
        fields["name"] = data["name"]
    for k in ("keep_synced", "two_way", "sync_removals", "add_uncertain", "download", "enabled"):
        if k in data and data[k] is not None:
            fields[k] = int(data[k])
    keep = fields.get("keep_synced", t["keep_synced"])
    if fields.get("two_way", t["two_way"]) and not keep:
        fields["two_way"] = 0
    if "schedule" in data or "keep_synced" in fields:
        sched = scheduler.normalize(data.get("schedule", t["schedule"]) or "0 */6 * * *") if keep else ""
        fields["schedule"] = sched
        fields["next_run_at"] = scheduler.next_run(sched) if sched else None
    db.update("transfers", tid, fields)
    return get_transfer(tid)


@router.delete("/transfers/{tid}")
def delete_transfer(tid: int):
    for j in manager.live():
        if j.get("transfer_id") == tid:
            manager.cancel(j["id"])
    db.execute("DELETE FROM transfers WHERE id = ?", [tid])
    return {"ok": True}


@router.post("/transfers/{tid}/run")
def run_transfer(tid: int):
    if not db.row("SELECT id FROM transfers WHERE id = ?", [tid]):
        raise HTTPException(404, "Transfer not found")
    return manager.enqueue("transfer", None, transfer_id=tid)


@router.get("/transfers/{tid}/items")
def transfer_items(tid: int, side: str = "a", status: str | None = None, q: str | None = None,
                   offset: int = 0, limit: int = 300):
    where, params = ["transfer_id = ?", "side = ?"], [tid, side]
    if status == "removed":
        where.append("present = 0")
    else:
        where.append("present = 1")
        if status:
            sts = [s for s in status.split(",") if s in ITEM_STATUSES]
            if sts:
                where.append(f"status IN ({','.join('?' for _ in sts)})")
                params += sts
    if q:
        where.append("(meta LIKE ? OR match_meta LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    w = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM transfer_items WHERE {w}", params)
    rows = db.rows(f"SELECT * FROM transfer_items WHERE {w} ORDER BY position LIMIT ? OFFSET ?",
                   [*params, min(limit, 2000), offset])
    for r in rows:
        r["meta"] = db.jload(r["meta"], {})
        r["match_meta"] = db.jload(r["match_meta"], None)
        r["candidates"] = db.jload(r["candidates"], None)
    return {"total": total, "items": rows}


@router.get("/transfers/{tid}/search")
async def transfer_search(tid: int, side: str, q: str):
    """Search on the opposite side of an item's side."""
    t = db.row("SELECT * FROM transfers WHERE id = ?", [tid])
    if not t:
        raise HTTPException(404, "Transfer not found")
    target = providers.load(t["b_conn_id"] if side == "a" else t["a_conn_id"])
    try:
        res = await target.search(q, limit=10)
        providers.save_creds(target)
        return res
    except (ProviderError, AuthError) as e:
        raise _err(e)


class Resolve(BaseModel):
    track: dict | None = None
    url: str | None = None
    skip: bool | None = None


@router.post("/transfers/{tid}/items/{item_id}/resolve")
def resolve_item(tid: int, item_id: int, body: Resolve):
    it = db.row("SELECT * FROM transfer_items WHERE id = ? AND transfer_id = ?", [item_id, tid])
    t = db.row("SELECT * FROM transfers WHERE id = ?", [tid])
    if not it or not t:
        raise HTTPException(404, "Item not found")
    if body.skip is not None:
        db.update("transfer_items", item_id, {"status": "skipped" if body.skip else "new", "updated_at": db.now()})
        return {"ok": True}
    dst_conn = db.row("SELECT * FROM connections WHERE id = ?", [t["b_conn_id"] if it["side"] == "a" else t["a_conn_id"]])
    src_conn = db.row("SELECT * FROM connections WHERE id = ?", [t["a_conn_id"] if it["side"] == "a" else t["b_conn_id"]])
    track = body.track
    if not track and body.url:
        cls = PROVIDERS[dst_conn["provider"]]
        tid_ = cls(dst_conn, {}).parse_track_id(body.url)
        track = {"id": tid_, "title": body.url, "artists": [], "album": None, "duration": None, "isrc": None,
                 "url": body.url, "extra": {}}
    if not track or not track.get("id"):
        raise HTTPException(400, "Pick a track or paste a link")
    db.update("transfer_items", item_id, {"match_id": str(track["id"]), "match_meta": db.jdump(track), "score": 1.0,
                                          "status": "pending_add", "manual": 1, "candidates": None,
                                          "updated_at": db.now()})
    matching.remember_manual(src_conn["provider"], it["track_id"], dst_conn["provider"], track)
    return {"ok": True}


@router.get("/settings/apple-key-check")
def apple_key_check():
    try:
        developer_token()
        return {"ok": True}
    except ProviderError as e:
        return {"ok": False, "error": str(e)}


__all__ = ["router", "LIKED"]

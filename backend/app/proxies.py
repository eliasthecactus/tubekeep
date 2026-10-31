"""Proxy pool: selection, cooldown after blocks, health checks."""
import random
import time
from urllib.parse import urlsplit, urlunsplit

from . import crypto, db, settings_store, ytdlp

_rr = 0
TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", first YouTube video


def mask(url: str) -> str:
    p = urlsplit(url)
    if p.password:
        netloc = f"{p.username}:****@{p.hostname}" + (f":{p.port}" if p.port else "")
        return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
    return url


def validate(url: str) -> str:
    url = url.strip()
    p = urlsplit(url)
    if p.scheme not in ("http", "https", "socks4", "socks4a", "socks5", "socks5h") or not p.hostname:
        raise ValueError("Proxy URL must look like http://host:port or socks5://user:pass@host:port")
    return url


def public(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k != "url_enc"}
    try:
        out["url"] = mask(crypto.decrypt(r["url_enc"]))
    except ValueError:
        out["url"] = "(cannot decrypt)"
    out["cooling_down"] = bool(r.get("cooldown_until") and r["cooldown_until"] > time.time())
    return out


def get_url(proxy_id: int) -> str | None:
    r = db.row("SELECT url_enc FROM proxies WHERE id = ?", [proxy_id])
    return crypto.decrypt(r["url_enc"]) if r else None


def _usable() -> list[dict]:
    return db.rows("SELECT * FROM proxies WHERE enabled = 1 AND (cooldown_until IS NULL OR cooldown_until < ?) "
                   "ORDER BY id", [time.time()])


def select(playlist: dict | None, account: dict | None, exclude: set[int] | None = None) -> dict | None:
    """Returns {'id', 'url'} or None for a direct connection."""
    global _rr
    exclude = exclude or set()
    # Accounts are pinned to one proxy (or none) so the session keeps one IP.
    if account is not None:
        pid = account.get("proxy_id")
        if pid:
            return {"id": pid, "url": get_url(pid)}
        return None
    if playlist:
        mode = playlist.get("proxy_mode") or "default"
        if mode == "none":
            return None
        if mode == "fixed" and playlist.get("proxy_id"):
            return {"id": playlist["proxy_id"], "url": get_url(playlist["proxy_id"])}
    strategy = settings_store.get("proxy_strategy")
    if strategy == "none" and not (playlist and playlist.get("proxy_mode") == "pool"):
        return None
    pool = [p for p in _usable() if p["id"] not in exclude]
    if not pool:
        return None
    if strategy == "random":
        p = random.choice(pool)
    else:
        _rr += 1
        p = pool[_rr % len(pool)]
    return {"id": p["id"], "url": crypto.decrypt(p["url_enc"])}


def report(proxy_id: int | None, ok: bool, error: str | None = None, blocked: bool = False) -> None:
    if not proxy_id:
        return
    now = time.time()
    if ok:
        db.execute("UPDATE proxies SET success_count = success_count + 1, consecutive_fails = 0, "
                   "last_ok_at = ?, last_error = NULL WHERE id = ?", [now, proxy_id])
        return
    s = settings_store.get_all()
    r = db.row("SELECT consecutive_fails FROM proxies WHERE id = ?", [proxy_id])
    if not r:
        return
    fails = r["consecutive_fails"] + 1
    cooldown = None
    if blocked:
        # grows with repeated blocks: 1x, 2x, 4x … the base cooldown, capped at 24h
        cooldown = now + min(86400, s["proxy_cooldown_minutes"] * 60 * (2 ** min(fails - 1, 6)))
    disable = fails >= int(s["proxy_max_consecutive_fails"] or 0) > 0
    db.execute("UPDATE proxies SET fail_count = fail_count + 1, consecutive_fails = ?, last_error = ?, "
               "cooldown_until = COALESCE(?, cooldown_until), enabled = CASE WHEN ? THEN 0 ELSE enabled END "
               "WHERE id = ?", [fails, (error or "")[:500], cooldown, int(disable), proxy_id])


async def test(proxy_id: int) -> dict:
    url = get_url(proxy_id)
    if not url:
        raise ValueError("Proxy not found")
    t0 = time.time()
    data, res = await ytdlp.run_json(ytdlp.base_args(proxy=url) + ["-J", "--skip-download", TEST_URL], timeout=90)
    latency = int((time.time() - t0) * 1000)
    ok = bool(data and data.get("id"))
    err = None if ok else (res.error_text or "no data")
    db.execute("UPDATE proxies SET last_checked_at = ?, last_latency_ms = ?, last_error = ?, "
               "last_ok_at = CASE WHEN ? THEN ? ELSE last_ok_at END, "
               "consecutive_fails = CASE WHEN ? THEN 0 ELSE consecutive_fails END, "
               "cooldown_until = CASE WHEN ? THEN NULL ELSE cooldown_until END WHERE id = ?",
               [time.time(), latency, err, int(ok), time.time(), int(ok), int(ok), proxy_id])
    return {"ok": ok, "latency_ms": latency, "error": err}

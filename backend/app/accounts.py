"""YouTube accounts backed by encrypted cookies.txt files."""
import os
import secrets
import time
from contextlib import asynccontextmanager

from . import config, crypto, db, proxies, ytdlp

TEST_URL = "https://www.youtube.com/playlist?list=WL"  # Watch Later only works when logged in
AUTH_COOKIES = {"SAPISID", "__Secure-3PSID", "__Secure-1PSID", "SID", "LOGIN_INFO", "__Secure-3PAPISID"}


def validate_cookies(text: str) -> dict:
    """Checks a Netscape cookies.txt. Returns summary info or raises ValueError."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("Cookies file is empty")
    names, yt = set(), 0
    expiry = []
    for line in text.split("\n"):
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            raise ValueError("Not a Netscape cookies.txt (every cookie line needs 7 tab-separated fields)")
        domain = parts[0].removeprefix("#HttpOnly_")
        if "youtube.com" in domain or "google.com" in domain:
            yt += 1
            names.add(parts[5])
            try:
                if int(parts[4]) > 0:
                    expiry.append(int(parts[4]))
            except ValueError:
                pass
    if not yt:
        raise ValueError("No youtube.com cookies found in the file")
    if not names & AUTH_COOKIES:
        raise ValueError("No login cookies found. Export the cookies while signed in to YouTube")
    if not text.startswith("# Netscape") and not text.startswith("# HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    return {"text": text + "\n", "cookie_count": yt, "expires_at": min(expiry) if expiry else None}


def public(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "cookies_enc"}


def get(account_id: int | None) -> dict | None:
    if not account_id:
        return None
    return db.row("SELECT * FROM accounts WHERE id = ?", [account_id])


@asynccontextmanager
async def cookies_file(account: dict | None):
    """Decrypts the account cookies to a private temp file, saves rotated cookies back afterwards."""
    if not account:
        yield None
        return
    text = crypto.decrypt(account["cookies_enc"])
    path = config.TMP_DIR / f"cookies-{account['id']}-{secrets.token_hex(6)}.txt"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        yield str(path)
    finally:
        try:
            new = path.read_text()
            if new.strip() and new != text:
                try:
                    validate_cookies(new)
                    db.execute("UPDATE accounts SET cookies_enc = ?, cookies_updated_at = ? WHERE id = ?",
                               [crypto.encrypt(new), time.time(), account["id"]])
                except ValueError:
                    pass
        finally:
            path.unlink(missing_ok=True)


def mark(account_id: int, status: str, error: str | None = None) -> None:
    db.execute("UPDATE accounts SET status = ?, last_error = ?, last_checked_at = ? WHERE id = ?",
               [status, error, time.time(), account_id])


async def test(account_id: int) -> dict:
    acc = get(account_id)
    if not acc:
        raise ValueError("Account not found")
    proxy = proxies.select(None, acc)
    async with cookies_file(acc) as cf:
        data, res = await ytdlp.run_json(
            ytdlp.base_args(cf, proxy and proxy["url"]) + ["-J", "--flat-playlist", "--playlist-items", "1", TEST_URL],
            timeout=120)
    ok = bool(data and (data.get("id") or data.get("entries") is not None))
    err = None if ok else (res.error_text or "Could not open Watch Later with these cookies")
    if not ok and ytdlp.classify(err, bool(proxy)) in ("blocked", "proxy"):
        mark(account_id, acc["status"], err)  # network problem, not the cookies
        return {"ok": False, "status": acc["status"], "error": err}
    mark(account_id, "valid" if ok else "expired", err)
    return {"ok": ok, "status": "valid" if ok else "expired", "error": err}


async def list_playlists(account_id: int) -> list[dict]:
    """Playlists of the signed-in account (library feed) plus Liked and Watch Later."""
    acc = get(account_id)
    if not acc:
        raise ValueError("Account not found")
    proxy = proxies.select(None, acc)
    out = [
        {"title": "Liked videos", "url": "https://www.youtube.com/playlist?list=LL", "id": "LL"},
        {"title": "Watch later", "url": "https://www.youtube.com/playlist?list=WL", "id": "WL"},
    ]
    async with cookies_file(acc) as cf:
        data, res = await ytdlp.run_json(
            ytdlp.base_args(cf, proxy and proxy["url"]) + ["-J", "--flat-playlist",
                                                           "https://www.youtube.com/feed/playlists"],
            timeout=180)
    if not data:
        raise ValueError(res.error_text or "Could not read the account's playlists")
    seen = {"LL", "WL"}
    for e in data.get("entries") or []:
        pid = e.get("id") or ""
        url = e.get("url") or ""
        if "list=" in url:
            pid = url.split("list=")[1].split("&")[0]
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append({"title": e.get("title") or pid, "id": pid,
                    "url": f"https://www.youtube.com/playlist?list={pid}",
                    "count": e.get("playlist_count")})
    return out

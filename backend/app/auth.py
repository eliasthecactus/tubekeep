"""Local accounts, sessions and request guards."""
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request

from . import config, db

ph = PasswordHasher()
COOKIE = "tubekeep_session"
_attempts: dict[str, deque] = defaultdict(deque)
MAX_ATTEMPTS = 8
WINDOW = 300


def hash_password(pw: str) -> str:
    return ph.hash(pw)


def verify_password(pw_hash: str, pw: str) -> bool:
    try:
        return ph.verify(pw_hash, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password(pw: str) -> None:
    if len(pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")


def has_users() -> bool:
    return bool(db.scalar("SELECT COUNT(*) FROM users"))


def create_user(username: str, password: str) -> int:
    username = username.strip()
    if not username:
        raise HTTPException(400, "Username required")
    validate_password(password)
    if db.row("SELECT id FROM users WHERE username = ?", [username]):
        raise HTTPException(409, "User already exists")
    return db.insert("users", {"username": username, "password_hash": hash_password(password), "created_at": db.now()})


def bootstrap_admin() -> None:
    if config.ADMIN_USER and config.ADMIN_PASSWORD and not has_users():
        create_user(config.ADMIN_USER, config.ADMIN_PASSWORD)


def check_rate_limit(ip: str) -> None:
    q = _attempts[ip]
    now = time.time()
    while q and q[0] < now - WINDOW:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many login attempts, try again in a few minutes")


def record_failure(ip: str) -> None:
    _attempts[ip].append(time.time())


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = db.now()
    db.insert("sessions", {
        "token": token, "user_id": user_id, "created_at": now,
        "expires_at": now + config.SESSION_DAYS * 86400, "last_seen": now,
    })
    db.execute("DELETE FROM sessions WHERE expires_at < ?", [now])
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    r = db.row(
        "SELECT u.id, u.username, s.last_seen FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ?", [token, db.now()])
    if not r:
        return None
    if db.now() - r["last_seen"] > 300:
        db.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", [db.now(), token])
    return {"id": r["id"], "username": r["username"]}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def require_user(request: Request) -> dict:
    user = session_user(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Not logged in")
    # CSRF guard: mutating requests must carry a custom header (cannot be sent cross-site without CORS).
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "tubekeep":
        raise HTTPException(403, "Missing X-Requested-With header")
    return user

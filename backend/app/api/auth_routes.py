from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth, db

router = APIRouter(prefix="/api/auth")


class Creds(BaseModel):
    username: str
    password: str


class PwChange(BaseModel):
    current: str
    new: str


def _set_cookie(request: Request, response: Response, token: str) -> None:
    secure = request.url.scheme == "https"
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=secure,
                        max_age=auth.config.SESSION_DAYS * 86400, path="/")


@router.get("/state")
def state(request: Request):
    return {"setup_needed": not auth.has_users(), "user": auth.session_user(request.cookies.get(auth.COOKIE)),
            "version": auth.config.VERSION}


@router.post("/setup")
def setup(body: Creds, request: Request, response: Response):
    if auth.has_users():
        raise HTTPException(409, "Already set up")
    uid = auth.create_user(body.username, body.password)
    _set_cookie(request, response, auth.create_session(uid))
    return {"ok": True}


@router.post("/login")
def login(body: Creds, request: Request, response: Response):
    ip = auth.client_ip(request)
    auth.check_rate_limit(ip)
    u = db.row("SELECT * FROM users WHERE username = ?", [body.username.strip()])
    if not u or not auth.verify_password(u["password_hash"], body.password):
        auth.record_failure(ip)
        raise HTTPException(401, "Wrong username or password")
    _set_cookie(request, response, auth.create_session(u["id"]))
    return {"ok": True, "user": {"id": u["id"], "username": u["username"]}}


@router.post("/logout")
def logout(request: Request, response: Response):
    tok = request.cookies.get(auth.COOKIE)
    if tok:
        db.execute("DELETE FROM sessions WHERE token = ?", [tok])
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(body: PwChange, user=Depends(auth.require_user)):
    u = db.row("SELECT * FROM users WHERE id = ?", [user["id"]])
    if not auth.verify_password(u["password_hash"], body.current):
        raise HTTPException(400, "Current password is wrong")
    auth.validate_password(body.new)
    db.update("users", u["id"], {"password_hash": auth.hash_password(body.new)})
    return {"ok": True}


users = APIRouter(prefix="/api/users", dependencies=[Depends(auth.require_user)])


@users.get("")
def list_users():
    return db.rows("SELECT id, username, created_at FROM users ORDER BY id")


@users.post("")
def add_user(body: Creds):
    return {"id": auth.create_user(body.username, body.password)}


@users.delete("/{user_id}")
def delete_user(user_id: int, user=Depends(auth.require_user)):
    if user_id == user["id"]:
        raise HTTPException(400, "You cannot delete yourself")
    db.execute("DELETE FROM users WHERE id = ?", [user_id])
    return {"ok": True}

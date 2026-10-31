"""Tubekeep: FastAPI app, serves the API and the built SPA."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import auth, config, db, scheduler, ytdlp
from .api import auth_routes, metadata, playlists, system, transfers
from .events import bus
from .jobs import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
VERSION = (Path(__file__).resolve().parent / "VERSION").read_text().strip() \
    if (Path(__file__).resolve().parent / "VERSION").exists() else "dev"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    auth.bootstrap_admin()
    bus.bind(asyncio.get_running_loop())
    manager.recover()
    if config.DISABLE_BACKGROUND:
        try:
            await ytdlp.ensure_installed()
        except Exception as e:
            logging.warning("yt-dlp not available: %s", e)
    scheduler.start(background=not config.DISABLE_BACKGROUND)
    yield
    for jid in list(manager.tasks):
        manager.cancel(jid)
    await scheduler.stop()


app = FastAPI(title="Tubekeep", version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")


@app.exception_handler(ValueError)
async def value_error(_: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, e: RequestValidationError):
    first = e.errors()[0] if e.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse({"detail": f"{loc}: {first.get('msg', 'invalid')}".strip(": ")}, status_code=422)


@app.middleware("http")
async def headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "ytdlp": ytdlp.state.get("version")}


app.include_router(auth_routes.router)
app.include_router(auth_routes.users)
app.include_router(playlists.router)   # before metadata: /items/ids must win over /items/{id}
app.include_router(metadata.router)
app.include_router(system.router)
app.include_router(transfers.router)


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    static = Path(config.STATIC_DIR)
    f = (static / path).resolve()
    if path and f.is_file() and static.resolve() in f.parents:
        cache = "public, max-age=31536000, immutable" if "/_app/immutable/" in f"/{path}" else "no-cache"
        return FileResponse(f, headers={"Cache-Control": cache})
    index = static / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built"}, status_code=404)

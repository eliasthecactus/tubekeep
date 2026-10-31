"""Background loops: playlist schedules, yt-dlp updates, account and proxy health checks."""
import asyncio
import logging
import time
from datetime import datetime

from croniter import croniter

from . import accounts, db, notify, proxies, settings_store, ytdlp
from .events import bus
from .jobs import manager

log = logging.getLogger("tubekeep.scheduler")
PRESETS = {"@hourly": "0 * * * *", "@daily": "0 3 * * *", "@weekly": "0 3 * * 1"}
_tasks: list[asyncio.Task] = []
_last: dict[str, float] = {}


def normalize(expr: str) -> str:
    expr = (expr or "").strip()
    expr = PRESETS.get(expr, expr)
    if expr and not croniter.is_valid(expr):
        raise ValueError(f"Invalid cron expression: {expr}")
    return expr


def next_run(expr: str, base: float | None = None) -> float | None:
    if not expr:
        return None
    base_dt = datetime.fromtimestamp(base or time.time()).astimezone()
    return croniter(expr, base_dt).get_next(float)


def refresh_next_runs() -> None:
    for t in db.rows("SELECT id, schedule, next_run_at, keep_synced FROM transfers"):
        if t["keep_synced"] and t["schedule"] and (not t["next_run_at"] or t["next_run_at"] < time.time()):
            db.update("transfers", t["id"], {"next_run_at": next_run(t["schedule"])})
    for pl in db.rows("SELECT id, schedule, next_run_at FROM playlists"):
        nr = next_run(pl["schedule"]) if pl["schedule"] else None
        if pl["next_run_at"] != nr and (nr is None or not pl["next_run_at"] or pl["next_run_at"] < time.time()):
            db.update("playlists", pl["id"], {"next_run_at": nr})


async def _schedule_loop() -> None:
    refresh_next_runs()
    while True:
        try:
            now = time.time()
            due = db.rows("SELECT p.*, a.status AS account_status FROM playlists p "
                          "LEFT JOIN accounts a ON a.id = p.account_id "
                          "WHERE p.enabled = 1 AND p.schedule != '' AND p.next_run_at IS NOT NULL "
                          "AND p.next_run_at <= ?", [now])
            for pl in due:
                db.update("playlists", pl["id"], {"next_run_at": next_run(pl["schedule"], now + 1)})
                if pl["account_id"] and pl["account_status"] == "expired":
                    log.info("skip %s: account expired", pl["id"])
                    continue
                manager.enqueue("sync", pl["id"], trigger="schedule")
            for t in db.rows("SELECT id, schedule FROM transfers WHERE enabled = 1 AND keep_synced = 1 AND "
                             "schedule != '' AND next_run_at IS NOT NULL AND next_run_at <= ?", [now]):
                db.update("transfers", t["id"], {"next_run_at": next_run(t["schedule"], now + 1)})
                manager.enqueue("transfer", None, trigger="schedule", transfer_id=t["id"])
        except Exception:
            log.exception("schedule loop")
        await asyncio.sleep(20)


def _due(name: str, hours: float) -> bool:
    if hours <= 0:
        return False
    return time.time() - _last.get(name, 0) >= hours * 3600


async def _ytdlp_loop() -> None:
    try:
        await ytdlp.ensure_installed()
    except Exception as e:
        log.error("yt-dlp install failed: %s", e)
    _last["ytdlp"] = 0
    while True:
        s = settings_store.get_all()
        if s["ytdlp_auto_update"] and _due("ytdlp", float(s["ytdlp_check_hours"] or 6)):
            _last["ytdlp"] = time.time()
            try:
                await ytdlp.update(channel=s["ytdlp_channel"])
            except Exception:
                pass
            bus.publish("system", {"ytdlp": ytdlp.state})
        await asyncio.sleep(60)


async def _accounts_loop() -> None:
    await asyncio.sleep(90)
    while True:
        hours = float(settings_store.get("account_check_hours") or 24)
        if _due("accounts", hours):
            _last["accounts"] = time.time()
            for a in db.rows("SELECT id, label, status FROM accounts"):
                try:
                    res = await accounts.test(a["id"])
                    if a["status"] != "expired" and res["status"] == "expired":
                        await notify.send("Tubekeep: account needs new cookies",
                                          f"{a['label']}: {res.get('error') or ''}"[:300])
                    bus.publish("account", {"id": a["id"]})
                except Exception as e:
                    log.warning("account check %s failed: %s", a["id"], e)
        await asyncio.sleep(300)


async def _proxy_loop() -> None:
    await asyncio.sleep(120)
    while True:
        hours = float(settings_store.get("proxy_check_hours") or 0)
        if _due("proxies", hours):
            _last["proxies"] = time.time()
            for p in db.rows("SELECT id FROM proxies WHERE enabled = 1"):
                try:
                    await proxies.test(p["id"])
                except Exception as e:
                    log.warning("proxy check %s failed: %s", p["id"], e)
            bus.publish("proxy", {})
        await asyncio.sleep(300)


def start(background: bool = True) -> None:
    _tasks.append(asyncio.create_task(_schedule_loop()))
    if background:
        _tasks.append(asyncio.create_task(_ytdlp_loop()))
        _tasks.append(asyncio.create_task(_accounts_loop()))
        _tasks.append(asyncio.create_task(_proxy_loop()))


async def stop() -> None:
    for t in _tasks:
        t.cancel()
    for t in _tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()

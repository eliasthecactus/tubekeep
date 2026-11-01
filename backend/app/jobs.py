"""Job engine: playlist sync, downloads, file verification. Runs in the app's event loop."""
import asyncio
import json
import threading
import logging
import os
import time
from pathlib import Path

from . import accounts, config, db, library, notify, proxies, settings_store, storage, tags, transfers, ytdlp
from .events import bus

log = logging.getLogger("tubekeep.jobs")

PLACEHOLDER_TITLES = {"[Private video]", "[Deleted video]", "[Unavailable video]"}
ACTIVE_JOB = ("queued", "running")


class JobError(Exception):
    pass


class StorageOffline(JobError):
    """The playlist folder lost its marker; the playlist page asks the user how to continue."""


class Slots:
    """Global limiter that reads its limit from a setting on every acquire."""

    def __init__(self, setting: str = "concurrency", default: int = 1) -> None:
        self.setting = setting
        self.default = default
        self.active = 0
        self.cond = asyncio.Condition()

    def limit(self) -> int:
        try:
            return max(1, min(16, int(settings_store.get(self.setting) or self.default)))
        except (TypeError, ValueError):
            return self.default

    async def __aenter__(self):
        async with self.cond:
            await self.cond.wait_for(lambda: self.active < self.limit())
            self.active += 1

    async def __aexit__(self, *exc):
        async with self.cond:
            self.active -= 1
            self.cond.notify_all()

    async def poke(self) -> None:
        async with self.cond:
            self.cond.notify_all()


class Ctx:
    """Per-job runtime state: log file, stats, live progress."""

    def __init__(self, job: dict) -> None:
        self.job = job
        self.id = job["id"]
        self.stats: dict = db.jload(job["stats"], {}) or {}
        self.phase = "queued"
        self.status = "queued"
        self.items: dict[int, dict] = {}
        self._log_lock = threading.Lock()
        self._last_pub: dict[int, float] = {}
        self._fh = open(config.JOB_LOG_DIR / f"{self.id}.log", "a", encoding="utf-8")

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def log(self, msg: str) -> None:
        line = time.strftime("%H:%M:%S ") + msg
        with self._log_lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        bus.publish("log", {"job_id": self.id, "line": line})

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.publish()

    def inc(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    def snapshot(self) -> dict:
        return {"id": self.id, "type": self.job["type"], "title": self.job["title"],
                "playlist_id": self.job["playlist_id"], "transfer_id": self.job.get("transfer_id"),
                "phase": self.phase,
                "stats": {k: v for k, v in self.stats.items() if not k.startswith("_")},
                "items": list(self.items.values()), "status": self.status}

    def publish(self) -> None:
        db.execute("UPDATE jobs SET stats = ? WHERE id = ?",
                   [db.jdump({k: v for k, v in self.stats.items() if not k.startswith("_")}), self.id])
        bus.publish("job", self.snapshot())


def item_event(item_id: int) -> None:
    r = db.row("SELECT i.id, i.playlist_id, i.status, i.error, i.error_kind, i.file_path, i.attempts, "
               "i.removed_at, i.meta FROM items i WHERE i.id = ?", [item_id])
    if r:
        r["meta"] = db.jload(r["meta"])
        bus.publish("item", r)


def set_item(item_id: int, **fields) -> None:
    fields["updated_at"] = db.now()
    db.update("items", item_id, fields)
    item_event(item_id)


class Manager:
    def __init__(self) -> None:
        self.tasks: dict[int, asyncio.Task] = {}
        self.ctx: dict[int, Ctx] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.global_lock = asyncio.Lock()   # verify-all etc.
        self.slots = Slots("concurrency", 2)          # yt-dlp downloads at the same time
        self.job_slots = Slots("max_parallel_jobs", 3)  # playlist syncs/transfers at the same time
        self.loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ lifecycle

    def recover(self) -> None:
        """After a restart nothing is running: fix rows left in running states."""
        self.loop = asyncio.get_running_loop()
        storage.loop = self.loop
        db.execute("UPDATE items SET status = 'pending' WHERE status IN ('queued', 'downloading')")
        # jobs that were waiting simply continue; jobs that were running start over
        waiting = db.rows("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id")
        running = db.rows("SELECT * FROM jobs WHERE status = 'running' ORDER BY id")
        db.execute("UPDATE jobs SET status = 'failed', error = 'Interrupted by restart, started again', "
                   "finished_at = ? WHERE status = 'running'", [db.now()])
        for job in waiting:
            self._start(job)
        for job in running:
            self.enqueue(job["type"], job["playlist_id"], db.jload(job["params"], {}), trigger="restart",
                         title=job["title"], transfer_id=job.get("transfer_id"))
        # keep the last 1000 job logs
        old = db.rows("SELECT id FROM jobs ORDER BY id DESC LIMIT -1 OFFSET 1000")
        for r in old:
            (config.JOB_LOG_DIR / f"{r['id']}.log").unlink(missing_ok=True)
        if old:
            db.execute("DELETE FROM jobs WHERE id <= ?", [old[0]["id"]])

    def lock_for(self, playlist_id: int | None, transfer_id: int | None = None) -> asyncio.Lock:
        if transfer_id is not None:
            return self.locks.setdefault(f"t{transfer_id}", asyncio.Lock())
        if playlist_id is None:
            return self.global_lock
        return self.locks.setdefault(playlist_id, asyncio.Lock())

    def enqueue(self, type_: str, playlist_id: int | None = None, params: dict | None = None,
                trigger: str = "manual", title: str | None = None, transfer_id: int | None = None) -> dict:
        if type_ == "transfer":
            existing = db.row("SELECT * FROM jobs WHERE type = 'transfer' AND transfer_id = ? "
                              "AND status IN ('queued','running')", [transfer_id])
            if existing:
                return existing
            if title is None:
                t = db.row("SELECT name FROM transfers WHERE id = ?", [transfer_id])
                title = f"Transfer {t['name'] if t else transfer_id}"
        if type_ in ("sync", "verify") and playlist_id is not None:
            existing = db.row("SELECT * FROM jobs WHERE type = ? AND playlist_id = ? AND status IN ('queued','running')",
                              [type_, playlist_id])
            if existing:
                return existing
        if title is None:
            pl = db.row("SELECT title, url FROM playlists WHERE id = ?", [playlist_id]) if playlist_id else None
            name = (pl and (pl["title"] or pl["url"])) or "all playlists"
            title = {"sync": f"Sync {name}", "download": f"Download in {name}",
                     "verify": f"Verify files of {name}"}.get(type_, type_)
        jid = db.insert("jobs", {"type": type_, "playlist_id": playlist_id, "transfer_id": transfer_id,
                                 "title": title, "status": "queued",
                                 "params": db.jdump(params or {}), "trigger": trigger, "created_at": db.now()})
        job = db.row("SELECT * FROM jobs WHERE id = ?", [jid])
        bus.publish("job", {**job, "stats": {}, "items": [], "phase": "queued"})
        self._call(self._start, job)
        return job

    def _start(self, job: dict) -> None:
        self.tasks[job["id"]] = asyncio.create_task(self._run(job))

    def _call(self, fn, *args) -> None:
        """Run fn in the event loop thread (API handlers may run in worker threads)."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is self.loop:
            fn(*args)
        else:
            self.loop.call_soon_threadsafe(fn, *args)

    def cancel(self, job_id: int) -> bool:
        t = self.tasks.get(job_id)
        if not t:
            return False
        self._call(t.cancel)
        return True

    def live(self) -> list[dict]:
        out = []
        for jid in self.tasks:
            c = self.ctx.get(jid)
            if c:
                out.append(c.snapshot())
            else:
                j = db.row("SELECT * FROM jobs WHERE id = ?", [jid])
                if j:
                    out.append({**j, "stats": {}, "items": [], "phase": "queued", "status": "queued"})
        return out

    async def _run(self, job: dict) -> None:
        ctx = Ctx(job)
        self.ctx[job["id"]] = ctx
        params = db.jload(job["params"], {}) or {}
        status, error, quiet = "done", None, False
        try:
            async with self.job_slots, self.lock_for(job["playlist_id"], job.get("transfer_id")):
                ctx.status = "running"
                db.update("jobs", job["id"], {"status": "running", "started_at": db.now()})
                ctx.set_phase("starting")
                handler = {"sync": self._sync, "download": self._download, "verify": self._verify,
                           "transfer": self._transfer}[job["type"]]
                await handler(ctx, job, params)
        except asyncio.CancelledError:
            status, error = "cancelled", "Cancelled"
            ctx.log("Cancelled")
        except (JobError, transfers.TransferError) as e:
            status, error = "failed", str(e)
            quiet = isinstance(e, StorageOffline)  # it sent its own notification once
            ctx.log(f"ERROR {e}")
        except Exception as e:  # unexpected
            status, error = "failed", f"{type(e).__name__}: {e}"
            log.exception("job %s failed", job["id"])
            ctx.log(f"ERROR {error}")
        finally:
            for item_id in list(ctx.items):
                r = db.row("SELECT status FROM items WHERE id = ?", [item_id])
                if r and r["status"] in ("queued", "downloading"):
                    set_item(item_id, status="pending")
            db.execute("UPDATE items SET status = 'pending' WHERE status = 'queued' AND id IN "
                       "(SELECT value FROM json_each(?))", [db.jdump(ctx.stats.get("_queued", []))])
            ctx.stats.pop("_queued", None)
            db.update("jobs", job["id"], {"status": status, "error": error, "finished_at": db.now(),
                                          "stats": db.jdump(ctx.stats)})
            if job["playlist_id"] and job["type"] == "sync":
                db.update("playlists", job["playlist_id"], {
                    "last_sync_at": db.now(),
                    "last_sync_status": status if status != "done" else
                    ("partial" if ctx.stats.get("failed") else "ok"),
                    "last_error": error})
                bus.publish("playlist", {"id": job["playlist_id"]})
            ctx.close()
            final = db.row("SELECT * FROM jobs WHERE id = ?", [job["id"]])
            final["stats"] = ctx.stats
            bus.publish("job", {**final, "items": [], "phase": status})
            self.ctx.pop(job["id"], None)
            self.tasks.pop(job["id"], None)
            if status == "failed" and job["trigger"] == "schedule" and not quiet:
                asyncio.create_task(notify.send("Tubekeep: job failed", f"{job['title']}: {error}"))

    # ------------------------------------------------------------ helpers

    def _playlist(self, playlist_id: int) -> dict:
        pl = db.row("SELECT * FROM playlists WHERE id = ?", [playlist_id])
        if not pl:
            raise JobError("Playlist was deleted")
        pl["options"] = db.jload(pl["options"], {}) or {}
        return pl

    async def _require_storage(self, pl: dict) -> None:
        if not await asyncio.to_thread(storage.check, pl):
            raise StorageOffline(storage.offline_message(pl))

    async def _enumerate(self, ctx: Ctx, pl: dict, acc: dict | None) -> dict:
        tried: set[int] = set()
        last = ""
        for attempt in range(4):
            proxy = proxies.select(pl, acc, tried)
            via = f" via proxy #{proxy['id']}" if proxy else ""
            ctx.log(f"Fetching playlist {pl['url']}{via}" + (f" as {acc['label']}" if acc else ""))
            async with accounts.cookies_file(acc) as cf:
                data, res = await ytdlp.run_json(
                    ytdlp.base_args(cf, proxy and proxy["url"]) + ["-J", "--flat-playlist", "--", pl["url"]],
                    timeout=1800)
            if data:
                proxies.report(proxy and proxy["id"], True)
                if acc and acc["status"] != "valid":
                    accounts.mark(acc["id"], "valid")
                return data
            last = res.error_text or "yt-dlp returned no data"
            kind = ytdlp.classify(last, bool(proxy))
            for ln in res.stderr[-6:]:
                ctx.log("  " + ln)
            proxies.report(proxy and proxy["id"], False, last, blocked=kind == "blocked")
            if proxy and kind in ("blocked", "proxy"):
                tried.add(proxy["id"])
                continue
            if kind == "auth":
                if acc:
                    accounts.mark(acc["id"], "expired", last)
                    asyncio.create_task(notify.send("Tubekeep: account needs new cookies",
                                                    f"{acc['label']}: {last[:200]}"))
                    raise JobError(f"YouTube rejected the cookies of account '{acc['label']}'. Upload fresh cookies.")
                raise JobError("This playlist needs a signed-in account. Add one under Accounts and assign it.")
            if kind == "unavailable":
                raise JobError(last)
            if attempt < 3:
                await asyncio.sleep(5 * (attempt + 1))
        raise JobError(last)

    def _upsert(self, ctx: Ctx, pl: dict, data: dict) -> None:
        entries = data.get("entries") if data.get("_type") == "playlist" or "entries" in data else [data]
        entries = [e for e in (entries or []) if e and e.get("id")]
        now = db.now()
        existing = {r["video_id"]: r for r in db.rows(
            "SELECT id, video_id, status, removed_at FROM items WHERE playlist_id = ?", [pl["id"]])}
        seen: set[str] = set()
        copies: dict[str, int] = {}
        for e in entries:
            copies[e["id"]] = copies.get(e["id"], 0) + 1
        new = became_available = 0
        with db.tx() as c:
            for pos, e in enumerate(entries, 1):
                vid = e["id"]
                if vid in seen:
                    continue
                seen.add(vid)
                title = e.get("title") or ""
                placeholder = title in PLACEHOLDER_TITLES
                thumbs = e.get("thumbnails") or []
                thumb = thumbs[-1]["url"] if thumbs else None
                c.execute(
                    "INSERT INTO videos (id, title, channel, channel_id, duration, thumbnail, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "title = CASE WHEN ? THEN videos.title ELSE excluded.title END, "
                    "channel = COALESCE(excluded.channel, videos.channel), "
                    "channel_id = COALESCE(excluded.channel_id, videos.channel_id), "
                    "duration = COALESCE(excluded.duration, videos.duration), "
                    "thumbnail = COALESCE(excluded.thumbnail, videos.thumbnail), updated_at = excluded.updated_at",
                    [vid, title or None, e.get("channel") or e.get("uploader"), e.get("channel_id"),
                     e.get("duration"), thumb, now, int(placeholder)])
                ex = existing.get(vid)
                if not ex:
                    new += 1
                    c.execute("INSERT INTO items (playlist_id, video_id, position, status, first_seen_at, updated_at, "
                              "copies) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              [pl["id"], vid, pos, "unavailable" if placeholder else "pending", now, now, copies[vid]])
                else:
                    status = ex["status"]
                    if status == "unavailable" and not placeholder:
                        status, became_available = "pending", became_available + 1
                    elif placeholder and status not in ("done", "ignored"):
                        status = "unavailable"
                    elif status == "deleted":
                        status = "pending"  # re-added after its file was deleted
                    c.execute("UPDATE items SET position = ?, removed_at = NULL, status = ?, updated_at = ?, copies = ? "
                              "WHERE id = ?", [pos, status, now, copies[vid], ex["id"]])
            removed_ids = [r["id"] for v, r in existing.items() if v not in seen and r["removed_at"] is None]
            for rid in removed_ids:
                c.execute("UPDATE items SET removed_at = ?, updated_at = ? WHERE id = ?", [now, now, rid])
            c.execute("UPDATE playlists SET title = COALESCE(?, title), yt_id = COALESCE(?, yt_id), entry_count = ? "
                      "WHERE id = ?", [data.get("title"), data.get("id"), len(seen), pl["id"]])
        ctx.stats.update(total=len(seen), new=new, removed=len(removed_ids))
        ctx.log(f"Playlist has {len(seen)} videos: {new} new, {len(removed_ids)} removed"
                + (f", {became_available} available again" if became_available else ""))

    def _verify_files(self, ctx: Ctx, pl: dict, redownload: bool | None = None) -> None:
        """Checks done items still have their file; re-links moved files; adopts existing files."""
        folder = pl["folder"]
        template = (pl["options"].get("filename_template") or settings_store.get("filename_template"))
        done = db.rows("SELECT id, file_path FROM items WHERE playlist_id = ? AND status = 'done'", [pl["id"]])
        missing = [r for r in done if not library.file_ok(r["file_path"])]
        candidates = db.rows(
            "SELECT i.id, i.status, v.id AS vid, v.title, v.channel, v.yt_artist, v.yt_track FROM items i "
            "JOIN videos v ON v.id = i.video_id WHERE i.playlist_id = ? AND i.removed_at IS NULL AND "
            "i.status IN ('pending', 'failed', 'missing', 'done')", [pl["id"]])
        missing_ids = {r["id"] for r in missing}
        need = [r for r in candidates if r["status"] != "done" or r["id"] in missing_ids]
        ctx.inc("missing", len(missing))
        if missing:
            ctx.log(f"{len(missing)} downloaded files are missing on disk")
        if not need:
            return
        ctx.set_phase("scanning folder")
        taken = {r["file_path"] for r in done if r["id"] not in missing_ids}
        index = library.scan(folder, skip_tags=taken)
        adopted = 0
        for r in need:
            p = library.find_file(index, {"id": r["vid"], "title": r["title"], "channel": r["channel"],
                                          "yt_artist": r["yt_artist"], "yt_track": r["yt_track"]}, template, taken)
            if p:
                taken.add(p)
                library.adopt(r["id"], p)
                item_event(r["id"])
                adopted += 1
                if r["id"] in missing_ids:
                    missing_ids.discard(r["id"])
                    ctx.log(f"Found moved file for {r['title']}: {p}")
        if adopted:
            ctx.inc("adopted", adopted)
            ctx.log(f"Linked {adopted} existing files in {folder}")
        redl = pl["redownload_missing"] if redownload is None else redownload
        for mid in missing_ids:
            if redl:
                set_item(mid, status="pending", error="File was missing, downloading again", error_kind=None,
                         file_path=None, attempts=0)
            else:
                set_item(mid, status="missing", error="File is missing on disk", file_path=None)

    def _todo(self, pl: dict, acc: dict | None) -> list[dict]:
        max_att = int(settings_store.get("max_attempts") or 3)
        return db.rows(
            "SELECT i.*, v.title AS v_title FROM items i JOIN videos v ON v.id = i.video_id "
            "WHERE i.playlist_id = ? AND i.removed_at IS NULL AND ("
            "  i.status IN ('pending', 'queued') OR "
            "  (i.status = 'failed' AND i.error_kind IN ('transient', 'blocked', 'proxy') AND i.attempts < ?) OR "
            "  (i.status = 'failed' AND i.error_kind = 'auth' AND ?)"
            ") ORDER BY i.position", [pl["id"], max_att * 3, int(bool(acc))])

    async def _download_all(self, ctx: Ctx, pl: dict, acc: dict | None, items: list[dict],
                            force: bool = False) -> None:
        if not items:
            ctx.log("Nothing to download")
            return
        await asyncio.to_thread(library.check_folder, pl["folder"])
        ctx.stats["to_download"] = len(items)
        ctx.stats["_queued"] = [it["id"] for it in items]

        def mark_queued():
            for it in items:
                set_item(it["id"], status="queued")
        await asyncio.to_thread(mark_queued)
        ctx.set_phase("downloading")
        ctx.log(f"Downloading {len(items)} tracks to {pl['folder']}")
        await asyncio.gather(*(self._download_one(ctx, pl, acc, it, force) for it in items))

    async def _download_one(self, ctx: Ctx, pl: dict, acc: dict | None, it: dict, force: bool) -> None:
        async with self.slots:
            await self._download_inner(ctx, pl, acc, it, force)

    async def _download_inner(self, ctx: Ctx, pl: dict, acc: dict | None, it: dict, force: bool) -> None:
        s = settings_store.get_all()
        max_att = max(1, int(s["max_attempts"] or 3))
        title = it.get("v_title") or it["video_id"]
        url = f"https://www.youtube.com/watch?v={it['video_id']}"
        live = {"item_id": it["id"], "title": title, "pct": 0, "speed": None, "eta": None, "stage": "starting"}
        ctx.items[it["id"]] = live
        bus.publish("progress", {"job_id": ctx.id, **live})
        set_item(it["id"], status="downloading")
        tried: set[int] = set()
        old_path = it.get("file_path")
        last_pub = [0.0]

        def on_line(stream: str, line: str) -> None:
            if line.startswith(ytdlp.PROG):
                try:
                    p = json.loads(line[len(ytdlp.PROG):])
                except ValueError:
                    return
                total = p.get("total_bytes") or p.get("total_bytes_estimate")
                if total:
                    live["pct"] = round(100 * (p.get("downloaded_bytes") or 0) / total, 1)
                live.update(speed=p.get("speed"), eta=p.get("eta"), stage="downloading", size=total)
                if time.time() - last_pub[0] > 0.7:
                    last_pub[0] = time.time()
                    bus.publish("progress", {"job_id": ctx.id, **live})
                return
            if line.startswith(ytdlp.DONE):
                return
            for tag, stage in (("[ExtractAudio]", "converting"), ("[EmbedThumbnail]", "adding cover"),
                               ("[Metadata]", "writing tags"), ("[SponsorBlock]", "cutting")):
                if tag in line:
                    live["stage"] = stage
                    bus.publish("progress", {"job_id": ctx.id, **live})
            if stream == "stderr" or line.startswith(("[download] Destination", "ERROR", "WARNING")) \
                    or "has already been downloaded" in line:
                ctx.log(f"[{it['video_id']}] {line}")

        try:
            for attempt in range(1, max_att + 1):
                proxy = proxies.select(pl, acc, tried)
                ctx.log(f"[{it['video_id']}] {title}: attempt {attempt}" +
                        (f" via proxy #{proxy['id']}" if proxy else ""))
                async with accounts.cookies_file(acc) as cf:
                    args = ytdlp.base_args(cf, proxy and proxy["url"]) + ytdlp.download_args(s, pl["folder"], pl["options"])
                    if force:
                        args.append("--force-overwrites")
                    res = await ytdlp.run(args + ["--", url], on_line=on_line, timeout=3 * 3600)
                path = None
                if res.done:
                    path = res.done[-1].get("filepath")
                if res.returncode == 0 and path and library.file_ok(path):
                    proxies.report(proxy and proxy["id"], True)
                    await asyncio.to_thread(self._finalize, ctx, it, res.done[-1], path, old_path)
                    return
                err = res.error_text or ("yt-dlp finished but no file was written" if res.returncode == 0
                                         else f"yt-dlp exited with {res.returncode}")
                kind = ytdlp.classify(err, bool(proxy))
                proxies.report(proxy and proxy["id"], False, err, blocked=kind == "blocked")
                db.execute("UPDATE items SET attempts = attempts + 1, error = ?, error_kind = ? WHERE id = ?",
                           [err[:2000], kind, it["id"]])
                ctx.log(f"[{it['video_id']}] failed ({kind}): {err.splitlines()[-1] if err else ''}")
                if kind == "unavailable":
                    set_item(it["id"], status="unavailable")
                    ctx.inc("unavailable")
                    return
                if kind == "auth":
                    if acc and "cookies" in err.lower():
                        accounts.mark(acc["id"], "expired", err)
                    set_item(it["id"], status="failed")
                    ctx.inc("failed")
                    return
                if proxy and kind in ("blocked", "proxy"):
                    tried.add(proxy["id"])
                    continue
                if attempt < max_att:
                    wait = (60 if kind == "blocked" else 5) * attempt
                    live.update(stage=f"retrying in {wait}s")
                    bus.publish("progress", {"job_id": ctx.id, **live})
                    await asyncio.sleep(wait)
            set_item(it["id"], status="failed")
            ctx.inc("failed")
        finally:
            ctx.items.pop(it["id"], None)
            ctx.publish()

    def _finalize(self, ctx: Ctx, it: dict, info: dict, path: str, old_path: str | None) -> None:
        db.execute(
            "UPDATE videos SET title = COALESCE(?, title), channel = COALESCE(?, channel), "
            "description = COALESCE(?, description), yt_artist = ?, yt_track = ?, yt_album = ?, "
            "yt_release_year = ?, upload_date = COALESCE(?, upload_date), duration = COALESCE(?, duration), "
            "thumbnail = COALESCE(?, thumbnail), updated_at = ? WHERE id = ?",
            [info.get("title"), info.get("channel") or info.get("uploader"), info.get("description"),
             info.get("artist"), info.get("track"), info.get("album"), info.get("release_year"),
             info.get("upload_date"), info.get("duration"), info.get("thumbnail"), db.now(), it["video_id"]])
        if old_path and old_path != path and os.path.exists(old_path):
            try:
                if Path(old_path).suffix == Path(path).suffix:
                    os.replace(path, old_path)  # keep a file name the user chose
                    path = old_path
                else:
                    os.remove(old_path)
            except OSError:
                pass
        override = db.jload(it.get("meta_override"), {}) or {}
        if override:
            try:
                tags.write(path, override)
                ctx.log(f"[{it['video_id']}] re-applied {len(override)} edited tags")
            except Exception as e:
                ctx.log(f"[{it['video_id']}] could not re-apply edited tags: {e}")
        meta = library.read_meta(path)
        set_item(it["id"], status="done", file_path=path, file_size=os.path.getsize(path), error=None,
                 error_kind=None, downloaded_at=db.now(), meta=db.jdump(meta) if meta else None)
        ctx.inc("downloaded")
        ctx.log(f"[{it['video_id']}] saved {Path(path).name}")

    def _handle_removed(self, ctx: Ctx, pl: dict) -> None:
        if pl["on_removed"] != "delete":
            return
        rows = db.rows("SELECT id, file_path FROM items WHERE playlist_id = ? AND removed_at IS NOT NULL "
                       "AND status = 'done'", [pl["id"]])
        for r in rows:
            if r["file_path"] and os.path.exists(r["file_path"]):
                try:
                    os.remove(r["file_path"])
                    ctx.log(f"Deleted {Path(r['file_path']).name} (removed from playlist)")
                except OSError as e:
                    ctx.log(f"Could not delete {r['file_path']}: {e}")
                    continue
            set_item(r["id"], status="deleted", file_path=None)
            ctx.inc("deleted")

    # ------------------------------------------------------------ handlers

    async def _sync(self, ctx: Ctx, job: dict, params: dict) -> None:
        pl = self._playlist(job["playlist_id"])
        acc = accounts.get(pl["account_id"])
        await self._require_storage(pl)
        ctx.set_phase("fetching playlist")
        data = await self._enumerate(ctx, pl, acc)
        if not pl["folder"]:
            folder = await asyncio.to_thread(library.check_folder,
                                             library.default_folder(data.get("title") or pl["url"]))
            db.update("playlists", pl["id"], {"folder": folder})
            ctx.log(f"Download folder: {folder}")
            await asyncio.to_thread(storage.write_marker, pl, folder)
        await asyncio.to_thread(self._upsert, ctx, pl, data)
        pl = self._playlist(pl["id"])
        if pl["title"] and ctx.job["title"] != f"Sync {pl['title']}":
            ctx.job["title"] = f"Sync {pl['title']}"
            db.update("jobs", ctx.id, {"title": ctx.job["title"]})
        bus.publish("playlist", {"id": pl["id"]})
        await self._require_storage(pl)  # the drive can go away while the playlist was fetched
        await asyncio.to_thread(library.check_folder, pl["folder"])
        ctx.set_phase("checking files")
        await asyncio.to_thread(self._verify_files, ctx, pl, params.get("redownload"))
        await self._download_all(ctx, pl, acc, await asyncio.to_thread(self._todo, pl, acc))
        await asyncio.to_thread(self._handle_removed, ctx, pl)
        ctx.set_phase("finished")
        ctx.log("Sync finished: " + ", ".join(f"{k} {v}" for k, v in ctx.stats.items() if not k.startswith("_")))

    async def _download(self, ctx: Ctx, job: dict, params: dict) -> None:
        pl = self._playlist(job["playlist_id"])
        acc = accounts.get(pl["account_id"])
        ids = [int(i) for i in params.get("item_ids", [])]
        if not ids:
            raise JobError("No items selected")
        marks = ",".join("?" for _ in ids)
        items = db.rows(f"SELECT i.*, v.title AS v_title FROM items i JOIN videos v ON v.id = i.video_id "
                        f"WHERE i.playlist_id = ? AND i.id IN ({marks}) ORDER BY i.position", [pl["id"], *ids])
        force = bool(params.get("force"))
        if not pl["folder"]:
            raise JobError("Playlist has no folder yet, run a sync first")
        await self._require_storage(pl)
        for it in items:
            db.update("items", it["id"], {"attempts": 0})
        await self._download_all(ctx, pl, acc, items, force=force)
        ctx.set_phase("finished")

    async def _transfer(self, ctx: Ctx, job: dict, params: dict) -> None:
        await transfers.run(ctx, job["transfer_id"])
        ctx.set_phase("finished")
        bus.publish("transfer", {"id": job["transfer_id"]})

    async def _verify(self, ctx: Ctx, job: dict, params: dict) -> None:
        ids = [job["playlist_id"]] if job["playlist_id"] else \
            [r["id"] for r in db.rows("SELECT id FROM playlists WHERE folder IS NOT NULL")]
        redownload = params.get("redownload")
        offline = []
        for pid in ids:
            pl = self._playlist(pid)
            if not pl["folder"]:
                continue
            if not await asyncio.to_thread(storage.check, pl):
                offline.append(pl["title"] or pl["url"])
                ctx.log(f"Skipped {pl['title'] or pl['url']}: folder marker missing in {pl['folder']}")
                continue
            ctx.log(f"Checking {pl['title'] or pl['url']}")
            ctx.set_phase("checking files")
            # verify never downloads; missing files get 'pending' so the next sync fetches them
            await asyncio.to_thread(self._verify_files, ctx, pl, redownload)
            bus.publish("playlist", {"id": pid})
        if offline and job["playlist_id"]:
            raise StorageOffline(storage.offline_message(self._playlist(job["playlist_id"])))
        if offline:
            ctx.inc("offline", len(offline))
        ctx.set_phase("finished")


manager = Manager()

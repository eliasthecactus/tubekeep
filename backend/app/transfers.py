"""Playlist transfer and sync between music services.

Every track of both playlists is an item (side 'a' or 'b'). A link is item.match_id = the other side's track id.
Rules:
  * additions flow A -> B (and B -> A when two-way) for tracks that were never linked
  * a linked track whose counterpart disappeared is not added again (the user removed it on purpose)
  * removals flow only when sync_removals is on, and only for tracks removed since the last run
"""
import asyncio

from . import db, matching, providers, settings_store
from .providers import LIKED, AuthError, Provider

ADD_BATCH = 50


class TransferError(Exception):
    pass


def get(tid: int) -> dict:
    t = db.row("SELECT * FROM transfers WHERE id = ?", [tid])
    if not t:
        raise TransferError("Transfer was deleted")
    return t


def _items(tid: int, side: str) -> dict[str, dict]:
    out = {}
    for r in db.rows("SELECT * FROM transfer_items WHERE transfer_id = ? AND side = ?", [tid, side]):
        r["meta"] = db.jload(r["meta"], {})
        r["match_meta"] = db.jload(r["match_meta"], None)
        out[r["track_id"]] = r
    return out


def _sync_side(tid: int, side: str, tracks: list[dict]) -> list[dict]:
    """Store the current content of one side. Returns items that disappeared since the last run."""
    existing = _items(tid, side)
    now = db.now()
    seen: set[str] = set()
    with db.tx() as c:
        for pos, t in enumerate(tracks):
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            ex = existing.get(t["id"])
            if ex:
                c.execute("UPDATE transfer_items SET meta = ?, position = ?, present = 1, updated_at = ? WHERE id = ?",
                          [db.jdump(t), pos, now, ex["id"]])
            else:
                c.execute("INSERT INTO transfer_items (transfer_id, side, track_id, meta, position, present, status, "
                          "first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, 'new', ?, ?)",
                          [tid, side, t["id"], db.jdump(t), pos, now, now])
        gone = [ex for k, ex in existing.items() if k not in seen and ex["present"]]
        for ex in gone:
            c.execute("UPDATE transfer_items SET present = 0, updated_at = ? WHERE id = ?", [now, ex["id"]])
    return gone


def _set(item_id: int, **fields) -> None:
    fields["updated_at"] = db.now()
    for k in ("match_meta", "candidates", "meta"):
        if k in fields and not isinstance(fields[k], (str, type(None))):
            fields[k] = db.jdump(fields[k])
    db.update("transfer_items", item_id, fields)


def _link(tid: int, src_item: dict, dst_side: str, dst_track: dict, status: str, score: float | None) -> None:
    _set(src_item["id"], match_id=dst_track["id"], match_meta=dst_track, score=score, status=status, candidates=None)
    now = db.now()
    db.execute("INSERT INTO transfer_items (transfer_id, side, track_id, meta, present, status, match_id, match_meta, "
               "score, first_seen_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(transfer_id, side, track_id) DO UPDATE SET match_id = excluded.match_id, "
               "match_meta = excluded.match_meta, present = 1, "
               "status = CASE WHEN transfer_items.status IN ('new','not_found','review') THEN excluded.status "
               "ELSE transfer_items.status END, updated_at = excluded.updated_at",
               [tid, dst_side, dst_track["id"], db.jdump(dst_track), "synced" if status == "added" else "matched",
                src_item["track_id"], db.jdump(src_item["meta"]), score, now, now])


async def _remove_pass(ctx, t: dict, src_side: str, dst_side: str, gone: list[dict], dst: Provider,
                       dst_pid: str, stats: dict) -> None:
    dst_items = _items(t["id"], dst_side)
    victims = []
    for it in gone:
        _set(it["id"], status="removed", note="removed from this playlist")
        d = dst_items.get(it["match_id"] or "")
        if d and d["present"]:
            victims.append(d)
    if not victims:
        return
    if not dst.can_remove:
        for d in victims:
            _set(d["id"], note=f"{dst.label} cannot remove tracks through its API, remove it by hand")
        ctx.log(f"{len(victims)} tracks should be removed from {dst.label}, but its API does not allow removing")
        return
    ctx.set_phase(f"removing {len(victims)} from {dst.label}")
    await dst.remove_tracks(dst_pid, [d["meta"] for d in victims])
    for d in victims:
        _set(d["id"], present=0, status="removed", note="removed because it was removed on the other side")
    stats[f"removed_from_{dst_side}"] = stats.get(f"removed_from_{dst_side}", 0) + len(victims)
    ctx.log(f"Removed {len(victims)} tracks from {dst.label}")


async def _add_pass(ctx, t: dict, src_side: str, dst_side: str, src: Provider, dst: Provider, dst_pid: str,
                    stats: dict) -> None:
    auto = float(settings_store.get("match_auto_threshold") or 0.82)
    review = float(settings_store.get("match_review_threshold") or 0.6)
    src_items = sorted((i for i in _items(t["id"], src_side).values() if i["present"]),
                       key=lambda i: i["position"] if i["position"] is not None else 1e9)
    dst_items = _items(t["id"], dst_side)
    linked_dst = {i["match_id"] for i in _items(t["id"], src_side).values() if i["match_id"]}
    pool = [d["meta"] for d in dst_items.values() if d["present"] and not d["match_id"] and d["track_id"] not in linked_dst]

    to_add: list[tuple[dict, dict, float]] = []
    search_queue: list[dict] = []
    for it in src_items:
        if it["status"] == "skipped" or (it["status"] == "synced" and it["match_id"]):
            continue
        if it["match_id"]:
            d = dst_items.get(it["match_id"])
            if d and d["present"]:
                if it["status"] not in ("added", "matched", "synced"):
                    _set(it["id"], status="matched")
                continue
            if d and not d["present"]:
                if it["status"] != "gone":
                    _set(it["id"], status="gone", note=f"removed on {dst.label}, not added again")
                continue
            # linked (e.g. picked by hand) but not on the target yet
            to_add.append((it, it["match_meta"] or {"id": it["match_id"], "title": "", "artists": [], "extra": {}},
                           it["score"] or 1.0))
            continue
        c, sc = matching.best_local(it["meta"], pool)
        if c and sc >= auto:
            pool.remove(c)
            _link(t["id"], it, dst_side, c, "matched", sc)
            stats["matched"] = stats.get("matched", 0) + 1
            continue
        search_queue.append(it)

    if search_queue:
        ctx.set_phase(f"matching {len(search_queue)} tracks on {dst.label}")
        sem = asyncio.Semaphore(4)
        done = [0]

        async def look(it):
            async with sem:
                try:
                    res = await matching.find(dst, it["meta"], src.name)
                except AuthError:
                    raise
                except Exception as e:
                    res = {"best": None, "score": 0, "candidates": [], "error": str(e)}
                done[0] += 1
                if done[0] % 10 == 0:
                    ctx.stats["progress"] = f"{done[0]}/{len(search_queue)}"
                    ctx.publish()
                return it, res
        results = await asyncio.gather(*(look(i) for i in search_queue))
        dst_present_ids = {d["track_id"] for d in dst_items.values() if d["present"]}
        for it, res in results:
            best, sc = res["best"], res["score"]
            if best and sc >= auto:
                if best["id"] in dst_present_ids:
                    _link(t["id"], it, dst_side, best, "matched", sc)
                    stats["matched"] = stats.get("matched", 0) + 1
                else:
                    to_add.append((it, best, sc))
            elif best and sc >= review:
                if t["add_uncertain"]:
                    to_add.append((it, best, sc))
                else:
                    _set(it["id"], status="review", score=sc, candidates=res["candidates"], note=None)
                    stats["review"] = stats.get("review", 0) + 1
            else:
                _set(it["id"], status="not_found", score=sc or None, candidates=res["candidates"],
                     note=res.get("error"))
                stats["not_found"] = stats.get("not_found", 0) + 1

    # add in playlist order, without duplicates
    present_ids = {d["track_id"] for d in dst_items.values() if d["present"]}
    unique: list[tuple[dict, dict, float]] = []
    seen: set[str] = set()
    for it, m, sc in to_add:
        if m["id"] in present_ids:  # already on the target, just link it
            _link(t["id"], it, dst_side, m, "matched", sc)
        elif m["id"] in seen:
            _set(it["id"], status="matched", match_id=m["id"], match_meta=m, score=sc, note="same song as another track")
        else:
            seen.add(m["id"])
            unique.append((it, m, sc))
    to_add = unique
    if not to_add:
        return
    ctx.set_phase(f"adding {len(to_add)} tracks to {dst.label}")
    for i in range(0, len(to_add), ADD_BATCH):
        batch = to_add[i:i + ADD_BATCH]
        await dst.add_tracks(dst_pid, [b[1] for b in batch])
        for it, m, sc in batch:
            _link(t["id"], it, dst_side, m, "added", sc)
        stats[f"added_to_{dst_side}"] = stats.get(f"added_to_{dst_side}", 0) + len(batch)
        ctx.log(f"Added {min(i + ADD_BATCH, len(to_add))}/{len(to_add)} tracks to {dst.label}")


def _download_link(t: dict, a: Provider, b: Provider, added: int) -> str | None:
    """Keep a Tubekeep download playlist for the YouTube side."""
    from . import scheduler
    from .jobs import manager
    side = b if b.name == "youtube" else a if a.name == "youtube" else None
    if not side:
        return None
    pid = t["b_playlist_id"] if side is b else t["a_playlist_id"]
    url = "https://www.youtube.com/playlist?list=" + ("LL" if pid == LIKED else pid)
    pl = db.row("SELECT id FROM playlists WHERE id = ?", [t["download_playlist_id"]]) if t["download_playlist_id"] else None
    if not pl:
        pl = db.row("SELECT id FROM playlists WHERE url = ?", [url])
    if not pl:
        schedule = settings_store.get("default_schedule") or ""
        new_id = db.insert("playlists", {
            "url": url, "account_id": side.conn.get("account_id"), "schedule": schedule,
            "next_run_at": scheduler.next_run(schedule), "options": "{}", "created_at": db.now()})
        pl = {"id": new_id}
        added = 1
    db.update("transfers", t["id"], {"download_playlist_id": pl["id"]})
    if added:
        manager.enqueue("sync", pl["id"], trigger="transfer")
    return url


async def run(ctx, transfer_id: int) -> None:
    t = get(transfer_id)
    a, b = providers.load(t["a_conn_id"]), providers.load(t["b_conn_id"])
    stats: dict = {}
    ctx.stats = stats
    try:
        if not t["b_playlist_id"]:
            name = t["b_playlist_name"] or t["a_playlist_name"] or "Tubekeep transfer"
            ctx.set_phase(f"creating playlist on {b.label}")
            pid = await b.create_playlist(name, f"Copied from {a.label} by Tubekeep")
            db.update("transfers", t["id"], {"b_playlist_id": pid, "b_playlist_name": name})
            ctx.log(f"Created playlist '{name}' on {b.label}")
            t = get(transfer_id)
        ctx.set_phase(f"reading {a.label}")
        a_tracks = await a.get_tracks(t["a_playlist_id"])
        ctx.set_phase(f"reading {b.label}")
        b_tracks = await b.get_tracks(t["b_playlist_id"])
        stats.update(a_total=len(a_tracks), b_total=len(b_tracks))
        ctx.log(f"{a.label}: {len(a_tracks)} tracks, {b.label}: {len(b_tracks)} tracks")
        first = t["last_run_at"] is None
        gone_a = _sync_side(t["id"], "a", a_tracks)
        gone_b = _sync_side(t["id"], "b", b_tracks)

        if t["sync_removals"] and not first:
            await _remove_pass(ctx, t, "a", "b", gone_a, b, t["b_playlist_id"], stats)
            if t["two_way"]:
                await _remove_pass(ctx, t, "b", "a", gone_b, a, t["a_playlist_id"], stats)

        await _add_pass(ctx, t, "a", "b", a, b, t["b_playlist_id"], stats)
        if t["two_way"]:
            await _add_pass(ctx, t, "b", "a", b, a, t["a_playlist_id"], stats)

        if t["download"]:
            url = _download_link(t, a, b, stats.get("added_to_b", 0) + stats.get("added_to_a", 0))
            if url:
                ctx.log(f"Download sync started for {url}")
        stats.pop("progress", None)
        status = "review" if stats.get("review") else "ok"
        db.update("transfers", t["id"], {"last_run_at": db.now(), "last_status": status, "last_error": None,
                                         "stats": db.jdump(stats)})
        ctx.log("Done: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in stats.items()))
    except AuthError as e:
        for p in (a, b):
            try:
                if p.label in str(e) or p.name in str(e).lower():
                    providers.mark(p.conn["id"], "expired", str(e))
            except Exception:
                pass
        db.update("transfers", t["id"], {"last_run_at": db.now(), "last_status": "failed", "last_error": str(e),
                                         "stats": db.jdump(stats)})
        raise TransferError(str(e)) from e
    except Exception as e:
        db.update("transfers", t["id"], {"last_run_at": db.now(), "last_status": "failed", "last_error": str(e),
                                         "stats": db.jdump(stats)})
        raise
    finally:
        providers.save_creds(a)
        providers.save_creds(b)

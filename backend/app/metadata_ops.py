"""Bulk metadata operations: preview (pure) and apply (writes files)."""
import os
import re
from pathlib import Path

from . import db, library, tags

JUNK = [
    r"\(\s*official\s*(music\s*)?(video|audio|lyric video|visuali[sz]er|clip)\s*\)",
    r"\[\s*official\s*(music\s*)?(video|audio|lyric video|visuali[sz]er|clip)\s*\]",
    r"\(\s*(lyrics?|lyric video|audio|hd|hq|4k|visuali[sz]er|videoclip|video clip|official)\s*\)",
    r"\[\s*(lyrics?|lyric video|audio|hd|hq|4k|visuali[sz]er|videoclip|video clip|official)\s*\]",
    r"\(\s*(official\s*)?(m/?v)\s*\)", r"\[\s*(official\s*)?(m/?v)\s*\]",
    r"\s*\|\s*official.*$", r"\s*-\s*topic$",
]


def context(item: dict) -> dict:
    """Values usable as {placeholders} in set/rename templates."""
    m = item.get("_meta") or {}
    return {
        **{k: m.get(k, "") for k in tags.FIELDS},
        "yt_title": item.get("v_title") or "", "channel": item.get("v_channel") or "",
        "playlist": item.get("p_title") or "", "position": item.get("position") or "",
        "id": item.get("video_id") or "", "upload_year": (item.get("v_upload_date") or "")[:4],
        "yt_artist": item.get("yt_artist") or "", "yt_album": item.get("yt_album") or "",
        "yt_track": item.get("yt_track") or "",
    }


def fill(template: str, ctx: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m.group(1), m.group(0))), template)


def _title_case(s: str) -> str:
    small = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "feat"}
    words = s.split(" ")
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if 0 < i < len(words) - 1 and lw in small:
            out.append(lw)
        elif w.isupper() and len(w) <= 4 and i > 0:
            out.append(w)  # keep acronyms like "DJ", "UK"
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def pattern_regex(pattern: str) -> re.Pattern:
    """'{artist} - {title}' -> regex with named groups. {_} ignores a part."""
    parts = re.split(r"(\{\w+\})", pattern)
    rx, n = "", 0
    for p in parts:
        m = re.fullmatch(r"\{(\w+)\}", p)
        if m:
            name = m.group(1)
            if name == "_" or name not in tags.FIELDS:
                n += 1
                rx += f"(?P<_skip{n}>.+?)"
            else:
                rx += f"(?P<{name}>.+?)"
        else:
            rx += re.escape(p)
    return re.compile("^\\s*" + rx + "\\s*$", re.S)


def apply_ops(meta: dict, ops: list[dict], ctx: dict, index: int, total: int) -> tuple[dict, str | None]:
    """Returns (new_meta, rename_to_basename_or_None)."""
    m = dict(meta)
    rename = None
    for op in ops:
        kind = op.get("op")
        fields = op.get("fields") or ([op["field"]] if op.get("field") else [])
        c = {**ctx, **m}
        if kind == "set":
            for f in fields:
                m[f] = fill(op.get("value", ""), c)
        elif kind == "clear":
            for f in fields:
                m[f] = ""
        elif kind == "copy":
            m[op["to"]] = m.get(op["from"], "") if op["from"] in tags.FIELDS else str(c.get(op["from"], ""))
        elif kind == "replace":
            flags = 0 if op.get("case_sensitive") else re.I
            find = op.get("find", "")
            if not find:
                continue
            for f in fields:
                pat = find if op.get("regex") else re.escape(find)
                try:
                    m[f] = re.sub(pat, op.get("replace", ""), m.get(f, ""), flags=flags)
                except re.error as e:
                    raise ValueError(f"Invalid regex: {e}") from e
        elif kind == "parse":
            src = op.get("source", "title")
            text = m.get(src, "") if src in tags.FIELDS else str(c.get(src, ""))
            mt = pattern_regex(op.get("pattern", "")).match(text or "")
            if mt:
                for k, v in mt.groupdict().items():
                    if not k.startswith("_skip"):
                        m[k] = v.strip()
        elif kind == "case":
            mode = op.get("mode", "title")
            for f in fields:
                v = m.get(f, "")
                m[f] = {"upper": v.upper(), "lower": v.lower(), "title": _title_case(v),
                        "sentence": v[:1].upper() + v[1:].lower()}.get(mode, v)
        elif kind == "clean":
            for f in fields or ["title"]:
                v = m.get(f, "")
                for j in JUNK:
                    v = re.sub(j, "", v, flags=re.I)
                v = re.sub(r"\s{2,}", " ", v).strip(" -–|")
                m[f] = v
        elif kind == "trim":
            for f in tags.FIELDS:
                if f != "lyrics" and m.get(f):
                    m[f] = re.sub(r"\s{2,}", " ", m[f]).strip()
        elif kind == "number":
            start = int(op.get("start") or 1)
            m["track"] = str(start + index)
            if op.get("total", True):
                m["track_total"] = str(total + start - 1)
        elif kind == "rename":
            rename = library.safe_filename(fill(op.get("template", "{artist} - {title}"), {**ctx, **m}))
        else:
            raise ValueError(f"Unknown operation {kind!r}")
    return m, rename


def load_items(item_ids: list[int]) -> list[dict]:
    if not item_ids:
        return []
    marks = ",".join("?" for _ in item_ids)
    items = db.rows(
        f"SELECT i.*, v.title AS v_title, v.channel AS v_channel, v.upload_date AS v_upload_date, "
        f"v.yt_artist, v.yt_album, v.yt_track, v.description AS v_description, p.title AS p_title "
        f"FROM items i JOIN videos v ON v.id = i.video_id JOIN playlists p ON p.id = i.playlist_id "
        f"WHERE i.id IN ({marks}) ORDER BY p.id, i.position", item_ids)
    for it in items:
        it["_meta"] = db.jload(it["meta"], None) or {k: "" for k in tags.FIELDS}
    return items


def preview(item_ids: list[int], ops: list[dict]) -> list[dict]:
    items = load_items(item_ids)
    out = []
    for i, it in enumerate(items):
        before = {k: it["_meta"].get(k, "") or "" for k in tags.FIELDS}
        after, rename = apply_ops(before, ops, context(it), i, len(items))
        changed = [k for k in tags.FIELDS if (after.get(k) or "") != (before.get(k) or "")]
        new_name = None
        if rename and it.get("file_path"):
            new_name = rename + Path(it["file_path"]).suffix
            if new_name == Path(it["file_path"]).name:
                new_name = None
        out.append({"item_id": it["id"], "title": it["v_title"], "file": it.get("file_path"),
                    "before": before, "after": after, "changed": changed, "rename_to": new_name})
    return out


def apply(changes: list[dict]) -> list[dict]:
    """changes: [{item_id, fields: {field: value}, rename_to?: 'New name.mp3'}]"""
    results = []
    by_id = {it["id"]: it for it in load_items([c["item_id"] for c in changes])}
    for ch in changes:
        it = by_id.get(ch["item_id"])
        if not it:
            results.append({"item_id": ch["item_id"], "ok": False, "error": "Item not found"})
            continue
        path = it.get("file_path")
        fields = {k: v for k, v in (ch.get("fields") or {}).items() if k in tags.FIELDS}
        try:
            if not library.file_ok(path):
                raise ValueError("File is missing, download it first")
            if fields:
                tags.write(path, fields)
            new_path = path
            if ch.get("rename_to"):
                target = Path(path).with_name(library.safe_filename(Path(ch["rename_to"]).stem) + Path(path).suffix)
                if target.exists() and str(target) != path:
                    raise ValueError(f"{target.name} already exists")
                os.rename(path, target)
                new_path = str(target)
            meta = library.read_meta(new_path) or {**it["_meta"], **fields}
            override = {**(db.jload(it["meta_override"], {}) or {}), **fields}
            db.update("items", it["id"], {"meta": db.jdump(meta), "meta_override": db.jdump(override),
                                          "file_path": new_path, "updated_at": db.now()})
            results.append({"item_id": it["id"], "ok": True, "meta": meta, "file_path": new_path})
        except Exception as e:
            results.append({"item_id": it["id"], "ok": False, "error": str(e)})
    return results

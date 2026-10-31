"""Find the same song on another service: ISRC first, then search + fuzzy scoring."""
import re
import unicodedata

from rapidfuzz import fuzz

from . import db, settings_store
from .providers import Provider

VERSION_WORDS = ["remix", "live", "acoustic", "instrumental", "karaoke", "cover", "sped up", "slowed", "nightcore",
                 "8d", "edit", "demo", "remaster", "extended", "mix", "version", "reprise", "unplugged", "orchestral"]
NOISE = re.compile(r"\((?:official|lyrics?|audio|video|visuali[sz]er|hd|hq|4k|clip|explicit|clean|mv)[^)]*\)|"
                   r"\[(?:official|lyrics?|audio|video|visuali[sz]er|hd|hq|4k|clip|explicit|clean|mv)[^\]]*\]|"
                   r"\b(?:official (?:music )?video|official audio|lyric video|with lyrics)\b", re.I)
FEAT = re.compile(r"[\(\[]?\s*\b(?:feat|ft|featuring|with)\b\.?\s+[^\)\]\-]*[\)\]]?", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode() or (s or "")
    s = s.lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def clean_title(t: str) -> str:
    t = NOISE.sub(" ", t or "")
    t = FEAT.sub(" ", t)
    t = re.sub(r"\s+-\s+(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?.*$", "", t, flags=re.I)
    t = re.sub(r"\((?:\d{4}\s+)?remaster(?:ed)?[^)]*\)", "", t, flags=re.I)
    return norm(t)


def versions(t: str) -> set[str]:
    n = norm(t)
    found = {w for w in VERSION_WORDS if re.search(rf"\b{re.escape(w)}\b", n)}
    return found - {"remaster", "version", "edit", "mix", "extended"}


def split_youtube_title(cand: dict) -> tuple[str, list[str]]:
    """YouTube videos are often titled 'Artist - Title' and uploaded by the label."""
    title = cand["title"]
    if cand.get("extra", {}).get("resultType") == "video" and " - " in title:
        a, _, t = title.partition(" - ")
        return t, [a] + cand["artists"]
    return title, cand["artists"]


def score(src: dict, cand: dict) -> float:
    if src.get("isrc") and cand.get("isrc") and src["isrc"] == cand["isrc"]:
        return 1.0
    ctitle, cartists = split_youtube_title(cand)
    st, ct = clean_title(src["title"]), clean_title(ctitle)
    t = fuzz.token_sort_ratio(st, ct) / 100
    if st and ct and (st in ct or ct in st) and min(len(st), len(ct)) >= 4:
        t = max(t, 0.92)
    sa = [norm(a) for a in src["artists"]] or [""]
    ca = [norm(a) for a in cartists] or [""]
    a = max(fuzz.token_set_ratio(x, y) for x in sa for y in ca) / 100
    if a < 0.7:  # artist mentioned in the other title ("Artist - Song" videos, "feat." credits)
        if any(x and x in norm(cand["title"]) for x in sa) or any(y and y in norm(src["title"]) for y in ca):
            a = max(a, 0.85)
        a = max(a, fuzz.token_set_ratio(" ".join(sa), " ".join(ca)) / 100 * 0.9)
    full = fuzz.token_set_ratio(f"{' '.join(sa)} {st}", f"{' '.join(ca)} {ct}") / 100
    d = 0.75
    if src.get("duration") and cand.get("duration"):
        diff = abs(src["duration"] - cand["duration"])
        d = 1.0 if diff <= 3 else 0.9 if diff <= 8 else 0.6 if diff <= 20 else 0.25 if diff <= 60 else 0.0
    penalty = 0.25 if versions(src["title"]) != versions(cand["title"]) else 0.0
    base = 0.4 * t + 0.3 * a + 0.3 * full
    return round(max(0.0, min(1.0, base * 0.8 + d * 0.2 - penalty)), 3)


def best_local(src: dict, pool: list[dict]) -> tuple[dict | None, float]:
    best, bs = None, 0.0
    for c in pool:
        if src.get("isrc") and c.get("isrc") == src["isrc"]:
            return c, 1.0
        s = score(src, c)
        if s > bs:
            best, bs = c, s
    return best, bs


async def find(target: Provider, src: dict, src_provider: str) -> dict:
    """Returns {"best": track|None, "score": float, "candidates": [track+score]} (cached)."""
    cache = db.row("SELECT * FROM match_cache WHERE src_provider = ? AND src_id = ? AND dst_provider = ?",
                   [src_provider, src["id"], target.name])
    if cache and cache["dst_id"] and (cache["score"] or 0) >= settings_store.get("match_auto_threshold"):
        m = db.jload(cache["meta"], None)
        if m:
            return {"best": m, "score": cache["score"], "candidates": [{**m, "score": cache["score"]}], "cached": True}
    if cache and not cache["dst_id"] and db.now() - cache["checked_at"] < 7 * 86400:
        return {"best": None, "score": 0.0, "candidates": [], "cached": True}

    cands: list[dict] = []
    if src.get("isrc") and target.can_isrc:
        cands = await target.by_isrc(src["isrc"])
        for c in cands:
            c["isrc"] = c.get("isrc") or src["isrc"]
    if not cands or not any(score(src, c) >= 0.75 for c in cands):
        cands += await target.search_track(src)
    seen, scored = set(), []
    for c in cands:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        scored.append({**c, "score": score(src, c)})
    scored.sort(key=lambda c: c["score"], reverse=True)
    best = scored[0] if scored else None
    db.execute("INSERT INTO match_cache (src_provider, src_id, dst_provider, dst_id, score, meta, checked_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(src_provider, src_id, dst_provider) DO UPDATE SET "
               "dst_id = excluded.dst_id, score = excluded.score, meta = excluded.meta, checked_at = excluded.checked_at",
               [src_provider, src["id"], target.name, best and best["id"], best and best["score"],
                db.jdump(best) if best else None, db.now()])
    return {"best": best, "score": best["score"] if best else 0.0, "candidates": scored[:6]}


def remember_manual(src_provider: str, src_id: str, dst_provider: str, dst: dict) -> None:
    db.execute("INSERT INTO match_cache (src_provider, src_id, dst_provider, dst_id, score, meta, checked_at) "
               "VALUES (?, ?, ?, ?, 1.0, ?, ?) ON CONFLICT(src_provider, src_id, dst_provider) DO UPDATE SET "
               "dst_id = excluded.dst_id, score = 1.0, meta = excluded.meta, checked_at = excluded.checked_at",
               [src_provider, src_id, dst_provider, dst["id"], db.jdump(dst), db.now()])

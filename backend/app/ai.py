"""AI metadata suggestions through OpenRouter."""
import asyncio
import json
import re

import httpx

from . import metadata_ops, settings_store, tags

API = "https://openrouter.ai/api/v1"

SYSTEM = """You fix music metadata for audio files that were downloaded from YouTube.
You get a JSON list of tracks. Each has the YouTube title, channel, an excerpt of the description,
YouTube Music fields (yt_artist, yt_track, yt_album, yt_year) when available, and the current tags.
Follow the user's instruction. Use real, well-known facts only (official artist names, album titles,
release years). When you are not sure about a value, keep the current tag unchanged.
Never invent albums or years. Typical cleanups: split "Artist - Title", move "feat. X" into a consistent
form, strip "(Official Video)", "[Lyrics]", "HD" and similar noise, use the real artist instead of a
"VEVO" or "- Topic" channel name.
Answer with JSON only: {"tracks": [{"id": <id>, <field>: <value>, ...}]}.
Include every track id. Only include the fields you are allowed to change: FIELDS."""


def _track_payload(it: dict) -> dict:
    m = it["_meta"]
    return {
        "id": it["id"], "youtube_title": it.get("v_title"), "channel": it.get("v_channel"),
        "description": (it.get("v_description") or "")[:400], "yt_artist": it.get("yt_artist"),
        "yt_track": it.get("yt_track"), "yt_album": it.get("yt_album"),
        "yt_year": (it.get("v_upload_date") or "")[:4], "playlist": it.get("p_title"),
        "position": it.get("position"),
        "current": {k: m.get(k, "") for k in tags.FIELDS if k not in ("lyrics", "comment") and m.get(k)},
    }


def _parse(content: str) -> list[dict]:
    content = content.strip()
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError("Model did not return JSON")
    data = json.loads(m.group(0))
    return data.get("tracks") or []


async def _ask(client: httpx.AsyncClient, key: str, model: str, instruction: str, fields: list[str],
               batch: list[dict]) -> list[dict]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM.replace("FIELDS", ", ".join(fields))},
            {"role": "user", "content": f"Instruction: {instruction or 'Clean up and complete the tags.'}\n\n"
                                        f"Tracks:\n{json.dumps([_track_payload(i) for i in batch], ensure_ascii=False)}"},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    r = await client.post(f"{API}/chat/completions", json=body, headers={
        "Authorization": f"Bearer {key}", "X-Title": "Tubekeep", "HTTP-Referer": "https://github.com/eliasthecactus/tubekeep"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        raise ValueError(f"OpenRouter error {r.status_code}: {msg[:300]}")
    content = r.json()["choices"][0]["message"]["content"] or ""
    return _parse(content)


async def suggest(item_ids: list[int], instruction: str, fields: list[str] | None = None,
                  model: str | None = None) -> dict:
    key = settings_store.openrouter_key()
    if not key:
        raise ValueError("Add an OpenRouter API key in Settings first")
    s = settings_store.get_all()
    model = model or s["openrouter_model"]
    fields = [f for f in (fields or ["title", "artist", "album", "album_artist", "year", "genre"]) if f in tags.FIELDS]
    items = metadata_ops.load_items(item_ids)
    size = max(1, min(int(s["ai_batch_size"] or 25), 100))
    batches = [items[i:i + size] for i in range(0, len(items), size)]
    sem = asyncio.Semaphore(3)
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=180) as client:
        async def run(b):
            async with sem:
                try:
                    return await _ask(client, key, model, instruction, fields, b)
                except Exception as e:
                    errors.append(str(e))
                    return []
        answers = [t for res in await asyncio.gather(*(run(b) for b in batches)) for t in res]

    by_id = {}
    for t in answers:
        try:
            by_id[int(t.get("id"))] = t
        except (TypeError, ValueError):
            continue
    proposals = []
    for it in items:
        before = {k: it["_meta"].get(k, "") or "" for k in tags.FIELDS}
        after = dict(before)
        ans = by_id.get(it["id"], {})
        for f in fields:
            if f in ans and ans[f] is not None:
                after[f] = str(ans[f]).strip()
        changed = [k for k in tags.FIELDS if after.get(k, "") != before.get(k, "")]
        proposals.append({"item_id": it["id"], "title": it["v_title"], "file": it.get("file_path"),
                          "before": before, "after": after, "changed": changed, "rename_to": None})
    return {"proposals": proposals, "errors": errors, "model": model}


async def models() -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}/models")
        r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        if ":" in m["id"]:
            continue
        p = m.get("pricing") or {}
        try:
            price = float(p.get("prompt") or 0) * 1e6
        except ValueError:
            price = None
        out.append({"id": m["id"], "name": m.get("name") or m["id"], "prompt_per_m": price,
                    "context": m.get("context_length")})
    return out

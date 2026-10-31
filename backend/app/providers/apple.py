"""Apple Music API. Needs a MusicKit key (team id, key id, .p8) for the developer token and a
Music User Token that the browser gets through MusicKit JS."""
import asyncio
import re
import time

import httpx
import jwt

from .. import crypto, settings_store
from .base import LIKED, AuthError, Provider, ProviderError, track

API = "https://api.music.apple.com/v1"
_dev_token: dict = {"token": None, "exp": 0, "key": None}


def developer_token() -> str:
    team = (settings_store.get("apple_team_id") or "").strip()
    kid = (settings_store.get("apple_key_id") or "").strip()
    enc = settings_store.get("apple_private_key_enc") or ""
    if not (team and kid and enc):
        raise ProviderError("Add your Apple Team ID, MusicKit Key ID and .p8 key under Transfers → Apple Music setup")
    cache_key = f"{team}:{kid}:{hash(enc)}"
    if _dev_token["token"] and _dev_token["key"] == cache_key and _dev_token["exp"] > time.time() + 86400:
        return _dev_token["token"]
    now = int(time.time())
    exp = now + 60 * 60 * 24 * 150  # Apple allows up to 6 months
    try:
        token = jwt.encode({"iss": team, "iat": now, "exp": exp}, crypto.decrypt(enc), algorithm="ES256",
                           headers={"kid": kid})
    except Exception as e:
        raise ProviderError(f"Could not sign the Apple developer token, check the .p8 key: {e}") from e
    _dev_token.update(token=token, exp=exp, key=cache_key)
    return token


def _attr_track(item: dict) -> dict | None:
    a = item.get("attributes") or {}
    cat = None
    rel = (item.get("relationships") or {}).get("catalog", {}).get("data") or []
    if rel:
        cat = rel[0]
    catalog_id = (a.get("playParams") or {}).get("catalogId") or (cat or {}).get("id")
    if item.get("type") == "songs":
        catalog_id = item.get("id")
    if not catalog_id:
        return None
    isrc = a.get("isrc") or ((cat or {}).get("attributes") or {}).get("isrc")
    return track(catalog_id, a.get("name"), [a.get("artistName")] if a.get("artistName") else [],
                 a.get("albumName"), (a.get("durationInMillis") or 0) / 1000 or None, isrc,
                 url=a.get("url") or ((cat or {}).get("attributes") or {}).get("url"), library_id=item.get("id"))


class AppleMusic(Provider):
    name = "apple"
    label = "Apple Music"
    can_remove = False
    can_isrc = True
    liked_label = "Library songs"

    def __init__(self, conn: dict, creds: dict):
        super().__init__(conn, creds)
        self._client: httpx.AsyncClient | None = None

    @property
    def storefront(self) -> str:
        return self.creds.get("storefront") or "us"

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        url = path if path.startswith("http") else (API + path if path.startswith("/") else f"{API}/{path}")
        headers = {"Authorization": f"Bearer {developer_token()}", "Music-User-Token": self.creds.get("music_user_token", "")}
        for attempt in range(5):
            r = await self._client.request(method, url, headers=headers, **kw)
            if r.status_code == 429 or (r.status_code >= 500 and attempt < 3):
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise AuthError(f"Apple Music rejected the tokens ({r.status_code}). Connect Apple Music again.")
            return r
        return r

    async def _json(self, method: str, path: str, **kw) -> dict:
        r = await self._req(method, path, **kw)
        if r.status_code >= 400:
            raise ProviderError(f"Apple Music {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def _pages(self, path: str, params: dict) -> list[dict]:
        out, nxt, first = [], path, True
        while nxt:
            d = await self._json("GET", nxt, params=params if first else None)
            first = False
            out += d.get("data") or []
            nxt = d.get("next")
            if nxt and not nxt.startswith("http"):
                nxt = "https://api.music.apple.com" + nxt
        return out

    async def whoami(self) -> str:
        d = await self._json("GET", "/me/storefront")
        sf = (d.get("data") or [{}])[0].get("id")
        if sf and sf != self.creds.get("storefront"):
            self.creds["storefront"] = sf
            self.creds_changed = True
        return f"Apple Music ({sf or self.storefront})"

    async def list_playlists(self) -> list[dict]:
        out = [{"id": LIKED, "name": self.liked_label, "count": None, "image": None, "editable": True}]
        for p in await self._pages("/me/library/playlists", {"limit": 100}):
            a = p.get("attributes") or {}
            art = (a.get("artwork") or {}).get("url")
            out.append({"id": p["id"], "name": a.get("name"), "count": None,
                        "image": art.replace("{w}", "120").replace("{h}", "120") if art else None,
                        "editable": bool(a.get("canEdit", True))})
        return out

    async def get_tracks(self, playlist_id: str) -> list[dict]:
        if playlist_id == LIKED:
            rows = await self._pages("/me/library/songs", {"limit": 100, "include": "catalog"})
        else:
            rows = await self._pages(f"/me/library/playlists/{playlist_id}/tracks", {"limit": 100, "include": "catalog"})
        return [t for t in (_attr_track(r) for r in rows) if t]

    async def playlist_name(self, playlist_id: str) -> str | None:
        if playlist_id == LIKED:
            return self.liked_label
        d = await self._json("GET", f"/me/library/playlists/{playlist_id}")
        return ((d.get("data") or [{}])[0].get("attributes") or {}).get("name")

    async def search(self, query: str, limit: int = 8) -> list[dict]:
        d = await self._json("GET", f"/catalog/{self.storefront}/search",
                             params={"term": query[:200], "types": "songs", "limit": min(limit, 25)})
        rows = ((d.get("results") or {}).get("songs") or {}).get("data") or []
        return [t for t in (_attr_track(r) for r in rows) if t]

    async def by_isrc(self, isrc: str) -> list[dict]:
        d = await self._json("GET", f"/catalog/{self.storefront}/songs", params={"filter[isrc]": isrc})
        return [t for t in (_attr_track(r) for r in d.get("data") or []) if t]

    async def create_playlist(self, name: str, description: str) -> str:
        d = await self._json("POST", "/me/library/playlists",
                             json={"attributes": {"name": name[:100], "description": description[:300]}})
        return d["data"][0]["id"]

    async def add_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        ids = [t["id"] for t in tracks]
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            if playlist_id == LIKED:
                r = await self._req("POST", "/me/library", params={"ids[songs]": ",".join(chunk)})
            else:
                r = await self._req("POST", f"/me/library/playlists/{playlist_id}/tracks",
                                    json={"data": [{"id": x, "type": "songs"} for x in chunk]})
            if r.status_code >= 400:
                raise ProviderError(f"Apple Music could not add tracks: {r.text[:200]}")

    def parse_playlist_id(self, text: str) -> str:
        m = re.search(r"(p\.[A-Za-z0-9]+)", text)
        return m.group(1) if m else text.strip()

    def parse_track_id(self, text: str) -> str:
        m = re.search(r"[?&]i=(\d+)", text) or re.search(r"/song/[^/]+/(\d+)", text)
        return m.group(1) if m else text.strip()

    def playlist_url(self, playlist_id: str) -> str | None:
        return "https://music.apple.com/library/songs" if playlist_id == LIKED else \
            f"https://music.apple.com/library/playlist/{playlist_id}"

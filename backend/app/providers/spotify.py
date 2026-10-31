"""Spotify Web API (Authorization Code + PKCE). Uses the 2026 routes (/items, POST /me/playlists)
and falls back to the older ones where Spotify still serves them."""
import asyncio
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode

import httpx

from .. import settings_store
from .base import LIKED, AuthError, Provider, ProviderError, track

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = ("playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public "
          "user-library-read user-library-modify user-read-private")


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state,
        "scope": SCOPES, "code_challenge_method": "S256", "code_challenge": challenge})


async def exchange_code(client_id: str, code: str, verifier: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": redirect_uri, "client_id": client_id,
                                          "code_verifier": verifier})
    if r.status_code != 200:
        raise ProviderError(f"Spotify login failed: {r.text[:300]}")
    d = r.json()
    return {"access_token": d["access_token"], "refresh_token": d.get("refresh_token"),
            "expires_at": time.time() + int(d.get("expires_in", 3600)) - 60, "client_id": client_id}


def _to_track(t: dict | None) -> dict | None:
    if not t or not t.get("id") or t.get("type", "track") != "track":
        return None
    return track(t["id"], t.get("name"), [a.get("name") for a in t.get("artists") or []],
                 (t.get("album") or {}).get("name"), (t.get("duration_ms") or 0) / 1000 or None,
                 (t.get("external_ids") or {}).get("isrc"),
                 url=(t.get("external_urls") or {}).get("spotify"), uri=t.get("uri"))


class Spotify(Provider):
    name = "spotify"
    label = "Spotify"
    can_isrc = True

    def __init__(self, conn: dict, creds: dict):
        super().__init__(conn, creds)
        self._client: httpx.AsyncClient | None = None

    async def _token(self) -> str:
        if self.creds.get("expires_at", 0) > time.time():
            return self.creds["access_token"]
        client_id = self.creds.get("client_id") or settings_store.get("spotify_client_id")
        if not self.creds.get("refresh_token"):
            raise AuthError("Spotify session expired, connect again")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": client_id,
                                              "refresh_token": self.creds["refresh_token"]})
        if r.status_code != 200:
            raise AuthError(f"Spotify refresh failed, connect again: {r.text[:200]}")
        d = r.json()
        self.creds.update(access_token=d["access_token"], expires_at=time.time() + int(d.get("expires_in", 3600)) - 60)
        if d.get("refresh_token"):
            self.creds["refresh_token"] = d["refresh_token"]
        self.creds_changed = True
        return self.creds["access_token"]

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        url = path if path.startswith("http") else API + path
        for attempt in range(6):
            r = await self._client.request(method, url, headers={"Authorization": f"Bearer {await self._token()}"}, **kw)
            if r.status_code == 429:
                await asyncio.sleep(min(60, int(r.headers.get("Retry-After", "2")) + 1))
                continue
            if r.status_code == 401:
                self.creds["expires_at"] = 0
                if attempt == 0:
                    continue
                raise AuthError("Spotify rejected the token, connect again")
            if r.status_code >= 500 and attempt < 3:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            return r
        return r

    async def _json(self, method: str, path: str, **kw) -> dict:
        r = await self._req(method, path, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message") or r.text
            except ValueError:
                msg = r.text
            if r.status_code == 403 and "premium" in msg.lower():
                msg += " (Spotify development apps need the app owner to have Premium)"
            raise ProviderError(f"Spotify {r.status_code}: {msg[:300]}")
        return r.json() if r.content else {}

    async def _pages(self, path: str, params: dict | None = None) -> list[dict]:
        items, url = [], path
        while url:
            d = await self._json("GET", url, params=params if url == path else None)
            items += d.get("items") or []
            url = d.get("next")
        return items

    async def whoami(self) -> str:
        me = await self._json("GET", "/me")
        self.creds["user_id"] = me.get("id")
        return me.get("display_name") or me.get("id")

    async def list_playlists(self) -> list[dict]:
        out = [{"id": LIKED, "name": self.liked_label, "count": None, "image": None, "editable": True}]
        if not self.creds.get("user_id"):
            await self.whoami()
        for p in await self._pages("/me/playlists", {"limit": 50}):
            if not p:
                continue
            imgs = p.get("images") or []
            owner = (p.get("owner") or {}).get("id")
            count = (p.get("items") or p.get("tracks") or {}).get("total")
            out.append({"id": p["id"], "name": p.get("name"), "count": count, "image": imgs[0]["url"] if imgs else None,
                        "editable": owner == self.creds.get("user_id") or bool(p.get("collaborative"))})
        return out

    async def get_tracks(self, playlist_id: str) -> list[dict]:
        if playlist_id == LIKED:
            rows = await self._pages("/me/tracks", {"limit": 50})
        else:
            r = await self._req("GET", f"/playlists/{playlist_id}/items", params={"limit": 50})
            if r.status_code in (403, 404, 410):
                rows = await self._pages(f"/playlists/{playlist_id}/tracks", {"limit": 100})
            else:
                if r.status_code >= 400:
                    raise ProviderError(f"Spotify {r.status_code}: {r.text[:200]}")
                d = r.json()
                rows = d.get("items") or []
                nxt = d.get("next")
                while nxt:
                    d = await self._json("GET", nxt)
                    rows += d.get("items") or []
                    nxt = d.get("next")
        return [t for t in (_to_track(row.get("item") or row.get("track")) for row in rows) if t]

    async def playlist_name(self, playlist_id: str) -> str | None:
        if playlist_id == LIKED:
            return self.liked_label
        return (await self._json("GET", f"/playlists/{playlist_id}", params={"fields": "name"})).get("name")

    async def search(self, query: str, limit: int = 8) -> list[dict]:
        d = await self._json("GET", "/search", params={"q": query, "type": "track", "limit": min(limit, 10)})
        return [t for t in (_to_track(i) for i in (d.get("tracks") or {}).get("items") or []) if t]

    async def search_track(self, t: dict) -> list[dict]:
        artist = t["artists"][0] if t["artists"] else ""
        title = re.sub(r"[\"()\[\]]", " ", t["title"])
        res = await self.search(f'track:"{title.strip()}" artist:"{artist}"') if artist else []
        if len(res) < 3:
            res += await self.search(f"{artist} {title}".strip())
        return res

    async def by_isrc(self, isrc: str) -> list[dict]:
        return await self.search(f"isrc:{isrc}", limit=3)

    async def create_playlist(self, name: str, description: str) -> str:
        body = {"name": name[:100], "description": description[:300], "public": False}
        r = await self._req("POST", "/me/playlists", json=body)
        if r.status_code in (404, 405):
            if not self.creds.get("user_id"):
                await self.whoami()
            r = await self._req("POST", f"/users/{self.creds['user_id']}/playlists", json=body)
        if r.status_code >= 400:
            raise ProviderError(f"Spotify could not create the playlist: {r.text[:200]}")
        return r.json()["id"]

    async def add_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        ids = [t["id"] for t in tracks]
        for i in range(0, len(ids), 50 if playlist_id == LIKED else 100):
            chunk = ids[i:i + 100]
            if playlist_id == LIKED:
                await self._json("PUT", "/me/tracks", json={"ids": chunk[:50]})
                continue
            uris = [f"spotify:track:{x}" for x in chunk]
            r = await self._req("POST", f"/playlists/{playlist_id}/items", json={"uris": uris})
            if r.status_code in (403, 404, 410):
                r = await self._req("POST", f"/playlists/{playlist_id}/tracks", json={"uris": uris})
            if r.status_code >= 400:
                raise ProviderError(f"Spotify could not add tracks: {r.text[:200]}")

    async def remove_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        ids = [t["id"] for t in tracks]
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            if playlist_id == LIKED:
                await self._json("DELETE", "/me/tracks", json={"ids": chunk})
                continue
            entries = [{"uri": f"spotify:track:{x}"} for x in chunk]
            r = await self._req("DELETE", f"/playlists/{playlist_id}/items", json={"items": entries})
            if r.status_code in (400, 403, 404, 410):
                r = await self._req("DELETE", f"/playlists/{playlist_id}/tracks", json={"tracks": entries})
            if r.status_code >= 400:
                raise ProviderError(f"Spotify could not remove tracks: {r.text[:200]}")

    def parse_playlist_id(self, text: str) -> str:
        m = re.search(r"playlist[/:]([A-Za-z0-9]+)", text)
        return m.group(1) if m else text.strip()

    def parse_track_id(self, text: str) -> str:
        m = re.search(r"track[/:]([A-Za-z0-9]+)", text)
        return m.group(1) if m else text.strip()

    def playlist_url(self, playlist_id: str) -> str | None:
        if playlist_id == LIKED:
            return "https://open.spotify.com/collection/tracks"
        return f"https://open.spotify.com/playlist/{playlist_id}"

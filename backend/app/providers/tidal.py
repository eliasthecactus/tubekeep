"""TIDAL through tidalapi (device login: the user opens link.tidal.com and enters a code)."""
import asyncio
import datetime
import re
import secrets
import time

from .base import LIKED, AuthError, Provider, ProviderError, track

# pending device logins: flow_id -> {"session", "future", "link", "label", "created", "result"}
FLOWS: dict[str, dict] = {}


def _session():
    import tidalapi
    return tidalapi.Session()


def start_login(label: str) -> dict:
    s = _session()
    link, future = s.login_oauth()
    fid = secrets.token_urlsafe(12)
    FLOWS[fid] = {"session": s, "future": future, "label": label, "created": time.time(),
                  "expires": time.time() + link.expires_in}
    for k in [k for k, v in FLOWS.items() if v["expires"] < time.time() - 600]:
        FLOWS.pop(k, None)
    url = link.verification_uri_complete
    if not url.startswith("http"):
        url = "https://" + url
    return {"flow_id": fid, "url": url, "code": link.user_code, "expires_in": int(link.expires_in)}


def creds_of(s) -> dict:
    exp = s.expiry_time
    return {"token_type": s.token_type, "access_token": s.access_token, "refresh_token": s.refresh_token,
            "expiry_time": exp.timestamp() if isinstance(exp, datetime.datetime) else None,
            "is_pkce": bool(getattr(s, "is_pkce", False))}


def _track(t) -> dict | None:
    if t is None or not getattr(t, "id", None):
        return None
    name = getattr(t, "full_name", None) or t.name
    return track(t.id, name, [a.name for a in (t.artists or []) if getattr(a, "name", None)],
                 getattr(t.album, "name", None) if getattr(t, "album", None) else None, getattr(t, "duration", None),
                 getattr(t, "isrc", None), url=f"https://tidal.com/browse/track/{t.id}")


class Tidal(Provider):
    name = "tidal"
    label = "TIDAL"
    can_isrc = True
    liked_label = "My tracks (favorites)"

    def __init__(self, conn: dict, creds: dict):
        super().__init__(conn, creds)
        self._s = None

    def _session(self):
        if self._s is None:
            s = _session()
            exp = self.creds.get("expiry_time")
            ok = s.load_oauth_session(self.creds["token_type"], self.creds["access_token"],
                                      self.creds.get("refresh_token"),
                                      datetime.datetime.fromtimestamp(exp) if exp else None,
                                      self.creds.get("is_pkce", False))
            if not ok or not s.check_login():
                raise AuthError("TIDAL session expired, connect again")
            self._s = s
        return self._s

    async def _call(self, fn):
        def run():
            try:
                s = self._session()
                res = fn(s)
                new = creds_of(s)
                if new["access_token"] != self.creds.get("access_token"):
                    self.creds.update(new)
                    self.creds_changed = True
                return res
            except AuthError:
                raise
            except Exception as e:
                msg = str(e)
                if "401" in msg:
                    raise AuthError(f"TIDAL rejected the session: {msg[:200]}") from e
                raise ProviderError(f"TIDAL: {msg[:300]}") from e
        return await asyncio.to_thread(run)

    async def whoami(self) -> str:
        return await self._call(lambda s: getattr(s.user, "username", None) or getattr(s.user, "email", None)
                                or f"TIDAL user {s.user.id}")

    async def list_playlists(self) -> list[dict]:
        def run(s):
            out = [{"id": LIKED, "name": self.liked_label, "count": None, "image": None, "editable": True}]
            for p in s.user.playlists():
                img = None
                try:
                    img = p.image(160)
                except Exception:
                    pass
                creator = getattr(p, "creator", None)
                out.append({"id": p.id, "name": p.name, "count": p.num_tracks, "image": img,
                            "editable": bool(creator and getattr(creator, "id", None) == s.user.id)})
            return out
        return await self._call(run)

    async def get_tracks(self, playlist_id: str) -> list[dict]:
        def run(s):
            items, offset = [], 0
            src = s.user.favorites if playlist_id == LIKED else s.playlist(playlist_id)
            while True:
                page = src.tracks(limit=100, offset=offset)
                items += page
                if len(page) < 100:
                    break
                offset += 100
            return [t for t in (_track(x) for x in items) if t]
        return await self._call(run)

    async def playlist_name(self, playlist_id: str) -> str | None:
        if playlist_id == LIKED:
            return self.liked_label
        return await self._call(lambda s: s.playlist(playlist_id).name)

    async def search(self, query: str, limit: int = 8) -> list[dict]:
        import tidalapi
        res = await self._call(lambda s: s.search(query, models=[tidalapi.Track], limit=limit))
        return [t for t in (_track(x) for x in res.get("tracks") or []) if t]

    async def by_isrc(self, isrc: str) -> list[dict]:
        try:
            res = await self._call(lambda s: s.get_tracks_by_isrc(isrc))
        except ProviderError:
            return []
        return [t for t in (_track(x) for x in res or []) if t]

    async def create_playlist(self, name: str, description: str) -> str:
        return await self._call(lambda s: s.user.create_playlist(name[:100], description[:300]).id)

    async def add_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        ids = [t["id"] for t in tracks]

        def run(s):
            if playlist_id == LIKED:
                for i in ids:
                    s.user.favorites.add_track(i)
                return
            pl = s.playlist(playlist_id)
            for i in range(0, len(ids), 100):
                pl.add(ids[i:i + 100], allow_duplicates=False)
        await self._call(run)

    async def remove_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        def run(s):
            if playlist_id == LIKED:
                for t in tracks:
                    s.user.favorites.remove_track(t["id"])
                return
            pl = s.playlist(playlist_id)
            for t in tracks:
                pl.remove_by_id(t["id"])
        await self._call(run)

    def parse_playlist_id(self, text: str) -> str:
        m = re.search(r"playlist/([0-9a-f-]{36})", text)
        return m.group(1) if m else text.strip()

    def parse_track_id(self, text: str) -> str:
        m = re.search(r"track/(\d+)", text)
        return m.group(1) if m else text.strip()

    def playlist_url(self, playlist_id: str) -> str | None:
        return "https://tidal.com/my-collection/tracks" if playlist_id == LIKED else \
            f"https://tidal.com/browse/playlist/{playlist_id}"

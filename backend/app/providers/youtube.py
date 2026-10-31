"""YouTube Music through ytmusicapi, authenticated with the cookies of a Tubekeep account."""
import asyncio
import re

from .. import accounts, crypto, proxies
from .base import LIKED, AuthError, Provider, ProviderError, track


def cookie_header(cookies_txt: str) -> str:
    """Netscape cookies.txt -> Cookie header for .youtube.com."""
    pairs = {}
    for line in cookies_txt.splitlines():
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain = parts[0].removeprefix("#HttpOnly_")
        if domain.endswith("youtube.com"):
            pairs[parts[5]] = parts[6]
    if "__Secure-3PAPISID" not in pairs and "SAPISID" in pairs:
        pairs["__Secure-3PAPISID"] = pairs["SAPISID"]
    if "__Secure-3PAPISID" not in pairs:
        raise AuthError("The account cookies have no SAPISID cookie, export them again while signed in")
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def _artists(item: dict) -> list[str]:
    return [a.get("name") for a in (item.get("artists") or []) if a.get("name")]


def _to_track(it: dict) -> dict | None:
    vid = it.get("videoId")
    if not vid:
        return None
    album = it.get("album")
    return track(vid, it.get("title"), _artists(it), album.get("name") if isinstance(album, dict) else None,
                 it.get("duration_seconds"), url=f"https://music.youtube.com/watch?v={vid}",
                 setVideoId=it.get("setVideoId"), videoType=it.get("videoType"),
                 resultType=it.get("resultType"))


class YouTubeMusic(Provider):
    name = "youtube"
    label = "YouTube Music"
    liked_label = "Liked music"

    def __init__(self, conn: dict, creds: dict):
        super().__init__(conn, creds)
        self._yt = None

    def _client(self):
        if self._yt is None:
            from ytmusicapi import YTMusic
            acc = accounts.get(self.conn.get("account_id"))
            if not acc:
                raise AuthError("The YouTube account of this connection was deleted")
            headers = {
                "cookie": cookie_header(crypto.decrypt(acc["cookies_enc"])),
                "authorization": "SAPISIDHASH 0",
                "x-goog-authuser": str(self.creds.get("authuser", "0")),
                "origin": "https://music.youtube.com",
                "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/140.0 Safari/537.36",
            }
            px = proxies.select(None, acc)
            kw = {"proxies": {"http": px["url"], "https": px["url"]}} if px else {}
            self._yt = YTMusic(auth=headers, **kw)
        return self._yt

    async def _call(self, fn, *a, **kw):
        def run():
            try:
                return fn(self._client(), *a, **kw)
            except AuthError:
                raise
            except Exception as e:
                msg = str(e)
                if "401" in msg or "403" in msg or "Sign in" in msg or "authenticated" in msg.lower():
                    raise AuthError(f"YouTube rejected the account cookies: {msg[:200]}") from e
                raise ProviderError(f"YouTube Music: {msg[:300]}") from e
        return await asyncio.to_thread(run)

    async def whoami(self) -> str:
        info = await self._call(lambda yt: yt.get_account_info())
        return info.get("accountName") or info.get("channelHandle") or "YouTube account"

    async def list_playlists(self) -> list[dict]:
        pls = await self._call(lambda yt: yt.get_library_playlists(limit=None))
        out = [{"id": LIKED, "name": self.liked_label, "count": None, "image": None, "editable": True}]
        for p in pls:
            pid = p.get("playlistId")
            if not pid or pid == "LM":
                continue
            thumbs = p.get("thumbnails") or []
            out.append({"id": pid, "name": p.get("title"), "count": p.get("count"),
                        "image": thumbs[-1]["url"] if thumbs else None, "editable": True})
        return out

    async def get_tracks(self, playlist_id: str) -> list[dict]:
        if playlist_id == LIKED:
            data = await self._call(lambda yt: yt.get_liked_songs(limit=None))
        else:
            data = await self._call(lambda yt: yt.get_playlist(playlist_id, limit=None))
        return [t for t in (_to_track(i) for i in data.get("tracks") or []) if t]

    async def playlist_name(self, playlist_id: str) -> str | None:
        if playlist_id == LIKED:
            return self.liked_label
        data = await self._call(lambda yt: yt.get_playlist(playlist_id, limit=1))
        return data.get("title")

    async def search(self, query: str, limit: int = 8) -> list[dict]:
        songs = await self._call(lambda yt: yt.search(query, filter="songs", limit=limit))
        out = [t for t in (_to_track(i) for i in songs) if t][:limit]
        if len(out) < 3:
            vids = await self._call(lambda yt: yt.search(query, filter="videos", limit=limit))
            out += [t for t in (_to_track(i) for i in vids) if t][:limit]
        return out

    async def create_playlist(self, name: str, description: str) -> str:
        res = await self._call(lambda yt: yt.create_playlist(re.sub(r"[<>]", "", name)[:150], description,
                                                              privacy_status="PRIVATE"))
        if not isinstance(res, str):
            raise ProviderError(f"Could not create the playlist: {str(res)[:200]}")
        return res

    async def add_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        ids = [t["id"] for t in tracks]
        if playlist_id == LIKED:
            for vid in ids:
                await self._call(lambda yt, v=vid: yt.rate_song(v, "LIKE"))
            return
        for i in range(0, len(ids), 50):
            res = await self._call(lambda yt, b=ids[i:i + 50]: yt.add_playlist_items(playlist_id, b, duplicates=True))
            if isinstance(res, dict) and res.get("status") not in (None, "STATUS_SUCCEEDED"):
                raise ProviderError(f"Adding to the playlist failed: {str(res)[:200]}")

    async def remove_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        if playlist_id == LIKED:
            for t in tracks:
                await self._call(lambda yt, v=t["id"]: yt.rate_song(v, "INDIFFERENT"))
            return
        videos = [{"videoId": t["id"], "setVideoId": t["extra"].get("setVideoId")} for t in tracks
                  if t["extra"].get("setVideoId")]
        if videos:
            await self._call(lambda yt: yt.remove_playlist_items(playlist_id, videos))

    def parse_playlist_id(self, text: str) -> str:
        m = re.search(r"list=([\w-]+)", text)
        return m.group(1) if m else text.strip()

    def parse_track_id(self, text: str) -> str:
        m = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", text)
        return m.group(1) if m else text.strip()

    def playlist_url(self, playlist_id: str) -> str | None:
        if playlist_id == LIKED:
            return "https://music.youtube.com/playlist?list=LM"
        return f"https://music.youtube.com/playlist?list={playlist_id}"

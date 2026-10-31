"""Common interface for music services.

A track is a plain dict:
  {"id": str, "title": str, "artists": [str], "album": str | None, "duration": float | None (seconds),
   "isrc": str | None, "url": str | None, "extra": dict}
"id" is the stable id used for matching and adding (Spotify track id, YouTube videoId, Apple catalog id,
TIDAL track id). "extra" carries what a provider needs to remove the track again (e.g. YouTube setVideoId).
"""
import re
from typing import Any

LIKED = "__liked__"   # virtual playlist id for liked/saved songs


class ProviderError(Exception):
    pass


class AuthError(ProviderError):
    """Credentials are invalid or expired; the user has to connect again."""


def track(id: Any, title: str | None, artists: list[str] | None, album: str | None = None,
          duration: float | None = None, isrc: str | None = None, url: str | None = None,
          **extra) -> dict:
    return {"id": str(id), "title": title or "", "artists": [a for a in (artists or []) if a],
            "album": album or None, "duration": float(duration) if duration else None,
            "isrc": (isrc or None) and isrc.upper(), "url": url, "extra": extra}


class Provider:
    name = "base"
    label = "Base"
    can_remove = True
    can_isrc = False
    liked_label = "Liked songs"

    def __init__(self, conn: dict, creds: dict):
        self.conn = conn
        self.creds = creds
        self.creds_changed = False

    # -- identity
    async def whoami(self) -> str:
        raise NotImplementedError

    # -- reading
    async def list_playlists(self) -> list[dict]:
        """[{id, name, count, image, editable}] incl. the liked songs pseudo playlist."""
        raise NotImplementedError

    async def get_tracks(self, playlist_id: str) -> list[dict]:
        raise NotImplementedError

    async def playlist_name(self, playlist_id: str) -> str | None:
        for p in await self.list_playlists():
            if p["id"] == playlist_id:
                return p["name"]
        return None

    # -- matching
    async def search(self, query: str, limit: int = 8) -> list[dict]:
        raise NotImplementedError

    async def search_track(self, t: dict) -> list[dict]:
        q = f"{' '.join(t['artists'][:2])} {t['title']}".strip()
        return await self.search(q)

    async def by_isrc(self, isrc: str) -> list[dict]:
        return []

    # -- writing
    async def create_playlist(self, name: str, description: str) -> str:
        raise NotImplementedError

    async def add_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        raise NotImplementedError

    async def remove_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        raise ProviderError(f"{self.label} does not support removing tracks")

    # -- helpers
    def parse_playlist_id(self, text: str) -> str:
        return text.strip()

    def parse_track_id(self, text: str) -> str:
        return text.strip()

    def playlist_url(self, playlist_id: str) -> str | None:
        return None


def last_path_part(url: str, marker: str) -> str | None:
    m = re.search(re.escape(marker) + r"([A-Za-z0-9._-]+)", url)
    return m.group(1) if m else None

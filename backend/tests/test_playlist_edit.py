"""Removing tracks from the real YouTube playlist and replacing dead videos, against a fake ytmusicapi."""
import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import crypto, db, playlist_edit
from app.providers.youtube import YouTubeMusic

H = {"X-Requested-With": "tubekeep"}
COOKIES = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tabc\n"


class FakeYTMusic:
    """The parts of ytmusicapi.YTMusic that Tubekeep uses for playlist edits."""

    def __init__(self):
        self.owned = True
        self.tracks: list[dict] = []
        self.n = 0
        self.catalog = [
            self.song("onemoretim1", "One More Time", "Daft Punk", 320),
            self.song("onemorelive", "One More Time (Live)", "Daft Punk", 400),
            self.song("wonderwall1", "Wonderwall", "Oasis", 258),
            self.song("wondercover", "Wonderwall (Cover)", "Someone", 240),
        ]

    @staticmethod
    def song(vid, title, artist, dur):
        return {"videoId": vid, "title": title, "artists": [{"name": artist}], "album": {"name": "Album"},
                "duration_seconds": dur, "resultType": "song", "thumbnails": [{"url": f"https://img/{vid}.jpg"}]}

    def entry(self, vid, title, artist=None, available=True):
        self.n += 1
        return {"videoId": vid, "setVideoId": f"set{self.n}" if self.owned else None, "title": title,
                "artists": [{"name": artist}] if artist else [], "isAvailable": available}

    def get_playlist(self, pid, limit=100):
        tracks = copy.deepcopy(self.tracks)
        if not self.owned:
            for t in tracks:
                t.pop("setVideoId", None)
        return {"id": pid, "owned": self.owned, "tracks": tracks}

    def remove_playlist_items(self, pid, videos):
        sets = {v["setVideoId"] for v in videos}
        self.tracks = [t for t in self.tracks if t["setVideoId"] not in sets]
        return "STATUS_SUCCEEDED"

    def add_playlist_items(self, pid, ids, duplicates=False):
        out = []
        for vid in ids:
            e = self.entry(vid, next((c["title"] for c in self.catalog if c["videoId"] == vid), vid))
            self.tracks.append(e)
            out.append({"videoId": vid, "setVideoId": e["setVideoId"]})
        return {"status": "STATUS_SUCCEEDED", "playlistEditResults": out}

    def edit_playlist(self, pid, moveItem=None):
        moved, before = moveItem
        t = next(x for x in self.tracks if x["setVideoId"] == moved)
        self.tracks.remove(t)
        i = next(k for k, x in enumerate(self.tracks) if x["setVideoId"] == before)
        self.tracks.insert(i, t)
        return "STATUS_SUCCEEDED"

    def search(self, q, filter=None, limit=10):
        words = q.lower().split()
        hits = [c for c in self.catalog if any(w in (c["title"] + " " + c["artists"][0]["name"]).lower() for w in words)]
        return copy.deepcopy(hits) if filter == "songs" else []


@pytest.fixture(scope="module")
def client(fake_yt):
    from app.main import app
    with TestClient(app) as c:
        if c.get("/api/auth/state").json()["setup_needed"]:
            c.post("/api/auth/setup", json={"username": "admin", "password": "longenough1"})
        else:
            c.post("/api/auth/login", json={"username": "admin", "password": "longenough1"})
        yield c


@pytest.fixture()
def yt(monkeypatch):
    fake = FakeYTMusic()
    monkeypatch.setattr(YouTubeMusic, "_client", lambda self: fake)
    playlist_edit._entries.clear()
    return fake


def wait_jobs(c, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if not c.get("/api/jobs/live").json():
            return
        time.sleep(0.2)
    raise AssertionError("jobs did not finish")


def items(c, pid):
    return {i["video_id"]: i for i in c.get(f"/api/playlists/{pid}/items?removed=include").json()["items"]}


def test_version_is_shown(client):
    assert client.get("/api/auth/state").json()["version"] == (Path(playlist_edit.__file__).parent / "VERSION") \
        .read_text().strip()


def test_remove_and_replace_on_youtube(client, fake_yt, yt):
    ents = [
        {"id": "keepsong001", "title": "Keep Me", "channel": "Chan", "duration": 100},
        {"id": "dropsong001", "title": "Drop Me", "channel": "Chan", "duration": 100},
        {"id": "unavail0002", "title": "One More Time", "channel": "Daft Punk - Topic", "duration": 321},
        {"id": "deadvideo01", "title": "[Deleted video]", "channel": None, "duration": None},
    ]
    (fake_yt / "playlist.json").write_text(json.dumps({"_type": "playlist", "id": "PLedit", "title": "Edit me",
                                                        "entries": ents}))
    pid = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLedit"},
                      headers=H).json()["created"][0]
    wait_jobs(client)
    its = items(client, pid)
    assert its["keepsong001"]["status"] == "done" and its["unavail0002"]["status"] == "unavailable"
    assert its["deadvideo01"]["status"] == "unavailable"
    yt.tracks = [yt.entry("keepsong001", "Keep Me"), yt.entry("dropsong001", "Drop Me"),
                 yt.entry("unavail0002", "One More Time", "Daft Punk", available=False),
                 yt.entry("deadvideo01", "Wonderwall", "Oasis", available=False)]

    # without an account nothing can be changed
    a = client.get(f"/api/playlists/{pid}/access").json()
    assert a["writable"] is False and "account" in a["reason"]
    r = client.post(f"/api/playlists/{pid}/remove", json={"item_ids": [its["dropsong001"]["id"]]}, headers=H)
    assert r.status_code == 400

    acc = db.insert("accounts", {"label": "me", "cookies_enc": crypto.encrypt(COOKIES), "status": "valid",
                                 "cookies_updated_at": db.now(), "created_at": db.now()})
    client.patch(f"/api/playlists/{pid}", json={"account_id": acc}, headers=H)
    assert client.get(f"/api/playlists/{pid}/access?refresh=true").json() == {"writable": True, "reason": None}

    # remove one downloaded track and delete its file
    path = Path(its["dropsong001"]["file_path"])
    assert path.exists()
    r = client.post(f"/api/playlists/{pid}/remove", json={"item_ids": [its["dropsong001"]["id"]],
                                                           "delete_files": True}, headers=H).json()
    assert r == {"removed": 1, "already_gone": 0, "deleted_files": 1}
    assert [t["videoId"] for t in yt.tracks] == ["keepsong001", "unavail0002", "deadvideo01"]
    assert not path.exists()
    drop = items(client, pid)["dropsong001"]
    assert drop["removed_at"] and drop["status"] == "deleted"

    # alternatives: title known from Tubekeep's own history, best match first, the live version lower
    r = client.post(f"/api/playlists/{pid}/items/{its['unavail0002']['id']}/alternatives", json={},
                    headers=H).json()
    assert r["query"] == "Daft Punk One More Time"
    assert r["candidates"][0]["video_id"] == "onemoretim1"
    assert r["candidates"][0]["score"] > r["candidates"][1]["score"]
    # a video that was already dead when first seen: title comes from YouTube Music's greyed-out entry
    r = client.post(f"/api/playlists/{pid}/items/{its['deadvideo01']['id']}/alternatives", json={},
                    headers=H).json()
    assert r["source"]["title"] == "Wonderwall" and r["candidates"][0]["video_id"] == "wonderwall1"
    # own search text
    r = client.post(f"/api/playlists/{pid}/items/{its['deadvideo01']['id']}/alternatives",
                    json={"query": "cover"}, headers=H).json()
    assert r["query"] == "cover" and r["candidates"][0]["video_id"] == "wondercover"

    # replace: new video takes the old spot on YouTube, old one is removed, new one downloads as a normal track
    ents.append({"id": "onemoretim1", "title": "One More Time", "channel": "Daft Punk", "duration": 320})
    (fake_yt / "playlist.json").write_text(json.dumps({"_type": "playlist", "id": "PLedit", "title": "Edit me",
                                                        "entries": ents}))
    best = client.post(f"/api/playlists/{pid}/items/{its['unavail0002']['id']}/alternatives", json={},
                       headers=H).json()["candidates"][0]
    r = client.post(f"/api/playlists/{pid}/items/{its['unavail0002']['id']}/alternative",
                    json={"candidate": best, "replace": True}, headers=H).json()
    assert r["replaced"] is True
    assert [t["videoId"] for t in yt.tracks] == ["keepsong001", "onemoretim1", "deadvideo01"]
    wait_jobs(client)
    now = items(client, pid)
    assert now["unavail0002"]["removed_at"]
    assert now["onemoretim1"]["status"] == "done" and not now["onemoretim1"]["removed_at"]

    # add only: the dead entry stays
    best = client.post(f"/api/playlists/{pid}/items/{its['deadvideo01']['id']}/alternatives", json={},
                       headers=H).json()["candidates"][0]
    client.post(f"/api/playlists/{pid}/items/{its['deadvideo01']['id']}/alternative",
                json={"candidate": best, "replace": False}, headers=H)
    assert [t["videoId"] for t in yt.tracks] == ["keepsong001", "onemoretim1", "deadvideo01", "wonderwall1"]
    assert not items(client, pid)["deadvideo01"]["removed_at"]
    wait_jobs(client)

    # someone else's playlist: read-only
    yt.owned = False
    a = client.get(f"/api/playlists/{pid}/access?refresh=true").json()
    assert a["writable"] is False
    r = client.post(f"/api/playlists/{pid}/remove", json={"item_ids": [its["keepsong001"]["id"]]}, headers=H)
    assert r.status_code == 400 and "can't edit" in r.json()["detail"]


def test_source_of_placeholders_and_video_titles():
    item = {"video_id": "x", "title": "[Private video]", "channel": None, "duration": None, "meta": None,
            "meta_override": None, "yt_artist": None, "yt_track": None}
    assert playlist_edit.source_of(item)["known"] is False
    item |= {"title": "Queen - Bohemian Rhapsody (Official Video)", "channel": None}
    s = playlist_edit.source_of(item)
    assert s["artists"] == ["Queen"] and s["title"].startswith("Bohemian Rhapsody")
    item |= {"meta_override": json.dumps({"title": "Song", "artist": "A & B"})}
    assert playlist_edit.source_of(item)["artists"] == ["A", "B"]


def test_special_playlists_are_read_only():
    assert playlist_edit._special("LL") and playlist_edit._special("WL") and playlist_edit._special("RDabc")
    assert playlist_edit._special("PLabc") is None

"""Duplicates: same video in several playlists, same song as different videos, a video twice in one playlist."""
import json
from pathlib import Path

from app import crypto, db, duplicates

from test_playlist_edit import COOKIES, H, client, items, wait_jobs, yt  # noqa: F401  (fixtures)


def song(title, artist, raw=None, channel=None):
    row = {"video_id": "x", "title": raw or title, "channel": channel or artist, "duration": None, "meta": None,
           "meta_override": None, "yt_artist": artist or None, "yt_track": title}
    return duplicates._song(row)


def test_same_song_rules():
    official = song("One More Time", "Daft Punk", "Daft Punk - One More Time (Official Video)")
    topic = song("One More Time", "Daft Punk")
    assert duplicates.same_song(official, topic, 320, 322)
    assert not duplicates.same_song(official, topic, 320, 380)  # long intro: not the same audio
    assert duplicates.same_song(official, topic, None, 322)
    assert not duplicates.same_song(topic, song("One More Time", "Britney Spears"), 320, 320)
    assert not duplicates.same_song(topic, song("One More Time (Live)", "Daft Punk"), 320, 320)
    assert not duplicates.same_song(song("One More Time", ""), topic, 320, 320)  # artist unknown: not sure
    assert duplicates.same_song(song("One More Time", "DaftPunkVEVO"), topic, 320, 320)
    assert duplicates.same_song(song("One More Time", "Daft Punk, Romanthony"), topic, 320, 320)
    assert not duplicates.same_song(song("Love", "Artist 12"), song("Love", "Artist 123"), 200, 200)
    assert not duplicates.same_song(song("Mr. Brightside", "The Killers"), song("Mr. Brightside", "The Kills"),
                                    222, 222)


def write(fake_yt, ents, title="Dup"):
    (fake_yt / "playlist.json").write_text(json.dumps({"_type": "playlist", "id": "PLdup", "title": title,
                                                        "entries": ents}))


def test_find_and_resolve(client, fake_yt, yt):  # noqa: F811
    s1 = {"id": "dupsong0001", "title": "Daft Punk - One More Time (Official Video)", "channel": "Daft Punk",
          "duration": 320}
    s2 = {"id": "dupsong0002", "title": "One More Time", "channel": "Daft Punk - Topic", "duration": 321}
    live = {"id": "duplive0003", "title": "One More Time (Live)", "channel": "Daft Punk", "duration": 400}
    rep = {"id": "duprep00004", "title": "Twice Added", "channel": "Band", "duration": 200}
    solo = {"id": "dupsolo0005", "title": "Only Once", "channel": "Band", "duration": 200}
    write(fake_yt, [s1, s2, live, rep, rep, solo])
    a = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLdupA"},
                    headers=H).json()["created"][0]
    wait_jobs(client)
    write(fake_yt, [s1], "Dup B")  # own folder, otherwise both copies share one file
    b = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLdupB"},
                    headers=H).json()["created"][0]
    wait_jobs(client)
    ia, ib = items(client, a), items(client, b)

    def listing(**kw):
        return client.get("/api/duplicates", params={"playlist_id": a, **kw}).json()

    r = listing()
    by_kind = {g["kind"]: g for g in r["groups"]}
    assert set(by_kind) == {"song", "repeat"}
    song_g = by_kind["song"]
    assert sorted(i["id"] for i in song_g["items"]) == sorted([ia["dupsong0001"]["id"], ia["dupsong0002"]["id"],
                                                              ib["dupsong0001"]["id"]])
    assert song_g["keep_id"] == ia["dupsong0002"]["id"]  # the clean topic upload
    assert song_g["extra"] == 2 and song_g["wasted_bytes"] > 0
    assert by_kind["repeat"]["items"][0]["copies"] == 2 and by_kind["repeat"]["extra"] == 1
    assert r["playlists"][str(a)]["title"]
    assert [g["kind"] for g in listing(kind="repeat")["groups"]] == ["repeat"]
    assert listing(q="twice")["groups"][0]["kind"] == "repeat"

    # not a duplicate: hidden, can be shown and undone
    client.post("/api/duplicates/dismiss", json={"key": song_g["key"]}, headers=H)
    assert [g["kind"] for g in listing()["groups"]] == ["repeat"]
    assert [g["key"] for g in listing(dismissed=True)["groups"]] == [song_g["key"]]
    client.post("/api/duplicates/dismiss", json={"key": song_g["key"], "undo": True}, headers=H)
    assert len(listing()["groups"]) == 2

    # ignore in Tubekeep: the copy leaves the group, its file stays
    bpath = Path(ib["dupsong0001"]["file_path"])
    client.post("/api/duplicates/apply", json={"item_ids": [ib["dupsong0001"]["id"]], "action": "ignore"},
                headers=H)
    assert items(client, b)["dupsong0001"]["status"] == "ignored" and bpath.exists()
    # delete the file only: gone from disk, stays on YouTube
    apath = Path(ia["dupsong0001"]["file_path"])
    r = client.post("/api/duplicates/apply", json={"item_ids": [ia["dupsong0001"]["id"]], "action": "file"},
                    headers=H).json()
    assert r["deleted_files"] == 1 and r["freed_bytes"] > 0 and not apath.exists()
    now = items(client, a)["dupsong0001"]
    assert now["status"] == "ignored" and not now["removed_at"] and not now["file_path"]
    assert [g["kind"] for g in listing()["groups"]] == ["repeat"]

    # twice in one YouTube playlist: needs an account that can edit it, removes only the second entry
    r = client.post("/api/duplicates/apply", json={"extra_copy_ids": [ia["duprep00004"]["id"]],
                                                   "action": "youtube"}, headers=H).json()
    assert r["removed"] == 0 and "account" in r["skipped"][0]["reason"]
    acc = db.insert("accounts", {"label": "dup", "cookies_enc": crypto.encrypt(COOKIES), "status": "valid",
                                 "cookies_updated_at": db.now(), "created_at": db.now()})
    client.patch(f"/api/playlists/{a}", json={"account_id": acc}, headers=H)
    client.get(f"/api/playlists/{a}/access?refresh=true")
    yt.tracks = [yt.entry("duprep00004", "Twice Added"), yt.entry("dupsolo0005", "Only Once"),
                 yt.entry("duprep00004", "Twice Added")]
    r = client.post("/api/duplicates/apply", json={"extra_copy_ids": [ia["duprep00004"]["id"]],
                                                   "action": "youtube"}, headers=H).json()
    assert r["removed"] == 1 and not r["skipped"]
    assert [t["videoId"] for t in yt.tracks] == ["duprep00004", "dupsolo0005"]
    assert listing()["groups"] == []
    assert not items(client, a)["duprep00004"]["removed_at"]


def test_file_shared_by_two_copies_is_kept(client):  # noqa: F811
    """Deleting the file of one copy never deletes a file another copy still uses."""
    rows = db.rows("SELECT id, playlist_id, file_path FROM items WHERE file_path IS NOT NULL AND removed_at IS NULL "
                   "AND status = 'done' LIMIT 2")
    assert len(rows) == 2
    path = rows[0]["file_path"]
    db.update("items", rows[1]["id"], {"file_path": path})
    r = client.post("/api/items/action", json={"item_ids": [rows[1]["id"]], "action": "delete_file"},
                    headers=H).json()
    assert r["ok"] and Path(path).exists()
    assert db.row("SELECT file_path FROM items WHERE id = ?", [rows[1]["id"]])["file_path"] is None

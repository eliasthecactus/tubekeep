"""End-to-end API tests against a fake yt-dlp (no network)."""
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

H = {"X-Requested-With": "tubekeep"}


def entries(*ids):
    return [{"id": i, "title": f"Song {i}", "channel": "Chan", "duration": 100,
             "thumbnails": [{"url": f"https://i.ytimg.com/vi/{i}/hq.jpg"}]} for i in ids]


@pytest.fixture(scope="module")
def client(fake_yt):
    from app.main import app
    with TestClient(app) as c:
        yield c


def set_playlist(fake_yt, title, ents):
    (fake_yt / "playlist.json").write_text(json.dumps({"_type": "playlist", "id": "PLtest", "title": title,
                                                         "entries": ents}))


def wait_jobs(c, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if not c.get("/api/jobs/live").json():
            return c.get("/api/jobs?limit=1").json()["jobs"][0]
        time.sleep(0.2)
    raise AssertionError("jobs did not finish")


def items(c, pid):
    return {i["video_id"]: i for i in c.get(f"/api/playlists/{pid}/items?removed=include").json()["items"]}


def test_setup_and_auth(client):
    assert client.get("/api/auth/state").json()["setup_needed"] is True
    assert client.get("/api/playlists").status_code == 401
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "short"})
    assert r.status_code == 400
    assert client.post("/api/auth/setup", json={"username": "admin", "password": "longenough1"}).status_code == 200
    assert client.post("/api/auth/setup", json={"username": "x", "password": "longenough1"}).status_code == 409
    # CSRF guard: mutating request without the custom header is rejected
    assert client.post("/api/playlists", json={"url": "https://x"}).status_code == 403
    client.post("/api/auth/logout", headers=H)
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "ADMIN", "password": "longenough1"}).status_code == 200


def test_sync_download_and_missing_files(client, fake_yt):
    set_playlist(fake_yt, "My Mix!", entries("aaaaaaaaaaa", "bbbbbbbbbbb", "unavail0001"))
    r = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLtest"}, headers=H)
    pid = r.json()["created"][0]
    job = wait_jobs(client)
    assert job["status"] == "done", job
    p = client.get(f"/api/playlists/{pid}").json()
    assert p["title"] == "My Mix!" and p["folder"].endswith("/My_Mix")
    its = items(client, pid)
    assert its["aaaaaaaaaaa"]["status"] == "done" and its["unavail0001"]["status"] == "unavailable"
    path = Path(its["aaaaaaaaaaa"]["file_path"])
    assert path.exists() and path.name == "Chan - Song aaaaaaaaaaa.mp3"

    # file deleted from disk: next sync downloads it again even though the DB says done
    path.unlink()
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    job = wait_jobs(client)
    assert job["stats"]["missing"] == 1 and job["stats"]["downloaded"] == 1
    assert path.exists()

    # file moved/renamed: verify re-links it via the embedded video id
    moved = path.parent / "sub" / "renamed.mp3"
    moved.parent.mkdir()
    path.rename(moved)
    client.post(f"/api/playlists/{pid}/verify", headers=H)
    wait_jobs(client)
    assert items(client, pid)["aaaaaaaaaaa"]["file_path"] == str(moved)

    # removed from playlist + new entry
    set_playlist(fake_yt, "My Mix!", entries("aaaaaaaaaaa", "ccccccccccc"))
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    wait_jobs(client)
    its = items(client, pid)
    assert its["bbbbbbbbbbb"]["removed_at"] and its["bbbbbbbbbbb"]["status"] == "done"
    assert its["ccccccccccc"]["status"] == "done"


def test_existing_files_are_adopted(client, fake_yt):
    folder = Path(os.environ["DOWNLOADS_DIR"]) / "legacy"
    folder.mkdir()
    (folder / "Chan - Song ddddddddddd.mp3").write_bytes((fake_yt / "silence.mp3").read_bytes())
    set_playlist(fake_yt, "Legacy", entries("ddddddddddd"))
    pid = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLlegacy",
                                              "folder": str(folder)}, headers=H).json()["created"][0]
    job = wait_jobs(client)
    assert job["stats"].get("adopted") == 1 and not job["stats"].get("downloaded")
    assert items(client, pid)["ddddddddddd"]["status"] == "done"


def test_private_playlist_needs_account(client, fake_yt):
    pid = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLprivate"},
                      headers=H).json()["created"][0]
    job = wait_jobs(client)
    assert job["status"] == "failed" and "signed-in account" in job["error"]
    # add an account (the fake accepts any cookies for Watch later)
    cookies = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSAPISID\tabc\n"
    r = client.post("/api/accounts", json={"label": "Main", "cookies": cookies}, headers=H).json()
    assert r["test"]["ok"] is True
    acc = client.get("/api/accounts").json()[0]
    assert acc["status"] == "valid" and "cookies_enc" not in acc
    client.patch(f"/api/playlists/{pid}", json={"account_id": acc["id"]}, headers=H)
    assert client.get(f"/api/playlists/{pid}").json()["account_label"] == "Main"


def test_metadata_preview_apply_and_override(client, fake_yt):
    pid = next(p["id"] for p in client.get("/api/playlists").json() if p["title"] == "My Mix!")
    it = items(client, pid)["ccccccccccc"]
    r = client.post("/api/metadata/preview", json={"item_ids": [it["id"]], "ops": [
        {"op": "set", "field": "album", "value": "{playlist}"}, {"op": "rename", "template": "{title}"}]},
        headers=H).json()
    prop = r["proposals"][0]
    assert prop["after"]["album"] == "My Mix!" and prop["rename_to"] == "Song ccccccccccc.mp3"
    r = client.post("/api/metadata/apply", json={"changes": [
        {"item_id": it["id"], "fields": {"album": "Best Of", "genre": "House"}, "rename_to": prop["rename_to"]}]},
        headers=H).json()
    assert r["results"][0]["ok"], r
    d = client.get(f"/api/items/{it['id']}").json()
    assert d["meta"]["album"] == "Best Of" and d["file_path"].endswith("Song ccccccccccc.mp3")
    assert d["meta_override"] == {"album": "Best Of", "genre": "House"}
    # re-download keeps the edited tags
    set_playlist(fake_yt, "My Mix!", entries("aaaaaaaaaaa", "ccccccccccc"))
    client.post("/api/items/download", json={"item_ids": [it["id"]], "force": True}, headers=H)
    job = wait_jobs(client)
    assert job["stats"].get("downloaded") == 1, job
    d = client.get(f"/api/items/{it['id']}").json()
    assert d["meta"]["album"] == "Best Of" and d["meta"]["genre"] == "House"
    assert d["file_path"].endswith("/Song ccccccccccc.mp3") and Path(d["file_path"]).exists()
    assert not (Path(d["file_path"]).parent / "Chan - Song ccccccccccc.mp3").exists()
    assert client.get(f"/api/items/{it['id']}/cover", follow_redirects=False).status_code in (200, 307)


def test_proxies_and_settings(client):
    r = client.post("/api/proxies", json={"text": "http://u:secret@1.2.3.4:8080\nsocks5://5.6.7.8:1080"}, headers=H)
    assert len(r.json()["created"]) == 2
    lst = client.get("/api/proxies").json()
    assert lst[0]["url"] == "http://u:****@1.2.3.4:8080"
    assert client.post("/api/proxies", json={"url": "ftp://x"}, headers=H).status_code == 400
    s = client.put("/api/settings", json={"concurrency": 99, "openrouter_key": "sk-or-123",
                                          "default_schedule": "@daily"}, headers=H).json()
    assert s["concurrency"] == 16 and s["openrouter_key_set"] is True and s["default_schedule"] == "0 3 * * *"
    assert "openrouter_key_enc" not in s
    assert client.put("/api/settings", json={"default_schedule": "nope"}, headers=H).status_code == 400


def test_delete_playlist_with_files(client):
    pl = next(p for p in client.get("/api/playlists").json() if p["title"] == "Legacy")
    f = Path(pl["folder"]) / "Chan - Song ddddddddddd.mp3"
    assert f.exists()
    client.delete(f"/api/playlists/{pl['id']}?delete_files=true", headers=H)
    assert not f.exists()
    assert all(p["id"] != pl["id"] for p in client.get("/api/playlists").json())

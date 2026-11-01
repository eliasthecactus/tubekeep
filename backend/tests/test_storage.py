"""Folder markers: an unmounted drive must not look like 'every file was deleted'."""
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, storage

H = {"X-Requested-With": "tubekeep"}


@pytest.fixture(scope="module")
def client(fake_yt):
    from app.main import app
    with TestClient(app) as c:
        if c.get("/api/auth/state").json()["setup_needed"]:
            c.post("/api/auth/setup", json={"username": "admin", "password": "longenough1"})
        else:
            c.post("/api/auth/login", json={"username": "admin", "password": "longenough1"})
        yield c


def wait_jobs(c, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if not c.get("/api/jobs/live").json():
            return c.get("/api/jobs?limit=1").json()["jobs"][0]
        time.sleep(0.2)
    raise AssertionError("jobs did not finish")


def statuses(c, pid):
    return {i["video_id"]: i["status"] for i in c.get(f"/api/playlists/{pid}/items").json()["items"]}


def set_playlist(fake_yt, pid, ids):
    (fake_yt / "playlist.json").write_text(json.dumps({"_type": "playlist", "id": pid, "title": f"Store {pid}",
        "entries": [{"id": i, "title": f"Song {i}", "channel": "Chan", "duration": 100} for i in ids]}))


def test_unmounted_folder_pauses_and_user_decides(client, fake_yt):
    ids = ["store000001", "store000002"]
    set_playlist(fake_yt, "PLstoreA", ids)
    pid = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLstoreA"},
                      headers=H).json()["created"][0]
    wait_jobs(client)
    p = client.get(f"/api/playlists/{pid}").json()
    folder = Path(p["folder"])
    assert (folder / storage.MARKER).exists() and p["storage_offline_at"] is None
    assert set(statuses(client, pid).values()) == {"done"}

    # "unmount": the folder is gone
    hidden = folder.with_name(folder.name + "-mounted")
    folder.rename(hidden)
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    job = wait_jobs(client)
    assert job["status"] == "failed" and "marker" in job["error"]
    assert not folder.exists(), "nothing may be created or downloaded on the empty mount point"
    assert set(statuses(client, pid).values()) == {"done"}
    assert client.get(f"/api/playlists/{pid}").json()["storage_offline_at"]
    # downloads and file checks are blocked too
    client.post(f"/api/playlists/{pid}/verify", headers=H)
    assert wait_jobs(client)["status"] == "failed"
    assert set(statuses(client, pid).values()) == {"done"}

    # check again while it's still gone: stays offline, no job
    assert client.post(f"/api/playlists/{pid}/storage", json={"action": "check"}, headers=H).json()["offline"]
    assert not client.get("/api/jobs/live").json()

    # drive is back: check again resumes with a sync, nothing downloaded again
    hidden.rename(folder)
    assert not client.post(f"/api/playlists/{pid}/storage", json={"action": "check"}, headers=H).json()["offline"]
    job = wait_jobs(client)
    assert job["status"] == "done" and not job["stats"].get("downloaded")
    assert client.get(f"/api/playlists/{pid}").json()["storage_offline_at"] is None

    # gone for good, user keeps them as missing: no downloads
    shutil.rmtree(folder)
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    wait_jobs(client)
    client.post(f"/api/playlists/{pid}/storage", json={"action": "keep_missing"}, headers=H)
    job = wait_jobs(client)
    assert job["type"] == "verify" and job["status"] == "done"
    assert set(statuses(client, pid).values()) == {"missing"}
    assert (folder / storage.MARKER).exists()

    # another time the user wants everything downloaded again
    shutil.rmtree(folder)
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    wait_jobs(client)
    client.post(f"/api/playlists/{pid}/storage", json={"action": "redownload"}, headers=H)
    job = wait_jobs(client)
    assert job["status"] == "done" and job["stats"]["downloaded"] == 2
    assert set(statuses(client, pid).values()) == {"done"}
    assert all((folder / f"Chan - Song {i}.mp3").exists() for i in ids)


def test_playlists_from_before_markers(client, fake_yt):
    set_playlist(fake_yt, "PLstoreB", ["store000003"])
    pid = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLstoreB"},
                      headers=H).json()["created"][0]
    wait_jobs(client)
    folder = Path(client.get(f"/api/playlists/{pid}").json()["folder"])

    # upgraded install with its files present: marker is written silently
    (folder / storage.MARKER).unlink()
    db.update("playlists", pid, {"marker_seen": 0})
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    assert wait_jobs(client)["status"] == "done"
    assert (folder / storage.MARKER).exists()

    # upgraded install while the drive is not mounted: offline, not 'all deleted'
    (folder / storage.MARKER).unlink()
    db.update("playlists", pid, {"marker_seen": 0})
    hidden = folder.with_name(folder.name + "-away")
    folder.rename(hidden)
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    assert wait_jobs(client)["status"] == "failed"
    assert statuses(client, pid) == {"store000003": "done"}

    # bulk: check all offline playlists once the drive is back
    hidden.rename(folder)
    r = client.post("/api/storage/offline", json={"action": "check"}, headers=H).json()["results"]
    assert {"id": pid, "offline": False} in r
    wait_jobs(client)


def test_marker_moves_with_the_folder_and_is_removed_with_the_playlist(client, fake_yt):
    set_playlist(fake_yt, "PLstoreC", ["store000004"])
    pid = client.post("/api/playlists", json={"url": "https://www.youtube.com/playlist?list=PLstoreC"},
                      headers=H).json()["created"][0]
    wait_jobs(client)
    old = Path(client.get(f"/api/playlists/{pid}").json()["folder"])
    new = old.with_name("Store_C_moved")
    client.patch(f"/api/playlists/{pid}", json={"folder": str(new), "move_files": True}, headers=H)
    assert (new / storage.MARKER).exists()
    client.post(f"/api/playlists/{pid}/sync", headers=H)
    assert wait_jobs(client)["status"] == "done"
    client.delete(f"/api/playlists/{pid}?delete_files=true", headers=H)
    assert not new.exists()

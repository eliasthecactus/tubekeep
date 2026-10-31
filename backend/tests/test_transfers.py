"""Transfer/sync engine against two in-memory fake music services."""
import asyncio
import copy

import pytest

from app import db, matching, providers, transfers
from app.providers.base import Provider, track

CATALOG = [
    track("1", "One More Time", ["Daft Punk"], "Discovery", 320, "ISRC001"),
    track("2", "Blinding Lights", ["The Weeknd"], "After Hours", 200, "ISRC002"),
    track("3", "Bohemian Rhapsody", ["Queen"], None, 355, "ISRC003"),
    track("4", "Wonderwall", ["Oasis"], None, 258, "ISRC004"),
    track("5", "Levels", ["Avicii"], None, 199, "ISRC005"),
    track("6", "Strobe", ["deadmau5"], None, 214, None),
]
STORE: dict[str, dict] = {}


def svc_track(svc: str, cat: dict) -> dict:
    """Same song, provider specific id (and sometimes noisier title)."""
    t = copy.deepcopy(cat)
    t["id"] = f"{svc}-{cat['id']}"
    if svc == "B" and cat["id"] == "3":
        t["title"] = "Bohemian Rhapsody - Remastered 2011"
        t["isrc"] = None
    return t


class Fake(Provider):
    can_isrc = True

    def __init__(self, conn, creds):
        super().__init__(conn, creds)
        self.svc = creds["svc"]
        self.name = f"fake{self.svc}"
        self.label = f"Fake {self.svc}"
        self.can_remove = creds.get("can_remove", True)
        self.st = STORE.setdefault(self.svc, {"playlists": {}, "n": 0})

    async def whoami(self):
        return self.label

    async def list_playlists(self):
        return [{"id": k, "name": k} for k in self.st["playlists"]]

    async def get_tracks(self, pid):
        return copy.deepcopy(self.st["playlists"][pid])

    async def playlist_name(self, pid):
        return pid

    async def search(self, query, limit=8):
        q = matching.norm(query)
        return [svc_track(self.svc, c) for c in CATALOG
                if matching.norm(c["title"]) in q or q in matching.norm(c["title"])][:limit]

    async def search_track(self, t):
        return await self.search(matching.clean_title(t["title"]))

    async def by_isrc(self, isrc):
        return [svc_track(self.svc, c) for c in CATALOG if c["isrc"] == isrc]

    async def create_playlist(self, name, description):
        self.st["n"] += 1
        pid = f"new{self.st['n']}"
        self.st["playlists"][pid] = []
        return pid

    async def add_tracks(self, pid, tracks):
        self.st["playlists"][pid] += copy.deepcopy(tracks)

    async def remove_tracks(self, pid, tracks):
        ids = {t["id"] for t in tracks}
        self.st["playlists"][pid] = [t for t in self.st["playlists"][pid] if t["id"] not in ids]


class Ctx:
    def __init__(self):
        self.stats, self.lines = {}, []

    def log(self, m):
        self.lines.append(m)

    def set_phase(self, p):
        pass

    def publish(self):
        pass


@pytest.fixture(autouse=True)
def setup(fake_yt):
    db.init()
    providers.PROVIDERS["fakeA"] = Fake
    providers.PROVIDERS["fakeB"] = Fake
    STORE.clear()
    db.execute("DELETE FROM match_cache")
    yield


def make(two_way=False, removals=False, b_can_remove=True, b_pid=None, a_tracks=("1", "2", "3")):
    STORE["A"] = {"playlists": {"mix": [svc_track("A", c) for c in CATALOG if c["id"] in a_tracks]}, "n": 0}
    STORE.setdefault("B", {"playlists": {}, "n": 0})
    a = providers.create_connection("fakeA", "A", {"svc": "A"})
    b = providers.create_connection("fakeB", "B", {"svc": "B", "can_remove": b_can_remove})
    return db.insert("transfers", {"name": "t", "a_conn_id": a, "a_playlist_id": "mix", "a_playlist_name": "Mix",
                                   "b_conn_id": b, "b_playlist_id": b_pid, "keep_synced": 1, "two_way": int(two_way),
                                   "sync_removals": int(removals), "created_at": db.now()})


def run(tid):
    ctx = Ctx()
    asyncio.run(transfers.run(ctx, tid))
    return ctx.stats


def ids(svc, pid):
    return [t["id"] for t in STORE[svc]["playlists"][pid]]


def b_pid(tid):
    return db.row("SELECT b_playlist_id FROM transfers WHERE id = ?", [tid])["b_playlist_id"]


def test_one_way_creates_and_fills_playlist():
    tid = make()
    stats = run(tid)
    pid = b_pid(tid)
    assert pid and ids("B", pid) == ["B-1", "B-2", "B-3"]
    assert stats["added_to_b"] == 3
    # running again changes nothing
    assert run(tid).get("added_to_b") is None
    assert ids("B", pid) == ["B-1", "B-2", "B-3"]


def test_unknown_track_is_not_found_and_can_be_resolved():
    tid = make()
    STORE["A"]["playlists"]["mix"].append(track("A-x", "Totally Unknown Song", ["Nobody"], None, 100))
    stats = run(tid)
    assert stats["not_found"] == 1
    it = db.row("SELECT * FROM transfer_items WHERE transfer_id = ? AND track_id = 'A-x'", [tid])
    assert it["status"] == "not_found"
    db.update("transfer_items", it["id"], {"status": "pending_add", "match_id": "B-6", "manual": 1,
                                           "match_meta": db.jdump(svc_track("B", CATALOG[5]))})
    run(tid)
    assert "B-6" in ids("B", b_pid(tid))


def test_one_way_additions_and_removals():
    tid = make(removals=True)
    run(tid)
    STORE["A"]["playlists"]["mix"] = [t for t in STORE["A"]["playlists"]["mix"] if t["id"] != "A-2"]
    STORE["A"]["playlists"]["mix"].append(svc_track("A", CATALOG[3]))
    stats = run(tid)
    assert stats["removed_from_b"] == 1 and stats["added_to_b"] == 1
    assert ids("B", b_pid(tid)) == ["B-1", "B-3", "B-4"]


def test_target_removal_is_not_undone_in_one_way():
    tid = make()
    run(tid)
    pid = b_pid(tid)
    STORE["B"]["playlists"][pid] = [t for t in STORE["B"]["playlists"][pid] if t["id"] != "B-1"]
    run(tid)
    assert "B-1" not in ids("B", pid)
    assert db.row("SELECT status FROM transfer_items WHERE transfer_id = ? AND track_id = 'A-1'", [tid])["status"] == "gone"


def test_two_way_merge_with_existing_target():
    STORE["B"] = {"playlists": {"existing": [svc_track("B", CATALOG[2]), svc_track("B", CATALOG[4])]}, "n": 0}
    tid = make(two_way=True, removals=True, b_pid="existing")
    stats = run(tid)
    # Queen is on both (matched through the remaster title), Daft Punk + Weeknd go to B, Avicii goes to A
    assert stats.get("matched") == 1
    assert sorted(ids("B", "existing")) == ["B-1", "B-2", "B-3", "B-5"]
    assert sorted(ids("A", "mix")) == ["A-1", "A-2", "A-3", "A-5"]
    # removal on B propagates to A, addition on A propagates to B
    STORE["B"]["playlists"]["existing"] = [t for t in STORE["B"]["playlists"]["existing"] if t["id"] != "B-1"]
    STORE["A"]["playlists"]["mix"].append(svc_track("A", CATALOG[3]))
    stats = run(tid)
    assert stats["removed_from_a"] == 1 and stats["added_to_b"] == 1
    assert "A-1" not in ids("A", "mix") and "B-4" in ids("B", "existing")
    # stable afterwards
    stats = run(tid)
    assert not any(k.startswith(("added", "removed")) for k in stats)


def test_target_without_remove_support():
    tid = make(removals=True, b_can_remove=False)
    run(tid)
    STORE["A"]["playlists"]["mix"].pop(0)
    stats = run(tid)
    assert "removed_from_b" not in stats
    assert len(ids("B", b_pid(tid))) == 3


def test_matching_scores():
    s = track("x", "Strobe - Radio Edit", ["deadmau5"], None, 214)
    assert matching.score(s, track("y", "Strobe (Radio Edit)", ["deadmau5"], None, 215)) > 0.9
    assert matching.score(s, track("y", "Strobe (Live)", ["deadmau5"], None, 400)) < 0.6
    vid = track("v", "Daft Punk - One More Time (Official Video)", ["Daft Punk"], None, 324, resultType="video")
    assert matching.score(track("x", "One More Time", ["Daft Punk"], None, 320), vid) > 0.85

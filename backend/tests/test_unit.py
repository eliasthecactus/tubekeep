import time

import pytest

from app import accounts, library, metadata_ops, scheduler, tags, ytdlp
from conftest import HAS_FFMPEG, ROOT, make_audio


def test_legacy_folder_name_matches_old_script():
    assert library.legacy_folder_name("Chill Vibes ☕ 2024!") == "Chill_Vibes__2024"
    assert library.legacy_folder_name("") == "UnknownPlaylist"


def test_norm_and_safe_filename():
    assert library.norm("AC/DC – Thunderstruck (Official)") == library.norm("ac⧸dc - thunderstruck official")
    assert "/" not in library.safe_filename("a/b: c?")


def test_find_file_by_id_name_and_title():
    index = {"by_id": {"aaaaaaaaaaa": "/x/one.mp3"},
             "by_name": {library.norm("Chan - Song Two"): ["/x/Chan - Song Two.mp3"],
                         library.norm("Other - Third Song Here"): ["/x/Other - Third Song Here.mp3"]}}
    t = "%(channel)s - %(title)s"
    assert library.find_file(index, {"id": "aaaaaaaaaaa", "title": "x"}, t, set()) == "/x/one.mp3"
    assert library.find_file(index, {"id": "b", "title": "Song Two", "channel": "Chan"}, t, set()) == \
        "/x/Chan - Song Two.mp3"
    # channel differs (old script mapped artist), title suffix still unique
    assert library.find_file(index, {"id": "c", "title": "Third Song Here", "channel": "Nope"}, t, set()) == \
        "/x/Other - Third Song Here.mp3"
    assert library.find_file(index, {"id": "d", "title": "Song Two", "channel": "Chan"}, t,
                             {"/x/Chan - Song Two.mp3"}) is None


def test_classify():
    assert ytdlp.classify("ERROR: [youtube] x: Video unavailable") == "unavailable"
    assert ytdlp.classify("ERROR: HTTP Error 429: Too Many Requests") == "blocked"
    assert ytdlp.classify("Sign in to confirm you're not a bot") == "blocked"
    assert ytdlp.classify("ERROR: This playlist is private") == "auth"
    assert ytdlp.classify("The provided YouTube account cookies are no longer valid") == "auth"
    assert ytdlp.classify("Unable to connect to proxy", used_proxy=True) == "proxy"
    assert ytdlp.classify("Unable to connect to proxy", used_proxy=False) == "transient"
    assert ytdlp.classify("Read timed out") == "transient"


def test_download_args_overrides():
    s = {"audio_format": "mp3", "audio_quality": "0", "filename_template": "%(title)s", "embed_thumbnail": True,
         "square_cover": False, "artist_from_channel": True, "extra_args": "--no-part"}
    args = ytdlp.download_args(s, "/music/x", {"audio_format": "opus", "square_cover": True})
    assert args[args.index("--audio-format") + 1] == "opus"
    assert "--ppa" in args and "home:/music/x" in args and "--no-part" in args
    assert args[args.index("-o") + 1] == "%(title)s.%(ext)s"


def test_validate_cookies():
    good = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSAPISID\tabc\n"
    info = accounts.validate_cookies(good)
    assert info["cookie_count"] == 1 and info["expires_at"] == 1893456000
    with pytest.raises(ValueError):
        accounts.validate_cookies(".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tx\n")  # no login cookie
    with pytest.raises(ValueError):
        accounts.validate_cookies("not a cookie file")


def test_schedule():
    assert scheduler.normalize("@daily") == "0 3 * * *"
    with pytest.raises(ValueError):
        scheduler.normalize("every day")
    nr = scheduler.next_run("0 * * * *")
    assert time.time() < nr <= time.time() + 3601


def _item(meta, **kw):
    return {"_meta": meta, "v_title": kw.get("yt", ""), "v_channel": "Chan", "p_title": "My List",
            "position": kw.get("pos", 1), "video_id": "abc"}


def test_metadata_ops_chain():
    meta = {k: "" for k in tags.FIELDS}
    it = _item(meta, yt="Daft Punk - One More Time (Official Video)")
    ops = [{"op": "parse", "source": "yt_title", "pattern": "{artist} - {title}"},
           {"op": "clean", "field": "title"},
           {"op": "set", "field": "album", "value": "{playlist}"},
           {"op": "number", "start": 1},
           {"op": "case", "field": "artist", "mode": "upper"},
           {"op": "rename", "template": "{artist} - {title}"}]
    out, rename = metadata_ops.apply_ops(meta, ops, metadata_ops.context(it), 2, 10)
    assert out["artist"] == "DAFT PUNK" and out["title"] == "One More Time"
    assert out["album"] == "My List" and out["track"] == "3" and out["track_total"] == "10"
    assert rename == "DAFT PUNK - One More Time"


def test_metadata_replace_regex_and_skip():
    meta = {k: "" for k in tags.FIELDS} | {"title": "Song (feat. X) [HD]"}
    out, _ = metadata_ops.apply_ops(meta, [{"op": "replace", "field": "title", "find": r"\s*\[.*?\]", "replace": "",
                                            "regex": True}], {}, 0, 1)
    assert out["title"] == "Song (feat. X)"
    out, _ = metadata_ops.apply_ops(meta | {"comment": "A | B | C"},
                                    [{"op": "parse", "source": "comment", "pattern": "{_} | {album} | {_}"}], {}, 0, 1)
    assert out["album"] == "B"
    with pytest.raises(ValueError):
        metadata_ops.apply_ops(meta, [{"op": "replace", "field": "title", "find": "(", "regex": True}], {}, 0, 1)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg needed to create audio files")
@pytest.mark.parametrize("fmt", ["mp3", "m4a", "opus", "flac"])
def test_tags_roundtrip(fmt):
    p = make_audio(ROOT / f"rt.{fmt}", fmt)
    tags.write(p, {"title": "T", "artist": "A", "album": "Al", "album_artist": "AA", "track": "3",
                   "track_total": "12", "disc": "1", "year": "2021", "genre": "Pop", "comment": "c",
                   "lyrics": "la la"})
    m = tags.read(p)
    assert (m["title"], m["artist"], m["album"], m["album_artist"]) == ("T", "A", "Al", "AA")
    assert (m["track"], m["track_total"], m["year"], m["genre"]) == ("3", "12", "2021", "Pop")
    assert m["lyrics"] == "la la" and m["comment"] == "c"
    tags.write(p, {"album": ""})
    assert tags.read(p)["album"] == ""
    from PIL import Image
    from io import BytesIO
    buf = BytesIO()
    Image.new("RGB", (64, 40), "red").save(buf, "JPEG")
    tags.write_cover(p, tags.square_jpeg(buf.getvalue()))
    data, mime = tags.read_cover(p)
    assert mime == "image/jpeg" and Image.open(BytesIO(data)).size == (40, 40)
    assert tags.read(p)["has_cover"]

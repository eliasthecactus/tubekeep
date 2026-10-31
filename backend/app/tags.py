"""Read/write common audio tags + cover art (mp3, m4a, opus/ogg, flac) with mutagen."""
import base64
import re
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, USLT,
                         ID3NoHeaderError)
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

FIELDS = ["title", "artist", "album", "album_artist", "track", "track_total", "disc", "year", "genre",
          "comment", "lyrics"]
YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")


class Unsupported(Exception):
    pass


def _split_num(v: str | None) -> tuple[str, str]:
    if not v:
        return "", ""
    a, _, b = str(v).partition("/")
    return a.strip(), b.strip()


def _clean(d: dict) -> dict:
    return {k: ("" if d.get(k) is None else str(d.get(k))) for k in FIELDS}


# ------------------------------------------------------------------ read

def read(path: str | Path) -> dict:
    """Returns {field: str} plus 'has_cover', 'url' (source URL if found), 'format'."""
    path = str(path)
    f = mutagen.File(path)
    if f is None:
        raise Unsupported("Unsupported file type")
    out: dict = {k: "" for k in FIELDS}
    url = ""
    has_cover = False
    if isinstance(f.tags, ID3) or path.lower().endswith(".mp3"):
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()

        def g(fid: str) -> str:
            fr = t.get(fid)
            return str(fr.text[0]) if fr and getattr(fr, "text", None) else ""
        out["title"], out["artist"], out["album"] = g("TIT2"), g("TPE1"), g("TALB")
        out["album_artist"], out["genre"] = g("TPE2"), g("TCON")
        out["year"] = g("TDRC")
        out["track"], out["track_total"] = _split_num(g("TRCK"))
        out["disc"], _ = _split_num(g("TPOS"))
        # ffmpeg writes the full comment as TXXX:comment plus a 30 char "ID3v1 Comment" COMM
        comms = [c for c in t.getall("COMM") if c.desc != "ID3v1 Comment"]
        txc = [x for x in t.getall("TXXX") if x.desc.lower() == "comment"]
        v1 = [c for c in t.getall("COMM") if c.desc == "ID3v1 Comment"]
        for src in (comms, txc, v1):
            if src and src[0].text:
                out["comment"] = str(src[0].text[0])
                break
        lyr = t.getall("USLT")
        out["lyrics"] = lyr[0].text if lyr else ""
        for tx in t.getall("TXXX"):
            if tx.desc.lower() in ("purl", "url", "source") and tx.text:
                url = str(tx.text[0])
        has_cover = bool(t.getall("APIC"))
        fmt = "mp3"
    elif isinstance(f, MP4):
        t = f.tags or {}

        def g(k: str) -> str:
            v = t.get(k)
            return str(v[0]) if v else ""
        out.update(title=g("\xa9nam"), artist=g("\xa9ART"), album=g("\xa9alb"), album_artist=g("aART"),
                   year=g("\xa9day"), genre=g("\xa9gen"), comment=g("\xa9cmt"), lyrics=g("\xa9lyr"))
        if t.get("trkn"):
            n, tot = t["trkn"][0]
            out["track"], out["track_total"] = (str(n) if n else ""), (str(tot) if tot else "")
        if t.get("disk"):
            out["disc"] = str(t["disk"][0][0] or "")
        has_cover = bool(t.get("covr"))
        fmt = "m4a"
    elif isinstance(f, (OggOpus, OggVorbis, FLAC)):
        t = f.tags or {}

        def g(k: str) -> str:
            v = t.get(k)
            return str(v[0]) if v else ""
        out.update(title=g("title"), artist=g("artist"), album=g("album"), album_artist=g("albumartist"),
                   year=g("date"), genre=g("genre"), comment=g("comment") or g("description"),
                   lyrics=g("lyrics"))
        out["track"], tt = _split_num(g("tracknumber"))
        out["track_total"] = tt or g("tracktotal") or g("totaltracks")
        out["disc"], _ = _split_num(g("discnumber"))
        url = g("purl")
        if isinstance(f, FLAC):
            has_cover = bool(f.pictures)
        else:
            has_cover = bool(t.get("metadata_block_picture"))
        fmt = "flac" if isinstance(f, FLAC) else ("opus" if isinstance(f, OggOpus) else "ogg")
    else:
        raise Unsupported(f"Tag editing not supported for {type(f).__name__}")
    out["has_cover"] = has_cover
    out["format"] = fmt
    out["url"] = url or (out["comment"] if "youtu" in out["comment"] else "")
    return out


def youtube_id(meta: dict) -> str | None:
    for k in ("url", "comment"):
        m = YT_ID_RE.search(meta.get(k) or "")
        if m:
            return m.group(1)
    return None


# ------------------------------------------------------------------ write

def write(path: str | Path, fields: dict) -> None:
    """Writes only the given fields. Empty string deletes the tag."""
    path = str(path)
    fields = {k: ("" if v is None else str(v).strip() if k != "lyrics" else str(v)) for k, v in fields.items()
              if k in FIELDS}
    if not fields:
        return
    f = mutagen.File(path)
    if f is None:
        raise Unsupported("Unsupported file type")
    if path.lower().endswith(".mp3") or isinstance(f.tags, ID3):
        _write_id3(path, fields)
    elif isinstance(f, MP4):
        _write_mp4(f, fields)
    elif isinstance(f, (OggOpus, OggVorbis, FLAC)):
        _write_vorbis(f, fields)
    else:
        raise Unsupported(f"Tag editing not supported for {type(f).__name__}")


def _write_id3(path: str, fields: dict) -> None:
    try:
        t = ID3(path)
    except ID3NoHeaderError:
        t = ID3()
    simple = {"title": TIT2, "artist": TPE1, "album": TALB, "album_artist": TPE2, "genre": TCON, "year": TDRC}
    for k, cls in simple.items():
        if k in fields:
            t.delall(cls.__name__)
            if fields[k]:
                t.add(cls(encoding=3, text=fields[k]))
    if "track" in fields or "track_total" in fields:
        cur_n, cur_t = _split_num(str(t.get("TRCK").text[0]) if t.get("TRCK") else "")
        n = fields.get("track", cur_n)
        tot = fields.get("track_total", cur_t)
        t.delall("TRCK")
        if n:
            t.add(TRCK(encoding=3, text=f"{n}/{tot}" if tot else n))
    if "disc" in fields:
        t.delall("TPOS")
        if fields["disc"]:
            t.add(TPOS(encoding=3, text=fields["disc"]))
    if "comment" in fields:
        # keep the yt-dlp source URL as TXXX:purl so the file stays identifiable
        for c in t.getall("COMM"):
            if "youtu" in "".join(c.text) and not any(x.desc == "purl" for x in t.getall("TXXX")):
                t.add(TXXX(encoding=3, desc="purl", text=c.text))
        t.delall("COMM")
        for x in [x for x in t.getall("TXXX") if x.desc.lower() == "comment"]:
            t.delall(x.HashKey)
        if fields["comment"]:
            t.add(COMM(encoding=3, lang="eng", desc="", text=fields["comment"]))
    if "lyrics" in fields:
        t.delall("USLT")
        if fields["lyrics"]:
            t.add(USLT(encoding=3, lang="eng", desc="", text=fields["lyrics"]))
    t.save(path, v2_version=3)


def _write_mp4(f: MP4, fields: dict) -> None:
    if f.tags is None:
        f.add_tags()
    t = f.tags
    keys = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "album_artist": "aART",
            "year": "\xa9day", "genre": "\xa9gen", "comment": "\xa9cmt", "lyrics": "\xa9lyr"}
    for k, mk in keys.items():
        if k in fields:
            if fields[k]:
                t[mk] = [fields[k]]
            else:
                t.pop(mk, None)
    if "track" in fields or "track_total" in fields:
        n0, t0 = (t.get("trkn") or [(0, 0)])[0]
        n = int(fields.get("track", n0) or 0) if str(fields.get("track", n0) or "0").isdigit() else 0
        tot = int(fields.get("track_total", t0) or 0) if str(fields.get("track_total", t0) or "0").isdigit() else 0
        if n:
            t["trkn"] = [(n, tot)]
        else:
            t.pop("trkn", None)
    if "disc" in fields:
        if str(fields["disc"]).isdigit():
            t["disk"] = [(int(fields["disc"]), 0)]
        else:
            t.pop("disk", None)
    f.save()


def _write_vorbis(f, fields: dict) -> None:
    if f.tags is None:
        f.add_tags()
    t = f.tags
    keys = {"title": "title", "artist": "artist", "album": "album", "album_artist": "albumartist",
            "year": "date", "genre": "genre", "comment": "comment", "lyrics": "lyrics", "disc": "discnumber",
            "track": "tracknumber", "track_total": "tracktotal"}
    for k, vk in keys.items():
        if k in fields:
            if fields[k]:
                t[vk] = [fields[k]]
            elif vk in t:
                del t[vk]
    f.save()


# ------------------------------------------------------------------ cover

def read_cover(path: str | Path) -> tuple[bytes, str] | None:
    path = str(path)
    f = mutagen.File(path)
    if f is None:
        return None
    if path.lower().endswith(".mp3") or isinstance(f.tags, ID3):
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            return None
        pics = t.getall("APIC")
        if pics:
            front = next((p for p in pics if p.type == 3), pics[0])
            return front.data, front.mime or "image/jpeg"
    elif isinstance(f, MP4):
        c = (f.tags or {}).get("covr")
        if c:
            mime = "image/png" if c[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            return bytes(c[0]), mime
    elif isinstance(f, FLAC):
        if f.pictures:
            return f.pictures[0].data, f.pictures[0].mime
    elif isinstance(f, (OggOpus, OggVorbis)):
        b = (f.tags or {}).get("metadata_block_picture")
        if b:
            p = Picture(base64.b64decode(b[0]))
            return p.data, p.mime
    return None


def write_cover(path: str | Path, data: bytes, mime: str = "image/jpeg") -> None:
    path = str(path)
    f = mutagen.File(path)
    if f is None:
        raise Unsupported("Unsupported file type")
    if path.lower().endswith(".mp3") or isinstance(f.tags, ID3):
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()
        t.delall("APIC")
        t.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        t.save(path, v2_version=3)
    elif isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        f.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        f.save()
    elif isinstance(f, (FLAC, OggOpus, OggVorbis)):
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
        if isinstance(f, FLAC):
            f.clear_pictures()
            f.add_picture(pic)
        else:
            if f.tags is None:
                f.add_tags()
            f.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
        f.save()
    else:
        raise Unsupported("Cover editing not supported for this format")


def square_jpeg(data: bytes, size: int | None = None) -> bytes:
    """Center-crops an image to a square JPEG (used for YouTube thumbnails)."""
    from io import BytesIO

    from PIL import Image
    im = Image.open(BytesIO(data)).convert("RGB")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    if size and s > size:
        im = im.resize((size, size), Image.LANCZOS)
    out = BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()

"""Filesystem side: folder names, scanning existing files, matching files to videos, verifying."""
import os
import re
import unicodedata
from pathlib import Path

from . import config, db, settings_store, tags


def legacy_folder_name(title: str) -> str:
    """Same as the old script: tr -cd '[:alnum:] _-' | sed 's/ /_/g'."""
    name = re.sub(r"[^A-Za-z0-9 _-]", "", title or "").replace(" ", "_")
    return name or "UnknownPlaylist"


def default_root() -> Path:
    root = settings_store.get("default_folder_root") or ""
    return Path(root) if root else config.DOWNLOADS_DIR


def default_folder(title: str) -> str:
    return str(default_root() / legacy_folder_name(title))


def check_folder(path: str, create: bool = True) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("Folder must be an absolute path inside the container, e.g. /downloads/Chill")
    p = Path(os.path.normpath(p))
    if create:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ValueError(f"Cannot create folder: {e}") from e
    if p.exists() and not os.access(p, os.W_OK):
        raise ValueError("Folder is not writable by the container user (check PUID/PGID)")
    return str(p)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").casefold()
    return "".join(ch for ch in s if ch.isalnum())


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', lambda m: {"/": "⧸", "\\": "⧹", ":": "：", "*": "＊", "?": "？",
                                                       '"': "＂", "<": "＜", ">": "＞", "|": "｜"}.get(m.group(0), ""),
                  name).strip().strip(".")
    return name[:200] or "untitled"


def list_audio(folder: str) -> list[Path]:
    root = Path(folder)
    if not root.is_dir():
        return []
    out = []
    for dirpath, _, files in os.walk(root):
        if "/." in dirpath:
            continue
        for fn in files:
            if fn.startswith(".") or fn.endswith((".part", ".ytdl", ".temp")):
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() in config.AUDIO_EXTS:
                out.append(p)
    return out


def scan(folder: str, read_tags: bool = True, skip_tags: set[str] | None = None) -> dict:
    """Index a folder: {'by_id': {ytid: path}, 'by_name': {normstem: [path]}, 'files': [path]}.
    Tags are only read for files not in skip_tags (files that already belong to a track)."""
    skip_tags = skip_tags or set()
    by_id: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    files = list_audio(folder)
    for p in files:
        by_name.setdefault(norm(p.stem), []).append(str(p))
        m = re.search(r"\[([\w-]{11})\]", p.stem)  # "%(title)s [%(id)s]" style templates
        if m:
            by_id.setdefault(m.group(1), str(p))
        if read_tags and str(p) not in skip_tags:
            try:
                vid = tags.youtube_id(tags.read(p))
                if vid:
                    by_id.setdefault(vid, str(p))
            except Exception:
                pass
    return {"by_id": by_id, "by_name": by_name, "files": [str(p) for p in files]}


def render_template(tmpl: str, v: dict) -> str:
    """Very small subset of yt-dlp templates, used only for matching existing files."""
    tmpl = tmpl.removesuffix(".%(ext)s")

    def rep(m: re.Match) -> str:
        keys = m.group(1).split(",")
        for k in keys:
            val = v.get(k.strip())
            if val:
                return str(val)
        return "NA"
    return re.sub(r"%\(([^)]+)\)[sdj]", rep, tmpl)


def find_file(index: dict, video: dict, template: str, taken: set[str]) -> str | None:
    vid = video["id"]
    p = index["by_id"].get(vid)
    if p and p not in taken:
        return p
    fields = {"id": vid, "title": video.get("title"), "channel": video.get("channel"),
              "uploader": video.get("channel"), "artist": video.get("yt_artist") or video.get("channel"),
              "track": video.get("yt_track") or video.get("title")}
    candidates = [render_template(template, fields), f"{video.get('channel')} - {video.get('title')}",
                  f"{video.get('yt_artist')} - {video.get('title')}", video.get("title") or ""]
    for c in candidates:
        n = norm(c)
        if len(n) < 3:
            continue
        for p in index["by_name"].get(n, []):
            if p not in taken:
                return p
    # suffix match on the title, only when unambiguous
    t = norm(video.get("title") or "")
    if len(t) >= 6:
        hits = [p for name, ps in index["by_name"].items() if name.endswith(t) for p in ps if p not in taken]
        if len(hits) == 1:
            return hits[0]
    return None


def file_ok(path: str | None) -> bool:
    if not path:
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def read_meta(path: str) -> dict | None:
    try:
        m = tags.read(path)
        return {k: m.get(k, "") for k in tags.FIELDS} | {"has_cover": m.get("has_cover", False),
                                                          "format": m.get("format")}
    except Exception:
        return None


def adopt(item_id: int, path: str) -> None:
    meta = read_meta(path)
    db.update("items", item_id, {
        "status": "done", "file_path": path, "file_size": os.path.getsize(path), "error": None,
        "error_kind": None, "downloaded_at": os.path.getmtime(path), "updated_at": db.now(),
        "meta": db.jdump(meta) if meta else None,
    })

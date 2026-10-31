import mimetypes

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel

from .. import ai, auth, db, library, metadata_ops, tags
from .playlists import ITEM_SELECT, item_public

router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


class PreviewIn(BaseModel):
    item_ids: list[int]
    ops: list[dict]


class ApplyIn(BaseModel):
    changes: list[dict]


class AiIn(BaseModel):
    item_ids: list[int]
    instruction: str = ""
    fields: list[str] | None = None
    model: str | None = None


def _item(item_id: int) -> dict:
    r = db.row(ITEM_SELECT + "WHERE i.id = ?", [item_id])
    if not r:
        raise HTTPException(404, "Item not found")
    return r


@router.get("/items/{item_id}")
def item_detail(item_id: int):
    r = item_public(_item(item_id))
    v = db.row("SELECT description FROM videos WHERE id = ?", [r["video_id"]])
    r["description"] = v and v["description"]
    if library.file_ok(r["file_path"]):
        fresh = library.read_meta(r["file_path"])
        if fresh and fresh != r["meta"]:
            db.update("items", item_id, {"meta": db.jdump(fresh)})
            r["meta"] = fresh
        r["file_exists"] = True
    else:
        r["file_exists"] = False
    r["fields"] = tags.FIELDS
    return r


@router.get("/items/{item_id}/cover")
def item_cover(item_id: int):
    r = _item(item_id)
    if library.file_ok(r["file_path"]):
        try:
            c = tags.read_cover(r["file_path"])
            if c:
                return Response(c[0], media_type=c[1], headers={"Cache-Control": "private, max-age=60"})
        except Exception:
            pass
    if r["thumbnail"]:
        return RedirectResponse(r["thumbnail"])
    raise HTTPException(404, "No cover")


@router.get("/items/{item_id}/audio")
def item_audio(item_id: int):
    r = _item(item_id)
    if not library.file_ok(r["file_path"]):
        raise HTTPException(404, "File missing")
    mime = mimetypes.guess_type(r["file_path"])[0] or "audio/mpeg"
    return FileResponse(r["file_path"], media_type=mime)


@router.put("/items/{item_id}/cover")
async def upload_cover(item_id: int, file: UploadFile = File(...), square: bool = False):
    r = _item(item_id)
    if not library.file_ok(r["file_path"]):
        raise HTTPException(400, "File missing")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Image too large")
    mime = file.content_type or "image/jpeg"
    if square or mime not in ("image/jpeg", "image/png"):
        data, mime = tags.square_jpeg(data) if square else _to_jpeg(data), "image/jpeg"
    tags.write_cover(r["file_path"], data, mime)
    _refresh_meta(item_id, r["file_path"])
    return {"ok": True}


def _to_jpeg(data: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image
    out = BytesIO()
    Image.open(BytesIO(data)).convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


def _refresh_meta(item_id: int, path: str) -> None:
    meta = library.read_meta(path)
    if meta:
        db.update("items", item_id, {"meta": db.jdump(meta), "updated_at": db.now()})


@router.post("/items/cover-from-thumbnail")
async def cover_from_thumbnail(body: dict):
    """Embed the YouTube thumbnail (max resolution, optionally square-cropped) as cover."""
    ids = [int(i) for i in body.get("item_ids", [])]
    square = bool(body.get("square", True))
    results = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        for iid in ids:
            r = _item(iid)
            try:
                if not library.file_ok(r["file_path"]):
                    raise ValueError("File missing")
                data = None
                for u in (f"https://i.ytimg.com/vi/{r['video_id']}/maxresdefault.jpg",
                          f"https://i.ytimg.com/vi/{r['video_id']}/hqdefault.jpg", r["thumbnail"]):
                    if not u:
                        continue
                    resp = await c.get(u)
                    if resp.status_code == 200 and len(resp.content) > 2000:
                        data = resp.content
                        break
                if not data:
                    raise ValueError("No thumbnail found")
                img = tags.square_jpeg(data, 1200) if square else _to_jpeg(data)
                tags.write_cover(r["file_path"], img, "image/jpeg")
                _refresh_meta(iid, r["file_path"])
                results.append({"item_id": iid, "ok": True})
            except Exception as e:
                results.append({"item_id": iid, "ok": False, "error": str(e)})
    return {"results": results}


@router.post("/metadata/preview")
def preview(body: PreviewIn):
    try:
        return {"proposals": metadata_ops.preview(body.item_ids, body.ops)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/metadata/apply")
def apply(body: ApplyIn):
    return {"results": metadata_ops.apply(body.changes)}


@router.post("/metadata/ai")
async def ai_suggest(body: AiIn):
    if len(body.item_ids) > 500:
        raise HTTPException(400, "Select at most 500 tracks per AI run")
    try:
        return await ai.suggest(body.item_ids, body.instruction, body.fields, body.model)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/ai/models")
async def ai_models():
    try:
        return await ai.models()
    except Exception as e:
        raise HTTPException(502, f"Could not load models: {e}")

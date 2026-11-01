from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, duplicates

router = APIRouter(prefix="/api/duplicates", dependencies=[Depends(auth.require_user)])


@router.get("")
def list_duplicates(kind: str | None = None, playlist_id: int | None = None, q: str | None = None,
                    dismissed: bool = False):
    return duplicates.listing(kind, playlist_id, q, dismissed)


class DismissIn(BaseModel):
    key: str
    undo: bool = False


@router.post("/dismiss")
def dismiss(body: DismissIn):
    duplicates.dismiss(body.key, body.undo)
    return {"ok": True}


class ApplyIn(BaseModel):
    item_ids: list[int] = []
    extra_copy_ids: list[int] = []
    action: str  # youtube | file | ignore
    delete_files: bool = False


@router.post("/apply")
async def apply(body: ApplyIn):
    if not body.item_ids and not body.extra_copy_ids:
        raise HTTPException(400, "No tracks selected")
    if body.action not in ("youtube", "file", "ignore"):
        raise HTTPException(400, "Unknown action")
    return await duplicates.apply(body.item_ids, body.action, body.delete_files, body.extra_copy_ids)

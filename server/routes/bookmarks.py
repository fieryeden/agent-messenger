"""Bookmarks routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import BookmarkCreate, BookmarkOut

router = APIRouter(prefix="/agents/{agent_id}/bookmarks", tags=["bookmarks"])


@router.post("", response_model=BookmarkOut, status_code=201)
def create_bookmark(agent_id: str, body: BookmarkCreate, db: MessengerDB = Depends(get_db)):
    msg = db.get_message(body.message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    bm = db.create_bookmark(agent_id, body.message_id, body.label)
    if not bm:
        raise HTTPException(409, "Bookmark already exists")
    return bm


@router.get("", response_model=list[BookmarkOut])
def list_bookmarks(agent_id: str, limit: int = Query(50, ge=1, le=200),
                    offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.list_bookmarks(agent_id, limit, offset)


@router.delete("/{bookmark_id}", status_code=204)
def delete_bookmark(agent_id: str, bookmark_id: str, db: MessengerDB = Depends(get_db)):
    bm = db.get_bookmark(bookmark_id)
    if not bm or bm["agent_id"] != agent_id:
        raise HTTPException(404, "Bookmark not found")
    db.delete_bookmark(bookmark_id)

"""Read Cursors routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import ReadCursorSet, ReadCursorOut

router = APIRouter(prefix="/agents/{agent_id}/read-cursors", tags=["read-cursors"])


@router.put("/conversations/{conversation_id}", response_model=ReadCursorOut)
def set_read_cursor(agent_id: str, conversation_id: str, body: ReadCursorSet,
                     db: MessengerDB = Depends(get_db)):
    db.set_read_cursor(agent_id, conversation_id, body.message_id)
    cursor = db.get_read_cursor(agent_id, conversation_id)
    if not cursor:
        raise HTTPException(500, "Failed to set read cursor")
    return cursor


@router.get("/conversations/{conversation_id}", response_model=ReadCursorOut)
def get_read_cursor(agent_id: str, conversation_id: str, db: MessengerDB = Depends(get_db)):
    cursor = db.get_read_cursor(agent_id, conversation_id)
    if not cursor:
        raise HTTPException(404, "No read cursor found")
    return cursor


@router.get("", response_model=list[ReadCursorOut])
def list_read_cursors(agent_id: str, db: MessengerDB = Depends(get_db)):
    return db.get_read_cursors_for_agent(agent_id)

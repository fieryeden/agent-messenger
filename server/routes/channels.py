"""Channels & Topics routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import ChannelCreate, ConversationUpdate

router = APIRouter(prefix="/channels", tags=["channels"])


@router.post("", status_code=201)
def create_channel(body: ChannelCreate, db: MessengerDB = Depends(get_db)):
    return db.create_channel(body.name, body.description, body.parent_id, body.member_ids)


@router.get("")
def list_channels(parent_id: str = None, limit: int = Query(100, ge=1, le=1000),
                   offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.list_channels(parent_id, limit, offset)


@router.put("/conversations/{conversation_id}")
def update_conversation(conversation_id: str, body: ConversationUpdate,
                          db: MessengerDB = Depends(get_db)):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # Update fields that are set
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.topic is not None:
        updates["topic"] = body.topic
    if body.description is not None:
        updates["description"] = body.description
    if body.parent_id is not None:
        updates["parent_id"] = body.parent_id
    if not updates:
        return conv
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [conversation_id]
    db.conn.execute(f"UPDATE conversations SET {set_clauses} WHERE id = ?", values)
    db.conn.commit()
    return db.get_conversation(conversation_id)

"""Scheduled Messages routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import ScheduledMessageCreate, ScheduledMessageOut

router = APIRouter(prefix="/scheduled-messages", tags=["scheduled-messages"])


@router.post("", response_model=ScheduledMessageOut, status_code=201)
def create_scheduled_message(body: ScheduledMessageCreate, agent_id: str,
                               db: MessengerDB = Depends(get_db)):
    conv = db.get_conversation(body.conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return db.create_scheduled_message(body.conversation_id, agent_id, body.content, body.scheduled_for)


@router.get("", response_model=list[ScheduledMessageOut])
def list_scheduled_messages(conversation_id: str = None, status: str = None,
                             limit: int = Query(50, ge=1, le=200),
                             offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.list_scheduled_messages(conversation_id, status, limit, offset)


@router.get("/{message_id}", response_model=ScheduledMessageOut)
def get_scheduled_message(message_id: str, db: MessengerDB = Depends(get_db)):
    sm = db.get_scheduled_message(message_id)
    if not sm:
        raise HTTPException(404, "Scheduled message not found")
    return sm


@router.delete("/{message_id}", status_code=200)
def cancel_scheduled_message(message_id: str, db: MessengerDB = Depends(get_db)):
    sm = db.get_scheduled_message(message_id)
    if not sm:
        raise HTTPException(404, "Scheduled message not found")
    ok = db.cancel_scheduled_message(message_id)
    if not ok:
        raise HTTPException(409, "Message cannot be cancelled (already sent or cancelled)")
    return {"status": "cancelled"}

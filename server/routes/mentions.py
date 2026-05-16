"""Mentions routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import MentionCreate, MentionOut

router = APIRouter(prefix="/messages/{message_id}/mentions", tags=["mentions"])


@router.post("", status_code=201)
def add_mentions(message_id: str, body: MentionCreate, db: MessengerDB = Depends(get_db)):
    msg = db.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    db.add_mentions(message_id, body.mentioned_agent_ids)
    return {"message_id": message_id, "mentioned_agent_ids": body.mentioned_agent_ids}


@router.get("", response_model=list[MentionOut])
def get_message_mentions(message_id: str, db: MessengerDB = Depends(get_db)):
    return db.get_message_mentions(message_id)


@router.get("/agents/{agent_id}")
def get_agent_mentions(agent_id: str, limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.get_mentions_for_agent(agent_id, limit, offset)

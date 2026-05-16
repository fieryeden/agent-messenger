"""Forwarding, Archiving & Message Expiry routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import MessageForward, ArchiveToggle, MessageExpirySet

router = APIRouter(prefix="/v06", tags=["v06-extended"])


# ── Message Forwarding ──

@router.post("/messages/{message_id}/forward")
def forward_message(message_id: str, body: MessageForward, db: MessengerDB = Depends(get_db)):
    msg = db.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    fwd = db.forward_message(message_id, body.target_conversation_id, body.sender_id)
    if not fwd:
        raise HTTPException(500, "Forwarding failed")
    return fwd


# ── Conversation Archiving ──

@router.put("/conversations/{conversation_id}/archive")
def archive_conversation(conversation_id: str, body: ArchiveToggle, db: MessengerDB = Depends(get_db)):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if body.archived:
        db.archive_conversation(conversation_id)
    else:
        db.unarchive_conversation(conversation_id)
    return db.get_conversation(conversation_id)


@router.put("/conversations/{conversation_id}/members/{agent_id}/archive")
def archive_conversation_for_member(conversation_id: str, agent_id: str,
                                      body: ArchiveToggle, db: MessengerDB = Depends(get_db)):
    if body.archived:
        db.archive_conversation_for_member(conversation_id, agent_id)
    else:
        db.unarchive_conversation_for_member(conversation_id, agent_id)
    return {"status": "ok", "archived": body.archived}


# ── Message Expiry ──

@router.put("/conversations/{conversation_id}/message-expiry")
def set_message_expiry(conversation_id: str, body: MessageExpirySet, db: MessengerDB = Depends(get_db)):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    db.set_message_expiry(conversation_id, body.ttl_seconds)
    return {"conversation_id": conversation_id, "message_ttl": body.ttl_seconds}


@router.post("/maintenance/expire-messages")
def expire_messages(db: MessengerDB = Depends(get_db)):
    count = db.expire_messages()
    return {"expired_count": count}

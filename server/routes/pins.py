"""Pinned messages routes — pin, unpin, list pinned."""

from fastapi import APIRouter, Depends, HTTPException, Query

from server.auth import AuthIdentity, get_current_identity
from server.db import MessengerDB
from server.db_accessor import get_db
from server.security import sanitize_uuid, sanitize_agent_id

router = APIRouter(prefix="/pins", tags=["pins"])


@router.post("/pin")
def pin_message(
    conversation_id: str = Query(...),
    message_id: str = Query(...),
    pinned_by: str = Query(...),
    db: MessengerDB = Depends(get_db),
    identity: AuthIdentity = Depends(get_current_identity),
):
    """Pin a message in a conversation."""
    conversation_id = sanitize_uuid(conversation_id)
    message_id = sanitize_uuid(message_id)
    pinned_by_local = sanitize_agent_id(pinned_by)
    # Auth scope: verify identity matches pinned_by unless admin
    if not identity.has_scope("admin") and identity.agent_id != pinned_by_local:
        raise HTTPException(403, "Not authorized to pin as another agent")

    # Verify message exists and belongs to conversation
    msg = db.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg["conversation_id"] != conversation_id:
        raise HTTPException(400, "Message does not belong to this conversation")

    result = db.pin_message(conversation_id, message_id, pinned_by_local)
    return {"pinned": True, "message_id": message_id, "pins": result}


@router.delete("/unpin")
def unpin_message(
    conversation_id: str = Query(...),
    message_id: str = Query(...),
    db: MessengerDB = Depends(get_db),
):
    """Unpin a message from a conversation."""
    conversation_id = sanitize_uuid(conversation_id)
    message_id = sanitize_uuid(message_id)

    if not db.unpin_message(conversation_id, message_id):
        raise HTTPException(404, "Pinned message not found")
    return {"unpinned": True, "message_id": message_id}


@router.get("/{conversation_id}")
def list_pinned(conversation_id: str, db: MessengerDB = Depends(get_db)):
    """List all pinned messages in a conversation."""
    conversation_id = sanitize_uuid(conversation_id)
    return db.get_pinned_messages(conversation_id)

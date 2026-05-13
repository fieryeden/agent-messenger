"""REST API routes — messages with input validation and audit logging."""

import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from server.db_accessor import get_db
from server.security import sanitize_agent_id, sanitize_content, sanitize_string, sanitize_uuid

logger = logging.getLogger("agent-messenger.messages")
router = APIRouter(prefix="/messages", tags=["messages"])


class MessageSend(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    sender_id: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1, max_length=100000)
    type: str = Field(default="text", max_length=32)
    metadata: Optional[dict] = None


class MarkRead(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)


@router.post("")
async def send_message(body: MessageSend):
    try:
        safe_conv = sanitize_uuid(body.conversation_id)
        safe_sender = sanitize_agent_id(body.sender_id)
        safe_content = sanitize_content(body.content)
        safe_type = sanitize_string(body.type, max_length=32)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        db = get_db()
        if not db.get_conversation(safe_conv):
            raise HTTPException(status_code=404, detail="Conversation not found")
        if not db.get_agent(safe_sender):
            raise HTTPException(status_code=400, detail="Sender agent not registered")

        msg = db.send_message(safe_conv, safe_sender, safe_content, safe_type, body.metadata)
        return {"status": "ok", "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to send message from %s: %s", body.sender_id, e)
        raise HTTPException(status_code=500, detail="Failed to send message")


@router.get("/conversation/{conv_id}")
async def get_messages(conv_id: str, limit: int = 50, before: Optional[str] = None):
    try:
        safe_conv = sanitize_uuid(conv_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        if not db.get_conversation(safe_conv):
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = db.get_messages(safe_conv, limit, before)
        return {"status": "ok", "messages": messages, "count": len(messages)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get messages for %s: %s", conv_id, e)
        raise HTTPException(status_code=500, detail="Failed to get messages")


@router.post("/{msg_id}/read")
async def mark_read(msg_id: str, body: MarkRead):
    try:
        safe_msg = sanitize_uuid(msg_id)
        safe_agent = sanitize_agent_id(body.agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        db.mark_read(safe_msg, safe_agent)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Failed to mark read for %s: %s", msg_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{msg_id}")
async def delete_message(msg_id: str):
    """Delete a message by ID."""
    try:
        safe_msg = sanitize_uuid(msg_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        ok = db.delete_message(safe_msg)
        if not ok:
            raise HTTPException(status_code=404, detail="Message not found")
        return {"status": "ok", "deleted": safe_msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete message %s: %s", msg_id, e)
        raise HTTPException(status_code=500, detail="Failed to delete message")


@router.get("/search")
async def search_messages(q: str = Query(..., min_length=1, max_length=500), limit: int = 20, offset: int = 0):
    try:
        safe_q = sanitize_string(q, max_length=500)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        results = db.search_messages(safe_q, limit, offset)
        return {"status": "ok", "results": results, "count": len(results)}
    except Exception as e:
        logger.error("Message search failed: %s", e)
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/feed")
async def global_feed(limit: int = 100, offset: int = 0):
    """Get the global message feed (all conversations)."""
    try:
        db = get_db()
        messages = db.global_feed(limit, offset)
        return {"status": "ok", "messages": messages, "count": len(messages)}
    except Exception as e:
        logger.error("Global feed failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get feed")

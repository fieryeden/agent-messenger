"""REST API routes — messages."""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from server.db_accessor import get_db

logger = logging.getLogger("agent-messenger.messages")
router = APIRouter(prefix="/messages", tags=["messages"])


class MessageSend(BaseModel):
    conversation_id: str
    sender_id: str
    content: str
    type: str = "text"
    metadata: Optional[dict] = None


class MarkRead(BaseModel):
    agent_id: str


@router.post("")
async def send_message(body: MessageSend):
    try:
        db = get_db()
        # Validate conversation exists
        if not db.get_conversation(body.conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        # Validate sender exists
        if not db.get_agent(body.sender_id):
            raise HTTPException(status_code=400, detail="Sender agent not registered")
        msg = db.send_message(body.conversation_id, body.sender_id, body.content, body.type, body.metadata)
        return {"status": "ok", "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to send message from %s: %s", body.sender_id, e)
        raise HTTPException(status_code=500, detail="Failed to send message")


@router.get("/conversation/{conv_id}")
async def get_messages(conv_id: str, limit: int = 50, before: Optional[str] = None):
    try:
        db = get_db()
        if not db.get_conversation(conv_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = db.get_messages(conv_id, limit, before)
        return {"status": "ok", "messages": messages, "count": len(messages)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get messages for %s: %s", conv_id, e)
        raise HTTPException(status_code=500, detail="Failed to get messages")


@router.post("/{msg_id}/read")
async def mark_read(msg_id: str, body: MarkRead):
    try:
        db = get_db()
        db.mark_read(msg_id, body.agent_id)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Failed to mark read for %s: %s", msg_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/search")
async def search_messages(q: str = Query(...), limit: int = 20):
    try:
        db = get_db()
        results = db.search_messages(q, limit)
        return {"status": "ok", "results": results, "count": len(results)}
    except Exception as e:
        logger.error("Message search failed: %s", e)
        raise HTTPException(status_code=500, detail="Search failed")

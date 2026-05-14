"""REST API routes — conversations with input validation."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from server.db_accessor import get_db
from server.security import sanitize_agent_id, sanitize_string, sanitize_uuid


class MarkRead(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)

logger = logging.getLogger("agent-messenger.conversations")
router = APIRouter(prefix="/conversations", tags=["conversations"])


class TypingRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)


class ConversationCreate(BaseModel):
    type: str = Field(default="dm", pattern=r"^(dm|group|channel)$")
    name: Optional[str] = Field(None, max_length=256)
    member_ids: list[str] = Field(..., min_length=1, max_length=100)


class MemberAdd(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    role: str = Field(default="member", pattern=r"^(member|admin|owner)$")


@router.post("")
async def create_conversation(body: ConversationCreate):
    try:
        db = get_db()
        safe_member_ids = [sanitize_agent_id(m) for m in body.member_ids]
        safe_name = sanitize_string(body.name, max_length=256) if body.name else None
        conv = db.create_conversation(body.type, safe_name, safe_member_ids)
        return {"status": "ok", "conversation": conv}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create conversation: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_conversations(agent_id: str, limit: int = 50, offset: int = 0):
    try:
        safe_agent = sanitize_agent_id(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        convs = db.list_conversations(safe_agent, limit=limit, offset=offset)
        return {"status": "ok", "conversations": convs, "count": len(convs)}
    except Exception as e:
        logger.error("Failed to list conversations for %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail="Failed to list conversations")


@router.get("/{conv_id}")
async def get_conversation(conv_id: str):
    try:
        safe_id = sanitize_uuid(conv_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        conv = db.get_conversation(safe_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"status": "ok", "conversation": conv}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get conversation %s: %s", conv_id, e)
        raise HTTPException(status_code=500, detail="Failed to get conversation")


@router.post("/{conv_id}/members")
async def add_member(conv_id: str, body: MemberAdd):
    try:
        safe_conv = sanitize_uuid(conv_id)
        safe_agent = sanitize_agent_id(body.agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        if not db.get_conversation(safe_conv):
            raise HTTPException(status_code=404, detail="Conversation not found")
        if not db.get_agent(safe_agent):
            raise HTTPException(status_code=400, detail="Agent not registered")
        db.add_conversation_member(safe_conv, safe_agent, body.role)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to add member %s to %s: %s", body.agent_id, conv_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{conv_id}/members/{agent_id}")
async def remove_member(conv_id: str, agent_id: str):
    try:
        safe_conv = sanitize_uuid(conv_id)
        safe_agent = sanitize_agent_id(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        db.remove_conversation_member(safe_conv, safe_agent)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Failed to remove member %s from %s: %s", agent_id, conv_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{conv_id}/read")
async def mark_conversation_read(conv_id: str, body: MarkRead):
    """Mark all messages in a conversation as read by an agent."""
    try:
        safe_conv = sanitize_uuid(conv_id)
        safe_agent = sanitize_agent_id(body.agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        db.mark_conversation_read(safe_conv, safe_agent)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Failed to mark read for %s in %s: %s", body.agent_id, conv_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{conv_id}/typing")
async def set_typing(conv_id: str, body: TypingRequest):
    """Set typing indicator for an agent in a conversation. Auto-expires after 5s."""
    try:
        safe_conv = sanitize_uuid(conv_id)
        safe_agent = sanitize_agent_id(body.agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        if not db.get_conversation(safe_conv):
            raise HTTPException(status_code=404, detail="Conversation not found")
        db.set_typing(safe_conv, safe_agent)
        # Broadcast via WebSocket
        from server.websocket import manager
        await manager.broadcast_to_conversation(safe_conv, {
            "type": "typing",
            "conversation_id": safe_conv,
            "agent_id": safe_agent,
        }, exclude=safe_agent)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to set typing for %s in %s: %s", body.agent_id, conv_id, e)
        raise HTTPException(status_code=500, detail="Failed to set typing")


@router.delete("/{conv_id}/typing")
async def clear_typing(conv_id: str, agent_id: str):
    """Clear typing indicator for an agent in a conversation."""
    try:
        safe_conv = sanitize_uuid(conv_id)
        safe_agent = sanitize_agent_id(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        db.clear_typing(safe_conv, safe_agent)
        from server.websocket import manager
        await manager.broadcast_to_conversation(safe_conv, {
            "type": "stop_typing",
            "conversation_id": safe_conv,
            "agent_id": safe_agent,
        }, exclude=safe_agent)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Failed to clear typing for %s in %s: %s", agent_id, conv_id, e)
        raise HTTPException(status_code=500, detail="Failed to clear typing")


@router.get("/{conv_id}/typing")
async def get_typing(conv_id: str):
    """Get currently-typing agents in a conversation. Stale entries (>5s) auto-expire."""
    try:
        safe_conv = sanitize_uuid(conv_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        db = get_db()
        typing = db.get_typing(safe_conv)
        return {"status": "ok", "typing": typing, "count": len(typing)}
    except Exception as e:
        logger.error("Failed to get typing for %s: %s", conv_id, e)
        raise HTTPException(status_code=500, detail="Failed to get typing")

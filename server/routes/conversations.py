"""REST API routes — conversations."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from server.db_accessor import get_db

logger = logging.getLogger("agent-messenger.conversations")
router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    type: str = "dm"
    name: Optional[str] = None
    member_ids: list[str]


class MemberAdd(BaseModel):
    agent_id: str
    role: str = "member"


@router.post("")
async def create_conversation(body: ConversationCreate):
    try:
        db = get_db()
        conv = db.create_conversation(body.type, body.name, body.member_ids)
        return {"status": "ok", "conversation": conv}
    except Exception as e:
        logger.error("Failed to create conversation: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_conversations(agent_id: str):
    try:
        db = get_db()
        convs = db.list_conversations(agent_id)
        return {"status": "ok", "conversations": convs, "count": len(convs)}
    except Exception as e:
        logger.error("Failed to list conversations for %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail="Failed to list conversations")


@router.get("/{conv_id}")
async def get_conversation(conv_id: str):
    try:
        db = get_db()
        conv = db.get_conversation(conv_id)
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
        db = get_db()
        if not db.get_conversation(conv_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        db.add_conversation_member(conv_id, body.agent_id, body.role)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to add member %s to %s: %s", body.agent_id, conv_id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{conv_id}/members/{agent_id}")
async def remove_member(conv_id: str, agent_id: str):
    try:
        db = get_db()
        db.remove_conversation_member(conv_id, agent_id)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Failed to remove member %s from %s: %s", agent_id, conv_id, e)
        raise HTTPException(status_code=400, detail=str(e))

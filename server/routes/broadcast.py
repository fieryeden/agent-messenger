"""REST API routes — broadcast messaging and agent capabilities."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.db_accessor import get_db
from server.security import sanitize_agent_id, sanitize_content

logger = logging.getLogger("agent-messenger.broadcast")

router = APIRouter(prefix="/broadcast", tags=["broadcast"])


class BroadcastSend(BaseModel):
    sender_id: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1, max_length=5000)
    type: str = Field(default="text", max_length=32)
    priority: str = Field(default="normal", pattern=r"^(urgent|normal|low)$")


@router.post("")
async def broadcast_message(body: BroadcastSend):
    """Send a message to all online agents via the broadcast channel."""
    try:
        safe_sender = sanitize_agent_id(body.sender_id)
        safe_content = sanitize_content(body.content, max_length=5000)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        db = get_db()
        if not db.get_agent(safe_sender):
            raise HTTPException(status_code=400, detail="Sender agent not registered")

        msg = db.broadcast_message(safe_sender, safe_content, body.type, priority=body.priority)

        # Broadcast via WebSocket to all connected agents
        from server.websocket import manager
        await manager.broadcast({
            "type": "broadcast",
            "from": safe_sender,
            "message": msg,
        }, exclude=safe_sender)

        return {"status": "ok", "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Broadcast from %s failed: %s", body.sender_id, e)
        raise HTTPException(status_code=500, detail="Broadcast failed")

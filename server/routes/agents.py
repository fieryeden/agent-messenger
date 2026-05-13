"""REST API routes — agent management."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from server.db_accessor import get_db

logger = logging.getLogger("agent-messenger.agents")
router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRegister(BaseModel):
    id: str
    name: str
    type: str = "detached"
    metadata: Optional[dict] = None


class AgentStatus(BaseModel):
    status: str


@router.post("/register")
async def register_agent(body: AgentRegister):
    try:
        db = get_db()
        agent = db.register_agent(body.id, body.name, body.type, body.metadata)
        return {"status": "ok", "agent": agent}
    except Exception as e:
        logger.error("Failed to register agent %s: %s", body.id, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_agents(status: Optional[str] = None):
    try:
        db = get_db()
        agents = db.list_agents(status)
        return {"status": "ok", "agents": agents, "count": len(agents)}
    except Exception as e:
        logger.error("Failed to list agents: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list agents")


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    try:
        db = get_db()
        agent = db.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "ok", "agent": agent}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail="Failed to get agent")


@router.put("/{agent_id}/status")
async def update_status(agent_id: str, body: AgentStatus):
    try:
        db = get_db()
        if not db.get_agent(agent_id):
            raise HTTPException(status_code=404, detail="Agent not found")
        db.update_agent_status(agent_id, body.status)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update status for %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail="Failed to update status")

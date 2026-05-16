"""REST API routes — agent management with input validation and audit logging."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from server.db_accessor import get_db
from server.schemas import AgentSingleResponse, AgentListResponse, AgentDeletedResponse, OkResponse
from server.security import sanitize_agent_id, sanitize_string

logger = logging.getLogger("agent-messenger.agents")

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRegister(BaseModel):
	id: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$", description="Agent ID: alphanumeric, dots, hyphens, underscores only")
	name: str = Field(..., min_length=1, max_length=256)
	type: str = Field(default="detached", max_length=64)
	metadata: Optional[dict] = None


class AgentStatus(BaseModel):
	status: str = Field(..., pattern=r"^(online|offline|busy|away)$")


class AgentUpdate(BaseModel):
	name: Optional[str] = Field(None, min_length=1, max_length=256)
	type: Optional[str] = Field(None, max_length=64)
	metadata: Optional[dict] = None


@router.post("/register", response_model=AgentSingleResponse)
async def register_agent(body: AgentRegister, request: Request):
	try:
		db = get_db()
		safe_id = sanitize_agent_id(body.id)
		safe_name = sanitize_string(body.name, max_length=256)
		safe_type = sanitize_string(body.type, max_length=64)
		agent = db.register_agent(safe_id, safe_name, safe_type, body.metadata)
		return {"status": "ok", "agent": agent}
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	except Exception as e:
		logger.error("Failed to register agent %s: %s", body.id, e)
		raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=AgentListResponse)
async def list_agents(
	status: Optional[str] = None,
	type: Optional[str] = None,
	limit: int = 100,
	offset: int = 0,
):
	try:
		db = get_db()
		agents = db.list_agents(status=status, agent_type=type, limit=limit, offset=offset)
		return {"status": "ok", "agents": agents, "count": len(agents)}
	except Exception as e:
		logger.error("Failed to list agents: %s", e)
		raise HTTPException(status_code=500, detail="Failed to list agents")


@router.get("/{agent_id}", response_model=AgentSingleResponse)
async def get_agent(agent_id: str):
	try:
		safe_id = sanitize_agent_id(agent_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		agent = db.get_agent(safe_id)
		if not agent:
			raise HTTPException(status_code=404, detail="Agent not found")
		return {"status": "ok", "agent": agent}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to get agent %s: %s", agent_id, e)
		raise HTTPException(status_code=500, detail="Failed to get agent")


@router.put("/{agent_id}/status", response_model=OkResponse)
async def update_status(agent_id: str, body: AgentStatus):
	try:
		safe_id = sanitize_agent_id(agent_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		if not db.get_agent(safe_id):
			raise HTTPException(status_code=404, detail="Agent not found")
		db.update_agent_status(safe_id, body.status)
		return {"status": "ok"}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to update status for %s: %s", agent_id, e)
		raise HTTPException(status_code=500, detail="Failed to update status")


@router.put("/{agent_id}", response_model=AgentSingleResponse)
async def update_agent(agent_id: str, body: AgentUpdate):
	"""Update agent metadata (name, type, metadata)."""
	try:
		safe_id = sanitize_agent_id(agent_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		existing = db.get_agent(safe_id)
		if not existing:
			raise HTTPException(status_code=404, detail="Agent not found")
		# Re-register with updated fields
		name = body.name or existing["name"]
		agent_type = body.type or existing["type"]
		metadata = body.metadata if body.metadata is not None else existing.get("metadata", {})
		agent = db.register_agent(safe_id, name, agent_type, metadata)
		return {"status": "ok", "agent": agent}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to update agent %s: %s", agent_id, e)
		raise HTTPException(status_code=500, detail="Failed to update agent")


@router.delete("/{agent_id}", response_model=AgentDeletedResponse)
async def delete_agent(agent_id: str):
	"""Delete an agent and all its memberships."""
	try:
		safe_id = sanitize_agent_id(agent_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		if not db.get_agent(safe_id):
			raise HTTPException(status_code=404, detail="Agent not found")
		ok = db.delete_agent(safe_id)
		return {"status": "ok", "deleted": safe_id}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to delete agent %s: %s", agent_id, e)
		raise HTTPException(status_code=500, detail="Failed to delete agent")

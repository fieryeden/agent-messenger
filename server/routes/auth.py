"""Auth routes — login, token refresh, API key management."""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from server.auth import (
    AgentApiKeyCreate,
    AgentApiKeyResponse,
    AuthIdentity,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    create_access_token,
    create_refresh_token,
    get_current_identity,
    generate_api_key,
    require_scope,
    verify_api_key,
)
from server.db_accessor import get_db

logger = logging.getLogger("agent-messenger.auth_routes")

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """Authenticate with agent_id + API key, get JWT tokens."""
    db = get_db()
    agent = db.get_agent(body.agent_id)
    if not agent:
        raise HTTPException(status_code=401, detail="Agent not found")

    stored_hash = agent.get("api_key_hash")
    if not stored_hash or not verify_api_key(body.api_key, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    scopes = agent.get("api_key_scopes", ["agent"])
    access = create_access_token(body.agent_id, scopes)
    refresh = create_refresh_token(body.agent_id)

    return LoginResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=3600,
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(body: RefreshRequest):
    """Refresh an access token using a refresh token."""
    from server.auth import _jwt_decode
    payload = _jwt_decode(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    agent_id = payload["sub"]
    db = get_db()
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=401, detail="Agent not found")

    scopes = agent.get("api_key_scopes", ["agent"])
    access = create_access_token(agent_id, scopes)
    refresh = create_refresh_token(agent_id)

    return LoginResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=3600,
    )


@router.post("/api-keys", response_model=AgentApiKeyResponse)
async def create_api_key(
    body: AgentApiKeyCreate,
    identity: AuthIdentity = Depends(require_scope("admin")),
):
    """Generate a new API key for an agent (admin only)."""
    db = get_db()
    agent = db.get_agent(body.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    raw_key, hashed_key = generate_api_key(body.agent_id, body.scopes)
    db.set_agent_api_key(body.agent_id, hashed_key, body.scopes)

    return AgentApiKeyResponse(
        agent_id=body.agent_id,
        api_key=raw_key,
        scopes=body.scopes,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/me")
async def get_me(identity: AuthIdentity = Depends(get_current_identity)):
    """Get current authenticated identity."""
    return {
        "agent_id": identity.agent_id,
        "scopes": identity.scopes,
        "auth_type": identity.auth_type,
    }

"""Shared Pydantic schemas for request/response validation across all routes."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Common ──

class StatusResponse(BaseModel):
    status: str = Field(..., pattern=r"^ok|error$")
    detail: Optional[str] = None


class PaginatedResponse(BaseModel):
    status: str = "ok"
    count: int = Field(..., ge=0)
    has_more: bool = False


# ── Agent Schemas ──

class AgentResponse(BaseModel):
    id: str
    name: str
    type: str
    status: str
    metadata: dict = {}
    created_at: str
    last_seen: str


class AgentListResponse(PaginatedResponse):
    agents: list[AgentResponse] = []


# ── Conversation Schemas ──

class ConversationResponse(BaseModel):
    id: str
    type: str
    name: Optional[str] = None
    created_at: str
    updated_at: str
    members: Optional[list] = None
    last_message: Optional[dict] = None
    unread_count: int = 0


class ConversationListResponse(PaginatedResponse):
    conversations: list[ConversationResponse] = []


# ── Message Schemas ──

class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    sender_name: Optional[str] = None
    content: str
    type: str = "text"
    metadata: dict = {}
    created_at: str
    read_by: list[str] = []


class MessageListResponse(PaginatedResponse):
    messages: list[MessageResponse] = []


# ── Stats ──

class StatsResponse(BaseModel):
    agents: int = 0
    online: int = 0
    conversations: int = 0
    dms: int = 0
    groups: int = 0
    messages: int = 0

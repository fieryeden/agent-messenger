"""Shared Pydantic schemas for request/response validation across all routes.

v0.6.0 — Full response model coverage for OpenAPI docs and type-safe clients.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Common ──

class StatusResponse(BaseModel):
	status: str = Field(..., pattern=r"^ok|error$")
	detail: Optional[str] = None


class OkResponse(BaseModel):
	"""Minimal ok acknowledgement."""
	status: str = "ok"


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


class AgentSingleResponse(BaseModel):
	status: str = "ok"
	agent: AgentResponse


class AgentListResponse(PaginatedResponse):
	agents: list[AgentResponse] = []


class AgentDeletedResponse(BaseModel):
	status: str = "ok"
	deleted: str


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


class ConversationSingleResponse(BaseModel):
	status: str = "ok"
	conversation: ConversationResponse


class ConversationListResponse(PaginatedResponse):
	conversations: list[ConversationResponse] = []


class TypingEntry(BaseModel):
	agent_id: str
	started_at: str


class TypingResponse(BaseModel):
	status: str = "ok"
	typing: list[TypingEntry] = []
	count: int = 0


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
	reply_to_id: Optional[str] = None
	priority: str = "normal"
	edited_at: Optional[str] = None
	edited_content: Optional[str] = None
	deleted_at: Optional[str] = None


class MessageSendResponse(BaseModel):
	status: str = "ok"
	message: MessageResponse


class MessageListResponse(PaginatedResponse):
	messages: list[MessageResponse] = []


class ReadersResponse(BaseModel):
	status: str = "ok"
	read_by: list[str] = []
	count: int = 0


class RepliesResponse(BaseModel):
	status: str = "ok"
	replies: list[MessageResponse] = []
	count: int = 0


class MessageEditResponse(BaseModel):
	status: str = "ok"
	message: MessageResponse


class MessageDeleteResponse(BaseModel):
	status: str = "ok"
	deleted: str
	soft: bool = True


class ReactionGroup(BaseModel):
	emoji: str
	count: int
	agents: list[str] = []


class ReactionsResponse(BaseModel):
	status: str = "ok"
	reactions: list[ReactionGroup] = []


class SearchResult(BaseModel):
	id: str
	conversation_id: str
	sender_id: str
	content: str
	type: str = "text"
	created_at: str
	rank: float = 0.0


class SearchResponse(BaseModel):
	status: str = "ok"
	results: list[SearchResult] = []
	count: int = 0


class FeedResponse(BaseModel):
	status: str = "ok"
	messages: list[MessageResponse] = []
	count: int = 0


# ── Broadcast Schemas ──

class BroadcastMessageResponse(BaseModel):
	status: str = "ok"
	message: MessageResponse


# ── File Schemas ──

class FileResponse(BaseModel):
	id: str
	message_id: str
	uploader_id: str
	filename: str
	content_type: str
	size_bytes: int
	storage_path: str
	expires_at: Optional[str] = None
	created_at: str


class FileListResponse(BaseModel):
	status: str = "ok"
	files: list[FileResponse] = []
	count: int = 0


class FileDeleteResponse(BaseModel):
	status: str = "deleted"
	file_id: str


class FileCleanupResponse(BaseModel):
	deleted: int = 0


# ── Pin Schemas ──

class PinEntry(BaseModel):
	message_id: str
	conversation_id: str
	pinned_by: str
	pinned_at: str


class PinResponse(BaseModel):
	pinned: bool = True
	message_id: str
	pins: list[PinEntry] = []


class UnpinResponse(BaseModel):
	unpinned: bool = True
	message_id: str


class PinnedListResponse(BaseModel):
	status: str = "ok"
	pins: list[PinEntry] = []
	count: int = 0


# ── Poll Schemas ──

class PollOption(BaseModel):
	index: int
	text: str
	votes: int = 0


class PollResponse(BaseModel):
	id: str
	conversation_id: str
	creator_id: str
	question: str
	options: list[PollOption] = []
	multi_vote: bool = False
	closed_at: Optional[str] = None
	created_at: str


class PollVoteResponse(BaseModel):
	status: str = "ok"
	poll: PollResponse


class PollListResponse(BaseModel):
	status: str = "ok"
	polls: list[PollResponse] = []
	count: int = 0


# ── Stats ──

class StatsResponse(BaseModel):
	agents: int = 0
	online: int = 0
	conversations: int = 0
	dms: int = 0
	groups: int = 0
	messages: int = 0

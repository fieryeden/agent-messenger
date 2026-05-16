"""Pydantic schemas for Agent Messenger v0.6.0 features."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Roles & Permissions ──

class RoleCreate(BaseModel):
    name: str
    permissions: dict
    is_default: bool = False

class RoleUpdate(BaseModel):
    name: Optional[str] = None
    permissions: Optional[dict] = None

class RoleOut(BaseModel):
    id: str
    conversation_id: str
    name: str
    permissions: dict
    is_default: bool
    created_at: str

class MemberPermissionUpdate(BaseModel):
    permissions: dict
    role: Optional[str] = None


# ── Read Cursors ──

class ReadCursorSet(BaseModel):
    message_id: str

class ReadCursorOut(BaseModel):
    agent_id: str
    conversation_id: str
    last_read_message_id: Optional[str]
    last_read_at: str


# ── Channels & Topics ──

class ChannelCreate(BaseModel):
    name: str
    description: Optional[str] = None
    parent_id: Optional[str] = None
    member_ids: Optional[list[str]] = None

class ConversationUpdate(BaseModel):
    name: Optional[str] = None
    topic: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None


# ── Mentions ──

class MentionCreate(BaseModel):
    mentioned_agent_ids: list[str]

class MentionOut(BaseModel):
    message_id: str
    mentioned_agent_id: str
    created_at: str


# ── Notification Preferences ──

class NotificationPrefsSet(BaseModel):
    muted: Optional[bool] = None
    mute_until: Optional[str] = None
    mention_only: Optional[bool] = None

class NotificationPrefsOut(BaseModel):
    agent_id: str
    conversation_id: Optional[str]
    muted: bool
    mute_until: Optional[str]
    mention_only: bool


# ── Forwarding ──

class MessageForward(BaseModel):
    target_conversation_id: str
    sender_id: str


# ── Bookmarks ──

class BookmarkCreate(BaseModel):
    message_id: str
    label: Optional[str] = None

class BookmarkOut(BaseModel):
    id: str
    agent_id: str
    message_id: str
    label: Optional[str]
    created_at: str


# ── Custom Emojis ──

class CustomEmojiCreate(BaseModel):
    name: str
    image_url: str
    animated: bool = False

class CustomEmojiOut(BaseModel):
    id: str
    name: str
    image_url: str
    creator_id: str
    animated: bool
    created_at: str


# ── Message Expiry ──

class MessageExpirySet(BaseModel):
    ttl_seconds: int

class ArchiveToggle(BaseModel):
    archived: bool


# ── Webhooks ──

class WebhookCreate(BaseModel):
    url: str
    events: list[str]
    secret: Optional[str] = None

class WebhookUpdate(BaseModel):
    url: Optional[str] = None
    events: Optional[list[str]] = None
    active: Optional[bool] = None

class WebhookOut(BaseModel):
    id: str
    conversation_id: str
    url: str
    events: list[str]
    active: bool
    created_by: str
    created_at: str
    updated_at: str


# ── Event Subscriptions ──

class EventSubscriptionCreate(BaseModel):
    event_type: str
    conversation_id: Optional[str] = None
    callback_url: Optional[str] = None

class EventSubscriptionOut(BaseModel):
    id: str
    agent_id: str
    event_type: str
    conversation_id: Optional[str]
    callback_url: Optional[str]
    active: bool
    created_at: str


# ── Slash Commands ──

class SlashCommandCreate(BaseModel):
    name: str
    description: Optional[str] = None
    handler_url: str
    conversation_id: Optional[str] = None

class SlashCommandOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    handler_url: str
    conversation_id: Optional[str]
    created_by: str
    created_at: str


# ── Scheduled Messages ──

class ScheduledMessageCreate(BaseModel):
    conversation_id: str
    content: str
    scheduled_for: str

class ScheduledMessageOut(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    content: str
    scheduled_for: str
    sent_at: Optional[str]
    status: str
    created_at: str


# ── Embeds ──

class EmbedCreate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    embed_type: str = "link"

class EmbedOut(BaseModel):
    id: str
    message_id: str
    title: Optional[str]
    description: Optional[str]
    url: Optional[str]
    image_url: Optional[str]
    thumbnail_url: Optional[str]
    embed_type: str
    created_at: str


# ── Translations ──

class TranslationCreate(BaseModel):
    language: str
    content: str

class TranslationOut(BaseModel):
    id: str
    message_id: str
    language: str
    content: str
    created_at: str

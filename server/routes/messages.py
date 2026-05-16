"""REST API routes — messages with threading, edit, soft-delete, reactions, delivery/read tracking."""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.db_accessor import get_db
from server.schemas import (
	MessageSendResponse, MessageListResponse, OkResponse, ReadersResponse,
	RepliesResponse, MessageEditResponse, MessageDeleteResponse,
	ReactionsResponse, SearchResponse, FeedResponse,
)
from server.security import sanitize_agent_id, sanitize_content, sanitize_string, sanitize_uuid

logger = logging.getLogger("agent-messenger.messages")

router = APIRouter(prefix="/messages", tags=["messages"])


# ── Request Models ──

class MessageSend(BaseModel):
	conversation_id: str = Field(..., min_length=1)
	sender_id: str = Field(..., min_length=1, max_length=128)
	content: str = Field(..., min_length=1, max_length=100000)
	type: str = Field(default="text", max_length=32)
	metadata: Optional[dict] = None
	reply_to_id: Optional[str] = Field(None, max_length=64, description="ID of message being replied to")
	priority: str = Field(default="normal", pattern=r"^(urgent|normal|low)$", description="Message priority")


class MarkRead(BaseModel):
	agent_id: str = Field(..., min_length=1, max_length=128)


class MessageEdit(BaseModel):
	content: str = Field(..., min_length=1, max_length=100000)
	edited_by: str = Field(..., min_length=1, max_length=128)


class ReactRequest(BaseModel):
	agent_id: str = Field(..., min_length=1, max_length=128)
	emoji: str = Field(..., min_length=1, max_length=10, description="Emoji reaction")


# ── Endpoints ──

@router.post("", response_model=MessageSendResponse)
async def send_message(body: MessageSend):
	"""Send a message, optionally replying to another message."""
	try:
		safe_conv = sanitize_uuid(body.conversation_id)
		safe_sender = sanitize_agent_id(body.sender_id)
		safe_content = sanitize_content(body.content)
		safe_type = sanitize_string(body.type, max_length=32)
		safe_reply_to = sanitize_uuid(body.reply_to_id) if body.reply_to_id else None
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))

	try:
		db = get_db()
		if not db.get_conversation(safe_conv):
			raise HTTPException(status_code=404, detail="Conversation not found")
		if not db.get_agent(safe_sender):
			raise HTTPException(status_code=400, detail="Sender agent not registered")
		if safe_reply_to:
			reply_msg = db.get_message(safe_reply_to)
			if not reply_msg:
				raise HTTPException(status_code=404, detail="Reply-to message not found")
			if reply_msg["conversation_id"] != safe_conv:
				raise HTTPException(status_code=400, detail="Reply-to message is in a different conversation")

		msg = db.send_message(safe_conv, safe_sender, safe_content, safe_type, body.metadata, safe_reply_to, body.priority)

		# Broadcast via WebSocket
		from server.websocket import manager
		ws_event = {"type": "new_message", "conversation_id": safe_conv, "message": msg}
		if body.priority == "urgent":
			# Urgent messages push to all conversation members even if unsubscribed
			for member in db.get_conversation_members(safe_conv):
				await manager.send_to_agent(member["agent_id"], ws_event)
		else:
			await manager.broadcast_to_conversation(safe_conv, ws_event, exclude=safe_sender)

		# Confirm to sender
		await manager.send_to_agent(safe_sender, {"type": "message_sent", "message": msg})
		return {"status": "ok", "message": msg}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to send message from %s: %s", body.sender_id, e)
		raise HTTPException(status_code=500, detail="Failed to send message")


@router.get("/conversation/{conv_id}", response_model=MessageListResponse)
async def get_messages(conv_id: str, limit: int = 50, before: Optional[str] = None):
	try:
		safe_conv = sanitize_uuid(conv_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		if not db.get_conversation(safe_conv):
			raise HTTPException(status_code=404, detail="Conversation not found")
		messages = db.get_messages(safe_conv, limit, before)
		return {"status": "ok", "messages": messages, "count": len(messages)}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to get messages for %s: %s", conv_id, e)
		raise HTTPException(status_code=500, detail="Failed to get messages")


@router.post("/{msg_id}/read", response_model=OkResponse)
async def mark_read(msg_id: str, body: MarkRead):
	try:
		safe_msg = sanitize_uuid(msg_id)
		safe_agent = sanitize_agent_id(body.agent_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		msg = db.get_message(safe_msg)
		if not msg:
			raise HTTPException(status_code=404, detail="Message not found")
		db.mark_read(safe_msg, safe_agent)
		# Broadcast read receipt via WebSocket
		from server.websocket import manager
		await manager.broadcast_to_conversation(msg["conversation_id"], {
			"type": "message_read",
			"message_id": safe_msg,
			"conversation_id": msg["conversation_id"],
			"agent_id": safe_agent,
		})
		return {"status": "ok"}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to mark read for %s: %s", msg_id, e)
		raise HTTPException(status_code=400, detail=str(e))


@router.get("/{msg_id}/readers", response_model=ReadersResponse)
async def get_readers(msg_id: str):
	"""Get list of agents who have read a message."""
	try:
		safe_msg = sanitize_uuid(msg_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		msg = db.get_message(safe_msg)
		if not msg:
			raise HTTPException(status_code=404, detail="Message not found")
		return {"status": "ok", "read_by": msg.get("read_by", []), "count": len(msg.get("read_by", []))}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to get readers for %s: %s", msg_id, e)
		raise HTTPException(status_code=500, detail="Failed to get readers")


@router.post("/{msg_id}/delivered", response_model=OkResponse)
async def mark_delivered(msg_id: str, body: MarkRead):
	"""Mark a message as delivered to an agent (received by their queue/client)."""
	try:
		safe_msg = sanitize_uuid(msg_id)
		safe_agent = sanitize_agent_id(body.agent_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		msg = db.get_message(safe_msg)
		if not msg:
			raise HTTPException(status_code=404, detail="Message not found")
		db.mark_delivered(safe_msg, safe_agent)
		# Broadcast delivery receipt
		from server.websocket import manager
		await manager.send_to_agent(msg["sender_id"], {
			"type": "message_delivered",
			"message_id": safe_msg,
			"conversation_id": msg["conversation_id"],
			"agent_id": safe_agent,
		})
		return {"status": "ok"}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to mark delivered for %s: %s", msg_id, e)
		raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{msg_id}", response_model=MessageEditResponse)
async def edit_message(msg_id: str, body: MessageEdit):
	"""Edit a message's content. Stores original in edited_content."""
	try:
		safe_msg = sanitize_uuid(msg_id)
		safe_content = sanitize_content(body.content)
		safe_editor = sanitize_agent_id(body.edited_by)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		msg = db.get_message(safe_msg)
		if not msg:
			raise HTTPException(status_code=404, detail="Message not found")
		if msg["sender_id"] != safe_editor:
			raise HTTPException(status_code=403, detail="Only the original sender can edit this message")
		if msg.get("deleted_at"):
			raise HTTPException(status_code=400, detail="Cannot edit a deleted message")
		updated = db.edit_message(safe_msg, safe_content)
		if not updated:
			raise HTTPException(status_code=500, detail="Edit failed")
		# Broadcast edit
		from server.websocket import manager
		await manager.broadcast_to_conversation(msg["conversation_id"], {
			"type": "message_edited",
			"conversation_id": msg["conversation_id"],
			"message": updated,
		})
		return {"status": "ok", "message": updated}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to edit message %s: %s", msg_id, e)
		raise HTTPException(status_code=500, detail="Failed to edit message")


@router.delete("/{msg_id}", response_model=MessageDeleteResponse)
async def delete_message(msg_id: str, soft: bool = True, deleted_by: str = ""):
	"""Delete a message. Default is soft delete (marks deleted_at). Use soft=false for hard delete."""
	try:
		safe_msg = sanitize_uuid(msg_id)
		safe_deleter = sanitize_agent_id(deleted_by) if deleted_by else ""
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		msg = db.get_message(safe_msg)
		if not msg:
			raise HTTPException(status_code=404, detail="Message not found")
		if soft:
			ok = db.soft_delete_message(safe_msg)
		else:
			ok = db.delete_message(safe_msg)
		if not ok:
			raise HTTPException(status_code=404, detail="Message not found or already deleted")
		# Broadcast deletion
		from server.websocket import manager
		await manager.broadcast_to_conversation(msg["conversation_id"], {
			"type": "message_deleted",
			"conversation_id": msg["conversation_id"],
			"message_id": safe_msg,
			"soft": soft,
		})
		return {"status": "ok", "deleted": safe_msg, "soft": soft}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to delete message %s: %s", msg_id, e)
		raise HTTPException(status_code=500, detail="Failed to delete message")


@router.get("/{msg_id}/replies", response_model=RepliesResponse)
async def get_replies(msg_id: str, limit: int = 50):
	"""Get all replies to a specific message (thread)."""
	try:
		safe_msg = sanitize_uuid(msg_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		msg = db.get_message(safe_msg)
		if not msg:
			raise HTTPException(status_code=404, detail="Message not found")
		replies = db.get_replies(safe_msg, limit)
		return {"status": "ok", "replies": replies, "count": len(replies)}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to get replies for %s: %s", msg_id, e)
		raise HTTPException(status_code=500, detail="Failed to get replies")


@router.post("/{msg_id}/react", response_model=OkResponse)
async def react_to_message(msg_id: str, body: ReactRequest):
	"""Add or toggle an emoji reaction on a message."""
	try:
		safe_msg = sanitize_uuid(msg_id)
		safe_agent = sanitize_agent_id(body.agent_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		msg = db.get_message(safe_msg)
		if not msg:
			raise HTTPException(status_code=404, detail="Message not found")
		db.react_to_message(safe_msg, safe_agent, body.emoji)
		# Broadcast reaction
		from server.websocket import manager
		await manager.broadcast_to_conversation(msg["conversation_id"], {
			"type": "message_reacted",
			"conversation_id": msg["conversation_id"],
			"message_id": safe_msg,
			"agent_id": safe_agent,
			"emoji": body.emoji,
		})
		return {"status": "ok"}
	except HTTPException:
		raise
	except Exception as e:
		logger.error("Failed to react to message %s: %s", msg_id, e)
		raise HTTPException(status_code=500, detail="Failed to add reaction")


@router.get("/{msg_id}/reactions", response_model=ReactionsResponse)
async def get_reactions(msg_id: str):
	"""Get all reactions for a message."""
	try:
		safe_msg = sanitize_uuid(msg_id)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		reactions = db.get_message_reactions(safe_msg)
		return {"status": "ok", "reactions": reactions}
	except Exception as e:
		logger.error("Failed to get reactions for %s: %s", msg_id, e)
		raise HTTPException(status_code=500, detail="Failed to get reactions")


@router.get("/search", response_model=SearchResponse)
async def search_messages(q: str = Query(..., min_length=1, max_length=500), limit: int = 20, offset: int = 0):
	try:
		safe_q = sanitize_string(q, max_length=500)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	try:
		db = get_db()
		results = db.search_messages(safe_q, limit, offset)
		return {"status": "ok", "results": results, "count": len(results)}
	except Exception as e:
		logger.error("Message search failed: %s", e)
		raise HTTPException(status_code=500, detail="Search failed")


@router.get("/feed", response_model=FeedResponse)
async def global_feed(limit: int = 100, offset: int = 0):
	"""Get the global message feed (all conversations)."""
	try:
		db = get_db()
		messages = db.global_feed(limit, offset)
		return {"status": "ok", "messages": messages, "count": len(messages)}
	except Exception as e:
		logger.error("Global feed failed: %s", e)
		raise HTTPException(status_code=500, detail="Failed to get feed")

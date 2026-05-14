"""WebSocket handler for real-time agent messaging with input validation."""

import asyncio
import json
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from server.security import sanitize_agent_id, sanitize_content, sanitize_uuid


class ConnectionManager:
    """Manages WebSocket connections for all connected agents."""

    def __init__(self):
        # agent_id -> WebSocket
        self.active: dict[str, WebSocket] = {}
        # agent_id -> set of subscribed conversation_ids
        self.subscriptions: dict[str, set[str]] = {}
        # agent_id -> connection timestamp
        self.connected_at: dict[str, float] = {}
        # agent_id -> list of capabilities advertised on connect
        self.capabilities: dict[str, list[str]] = {}

    async def connect(self, agent_id: str, websocket: WebSocket):
        await websocket.accept()

        # Disconnect existing connection for same agent (single-session per agent)
        if agent_id in self.active:
            try:
                await self.active[agent_id].close(code=4002, reason="Replaced by new connection")
            except Exception:
                pass

        self.active[agent_id] = websocket
        self.subscriptions[agent_id] = set()

        import time
        self.connected_at[agent_id] = time.time()

    def disconnect(self, agent_id: str):
        self.active.pop(agent_id, None)
        self.subscriptions.pop(agent_id, None)
        self.connected_at.pop(agent_id, None)
        self.capabilities.pop(agent_id, None)

    async def send_to_agent(self, agent_id: str, data: dict):
        """Send a message to a specific agent's WebSocket."""
        ws = self.active.get(agent_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(agent_id)

    async def broadcast_to_conversation(self, conversation_id: str, data: dict, exclude: Optional[str] = None):
        """Send to all agents subscribed to a conversation."""
        for agent_id, subs in list(self.subscriptions.items()):
            if conversation_id in subs and agent_id != exclude:
                await self.send_to_agent(agent_id, data)

    async def broadcast(self, data: dict, exclude: Optional[str] = None):
        """Broadcast to all connected agents."""
        for agent_id in list(self.active.keys()):
            if agent_id != exclude:
                await self.send_to_agent(agent_id, data)

    def subscribe(self, agent_id: str, conversation_id: str):
        if agent_id in self.subscriptions:
            self.subscriptions[agent_id].add(conversation_id)

    def unsubscribe(self, agent_id: str, conversation_id: str):
        if agent_id in self.subscriptions:
            self.subscriptions[agent_id].discard(conversation_id)

    def is_online(self, agent_id: str) -> bool:
        return agent_id in self.active

    @property
    def online_agents(self) -> list[str]:
        return list(self.active.keys())


# Global instance
manager = ConnectionManager()


async def websocket_handler(websocket: WebSocket, agent_id: str, db):
    """Handle a WebSocket connection for an agent."""

    try:
        safe_agent = sanitize_agent_id(agent_id)
    except ValueError:
        await websocket.close(code=4000, reason="Invalid agent ID")
        return

    await manager.connect(safe_agent, websocket)

    # Auto-subscribe to all conversations this agent is in
    convs = db.list_conversations(safe_agent)
    for conv in convs:
        manager.subscribe(safe_agent, conv["id"])

    # Deliver any undelivered messages from while offline
    undelivered = db.get_undelivered_messages(safe_agent, limit=100)
    if undelivered:
        await manager.send_to_agent(safe_agent, {
            "type": "offline_messages",
            "messages": undelivered,
            "count": len(undelivered),
        })
        # Mark them as delivered
        for msg in undelivered:
            db.mark_delivered(msg["id"], safe_agent)

    # Notify others this agent is online
    online_payload = {
        "type": "agent_status",
        "agent_id": safe_agent,
        "status": "online",
    }
    if safe_agent in manager.capabilities:
        online_payload["capabilities"] = manager.capabilities[safe_agent]
    await manager.broadcast(online_payload, exclude=safe_agent)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_to_agent(safe_agent, {"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = data.get("type")

            if msg_type == "send_message":
                conv_id = data.get("conversation_id", "")
                content = data.get("content", "")
                msg_type_field = data.get("msg_type", "text")
                reply_to_id = data.get("reply_to_id")
                priority = data.get("priority", "normal")

                if conv_id and content:
                    try:
                        safe_conv = sanitize_uuid(conv_id)
                        safe_content = sanitize_content(content)
                        safe_reply_to = sanitize_uuid(reply_to_id) if reply_to_id else None
                    except ValueError as e:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": str(e)})
                        continue

                    msg = db.send_message(safe_conv, safe_agent, safe_content, msg_type_field,
                                         data.get("metadata"), safe_reply_to, priority)

                    # Broadcast to conversation members
                    ws_event = {"type": "new_message", "conversation_id": safe_conv, "message": msg}
                    if priority == "urgent":
                        for member in db.get_conversation_members(safe_conv):
                            await manager.send_to_agent(member["agent_id"], ws_event)
                    else:
                        await manager.broadcast_to_conversation(safe_conv, ws_event, exclude=safe_agent)

                    # Confirm to sender
                    await manager.send_to_agent(safe_agent, {"type": "message_sent", "message": msg})

            elif msg_type == "edit_message":
                msg_id = data.get("message_id", "")
                new_content = data.get("content", "")
                if msg_id and new_content:
                    try:
                        safe_msg = sanitize_uuid(msg_id)
                        safe_content = sanitize_content(new_content)
                    except ValueError as e:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": str(e)})
                        continue

                    updated = db.edit_message(safe_msg, safe_content)
                    if updated:
                        await manager.broadcast_to_conversation(updated["conversation_id"], {
                            "type": "message_edited",
                            "conversation_id": updated["conversation_id"],
                            "message": updated,
                        })
                    else:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": "Edit failed"})

            elif msg_type == "delete_message":
                msg_id = data.get("message_id", "")
                soft = data.get("soft", True)
                if msg_id:
                    try:
                        safe_msg = sanitize_uuid(msg_id)
                    except ValueError as e:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": str(e)})
                        continue

                    msg = db.get_message(safe_msg)
                    if not msg:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": "Message not found"})
                        continue

                    if soft:
                        ok = db.soft_delete_message(safe_msg)
                    else:
                        ok = db.delete_message(safe_msg)

                    if ok:
                        await manager.broadcast_to_conversation(msg["conversation_id"], {
                            "type": "message_deleted",
                            "conversation_id": msg["conversation_id"],
                            "message_id": safe_msg,
                            "soft": soft,
                        })

            elif msg_type == "react":
                msg_id = data.get("message_id", "")
                emoji = data.get("emoji", "")
                if msg_id and emoji:
                    try:
                        safe_msg = sanitize_uuid(msg_id)
                    except ValueError as e:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": str(e)})
                        continue

                    msg = db.get_message(safe_msg)
                    if msg:
                        db.react_to_message(safe_msg, safe_agent, emoji)
                        await manager.broadcast_to_conversation(msg["conversation_id"], {
                            "type": "message_reacted",
                            "conversation_id": msg["conversation_id"],
                            "message_id": safe_msg,
                            "agent_id": safe_agent,
                            "emoji": emoji,
                        })

            elif msg_type == "mark_read":
                msg_id = data.get("message_id", "")
                if msg_id:
                    try:
                        safe_msg = sanitize_uuid(msg_id)
                    except ValueError as e:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": str(e)})
                        continue

                    msg = db.get_message(safe_msg)
                    if msg:
                        db.mark_read(safe_msg, safe_agent)
                        await manager.broadcast_to_conversation(msg["conversation_id"], {
                            "type": "message_read",
                            "message_id": safe_msg,
                            "conversation_id": msg["conversation_id"],
                            "agent_id": safe_agent,
                        })

            elif msg_type == "mark_delivered":
                msg_id = data.get("message_id", "")
                if msg_id:
                    try:
                        safe_msg = sanitize_uuid(msg_id)
                    except ValueError as e:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": str(e)})
                        continue

                    msg = db.get_message(safe_msg)
                    if msg:
                        db.mark_delivered(safe_msg, safe_agent)
                        await manager.send_to_agent(msg["sender_id"], {
                            "type": "message_delivered",
                            "message_id": safe_msg,
                            "conversation_id": msg["conversation_id"],
                            "agent_id": safe_agent,
                        })

            elif msg_type == "typing":
                conv_id = data.get("conversation_id", "")
                if conv_id:
                    try:
                        safe_conv = sanitize_uuid(conv_id)
                    except ValueError:
                        continue
                    db.set_typing(safe_conv, safe_agent)
                    await manager.broadcast_to_conversation(safe_conv, {
                        "type": "typing",
                        "conversation_id": safe_conv,
                        "agent_id": safe_agent,
                    }, exclude=safe_agent)

            elif msg_type == "stop_typing":
                conv_id = data.get("conversation_id", "")
                if conv_id:
                    try:
                        safe_conv = sanitize_uuid(conv_id)
                    except ValueError:
                        continue
                    db.clear_typing(safe_conv, safe_agent)
                    await manager.broadcast_to_conversation(safe_conv, {
                        "type": "stop_typing",
                        "conversation_id": safe_conv,
                        "agent_id": safe_agent,
                    }, exclude=safe_agent)

            elif msg_type == "subscribe":
                conv_id = data.get("conversation_id", "")
                if conv_id:
                    try:
                        safe_conv = sanitize_uuid(conv_id)
                    except ValueError:
                        continue
                    manager.subscribe(safe_agent, safe_conv)

            elif msg_type == "unsubscribe":
                conv_id = data.get("conversation_id", "")
                if conv_id:
                    try:
                        safe_conv = sanitize_uuid(conv_id)
                    except ValueError:
                        continue
                    manager.unsubscribe(safe_agent, safe_conv)

            elif msg_type == "ping":
                await manager.send_to_agent(safe_agent, {"type": "pong"})

            elif msg_type == "broadcast":
                content = data.get("content", "")
                safe_broadcast = sanitize_content(content, max_length=5000)
                msg = db.broadcast_message(safe_agent, safe_broadcast)
                await manager.broadcast({
                    "type": "broadcast",
                    "from": safe_agent,
                    "message": msg,
                }, exclude=safe_agent)

            elif msg_type == "capabilities":
                # Agent advertises its capabilities on connect or update
                caps = data.get("capabilities", [])
                if isinstance(caps, list):
                    manager.capabilities[safe_agent] = caps
                    db.set_agent_capabilities(safe_agent, caps)
                    await manager.broadcast({
                        "type": "capabilities_update",
                        "agent_id": safe_agent,
                        "capabilities": caps,
                    }, exclude=safe_agent)

            else:
                await manager.send_to_agent(safe_agent, {
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        manager.disconnect(safe_agent)
        db.update_agent_status(safe_agent, "offline")
        db.clear_typing_all(safe_agent)
        await manager.broadcast({
            "type": "agent_status",
            "agent_id": safe_agent,
            "status": "offline",
        })

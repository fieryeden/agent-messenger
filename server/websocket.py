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

    # Notify others this agent is online
    await manager.broadcast({
        "type": "agent_status",
        "agent_id": safe_agent,
        "status": "online",
    }, exclude=safe_agent)

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
                if conv_id and content:
                    try:
                        safe_conv = sanitize_uuid(conv_id)
                        safe_content = sanitize_content(content)
                    except ValueError as e:
                        await manager.send_to_agent(safe_agent, {"type": "error", "message": str(e)})
                        continue

                    msg = db.send_message(safe_conv, safe_agent, safe_content, msg_type_field)
                    # Broadcast to conversation members
                    await manager.broadcast_to_conversation(safe_conv, {
                        "type": "new_message",
                        "conversation_id": safe_conv,
                        "message": msg,
                    }, exclude=safe_agent)
                    # Confirm to sender
                    await manager.send_to_agent(safe_agent, {
                        "type": "message_sent",
                        "message": msg,
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
                await manager.broadcast({
                    "type": "broadcast",
                    "from": safe_agent,
                    "content": safe_broadcast,
                }, exclude=safe_agent)

            else:
                await manager.send_to_agent(safe_agent, {
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        manager.disconnect(safe_agent)
        db.update_agent_status(safe_agent, "offline")
        db.clear_typing_all(safe_agent)  # Clear typing for all conversations
        await manager.broadcast({
            "type": "agent_status",
            "agent_id": safe_agent,
            "status": "offline",
        })

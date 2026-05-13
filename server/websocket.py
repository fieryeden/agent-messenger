"""WebSocket handler for real-time agent messaging."""

import asyncio
import json
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """Manages WebSocket connections for all connected agents."""

    def __init__(self):
        # agent_id -> WebSocket
        self.active: dict[str, WebSocket] = {}
        # agent_id -> set of subscribed conversation_ids
        self.subscriptions: dict[str, set[str]] = {}

    async def connect(self, agent_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active[agent_id] = websocket
        self.subscriptions[agent_id] = set()

    def disconnect(self, agent_id: str):
        self.active.pop(agent_id, None)
        self.subscriptions.pop(agent_id, None)

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
        for agent_id, subs in self.subscriptions.items():
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
    await manager.connect(agent_id, websocket)

    # Auto-subscribe to all conversations this agent is in
    convs = db.list_conversations(agent_id)
    for conv in convs:
        manager.subscribe(agent_id, conv["id"])

    # Notify others this agent is online
    await manager.broadcast({
        "type": "agent_status",
        "agent_id": agent_id,
        "status": "online",
    }, exclude=agent_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_to_agent(agent_id, {"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = data.get("type")

            if msg_type == "send_message":
                conv_id = data.get("conversation_id")
                content = data.get("content", "")
                if conv_id and content:
                    msg = db.send_message(conv_id, agent_id, content)
                    # Broadcast to conversation members
                    await manager.broadcast_to_conversation(conv_id, {
                        "type": "new_message",
                        "conversation_id": conv_id,
                        "message": msg,
                    }, exclude=agent_id)
                    # Confirm to sender
                    await manager.send_to_agent(agent_id, {
                        "type": "message_sent",
                        "message": msg,
                    })

            elif msg_type == "subscribe":
                conv_id = data.get("conversation_id")
                if conv_id:
                    manager.subscribe(agent_id, conv_id)

            elif msg_type == "unsubscribe":
                conv_id = data.get("conversation_id")
                if conv_id:
                    manager.unsubscribe(agent_id, conv_id)

            elif msg_type == "ping":
                await manager.send_to_agent(agent_id, {"type": "pong"})

            elif msg_type == "broadcast":
                content = data.get("content", "")
                # Create/find a broadcast conversation or send to all
                await manager.broadcast({
                    "type": "broadcast",
                    "from": agent_id,
                    "content": content,
                }, exclude=agent_id)

    except WebSocketDisconnect:
        manager.disconnect(agent_id)
        db.update_agent_status(agent_id, "offline")
        await manager.broadcast({
            "type": "agent_status",
            "agent_id": agent_id,
            "status": "offline",
        })

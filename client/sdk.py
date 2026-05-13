"""Agent Messenger Python SDK — async client for agents to connect and communicate."""

import asyncio
import json
import uuid
from typing import Callable, Optional
import aiohttp


class Message:
    def __init__(self, data: dict):
        self.id = data.get("id")
        self.conversation_id = data.get("conversation_id")
        self.sender_id = data.get("sender_id")
        self.sender_name = data.get("sender_name", "")
        self.content = data.get("content", "")
        self.type = data.get("type", "text")
        self.created_at = data.get("created_at", "")
        self.read_by = data.get("read_by", [])

    def __repr__(self):
        return f"Message(from={self.sender_id}, content={self.content[:50]})"


class MessengerClient:
    """Async client for the Agent Messenger server.

    Usage:
        client = MessengerClient("my-agent-01", server_url="http://localhost:8096")
        await client.connect()
        await client.send_dm("other-agent", "Hello!")
    """

    def __init__(self, agent_id: str, name: str = None, server_url: str = "http://localhost:8096",
                 agent_type: str = "detached", metadata: dict = None):
        self.agent_id = agent_id
        self.name = name or agent_id
        self.server_url = server_url.rstrip("/")
        self.agent_type = agent_type
        self.metadata = metadata or {}

        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._callbacks: dict[str, list[Callable]] = {}
        self._conversations: dict[str, str] = {}  # other_agent_id -> conversation_id
        self._running = False

    async def connect(self):
        """Register agent and open WebSocket connection."""
        self._session = aiohttp.ClientSession()

        # Register via REST
        async with self._session.post(f"{self.server_url}/api/agents/register", json={
            "id": self.agent_id,
            "name": self.name,
            "type": self.agent_type,
            "metadata": self.metadata,
        }) as resp:
            data = await resp.json()
            if data.get("status") != "ok":
                raise ConnectionError(f"Failed to register: {data}")

        # Load existing conversations
        async with self._session.get(f"{self.server_url}/api/conversations", params={"agent_id": self.agent_id}) as resp:
            data = await resp.json()
            for conv in data.get("conversations", []):
                for member_id in conv.get("members", []):
                    if member_id != self.agent_id:
                        self._conversations[member_id] = conv["id"]

        # Connect WebSocket
        ws_url = self.server_url.replace("http", "ws") + f"/ws/{self.agent_id}"
        self._ws = await self._session.ws_connect(ws_url)
        self._running = True

        # Start listener
        asyncio.create_task(self._listen())

    async def _listen(self):
        """Background task to listen for incoming messages."""
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await self._dispatch(data)
                except json.JSONDecodeError:
                    pass
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                self._running = False
                break

    async def _dispatch(self, data: dict):
        """Dispatch incoming data to registered callbacks."""
        msg_type = data.get("type", "")
        for cb in self._callbacks.get(msg_type, []):
            try:
                if msg_type == "new_message":
                    await cb(Message(data.get("message", {})))
                else:
                    await cb(data)
            except Exception as e:
                print(f"[messenger-sdk] Callback error: {e}")

        # Also call wildcard callbacks
        for cb in self._callbacks.get("*", []):
            try:
                await cb(data)
            except Exception:
                pass

    def on(self, event_type: str, callback: Callable):
        """Register a callback for an event type. Use '*' for all events."""
        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(callback)

    def on_message(self, callback: Callable):
        """Shorthand for on('new_message', callback)."""
        self.on("new_message", callback)

    async def send_dm(self, recipient_id: str, content: str) -> Message:
        """Send a direct message to another agent. Creates conversation if needed."""
        # Find or create conversation
        conv_id = self._conversations.get(recipient_id)
        if not conv_id:
            async with self._session.post(f"{self.server_url}/api/conversations", json={
                "type": "dm",
                "member_ids": [self.agent_id, recipient_id],
            }) as resp:
                data = await resp.json()
                conv_id = data["conversation"]["id"]
                self._conversations[recipient_id] = conv_id

        # Send via REST (also broadcasts via WebSocket)
        async with self._session.post(f"{self.server_url}/api/messages", json={
            "conversation_id": conv_id,
            "sender_id": self.agent_id,
            "content": content,
        }) as resp:
            data = await resp.json()
            return Message(data.get("message", {}))

    async def send_to_conversation(self, conversation_id: str, content: str) -> Message:
        """Send a message to an existing conversation."""
        async with self._session.post(f"{self.server_url}/api/messages", json={
            "conversation_id": conversation_id,
            "sender_id": self.agent_id,
            "content": content,
        }) as resp:
            data = await resp.json()
            return Message(data.get("message", {}))

    async def broadcast(self, content: str):
        """Broadcast a message to all connected agents."""
        if self._ws and not self._ws.closed:
            await self._ws.send_json({
                "type": "broadcast",
                "content": content,
            })

    async def get_messages(self, conversation_id: str, limit: int = 50) -> list[Message]:
        """Get message history for a conversation."""
        async with self._session.get(
            f"{self.server_url}/api/messages/conversation/{conversation_id}",
            params={"limit": limit},
        ) as resp:
            data = await resp.json()
            return [Message(m) for m in data.get("messages", [])]

    async def list_agents(self) -> list[dict]:
        """List all registered agents."""
        async with self._session.get(f"{self.server_url}/api/agents") as resp:
            data = await resp.json()
            return data.get("agents", [])

    async def disconnect(self):
        """Close the connection."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()

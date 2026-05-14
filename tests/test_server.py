"""Tests for Agent Messenger Server — DB layer, REST routes, WebSocket, and auth."""

import json
from datetime import datetime, timezone, timedelta
import pytest
import asyncio

from server.db import MessengerDB


# ── Fixtures ──

@pytest.fixture
def db(tmp_path):
    """Fresh DB for each test."""
    database = MessengerDB(str(tmp_path / "test_messenger.db"))
    yield database
    database.close()


# ── DB Layer Tests ──

class TestMessengerDB:
    def test_register_agent(self, db):
        agent = db.register_agent("eden-01", "Eden Worker", "cluster")
        assert agent["id"] == "eden-01"
        assert agent["name"] == "Eden Worker"
        assert agent["status"] == "online"

    def test_register_agent_upsert(self, db):
        db.register_agent("a1", "Agent One", "cluster")
        updated = db.register_agent("a1", "Agent One Renamed", "detached")
        assert updated["name"] == "Agent One Renamed"
        assert updated["type"] == "detached"

    def test_get_agent_not_found(self, db):
        assert db.get_agent("nonexistent") is None

    def test_list_agents(self, db):
        db.register_agent("a", "Agent A")
        db.register_agent("b", "Agent B")
        agents = db.list_agents()
        assert len(agents) == 2

    def test_list_agents_by_status(self, db):
        db.register_agent("a", "Online Agent")
        db.register_agent("b", "Also Online")
        db.update_agent_status("b", "offline")
        online = db.list_agents(status="online")
        assert len(online) == 1

    def test_update_agent_status(self, db):
        db.register_agent("a", "Test")
        db.update_agent_status("a", "offline")
        agent = db.get_agent("a")
        assert agent["status"] == "offline"

    # ── Conversations ──

    def test_create_dm(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        assert conv["type"] == "dm"
        assert conv["id"] is not None

    def test_dm_dedup(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv1 = db.create_conversation("dm", None, ["a", "b"])
        conv2 = db.create_conversation("dm", None, ["a", "b"])
        assert conv1["id"] == conv2["id"]

    def test_create_group(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        db.register_agent("c", "C")
        conv = db.create_conversation("group", "War Room", ["a", "b", "c"])
        assert conv["type"] == "group"
        assert conv["name"] == "War Room"

    def test_get_conversation(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        fetched = db.get_conversation(conv["id"])
        assert fetched is not None
        assert len(fetched["members"]) == 2

    def test_list_conversations(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        db.create_conversation("dm", None, ["a", "b"])
        convs = db.list_conversations("a")
        assert len(convs) == 1

    def test_add_remove_member(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        db.register_agent("c", "C")
        conv = db.create_conversation("group", "Test", ["a", "b"])
        db.add_conversation_member(conv["id"], "c")
        fetched = db.get_conversation(conv["id"])
        assert len(fetched["members"]) == 3
        db.remove_conversation_member(conv["id"], "c")
        fetched = db.get_conversation(conv["id"])
        assert len(fetched["members"]) == 2

    # ── Messages ──

    def test_send_message(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        msg = db.send_message(conv["id"], "a", "Hello!")
        assert msg["content"] == "Hello!"
        assert msg["sender_id"] == "a"

    def test_get_messages(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        db.send_message(conv["id"], "a", "First")
        db.send_message(conv["id"], "b", "Second")
        msgs = db.get_messages(conv["id"])
        assert len(msgs) == 2

    def test_mark_read(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        msg = db.send_message(conv["id"], "a", "Read me")
        db.mark_read(msg["id"], "b")
        updated = db.get_message(msg["id"])
        assert "b" in updated["read_by"]

    def test_search_messages(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        db.send_message(conv["id"], "a", "Deploy to production")
        db.send_message(conv["id"], "a", "Fix the bug")
        results = db.search_messages("Deploy")
        assert len(results) >= 1

    def test_global_feed(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        db.send_message(conv["id"], "a", "Global message")
        feed = db.global_feed(limit=10)
        assert len(feed) >= 1

    def test_stats(self, db):
        db.register_agent("a", "A")
        db.register_agent("b", "B")
        conv = db.create_conversation("dm", None, ["a", "b"])
        db.send_message(conv["id"], "a", "Stats msg")
        stats = db.stats()
        assert stats["agents"] == 2
        assert stats["conversations"] == 1
        assert stats["messages"] == 1


# ── REST API Integration Tests ──

class TestRESTAPI:
    @pytest.fixture
    def client(self, tmp_path):
        """httpx AsyncClient wired to the FastAPI app."""
        from server.main import create_app, load_config
        config = load_config("config.yaml")
        config["database"]["path"] = str(tmp_path / "test_api.db")
        config["auth"]["enabled"] = False
        app = create_app(config)
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_register_and_list_agents(self, client):
        resp = await client.post("/api/agents/register", json={
            "id": "test-agent", "name": "Test Agent", "type": "detached"
        })
        assert resp.status_code == 200
        resp = await client.get("/api/agents")
        data = resp.json()
        assert data["count"] >= 1

    @pytest.mark.asyncio
    async def test_get_agent(self, client):
        await client.post("/api/agents/register", json={"id": "a1", "name": "A1"})
        resp = await client.get("/api/agents/a1")
        assert resp.status_code == 200
        assert resp.json()["agent"]["id"] == "a1"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, client):
        resp = await client.get("/api/agents/ghost")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_conversation(self, client):
        await client.post("/api/agents/register", json={"id": "x", "name": "X"})
        await client.post("/api/agents/register", json={"id": "y", "name": "Y"})
        resp = await client.post("/api/conversations", json={
            "type": "dm", "member_ids": ["x", "y"]
        })
        assert resp.status_code == 200
        assert resp.json()["conversation"]["type"] == "dm"

    @pytest.mark.asyncio
    async def test_send_and_get_messages(self, client):
        await client.post("/api/agents/register", json={"id": "s", "name": "Sender"})
        await client.post("/api/agents/register", json={"id": "r", "name": "Receiver"})
        conv = await client.post("/api/conversations", json={
            "type": "dm", "member_ids": ["s", "r"]
        })
        conv_id = conv.json()["conversation"]["id"]
        # Send
        resp = await client.post("/api/messages", json={
            "conversation_id": conv_id, "sender_id": "s", "content": "Hello REST"
        })
        assert resp.status_code == 200
        # Get
        resp = await client.get(f"/api/messages/conversation/{conv_id}")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    @pytest.mark.asyncio
    async def test_send_message_invalid_conversation(self, client):
        await client.post("/api/agents/register", json={"id": "z", "name": "Z"})
        resp = await client.post("/api/messages", json={
            "conversation_id": "00000000-0000-0000-0000-000000000000", "sender_id": "z", "content": "Oops"
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_send_message_unregistered_sender(self, client):
        await client.post("/api/agents/register", json={"id": "m1", "name": "M1"})
        await client.post("/api/agents/register", json={"id": "m2", "name": "M2"})
        conv = await client.post("/api/conversations", json={
            "type": "dm", "member_ids": ["m1", "m2"]
        })
        conv_id = conv.json()["conversation"]["id"]
        resp = await client.post("/api/messages", json={
            "conversation_id": conv_id, "sender_id": "ghost", "content": "Nope"
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_search_messages(self, client):
        await client.post("/api/agents/register", json={"id": "q1", "name": "Q1"})
        await client.post("/api/agents/register", json={"id": "q2", "name": "Q2"})
        conv = await client.post("/api/conversations", json={
            "type": "dm", "member_ids": ["q1", "q2"]
        })
        conv_id = conv.json()["conversation"]["id"]
        await client.post("/api/messages", json={
            "conversation_id": conv_id, "sender_id": "q1", "content": "Searchable content here"
        })
        resp = await client.get("/api/messages/search?q=Searchable")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    @pytest.mark.asyncio
    async def test_dashboard_stats(self, client):
        resp = await client.get("/dashboard/stats")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_feed(self, client):
        resp = await client.get("/dashboard/feed")
        assert resp.status_code == 200


# ── Auth Tests ──

class TestAuth:
    @pytest.mark.asyncio
    async def test_auth_reject(self, tmp_path):
        """Auth enabled, no key → 401 on API routes."""
        from server.main import create_app, load_config
        config = load_config("config.yaml")
        config["database"]["path"] = str(tmp_path / "test_auth.db")
        config["auth"]["enabled"] = True
        config["auth"]["api_keys"] = ["secret-123"]
        app = create_app(config)
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        resp = await client.get("/api/agents")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_accept(self, tmp_path):
        """Auth enabled + valid key → 200."""
        from server.main import create_app, load_config
        config = load_config("config.yaml")
        config["database"]["path"] = str(tmp_path / "test_auth2.db")
        config["auth"]["enabled"] = True
        config["auth"]["api_keys"] = ["secret-123"]
        app = create_app(config)
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        resp = await client.get("/api/agents", headers={"Authorization": "Bearer secret-123"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, tmp_path):
        """Health endpoint skips auth even when enabled."""
        from server.main import create_app, load_config
        config = load_config("config.yaml")
        config["database"]["path"] = str(tmp_path / "test_auth3.db")
        config["auth"]["enabled"] = True
        config["auth"]["api_keys"] = ["secret-123"]
        app = create_app(config)
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        resp = await client.get("/health")
        assert resp.status_code == 200


# ── v0.4.0 Feature Tests ──

class TestMessageThreading:
    """Feature 2: reply_to_id threading."""

    def test_send_reply(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        parent = db.send_message(conv_id, "a1", "Hello")
        reply = db.send_message(conv_id, "a1", "Reply", reply_to_id=parent["id"])

        assert reply["reply_to_id"] == parent["id"]

        replies = db.get_replies(parent["id"])
        assert len(replies) == 1
        assert replies[0]["content"] == "Reply"

    def test_get_replies_empty(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Standalone")
        replies = db.get_replies(msg["id"])
        assert replies == []


class TestMessageEdit:
    """Feature 3: edit_message with original preservation."""

    def test_edit_message(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Original")
        edited = db.edit_message(msg["id"], "Updated")

        assert edited is not None
        assert edited["edited_at"] is not None
        assert edited["original_content"] == "Original"
        assert edited["content"] == "Updated"

    def test_edit_deleted_message_fails(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "To be deleted")
        db.soft_delete_message(msg["id"])
        result = db.edit_message(msg["id"], "Try edit")
        assert result is None


class TestSoftDelete:
    """Feature 4: soft delete with content masking."""

    def test_soft_delete(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Secret info")
        ok = db.soft_delete_message(msg["id"])
        assert ok is True

        deleted = db.get_message(msg["id"])
        assert deleted["content"] == "[message deleted]"
        assert deleted["deleted_at"] is not None

    def test_soft_delete_idempotent(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Content")
        assert db.soft_delete_message(msg["id"]) is True
        assert db.soft_delete_message(msg["id"]) is False


class TestReactions:
    """Feature 5: emoji reactions with toggle."""

    def test_add_reaction(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.register_agent("a2", "Bob", "cluster")
        db.create_conversation("dm", "test", ["a1", "a2"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Hello")
        db.react_to_message(msg["id"], "a2", "thumbsup")

        reactions = db.get_message_reactions(msg["id"])
        assert len(reactions) == 1
        assert reactions[0]["count"] == 1

    def test_toggle_reaction_off(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Hello")
        db.react_to_message(msg["id"], "a1", "thumbsup")
        db.react_to_message(msg["id"], "a1", "thumbsup")  # Toggle off

        reactions = db.get_message_reactions(msg["id"])
        assert reactions == []

    def test_multiple_agents_react(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.register_agent("a2", "Bob", "cluster")
        db.register_agent("a3", "Carol", "cluster")
        db.create_conversation("group", "test", ["a1", "a2", "a3"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Big news")
        db.react_to_message(msg["id"], "a2", "fire")
        db.react_to_message(msg["id"], "a3", "fire")
        db.react_to_message(msg["id"], "a2", "party")

        reactions = db.get_message_reactions(msg["id"])
        fire = [r for r in reactions if r["emoji"] == "fire"][0]
        assert fire["count"] == 2
        party = [r for r in reactions if r["emoji"] == "party"][0]
        assert party["count"] == 1


class TestDeliveryTracking:
    """Feature 6: message delivery receipts."""

    def test_mark_delivered(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.register_agent("a2", "Bob", "cluster")
        db.create_conversation("dm", "test", ["a1", "a2"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Hello")
        db.mark_delivered(msg["id"], "a2")

        status = db.get_delivery_status(msg["id"])
        assert len(status) == 1
        assert status[0]["agent_id"] == "a2"

    def test_undelivered_messages(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.register_agent("a2", "Bob", "cluster")
        db.create_conversation("dm", "test", ["a1", "a2"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg1 = db.send_message(conv_id, "a1", "Msg 1")
        msg2 = db.send_message(conv_id, "a1", "Msg 2")

        db.mark_delivered(msg1["id"], "a2")

        undelivered = db.get_undelivered_messages("a2")
        assert len(undelivered) == 1
        assert undelivered[0]["id"] == msg2["id"]


class TestPriorityMessages:
    """Feature 7: priority field on messages."""

    def test_send_urgent_message(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "URGENT", priority="urgent")
        assert msg["priority"] == "urgent"

    def test_default_priority_normal(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.create_conversation("dm", "test", ["a1"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        msg = db.send_message(conv_id, "a1", "Normal")
        assert msg["priority"] == "normal"


class TestMigrationV2:
    """Verify migration v2 adds columns to existing DB."""

    def test_migration_adds_columns(self, tmp_path):
        import sqlite3
        db_path = str(tmp_path / "migrate_test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT, type TEXT, status TEXT, metadata TEXT DEFAULT '{}', created_at TEXT);
            CREATE TABLE conversations (id TEXT PRIMARY KEY, type TEXT, name TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE conversation_members (conversation_id TEXT, agent_id TEXT, role TEXT DEFAULT 'member', PRIMARY KEY(conversation_id, agent_id));
            CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT, sender_id TEXT, content TEXT, type TEXT DEFAULT 'text', metadata TEXT DEFAULT '{}', created_at TEXT, read_by TEXT DEFAULT '[]');
            CREATE TABLE typing_indicators (conversation_id TEXT, agent_id TEXT, started_at TEXT, PRIMARY KEY(conversation_id, agent_id));
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
            INSERT INTO schema_migrations VALUES (1, '2026-01-01');
        """)
        conn.commit()
        conn.close()

        db = MessengerDB(db_path)
        cols = [c[1] for c in db.conn.execute("PRAGMA table_info(messages)").fetchall()]
        db.close()

        assert "reply_to_id" in cols
        assert "priority" in cols
        assert "edited_at" in cols
        assert "edited_content" in cols
        assert "deleted_at" in cols


class TestTypingTimeout:
    """Feature 1: typing indicator auto-timeout."""

    def test_expired_typing_filtered(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.register_agent("a2", "Bob", "cluster")
        db.create_conversation("dm", "test", ["a1", "a2"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        db.conn.execute(
            "INSERT INTO typing_indicators (conversation_id, agent_id, started_at) VALUES (?, ?, ?)",
            (conv_id, "a1", "2026-01-01T00:00:00+00:00"),
        )
        db.conn.commit()

        result = db.get_typing(conv_id, timeout_seconds=15)
        assert len(result) == 0

    def test_recent_typing_preserved(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.register_agent("a2", "Bob", "cluster")
        db.create_conversation("dm", "test", ["a1", "a2"])
        convs = db.list_conversations("a1")
        conv_id = convs[0]["id"]

        db.set_typing(conv_id, "a1")
        result = db.get_typing(conv_id, timeout_seconds=15)
        assert len(result) == 1
        assert result[0]["agent_id"] == "a1"


class TestAgentCapabilities:
    """Feature 8: agent capabilities."""

    def test_set_capabilities(self, db):
        db.register_agent("a1", "Alice", "cluster")
        result = db.set_agent_capabilities("a1", ["research", "writing"])
        assert result is not None
        assert "research" in result["metadata"]["capabilities"]

    def test_find_by_capability(self, db):
        db.register_agent("a1", "Alice", "cluster")
        db.register_agent("a2", "Bob", "cluster")
        db.set_agent_capabilities("a1", ["research", "writing"])
        db.set_agent_capabilities("a2", ["coding", "research"])

        researchers = db.find_agents_by_capability("research")
        assert len(researchers) == 2

        coders = db.find_agents_by_capability("coding")
        assert len(coders) == 1


class TestFileAttachments:
    """v0.5.0 Feature 1: File upload/download/delete."""

    def test_store_file(self, db):
        db.register_agent("a1", "Uploader")
        conv = db.create_conversation("file-conv", "File Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "See attached")
        f = db.store_file(msg["id"], "a1", "report.pdf", "application/pdf", 1024, "/tmp/report.pdf")
        assert f["filename"] == "report.pdf"
        assert f["size_bytes"] == 1024

    def test_get_file(self, db):
        db.register_agent("a1", "Uploader")
        conv = db.create_conversation("fc2", "Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "msg")
        f = db.store_file(msg["id"], "a1", "data.csv", "text/csv", 500, "/tmp/data.csv")
        got = db.get_file(f["id"])
        assert got["content_type"] == "text/csv"

    def test_get_files_by_message(self, db):
        db.register_agent("a1", "Uploader")
        conv = db.create_conversation("fc3", "Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "msg")
        db.store_file(msg["id"], "a1", "f1.txt", "text/plain", 10, "/tmp/f1")
        db.store_file(msg["id"], "a1", "f2.txt", "text/plain", 20, "/tmp/f2")
        files = db.get_files_by_message(msg["id"])
        assert len(files) == 2

    def test_delete_file(self, db):
        db.register_agent("a1", "Uploader")
        conv = db.create_conversation("fc4", "Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "msg")
        f = db.store_file(msg["id"], "a1", "del.txt", "text/plain", 5, "/tmp/del")
        assert db.delete_file(f["id"]) is True
        assert db.get_file(f["id"]) is None

    def test_cleanup_expired_files(self, db):
        db.register_agent("a1", "Uploader")
        conv = db.create_conversation("fc5", "Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "msg")
        # expired 1 hour ago
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        f = db.store_file(msg["id"], "a1", "exp.txt", "text/plain", 5, "/tmp/exp", expires_at=past)
        count = db.cleanup_expired_files()
        assert count == 1
        assert db.get_file(f["id"]) is None


class TestPinnedMessages:
    """v0.5.0 Feature 3: Pin/unpin messages."""

    def test_pin_message(self, db):
        db.register_agent("a1", "Pinner")
        conv = db.create_conversation("pc1", "Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "Important!")
        result = db.pin_message(conv["id"], msg["id"], "a1")
        assert len(result) == 1
        assert result[0]["message_id"] == msg["id"]

    def test_unpin_message(self, db):
        db.register_agent("a1", "Pinner")
        conv = db.create_conversation("pc2", "Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "Important!")
        db.pin_message(conv["id"], msg["id"], "a1")
        assert db.unpin_message(conv["id"], msg["id"]) is True
        assert len(db.get_pinned_messages(conv["id"])) == 0

    def test_get_pinned_messages(self, db):
        db.register_agent("a1", "Pinner")
        conv = db.create_conversation("pc3", "Conv", ["a1"])
        msg1 = db.send_message(conv["id"], "a1", "Pin me 1")
        msg2 = db.send_message(conv["id"], "a1", "Pin me 2")
        db.pin_message(conv["id"], msg1["id"], "a1")
        db.pin_message(conv["id"], msg2["id"], "a1")
        pinned = db.get_pinned_messages(conv["id"])
        assert len(pinned) == 2

    def test_duplicate_pin_ignored(self, db):
        db.register_agent("a1", "Pinner")
        conv = db.create_conversation("pc4", "Conv", ["a1"])
        msg = db.send_message(conv["id"], "a1", "Once!")
        db.pin_message(conv["id"], msg["id"], "a1")
        db.pin_message(conv["id"], msg["id"], "a1")  # OR IGNORE
        assert len(db.get_pinned_messages(conv["id"])) == 1


class TestPolls:
    """v0.5.0 Feature 4: Polls and voting."""

    def test_create_poll(self, db):
        db.register_agent("a1", "Pollster")
        conv = db.create_conversation("plc1", "Conv", ["a1"])
        poll = db.create_poll(conv["id"], "a1", "Best language?", ["Python", "Rust", "Go"])
        assert poll["question"] == "Best language?"
        assert len(poll["options"]) == 3
        assert poll["vote_counts"] == [0, 0, 0]

    def test_vote_poll(self, db):
        db.register_agent("a1", "Pollster")
        db.register_agent("a2", "Voter")
        conv = db.create_conversation("plc2", "Conv", ["a1", "a2"])
        poll = db.create_poll(conv["id"], "a1", "Pick one", ["A", "B"])
        result = db.vote_poll(poll["id"], "a2", 1)
        assert result["vote_counts"] == [0, 1]
        assert result["total_votes"] == 1

    def test_vote_poll_single_overwrite(self, db):
        db.register_agent("a1", "Pollster")
        db.register_agent("a2", "Voter")
        conv = db.create_conversation("plc3", "Conv", ["a1", "a2"])
        poll = db.create_poll(conv["id"], "a1", "Pick one", ["A", "B"])
        db.vote_poll(poll["id"], "a2", 0)
        result = db.vote_poll(poll["id"], "a2", 1)  # Overwrites
        assert result["vote_counts"] == [0, 1]
        assert result["total_votes"] == 1

    def test_vote_poll_multi(self, db):
        db.register_agent("a1", "Pollster")
        db.register_agent("a2", "Voter")
        conv = db.create_conversation("plc4", "Conv", ["a1", "a2"])
        poll = db.create_poll(conv["id"], "a1", "Pick many", ["X", "Y", "Z"], multi_vote=True)
        db.vote_poll(poll["id"], "a2", 0)
        result = db.vote_poll(poll["id"], "a2", 2)
        assert result["vote_counts"] == [1, 0, 1]
        assert result["total_votes"] == 2

    def test_close_poll(self, db):
        db.register_agent("a1", "Pollster")
        conv = db.create_conversation("plc5", "Conv", ["a1"])
        poll = db.create_poll(conv["id"], "a1", "Done?", ["Yes", "No"])
        result = db.close_poll(poll["id"])
        assert result["closed_at"] is not None
        # Can't vote after close
        assert db.vote_poll(poll["id"], "a1", 0) is None

    def test_list_polls(self, db):
        db.register_agent("a1", "Pollster")
        conv = db.create_conversation("plc6", "Conv", ["a1"])
        db.create_poll(conv["id"], "a1", "Q1", ["A", "B"])
        db.create_poll(conv["id"], "a1", "Q2", ["C", "D"])
        active = db.list_polls(conv["id"])
        assert len(active) == 2
        # Close one
        db.close_poll(active[0]["id"])
        still_active = db.list_polls(conv["id"])
        assert len(still_active) == 1
        all_polls = db.list_polls(conv["id"], include_closed=True)
        assert len(all_polls) == 2

    def test_vote_invalid_option(self, db):
        db.register_agent("a1", "Pollster")
        conv = db.create_conversation("plc7", "Conv", ["a1"])
        poll = db.create_poll(conv["id"], "a1", "Q", ["A", "B"])
        assert db.vote_poll(poll["id"], "a1", 99) is None


class TestFTS5Search:
    """v0.5.0 Feature 2: FTS5 full-text search."""

    def test_fts5_basic_search(self, db):
        db.register_agent("a1", "Sender")
        conv = db.create_conversation("sc1", "Conv", ["a1"])
        db.send_message(conv["id"], "a1", "The quick brown fox jumps over the lazy dog")
        db.send_message(conv["id"], "a1", "Hello world from agent messenger")
        db.send_message(conv["id"], "a1", "Machine learning is transforming research")
        db.rebuild_fts_index()
        results = db.search_messages_fts("machine learning")
        assert len(results) >= 1
        assert "Machine learning" in results[0]["content"]

    def test_fts5_with_conversation_filter(self, db):
        db.register_agent("a1", "Sender")
        conv1 = db.create_conversation("sc2a", "Conv1", ["a1"])
        conv2 = db.create_conversation("sc2b", "Conv2", ["a1"])
        db.send_message(conv1["id"], "a1", "unique keyword pineapple")
        db.send_message(conv2["id"], "a1", "unique keyword pineapple")
        db.rebuild_fts_index()
        results = db.search_messages_fts("pineapple", conversation_id=conv1["id"])
        assert len(results) == 1

    def test_search_messages_fallback(self, db):
        """search_messages should work even if FTS5 has issues."""
        db.register_agent("a1", "Sender")
        conv = db.create_conversation("sc3", "Conv", ["a1"])
        db.send_message(conv["id"], "a1", "Findable content here")
        db.rebuild_fts_index()
        results = db.search_messages("Findable")
        assert len(results) >= 1

    def test_rebuild_fts_index(self, db):
        db.register_agent("a1", "Sender")
        conv = db.create_conversation("sc4", "Conv", ["a1"])
        db.send_message(conv["id"], "a1", "indexed content")
        # Rebuild should not raise
        db.rebuild_fts_index()
        results = db.search_messages_fts("indexed")
        assert len(results) == 1

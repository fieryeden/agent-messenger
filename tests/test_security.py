"""Tests for agent-messenger server v0.2.0 — security, input validation, typing, pagination."""

import json
import pytest

from server.db import MessengerDB
from server.security import (
    AuditLogger,
    RateLimiter,
    sanitize_agent_id,
    sanitize_content,
    sanitize_sql_like,
    sanitize_string,
    sanitize_uuid,
)


@pytest.fixture
def db(tmp_path):
    return MessengerDB(str(tmp_path / "test.db"))


@pytest.fixture
def audit_db(tmp_path):
    return AuditLogger(str(tmp_path / "audit.db"))


@pytest.fixture
def rate_limiter():
    return RateLimiter(requests_per_minute=10, burst=3)


# ── Input Sanitization (same as shared-memory but verify in messenger context) ──

class TestSanitization:
    def test_sanitize_agent_id(self):
        assert sanitize_agent_id("bot-01_prod.test") == "bot-01_prod.test"

    def test_sanitize_agent_id_rejects_bad(self):
        with pytest.raises(ValueError):
            sanitize_agent_id("")

    def test_sanitize_content_html(self):
        assert "<script>" not in sanitize_content("<script>alert(1)</script>")

    def test_sanitize_uuid(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        assert sanitize_uuid(uid) == uid

    def test_sanitize_uuid_rejects_invalid(self):
        with pytest.raises(ValueError):
            sanitize_uuid("not-a-uuid")


# ── Rate Limiting ──

class TestRateLimiting:
    def test_allows_under_limit(self, rate_limiter):
        assert rate_limiter.is_allowed("c1") is True

    def test_blocks_over_burst(self, rate_limiter):
        for _ in range(3):
            rate_limiter.is_allowed("c2")
        assert rate_limiter.is_allowed("c2") is False

    def test_independent_clients(self, rate_limiter):
        rate_limiter.is_allowed("a")
        rate_limiter.is_allowed("a")
        rate_limiter.is_allowed("a")
        assert rate_limiter.is_allowed("b") is True


# ── Audit Logger ──

class TestAudit:
    def test_log_and_query(self, audit_db):
        audit_db.log("send_message", agent_id="bot1", resource_type="message")
        entries = audit_db.query(agent_id="bot1")
        assert len(entries) == 1
        assert entries[0]["action"] == "send_message"

    def test_stats(self, audit_db):
        audit_db.log("register", agent_id="a1")
        audit_db.log("send_message", agent_id="a1")
        stats = audit_db.stats()
        assert stats["total_entries"] == 2


# ── DB: LIKE injection protection ──

class TestDBSearchSafety:
    def test_search_with_percent(self, db):
        db.register_agent("a1", "Agent1")
        db.register_agent("a2", "Agent2")
        conv = db.create_conversation("dm", None, ["a1", "a2"])
        db.send_message(conv["id"], "a1", "100% done")
        results = db.search_messages("100%")
        # Should not crash — % treated as literal
        assert isinstance(results, list)

    def test_search_with_underscore(self, db):
        db.register_agent("a3", "Agent3")
        db.register_agent("a4", "Agent4")
        conv = db.create_conversation("dm", None, ["a3", "a4"])
        db.send_message(conv["id"], "a3", "test_value here")
        results = db.search_messages("test_value")
        assert isinstance(results, list)


# ── DB: New features ──

class TestDBTypingIndicators:
    def test_set_and_get_typing(self, db):
        db.register_agent("t1", "Typer1")
        db.register_agent("t2", "Typer2")
        conv = db.create_conversation("dm", None, ["t1", "t2"])
        db.set_typing(conv["id"], "t1")
        typing = db.get_typing(conv["id"])
        assert len(typing) == 1
        assert typing[0]["agent_id"] == "t1"

    def test_clear_typing(self, db):
        db.register_agent("t3", "Typer3")
        db.register_agent("t4", "Typer4")
        conv = db.create_conversation("dm", None, ["t3", "t4"])
        db.set_typing(conv["id"], "t3")
        db.clear_typing(conv["id"], "t3")
        typing = db.get_typing(conv["id"])
        assert len(typing) == 0

    def test_clear_typing_all(self, db):
        db.register_agent("t5", "Typer5")
        db.register_agent("t6", "Typer6")
        conv = db.create_conversation("dm", None, ["t5", "t6"])
        db.set_typing(conv["id"], "t5")
        db.clear_typing_all("t5")
        typing = db.get_typing(conv["id"])
        assert len(typing) == 0


class TestDBPagination:
    def test_list_agents_with_limit(self, db):
        for i in range(5):
            db.register_agent(f"pag-{i}", f"Agent {i}")
        agents = db.list_agents(limit=3)
        assert len(agents) == 3

    def test_list_agents_with_offset(self, db):
        for i in range(5):
            db.register_agent(f"off-{i}", f"Agent {i}")
        page1 = db.list_agents(limit=2, offset=0)
        page2 = db.list_agents(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]


class TestDBMessageOps:
    def test_delete_message(self, db):
        db.register_agent("d1", "Deleter1")
        db.register_agent("d2", "Deleter2")
        conv = db.create_conversation("dm", None, ["d1", "d2"])
        msg = db.send_message(conv["id"], "d1", "delete me")
        assert db.delete_message(msg["id"]) is True
        assert db.get_message(msg["id"]) is None

    def test_mark_conversation_read(self, db):
        db.register_agent("r1", "Reader1")
        db.register_agent("r2", "Reader2")
        conv = db.create_conversation("dm", None, ["r1", "r2"])
        db.send_message(conv["id"], "r1", "msg1")
        db.send_message(conv["id"], "r1", "msg2")
        db.mark_conversation_read(conv["id"], "r2")
        # All messages should have r2 in read_by
        msgs = db.get_messages(conv["id"])
        for m in msgs:
            assert "r2" in m["read_by"]

    def test_unread_count(self, db):
        db.register_agent("u1", "Unreader1")
        db.register_agent("u2", "Unreader2")
        conv = db.create_conversation("dm", None, ["u1", "u2"])
        db.send_message(conv["id"], "u1", "hello")
        db.send_message(conv["id"], "u1", "world")
        convs = db.list_conversations("u2")
        assert convs[0]["unread_count"] == 2


class TestDBStats:
    def test_stats(self, db):
        db.register_agent("s1", "Stat1")
        db.register_agent("s2", "Stat2")
        conv = db.create_conversation("dm", None, ["s1", "s2"])
        db.send_message(conv["id"], "s1", "stat msg")
        stats = db.stats()
        assert stats["agents"] >= 2
        assert stats["messages"] >= 1
        assert "dms" in stats
        assert "groups" in stats


class TestDBAgentOps:
    def test_delete_agent(self, db):
        db.register_agent("del-agent", "ToDelete")
        assert db.delete_agent("del-agent") is True
        assert db.get_agent("del-agent") is None

    def test_list_agents_by_type(self, db):
        db.register_agent("ta1", "Type A", "cluster")
        db.register_agent("tb1", "Type B", "detached")
        cluster = db.list_agents(agent_type="cluster")
        assert all(a["type"] == "cluster" for a in cluster)

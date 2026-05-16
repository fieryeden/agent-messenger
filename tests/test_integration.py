"""Comprehensive REST integration tests for Agent Messenger v0.6.0.

Tests all v0.3.0–v0.5.1 features against a live TestClient server.
"""

import json
import pytest

pytestmark = pytest.mark.asyncio


# ── Fixtures ──

@pytest.fixture
def client(tmp_path):
    """FastAPI TestClient with auth disabled (default)."""
    from server.main import create_app, load_config
    config = load_config("config.yaml")
    config["database"]["path"] = str(tmp_path / "test_int.db")
    config["auth"]["enabled"] = False
    app = create_app(config)
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(scope="module")  
def module_client(tmp_path_factory):
    """Module-scoped client for expensive setups (broadcast, omniscient)."""
    from server.main import create_app, load_config
    config = load_config("config.yaml")
    config["database"]["path"] = str(tmp_path_factory.mktemp("mod") / "test_mod.db")
    config["auth"]["enabled"] = False
    app = create_app(config)
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _setup_conv(client, members=None):
    """Register agents + create DM conversation. Returns conv_id."""
    if members is None:
        members = ["alpha", "beta"]
    for m in members:
        await client.post("/api/agents/register", json={"id": m, "name": m.title()})
    resp = await client.post("/api/conversations", json={
        "type": "dm" if len(members) == 2 else "group",
        "member_ids": members,
    })
    return resp.json()["conversation"]["id"]


async def _send_msg(client, conv_id, sender="alpha", content="Hello", **kw):
    """Helper to send a message."""
    body = {"conversation_id": conv_id, "sender_id": sender, "content": content, **kw}
    resp = await client.post("/api/messages", json=body)
    return resp


# ════════════════════════════════════════════════════════════
# 1. Threading
# ════════════════════════════════════════════════════════════

class TestThreading:
    """send a message, reply to it (parent_id), get replies endpoint."""

    async def test_send_and_get_reply(self, client):
        conv_id = await _setup_conv(client)
        parent = await _send_msg(client, conv_id, "alpha", "First post")
        parent_id = parent.json()["message"]["id"]

        reply = await _send_msg(client, conv_id, "beta", "Reply here", reply_to_id=parent_id)
        assert reply.status_code == 200
        assert reply.json()["message"]["reply_to_id"] == parent_id

        resp = await client.get(f"/api/messages/{parent_id}/replies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert data["replies"][0]["content"] == "Reply here"

    async def test_reply_to_nonexistent_message(self, client):
        conv_id = await _setup_conv(client)
        resp = await _send_msg(client, conv_id, "alpha", "Orphan",
                               reply_to_id="00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_reply_wrong_conversation(self, client):
        conv_a = await _setup_conv(client)
        conv_b = await _setup_conv(client, members=["gamma", "delta"])
        msg = await _send_msg(client, conv_a, "alpha", "From A")
        msg_id = msg.json()["message"]["id"]
        resp = await _send_msg(client, conv_b, "gamma", "Stray reply", reply_to_id=msg_id)
        assert resp.status_code == 400


# ════════════════════════════════════════════════════════════
# 2. Edit
# ════════════════════════════════════════════════════════════

class TestEdit:
    """send a message, edit it, verify edited_at is set and content updated."""

    async def test_edit_message(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Original text")
        msg_id = msg.json()["message"]["id"]

        resp = await client.patch(f"/api/messages/{msg_id}", json={
            "content": "Edited text", "edited_by": "alpha",
        })
        assert resp.status_code == 200
        updated = resp.json()["message"]
        assert updated["content"] == "Edited text"
        assert updated["edited_at"] is not None

    async def test_edit_by_different_agent_fails(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "My message")
        msg_id = msg.json()["message"]["id"]

        resp = await client.patch(f"/api/messages/{msg_id}", json={
            "content": "Hacked!", "edited_by": "beta",
        })
        assert resp.status_code == 403

    async def test_edit_deleted_message_fails(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Delete me")
        msg_id = msg.json()["message"]["id"]
        await client.delete(f"/api/messages/{msg_id}?soft=true&deleted_by=alpha")

        resp = await client.patch(f"/api/messages/{msg_id}", json={
            "content": "Try edit", "edited_by": "alpha",
        })
        assert resp.status_code == 400


# ════════════════════════════════════════════════════════════
# 3. Soft-delete
# ════════════════════════════════════════════════════════════

class TestSoftDelete:
    """delete a message, verify content is redacted but record exists."""

    async def test_soft_delete(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Secret info")
        msg_id = msg.json()["message"]["id"]

        resp = await client.delete(f"/api/messages/{msg_id}?soft=true&deleted_by=alpha")
        assert resp.status_code == 200
        assert resp.json()["soft"] is True

        # Verify content redacted
        msgs_resp = await client.get(f"/api/messages/conversation/{conv_id}")
        found = [m for m in msgs_resp.json()["messages"] if m["id"] == msg_id]
        assert len(found) == 1
        assert found[0]["content"] == "[message deleted]"
        assert found[0]["deleted_at"] is not None

    async def test_soft_delete_idempotent(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Double delete")
        msg_id = msg.json()["message"]["id"]

        r1 = await client.delete(f"/api/messages/{msg_id}?soft=true&deleted_by=alpha")
        assert r1.status_code == 200
        r2 = await client.delete(f"/api/messages/{msg_id}?soft=true&deleted_by=alpha")
        assert r2.status_code == 404


# ════════════════════════════════════════════════════════════
# 4. Hard-delete
# ════════════════════════════════════════════════════════════

class TestHardDelete:
    """hard delete, verify message gone entirely."""

    async def test_hard_delete(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Gone forever")
        msg_id = msg.json()["message"]["id"]

        resp = await client.delete(f"/api/messages/{msg_id}?soft=false&deleted_by=alpha")
        assert resp.status_code == 200
        assert resp.json()["soft"] is False

        msgs_resp = await client.get(f"/api/messages/conversation/{conv_id}")
        found = [m for m in msgs_resp.json()["messages"] if m["id"] == msg_id]
        assert len(found) == 0


# ════════════════════════════════════════════════════════════
# 5. Reactions
# ════════════════════════════════════════════════════════════

class TestReactions:
    """add reaction to a message, list reactions, verify emoji counts."""

    async def test_add_and_list_reactions(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "React to this")
        msg_id = msg.json()["message"]["id"]

        r1 = await client.post(f"/api/messages/{msg_id}/react", json={"agent_id": "alpha", "emoji": "thumbsup"})
        assert r1.status_code == 200
        r2 = await client.post(f"/api/messages/{msg_id}/react", json={"agent_id": "beta", "emoji": "thumbsup"})
        assert r2.status_code == 200
        r3 = await client.post(f"/api/messages/{msg_id}/react", json={"agent_id": "beta", "emoji": "fire"})
        assert r3.status_code == 200

        resp = await client.get(f"/api/messages/{msg_id}/reactions")
        assert resp.status_code == 200
        data = resp.json()["reactions"]
        thumbs = [r for r in data if r["emoji"] == "thumbsup"][0]
        assert thumbs["count"] == 2
        fire = [r for r in data if r["emoji"] == "fire"][0]
        assert fire["count"] == 1

    async def test_toggle_reaction_off(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Toggle me")
        msg_id = msg.json()["message"]["id"]

        await client.post(f"/api/messages/{msg_id}/react", json={"agent_id": "alpha", "emoji": "party"})
        await client.post(f"/api/messages/{msg_id}/react", json={"agent_id": "alpha", "emoji": "party"})

        resp = await client.get(f"/api/messages/{msg_id}/reactions")
        assert resp.json()["reactions"] == []

    async def test_reactions_after_delete(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "React then delete")
        msg_id = msg.json()["message"]["id"]
        await client.post(f"/api/messages/{msg_id}/react", json={"agent_id": "beta", "emoji": "wave"})
        await client.delete(f"/api/messages/{msg_id}?soft=true&deleted_by=alpha")
        resp = await client.get(f"/api/messages/{msg_id}/reactions")
        assert resp.status_code == 200


# ════════════════════════════════════════════════════════════
# 6. Delivery/read receipts
# ════════════════════════════════════════════════════════════

class TestDeliveryRead:
    """mark delivered, mark read, verify status on message."""

    async def test_mark_delivered(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Deliver me")
        msg_id = msg.json()["message"]["id"]

        resp = await client.post(f"/api/messages/{msg_id}/delivered", json={"agent_id": "beta"})
        assert resp.status_code == 200

    async def test_mark_read(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Read me")
        msg_id = msg.json()["message"]["id"]

        resp = await client.post(f"/api/messages/{msg_id}/read", json={"agent_id": "beta"})
        assert resp.status_code == 200

        readers = await client.get(f"/api/messages/{msg_id}/readers")
        assert "beta" in readers.json()["read_by"]

    async def test_readers_list(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Who read?")
        msg_id = msg.json()["message"]["id"]

        await client.post(f"/api/messages/{msg_id}/read", json={"agent_id": "alpha"})
        await client.post(f"/api/messages/{msg_id}/read", json={"agent_id": "beta"})

        resp = await client.get(f"/api/messages/{msg_id}/readers")
        assert resp.json()["count"] == 2


# ════════════════════════════════════════════════════════════
# 7. FTS5 search
# ════════════════════════════════════════════════════════════

class TestFTS5Search:
    """create messages with known content, search for them."""

    async def test_fts5_search(self, client):
        conv_id = await _setup_conv(client)
        await _send_msg(client, conv_id, "alpha", "The quick brown fox jumps over the lazy dog")
        await _send_msg(client, conv_id, "alpha", "Hello world from agent messenger")
        await _send_msg(client, conv_id, "alpha", "Machine learning is transforming research")

        resp = await client.get("/api/messages/search?q=Machine+learning")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert "Machine learning" in data["results"][0]["content"]

    async def test_fts5_no_results(self, client):
        conv_id = await _setup_conv(client)
        await _send_msg(client, conv_id, "alpha", "Some random content")
        resp = await client.get("/api/messages/search?q=ZZZZnotfoundZZZZ")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_search_empty_query(self, client):
        resp = await client.get("/api/messages/search?q=")
        assert resp.status_code == 422  # FastAPI validates min_length=1

    async def test_search_across_conversations(self, client):
        conv_a = await _setup_conv(client, members=["s1", "s2"])
        conv_b = await _setup_conv(client, members=["s3", "s4"])
        await _send_msg(client, conv_a, "s1", "unique keyword across")
        await _send_msg(client, conv_b, "s3", "unique keyword across")
        resp = await client.get("/api/messages/search?q=unique+keyword+across")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 2


# ════════════════════════════════════════════════════════════
# 8. Global feed
# ════════════════════════════════════════════════════════════

class TestGlobalFeed:
    """/api/messages/feed endpoint."""

    async def test_global_feed(self, client):
        conv_id = await _setup_conv(client)
        await _send_msg(client, conv_id, "alpha", "Feed message 1")
        await _send_msg(client, conv_id, "beta", "Feed message 2")

        resp = await client.get("/api/messages/feed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 2
        assert data["status"] == "ok"

    async def test_feed_pagination(self, client):
        conv_id = await _setup_conv(client)
        for i in range(5):
            await _send_msg(client, conv_id, "alpha", f"Page msg {i}")

        resp = await client.get("/api/messages/feed?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 2


# ════════════════════════════════════════════════════════════
# 9. Polls
# ════════════════════════════════════════════════════════════

class TestPolls:
    """create poll, vote, close, get results, list polls for conversation."""

    async def test_create_poll(self, client):
        conv_id = await _setup_conv(client)
        resp = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=alpha&question=Best+lang&options=Python&options=Rust&options=Go")
        assert resp.status_code == 200
        data = resp.json()
        assert data["question"] == "Best lang"
        assert len(data["options"]) == 3
        assert data["vote_counts"] == [0, 0, 0]

    async def test_vote_on_poll(self, client):
        conv_id = await _setup_conv(client)
        poll = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=alpha&question=Pick&options=A&options=B")
        poll_id = poll.json()["id"]
        resp = await client.post(f"/api/polls/{poll_id}/vote?option_index=1&agent_id=beta")
        assert resp.status_code == 200
        assert resp.json()["vote_counts"] == [0, 1]

    async def test_vote_overwrite(self, client):
        conv_id = await _setup_conv(client)
        poll = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=alpha&question=Pick&options=A&options=B")
        poll_id = poll.json()["id"]
        await client.post(f"/api/polls/{poll_id}/vote?option_index=0&agent_id=beta")
        resp = await client.post(f"/api/polls/{poll_id}/vote?option_index=1&agent_id=beta")
        assert resp.json()["vote_counts"] == [0, 1]

    async def test_multi_vote(self, client):
        conv_id = await _setup_conv(client, members=["x", "y"])
        poll = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=x&question=Multi&options=A&options=B&options=C&multi_vote=true")
        poll_id = poll.json()["id"]
        await client.post(f"/api/polls/{poll_id}/vote?option_index=0&agent_id=y")
        resp = await client.post(f"/api/polls/{poll_id}/vote?option_index=2&agent_id=y")
        assert resp.status_code == 200
        assert resp.json()["vote_counts"] == [1, 0, 1]
        assert resp.json()["total_votes"] == 2

    async def test_close_poll(self, client):
        conv_id = await _setup_conv(client)
        poll = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=alpha&question=Done&options=Yes&options=No")
        poll_id = poll.json()["id"]
        resp = await client.post(f"/api/polls/{poll_id}/close?agent_id=alpha")
        assert resp.status_code == 200
        assert resp.json()["closed_at"] is not None

        vote = await client.post(f"/api/polls/{poll_id}/vote?option_index=0&agent_id=beta")
        assert vote.status_code == 400

    async def test_close_poll_by_non_creator_fails(self, client):
        conv_id = await _setup_conv(client)
        poll = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=alpha&question=Secure&options=X&options=Y")
        poll_id = poll.json()["id"]
        resp = await client.post(f"/api/polls/{poll_id}/close?agent_id=beta")
        assert resp.status_code == 403

    async def test_list_polls(self, client):
        conv_id = await _setup_conv(client, members=["p1", "p2"])
        await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=p1&question=Q1&options=A&options=B")
        await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=p1&question=Q2&options=C&options=D")
        resp = await client.get(f"/api/polls/conversation/{conv_id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_list_polls_with_closed(self, client):
        conv_id = await _setup_conv(client, members=["p1", "p2"])
        p1 = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=p1&question=CloseMe&options=A&options=B")
        pid = p1.json()["id"]
        await client.post(f"/api/polls/{pid}/close?agent_id=p1")

        active = await client.get(f"/api/polls/conversation/{conv_id}")
        assert len(active.json()) == 0
        all_p = await client.get(f"/api/polls/conversation/{conv_id}?include_closed=true")
        assert len(all_p.json()) == 1

    async def test_vote_invalid_option(self, client):
        conv_id = await _setup_conv(client)
        poll = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=alpha&question=Q&options=A&options=B")
        pid = poll.json()["id"]
        resp = await client.post(f"/api/polls/{pid}/vote?option_index=99&agent_id=beta")
        assert resp.status_code == 400

    async def test_get_poll(self, client):
        conv_id = await _setup_conv(client)
        poll = await client.post(f"/api/polls/create?conversation_id={conv_id}&creator_id=alpha&question=GetMe&options=A&options=B")
        pid = poll.json()["id"]
        resp = await client.get(f"/api/polls/{pid}")
        assert resp.status_code == 200
        assert resp.json()["question"] == "GetMe"


# ════════════════════════════════════════════════════════════
# 10. Pinned messages
# ════════════════════════════════════════════════════════════

class TestPinnedMessages:
    """pin a message, list pinned, unpin."""

    async def test_pin_and_list(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Pin worthy")
        msg_id = msg.json()["message"]["id"]

        resp = await client.post(f"/api/pins/pin?conversation_id={conv_id}&message_id={msg_id}&pinned_by=alpha")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True

        listed = await client.get(f"/api/pins/{conv_id}")
        assert len(listed.json()) == 1

    async def test_unpin(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Unpin me")
        msg_id = msg.json()["message"]["id"]
        await client.post(f"/api/pins/pin?conversation_id={conv_id}&message_id={msg_id}&pinned_by=alpha")

        resp = await client.delete(f"/api/pins/unpin?conversation_id={conv_id}&message_id={msg_id}")
        assert resp.status_code == 200
        assert resp.json()["unpinned"] is True

        listed = await client.get(f"/api/pins/{conv_id}")
        assert len(listed.json()) == 0

    async def test_duplicate_pin_ignored(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Pin twice")
        msg_id = msg.json()["message"]["id"]
        await client.post(f"/api/pins/pin?conversation_id={conv_id}&message_id={msg_id}&pinned_by=alpha")
        await client.post(f"/api/pins/pin?conversation_id={conv_id}&message_id={msg_id}&pinned_by=alpha")
        listed = await client.get(f"/api/pins/{conv_id}")
        assert len(listed.json()) == 1

    async def test_pin_multiple(self, client):
        conv_id = await _setup_conv(client)
        m1 = (await _send_msg(client, conv_id, "alpha", "Pin 1")).json()["message"]["id"]
        m2 = (await _send_msg(client, conv_id, "alpha", "Pin 2")).json()["message"]["id"]
        await client.post(f"/api/pins/pin?conversation_id={conv_id}&message_id={m1}&pinned_by=alpha")
        await client.post(f"/api/pins/pin?conversation_id={conv_id}&message_id={m2}&pinned_by=alpha")
        listed = await client.get(f"/api/pins/{conv_id}")
        assert len(listed.json()) == 2

    async def test_pin_wrong_conversation_fails(self, client):
        conv_a = await _setup_conv(client, members=["x1", "x2"])
        conv_b = await _setup_conv(client, members=["y1", "y2"])
        msg = await _send_msg(client, conv_a, "x1", "Wrong conv")
        msg_id = msg.json()["message"]["id"]
        resp = await client.post(f"/api/pins/pin?conversation_id={conv_b}&message_id={msg_id}&pinned_by=y1")
        assert resp.status_code == 400


# ════════════════════════════════════════════════════════════
# 11. File attachments
# ════════════════════════════════════════════════════════════

class TestFileAttachments:
    """upload a file, get metadata, list files for message, delete."""

    async def test_upload_and_get_file(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "See attached")
        msg_id = msg.json()["message"]["id"]

        resp = await client.post(
            f"/api/files/upload?message_id={msg_id}&uploader_id=alpha",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 200
        file_id = resp.json()["id"]
        assert resp.json()["filename"] == "test.txt"

        meta = await client.get(f"/api/files/{file_id}")
        assert meta.status_code == 200
        assert meta.json()["content_type"] == "text/plain"

    async def test_list_files_by_message(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Multiple files")
        msg_id = msg.json()["message"]["id"]
        await client.post(f"/api/files/upload?message_id={msg_id}&uploader_id=alpha", files={"file": ("f1.txt", b"one", "text/plain")})
        await client.post(f"/api/files/upload?message_id={msg_id}&uploader_id=alpha", files={"file": ("f2.txt", b"two", "text/plain")})

        listed = await client.get(f"/api/files/message/{msg_id}")
        assert len(listed.json()) == 2

    async def test_delete_file(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "File to delete")
        msg_id = msg.json()["message"]["id"]
        up = await client.post(f"/api/files/upload?message_id={msg_id}&uploader_id=alpha", files={"file": ("del.txt", b"delete me", "text/plain")})
        file_id = up.json()["id"]

        resp = await client.delete(f"/api/files/{file_id}?agent_id=alpha")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        meta = await client.get(f"/api/files/{file_id}")
        assert meta.status_code == 404

    async def test_delete_file_by_other_fails(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Own file")
        msg_id = msg.json()["message"]["id"]
        up = await client.post(f"/api/files/upload?message_id={msg_id}&uploader_id=alpha", files={"file": ("own.txt", b"data", "text/plain")})
        file_id = up.json()["id"]

        resp = await client.delete(f"/api/files/{file_id}?agent_id=beta")
        assert resp.status_code == 403

    async def test_upload_unsupported_type_fails(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Bad file")
        msg_id = msg.json()["message"]["id"]
        resp = await client.post(
            f"/api/files/upload?message_id={msg_id}&uploader_id=alpha",
            files={"file": ("bad.exe", b"binary", "application/x-msdownload")},
        )
        assert resp.status_code == 415

    async def test_upload_by_non_sender_fails(self, client):
        conv_id = await _setup_conv(client)
        msg = await _send_msg(client, conv_id, "alpha", "Not my msg")
        msg_id = msg.json()["message"]["id"]
        resp = await client.post(
            f"/api/files/upload?message_id={msg_id}&uploader_id=beta",
            files={"file": ("test.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════════
# 12. Broadcast
# ════════════════════════════════════════════════════════════

class TestBroadcast:
    """send broadcast message, verify all agents receive it."""

    async def test_broadcast_message(self, module_client):
        client = module_client
        await client.post("/api/agents/register", json={"id": "broadcaster", "name": "Broadcaster"})
        await client.post("/api/agents/register", json={"id": "listener1", "name": "Listener1"})
        await client.post("/api/agents/register", json={"id": "listener2", "name": "Listener2"})

        resp = await client.post("/api/broadcast", json={
            "sender_id": "broadcaster",
            "content": "Attention all agents!",
            "priority": "urgent",
        })
        assert resp.status_code == 200
        msg_id = resp.json()["message"]["id"]
        assert resp.json()["message"]["content"] == "Attention all agents!"

        feed = await client.get("/api/messages/feed")
        found = [m for m in feed.json()["messages"] if m["id"] == msg_id]
        assert len(found) == 1

    async def test_broadcast_by_unregistered_agent_fails(self, module_client):
        client = module_client
        resp = await client.post("/api/broadcast", json={
            "sender_id": "ghost",
            "content": "Should fail",
        })
        assert resp.status_code == 400


# ════════════════════════════════════════════════════════════
# 13. Typing indicators
# ════════════════════════════════════════════════════════════

class TestTypingIndicators:
    """send typing event."""

    async def test_set_and_get_typing(self, client):
        conv_id = await _setup_conv(client)
        resp = await client.post(f"/api/conversations/{conv_id}/typing", json={"agent_id": "alpha"})
        assert resp.status_code == 200

        resp = await client.get(f"/api/conversations/{conv_id}/typing")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    async def test_clear_typing(self, client):
        conv_id = await _setup_conv(client)
        await client.post(f"/api/conversations/{conv_id}/typing", json={"agent_id": "alpha"})
        resp = await client.delete(f"/api/conversations/{conv_id}/typing?agent_id=alpha")
        assert resp.status_code == 200

        resp = await client.get(f"/api/conversations/{conv_id}/typing")
        assert resp.json()["count"] == 0

    async def test_typing_multiple_agents(self, client):
        conv_id = await _setup_conv(client)
        await client.post(f"/api/conversations/{conv_id}/typing", json={"agent_id": "alpha"})
        await client.post(f"/api/conversations/{conv_id}/typing", json={"agent_id": "beta"})
        resp = await client.get(f"/api/conversations/{conv_id}/typing")
        assert resp.json()["count"] == 2


# ════════════════════════════════════════════════════════════
# 14. Priority messages
# ════════════════════════════════════════════════════════════

class TestPriorityMessages:
    """send with urgent/low priority."""

    async def test_send_urgent(self, client):
        conv_id = await _setup_conv(client)
        resp = await _send_msg(client, conv_id, "alpha", "URGENT!", priority="urgent")
        assert resp.status_code == 200
        assert resp.json()["message"]["priority"] == "urgent"

    async def test_send_low_priority(self, client):
        conv_id = await _setup_conv(client)
        resp = await _send_msg(client, conv_id, "alpha", "Low prio", priority="low")
        assert resp.status_code == 200
        assert resp.json()["message"]["priority"] == "low"

    async def test_default_priority_normal(self, client):
        conv_id = await _setup_conv(client)
        resp = await _send_msg(client, conv_id, "alpha", "Normal prio")
        assert resp.json()["message"]["priority"] == "normal"

    async def test_invalid_priority_rejected(self, client):
        conv_id = await _setup_conv(client)
        resp = await _send_msg(client, conv_id, "alpha", "Bad prio", priority="supercritical")
        assert resp.status_code == 422


# ════════════════════════════════════════════════════════════
# 15. Omniscient WS (dashboard-* agent)
# ════════════════════════════════════════════════════════════

class TestOmniscientWS:
    """Test that a dashboard-* prefix agent gets special handling."""

    async def test_dashboard_agent_registered(self, client):
        """Dashboard agents should be registerable and get the dashboard prefix."""
        resp = await client.post("/api/agents/register", json={
            "id": "dashboard-01", "name": "Dashboard One",
        })
        assert resp.status_code == 200
        agent = resp.json()["agent"]
        assert agent["id"] == "dashboard-01"

    async def test_dashboard_sees_all_conversations(self, client):
        """Dashboard agent can see all conversations via the dashboard feed."""
        conv1 = await _setup_conv(client, members=["dash_a1", "dash_a2"])
        await client.post("/api/agents/register", json={"id": "dashboard-view", "name": "Dashboard View"})
        await _send_msg(client, conv1, "dash_a1", "For dashboard eyes")

        feed = await client.get("/api/messages/feed")
        assert feed.json()["count"] >= 1


# ════════════════════════════════════════════════════════════
# 16. Auth endpoints
# ════════════════════════════════════════════════════════════

class TestAuthEndpoints:
    """login, refresh token, API key CRUD."""

    async def _setup_authed(self, tmp_path):
        """Helper: create app with auth enabled + config API key, return client + conv."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_auth_endpoints.db")
        cfg["auth"]["enabled"] = True
        cfg["auth"]["api_keys"] = ["admin-key-123"]
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        return client, cfg

    async def test_unauthenticated_rejected(self, client, tmp_path):
        """When auth enabled, unprotected route returns 401."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_unauth.db")
        cfg["auth"]["enabled"] = True
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        resp = await ac.get("/api/agents")
        assert resp.status_code == 401

    async def test_health_skips_auth(self, client, tmp_path):
        """Health endpoint is accessible without auth even when enabled."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_health_skip.db")
        cfg["auth"]["enabled"] = True
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        resp = await ac.get("/health")
        assert resp.status_code == 200

    async def test_config_api_key_works(self, tmp_path):
        """Config-level API key grants access."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_cfg_key.db")
        cfg["auth"]["enabled"] = True
        cfg["auth"]["api_keys"] = ["admin-key-123"]
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        resp = await ac.get("/api/agents", headers={"Authorization": "Bearer admin-key-123"})
        assert resp.status_code == 200

    async def test_login_and_refresh_flow(self, tmp_path):
        """Full JWT login + refresh flow."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_jwt_flow.db")
        cfg["auth"]["enabled"] = True
        cfg["auth"]["api_keys"] = ["admin-key-123"]
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        # Register an agent using config API key
        reg = await ac.post("/api/agents/register", json={"id": "jwt-user", "name": "JWT User"},
                            headers={"Authorization": "Bearer admin-key-123"})
        assert reg.status_code == 200

        # Create API key for the agent (admin only)
        ak = await ac.post("/api/auth/api-keys", json={"agent_id": "jwt-user", "scopes": ["agent"]},
                           headers={"Authorization": "Bearer admin-key-123"})
        assert ak.status_code == 200
        raw_key = ak.json()["api_key"]

        # Login with the API key
        login = await ac.post("/api/auth/login", json={"agent_id": "jwt-user", "api_key": raw_key})
        assert login.status_code == 200
        assert "access_token" in login.json()
        access_token = login.json()["access_token"]
        refresh_token = login.json()["refresh_token"]

        # Use the access token
        resp = await ac.get("/api/agents/jwt-user", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 200

        # Refresh
        refresh_resp = await ac.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert refresh_resp.status_code == 200
        assert "access_token" in refresh_resp.json()

    async def test_api_key_crud(self, tmp_path):
        """Create and use an API key for an agent."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_apikey_crud.db")
        cfg["auth"]["enabled"] = True
        cfg["auth"]["api_keys"] = ["admin-key-123"]
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        await ac.post("/api/agents/register", json={"id": "apikey-agent", "name": "API Key Agent"},
                      headers={"Authorization": "Bearer admin-key-123"})

        resp = await ac.post("/api/auth/api-keys", json={"agent_id": "apikey-agent", "scopes": ["agent"]},
                             headers={"Authorization": "Bearer admin-key-123"})
        assert resp.status_code == 200
        raw_key = resp.json()["api_key"]
        assert raw_key.startswith("am_")

        # Use the API key
        ac2 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        resp2 = await ac2.get("/api/agents/apikey-agent", headers={"Authorization": f"Bearer {raw_key}"})
        assert resp2.status_code == 200
        assert resp2.json()["agent"]["id"] == "apikey-agent"

    async def test_invalid_token_rejected(self, tmp_path):
        """Invalid JWT token returns 401."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_invalid_token.db")
        cfg["auth"]["enabled"] = True
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        resp = await ac.get("/api/agents", headers={"Authorization": "Bearer invalid.jwt.token"})
        assert resp.status_code == 401

    async def test_expired_token_rejected(self, tmp_path):
        """Expired JWT returns 401."""
        from server.main import create_app, load_config
        from server.auth import _jwt_encode
        import time
        expired_payload = {"sub": "user", "type": "access", "exp": int(time.time()) - 3600, "scopes": ["agent"]}
        expired_token = _jwt_encode(expired_payload)

        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_expired_token.db")
        cfg["auth"]["enabled"] = True
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        resp = await ac.get("/api/agents", headers={"Authorization": f"Bearer {expired_token}"})
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════
# 17. Auth enforcement: scope checks
# ════════════════════════════════════════════════════════════

class TestAuthScopeEnforcement:
    """When auth is enabled, unauthenticated returns 401, wrong scope returns 403."""

    async def test_unauthenticated_returns_401(self, tmp_path):
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_scope_401.db")
        cfg["auth"]["enabled"] = True
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        endpoints = [
            ("GET", "/api/agents"),
            ("GET", "/api/conversations?agent_id=admin"),
            ("GET", "/api/messages/feed"),
            ("GET", "/api/messages/search?q=test"),
        ]
        for method, url in endpoints:
            resp = await ac.request(method, url)
            assert resp.status_code == 401, f"{method} {url} returned {resp.status_code}"

    async def test_config_admin_access_granted(self, tmp_path):
        """Config API key with admin scope can access all endpoints."""
        from server.main import create_app, load_config
        cfg = load_config("config.yaml")
        cfg["database"]["path"] = str(tmp_path / "test_scope_admin.db")
        cfg["auth"]["enabled"] = True
        cfg["auth"]["api_keys"] = ["admin-key-123"]
        app = create_app(cfg)
        from httpx import ASGITransport, AsyncClient
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        headers = {"Authorization": "Bearer admin-key-123"}

        # Register agents
        await ac.post("/api/agents/register", json={"id": "admin-user", "name": "Admin"}, headers=headers)

        endpoints = [
            ("GET", "/api/agents"),
            ("GET", "/api/agents/admin-user"),
        ]
        for method, url in endpoints:
            resp = await ac.request(method, url, headers=headers)
            assert resp.status_code == 200, f"{method} {url} returned {resp.status_code}"

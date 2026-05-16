"""Database layer for Agent Messenger — SQLite persistence with migrations, auth support, and LIKE injection protection."""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from server.security import sanitize_sql_like

logger = logging.getLogger("agent-messenger.db")


def _now(offset_seconds: int = 0) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


class MessengerDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()
        self._migrate()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT DEFAULT 'detached',
                status TEXT DEFAULT 'offline',
                metadata TEXT DEFAULT '{}',
                api_key_hash TEXT,
                api_key_scopes TEXT DEFAULT '["agent"]',
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                type TEXT DEFAULT 'dm',
                name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_members (
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                role TEXT DEFAULT 'member',
                joined_at TEXT NOT NULL,
                PRIMARY KEY (conversation_id, agent_id)
            );
 CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sender_id TEXT NOT NULL REFERENCES agents(id),
            content TEXT NOT NULL,
            type TEXT DEFAULT 'text',
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            read_by TEXT DEFAULT '[]',
            reply_to_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            priority TEXT DEFAULT 'normal',
            edited_at TEXT,
            edited_content TEXT,
            deleted_at TEXT
        );
            CREATE TABLE IF NOT EXISTS typing_indicators (
                conversation_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY (conversation_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msgs_conv ON messages(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_msgs_sender ON messages(sender_id);
            CREATE INDEX IF NOT EXISTS idx_conv_members ON conversation_members(agent_id);
            CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
            CREATE INDEX IF NOT EXISTS idx_agents_type ON agents(type);
            CREATE INDEX IF NOT EXISTS idx_conv_type ON conversations(type);
            CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
        CREATE TABLE IF NOT EXISTS message_reactions (
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            emoji TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, agent_id, emoji)
        );
        CREATE TABLE IF NOT EXISTS message_delivery (
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            PRIMARY KEY (message_id, agent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_msg_reactions ON message_reactions(message_id);
        CREATE INDEX IF NOT EXISTS idx_msg_delivery ON message_delivery(message_id);
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
            uploader_id TEXT NOT NULL REFERENCES agents(id),
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            storage_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pinned_messages (
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            pinned_by TEXT NOT NULL REFERENCES agents(id),
            pinned_at TEXT NOT NULL,
            PRIMARY KEY (conversation_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS polls (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            creator_id TEXT NOT NULL REFERENCES agents(id),
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            multi_vote INTEGER DEFAULT 0,
            closed_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id TEXT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            option_index INTEGER NOT NULL,
            voted_at TEXT NOT NULL,
            PRIMARY KEY (poll_id, agent_id, option_index)
        );
        CREATE INDEX IF NOT EXISTS idx_files_message ON files(message_id);
        CREATE INDEX IF NOT EXISTS idx_files_uploader ON files(uploader_id);
        CREATE INDEX IF NOT EXISTS idx_pinned_conv ON pinned_messages(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_polls_conv ON polls(conversation_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content, content='messages', content_rowid='rowid', tokenize='porter unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        """)
        self.conn.commit()

    def _migrate(self):
        """Run schema migrations for existing databases."""
        # Check current version
        row = self.conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        current = row[0] if row[0] is not None else 0

        migrations = [
            # (version, description, sql)
            (1, "add_api_key_columns", [
                "ALTER TABLE agents ADD COLUMN api_key_hash TEXT",
                "ALTER TABLE agents ADD COLUMN api_key_scopes TEXT DEFAULT '[\"agent\"]'",
            ]),
            (2, "add_message_threading_edit_delete", [
                "ALTER TABLE messages ADD COLUMN reply_to_id TEXT REFERENCES messages(id) ON DELETE SET NULL",
                "ALTER TABLE messages ADD COLUMN priority TEXT DEFAULT 'normal'",
                "ALTER TABLE messages ADD COLUMN edited_at TEXT",
                "ALTER TABLE messages ADD COLUMN edited_content TEXT",
                "ALTER TABLE messages ADD COLUMN deleted_at TEXT",
                "CREATE INDEX IF NOT EXISTS idx_msg_reply ON messages(reply_to_id)",
            ]),
            (3, "add_files_pinned_polls_fts", [
        "CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, message_id TEXT REFERENCES messages(id) ON DELETE CASCADE, uploader_id TEXT NOT NULL REFERENCES agents(id), filename TEXT NOT NULL, content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, storage_path TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT)",
        "CREATE TABLE IF NOT EXISTS pinned_messages (conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE, pinned_by TEXT NOT NULL REFERENCES agents(id), pinned_at TEXT NOT NULL, PRIMARY KEY (conversation_id, message_id))",
        "CREATE TABLE IF NOT EXISTS polls (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, creator_id TEXT NOT NULL REFERENCES agents(id), question TEXT NOT NULL, options TEXT NOT NULL, multi_vote INTEGER DEFAULT 0, closed_at TEXT, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS poll_votes (poll_id TEXT NOT NULL REFERENCES polls(id) ON DELETE CASCADE, agent_id TEXT NOT NULL, option_index INTEGER NOT NULL, voted_at TEXT NOT NULL, PRIMARY KEY (poll_id, agent_id, option_index))",
        "CREATE INDEX IF NOT EXISTS idx_files_message ON files(message_id)",
        "CREATE INDEX IF NOT EXISTS idx_files_uploader ON files(uploader_id)",
        "CREATE INDEX IF NOT EXISTS idx_pinned_conv ON pinned_messages(conversation_id)",
        "CREATE INDEX IF NOT EXISTS idx_polls_conv ON polls(conversation_id)",
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='rowid', tokenize='porter unicode61')",
        "CREATE TRIGGER IF NOT EXISTS messages_fts_ins AFTER INSERT ON messages BEGIN INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END",
        "CREATE TRIGGER IF NOT EXISTS messages_fts_del AFTER DELETE ON messages BEGIN INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content); END",
        "CREATE TRIGGER IF NOT EXISTS messages_fts_upd AFTER UPDATE ON messages BEGIN INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content); INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END",
    ]),

        # ── v0.6.0 migrations ──
        (4, "v06_roles_and_permissions", [
            "ALTER TABLE conversation_members ADD COLUMN permissions TEXT DEFAULT '{\"send_messages\":true,\"read_messages\":true,\"manage_members\":false,\"pin_messages\":false,\"manage_conversation\":false}'",
            "CREATE TABLE IF NOT EXISTS conversation_roles (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, name TEXT NOT NULL, permissions TEXT NOT NULL, is_default INTEGER DEFAULT 0, created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_conv_roles_conv ON conversation_roles(conversation_id)",
        ]),
        (5, "v06_read_cursors", [
            "CREATE TABLE IF NOT EXISTS read_cursors (agent_id TEXT NOT NULL, conversation_id TEXT NOT NULL, last_read_message_id TEXT, last_read_at TEXT NOT NULL, PRIMARY KEY (agent_id, conversation_id))",
            "CREATE INDEX IF NOT EXISTS idx_read_cursors_agent ON read_cursors(agent_id)",
        ]),
        (6, "v06_channels_and_topics", [
            "ALTER TABLE conversations ADD COLUMN parent_id TEXT REFERENCES conversations(id) ON DELETE CASCADE",
            "ALTER TABLE conversations ADD COLUMN topic TEXT",
            "ALTER TABLE conversations ADD COLUMN description TEXT",
            "CREATE INDEX IF NOT EXISTS idx_conv_parent ON conversations(parent_id)",
        ]),
        (7, "v06_mentions", [
            "CREATE TABLE IF NOT EXISTS message_mentions (message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE, mentioned_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE, created_at TEXT NOT NULL, PRIMARY KEY (message_id, mentioned_agent_id))",
            "CREATE INDEX IF NOT EXISTS idx_mentions_msg ON message_mentions(message_id)",
            "CREATE INDEX IF NOT EXISTS idx_mentions_agent ON message_mentions(mentioned_agent_id)",
        ]),
        (8, "v06_notification_prefs", [
            "CREATE TABLE IF NOT EXISTS notification_prefs (agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE, muted INTEGER DEFAULT 0, mute_until TEXT, mention_only INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (agent_id, conversation_id))",
            "CREATE INDEX IF NOT EXISTS idx_notif_prefs_agent ON notification_prefs(agent_id)",
        ]),
        (9, "v06_forwarding_and_bookmarks", [
            "ALTER TABLE messages ADD COLUMN forwarded_from_id TEXT REFERENCES messages(id) ON DELETE SET NULL",
            "ALTER TABLE messages ADD COLUMN forwarded_from_conversation_id TEXT",
            "ALTER TABLE conversation_members ADD COLUMN archived_at TEXT",
            "CREATE TABLE IF NOT EXISTS bookmarks (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE, message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE, label TEXT, created_at TEXT NOT NULL, UNIQUE(agent_id, message_id))",
            "CREATE INDEX IF NOT EXISTS idx_bookmarks_agent ON bookmarks(agent_id)",
        ]),
        (10, "v06_custom_emojis", [
            "CREATE TABLE IF NOT EXISTS custom_emojis (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, image_url TEXT NOT NULL, creator_id TEXT NOT NULL REFERENCES agents(id), animated INTEGER DEFAULT 0, created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_custom_emojis_name ON custom_emojis(name)",
        ]),
        (11, "v06_message_expiry_and_archive", [
            "ALTER TABLE conversations ADD COLUMN message_ttl INTEGER",
            "ALTER TABLE conversations ADD COLUMN archived INTEGER DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN expires_at TEXT",
        ]),
        (12, "v06_webhooks", [
            "CREATE TABLE IF NOT EXISTS webhooks (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, url TEXT NOT NULL, events TEXT NOT NULL, secret TEXT, active INTEGER DEFAULT 1, created_by TEXT NOT NULL REFERENCES agents(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_webhooks_conv ON webhooks(conversation_id)",
        ]),
        (13, "v06_event_subscriptions", [
            "CREATE TABLE IF NOT EXISTS event_subscriptions (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE, event_type TEXT NOT NULL, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE, callback_url TEXT, active INTEGER DEFAULT 1, created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_event_subs_agent ON event_subscriptions(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_event_subs_type ON event_subscriptions(event_type)",
        ]),
        (14, "v06_slash_commands", [
            "CREATE TABLE IF NOT EXISTS slash_commands (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, handler_url TEXT NOT NULL, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE, created_by TEXT NOT NULL REFERENCES agents(id), created_at TEXT NOT NULL, UNIQUE(name, conversation_id))",
            "CREATE INDEX IF NOT EXISTS idx_slash_commands_name ON slash_commands(name)",
        ]),
        (15, "v06_scheduled_messages", [
            "CREATE TABLE IF NOT EXISTS scheduled_messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, sender_id TEXT NOT NULL REFERENCES agents(id), content TEXT NOT NULL, scheduled_for TEXT NOT NULL, sent_at TEXT, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_scheduled_conv ON scheduled_messages(conversation_id)",
            "CREATE INDEX IF NOT EXISTS idx_scheduled_time ON scheduled_messages(scheduled_for)",
        ]),
        (16, "v06_embeds_and_translations", [
            "CREATE TABLE IF NOT EXISTS message_embeds (id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE, title TEXT, description TEXT, url TEXT, image_url TEXT, thumbnail_url TEXT, embed_type TEXT DEFAULT 'link', created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_embeds_msg ON message_embeds(message_id)",
            "CREATE TABLE IF NOT EXISTS message_translations (id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE, language TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(message_id, language))",
            "CREATE INDEX IF NOT EXISTS idx_translations_msg ON message_translations(message_id)",
        ]),

]

        for version, desc, sqls in migrations:
            if current >= version:
                continue
            try:
                for sql in sqls:
                    self.conn.execute(sql)
                self.conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )
                self.conn.commit()
                logger.info("Migration v%d (%s) applied", version, desc)
            except Exception as e:
                # Column already exists = already migrated
                if "duplicate column" in str(e).lower():
                    self.conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, _now()),
                    )
                    self.conn.commit()
                else:
                    logger.error("Migration v%d failed: %s", version, e)

    # ── Agents ──

    def register_agent(self, agent_id: str, name: str, agent_type: str = "detached", metadata: dict = None) -> dict:
        now = _now()
        meta_json = json.dumps(metadata or {})
        self.conn.execute(
            """INSERT INTO agents (id, name, type, status, metadata, created_at, last_seen)
               VALUES (?, ?, ?, 'online', ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=?, type=?, metadata=?, status='online', last_seen=?""",
            (agent_id, name, agent_type, meta_json, now, now, name, agent_type, meta_json, now),
        )
        self.conn.commit()
        return self.get_agent(agent_id)

    def get_agent(self, agent_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return self._agent_row(row) if row else None

    def get_agent_by_api_key(self, hashed_key: str) -> Optional[dict]:
        """Look up an agent by their API key hash."""
        row = self.conn.execute("SELECT * FROM agents WHERE api_key_hash = ?", (hashed_key,)).fetchone()
        return self._agent_row(row) if row else None

    def set_agent_api_key(self, agent_id: str, hashed_key: str, scopes: list[str]):
        """Set the API key hash and scopes for an agent."""
        self.conn.execute(
            "UPDATE agents SET api_key_hash = ?, api_key_scopes = ? WHERE id = ?",
            (hashed_key, json.dumps(scopes), agent_id),
        )
        self.conn.commit()

    def list_agents(self, status: Optional[str] = None, agent_type: Optional[str] = None, limit: int = 100, offset: int = 0) -> list[dict]:
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if agent_type:
            conditions.append("type = ?")
            params.append(agent_type)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self.conn.execute(
            f"SELECT * FROM agents WHERE {where} ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._agent_row(r) for r in rows]

    def update_agent_status(self, agent_id: str, status: str):
        self.conn.execute(
            "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
            (status, _now(), agent_id),
        )
        self.conn.commit()

    def delete_agent(self, agent_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ── Conversations ──

    def create_conversation(self, conversation_type: str = "dm", name: Optional[str] = None, member_ids: list[str] = None) -> dict:
        conv_id = str(uuid.uuid4())
        now = _now()
        # For DMs between 2 agents, check if conversation already exists
        if conversation_type == "dm" and member_ids and len(member_ids) == 2:
            existing = self.conn.execute("""
                SELECT c.* FROM conversations c
                JOIN conversation_members cm1 ON c.id = cm1.conversation_id AND cm1.agent_id = ?
                JOIN conversation_members cm2 ON c.id = cm2.conversation_id AND cm2.agent_id = ?
                WHERE c.type = 'dm'
            """, (member_ids[0], member_ids[1])).fetchone()
            if existing:
                return self._conv_row(existing)

        self.conn.execute(
            "INSERT INTO conversations (id, type, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, conversation_type, name, now, now),
        )
        if member_ids:
            for mid in member_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO conversation_members (conversation_id, agent_id, role, joined_at) VALUES (?, ?, 'member', ?)",
                    (conv_id, mid, now),
                )
        self.conn.commit()
        return self._conv_row(self.conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone())

    def get_conversation(self, conv_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            return None
        result = self._conv_row(row)
        members = self.conn.execute(
            "SELECT agent_id, role, joined_at FROM conversation_members WHERE conversation_id = ?",
            (conv_id,),
        ).fetchall()
        result["members"] = [dict(m) for m in members]
        return result

    def list_conversations(self, agent_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute("""
            SELECT c.* FROM conversations c
            JOIN conversation_members cm ON c.id = cm.conversation_id
            WHERE cm.agent_id = ?
            ORDER BY c.updated_at DESC LIMIT ? OFFSET ?
        """, (agent_id, limit, offset)).fetchall()
        results = []
        for row in rows:
            conv = self._conv_row(row)
            members = self.conn.execute(
                "SELECT agent_id FROM conversation_members WHERE conversation_id = ?",
                (conv["id"],),
            ).fetchall()
            conv["members"] = [m["agent_id"] for m in members]
            # Get last message
            last_msg = self.conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
                (conv["id"],),
            ).fetchone()
            conv["last_message"] = self._msg_row(last_msg) if last_msg else None
            # Unread count
            unread = self.conn.execute(
                """SELECT COUNT(*) FROM messages
                   WHERE conversation_id = ? AND ? NOT IN (SELECT value FROM json_each(read_by))""",
                (conv["id"], agent_id),
            ).fetchone()[0]
            conv["unread_count"] = unread
            results.append(conv)
        return results

    def list_all_conversations(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """List all conversations (dashboard view)."""
        rows = self.conn.execute(
            """
            SELECT c.* FROM conversations c
            ORDER BY c.updated_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        results = []
        for row in rows:
            conv = self._conv_row(row)
            members = self.conn.execute(
                "SELECT agent_id FROM conversation_members WHERE conversation_id = ?",
                (conv["id"],),
            ).fetchall()
            conv["members"] = [m["agent_id"] for m in members]
            last_msg = self.conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
                (conv["id"],),
            ).fetchone()
            conv["last_message"] = self._msg_row(last_msg) if last_msg else None
            results.append(conv)
        return results

    def add_conversation_member(self, conv_id: str, agent_id: str, role: str = "member"):
        self.conn.execute(
            "INSERT OR IGNORE INTO conversation_members (conversation_id, agent_id, role, joined_at) VALUES (?, ?, ?, ?)",
            (conv_id, agent_id, role, _now()),
        )
        self.conn.commit()

    def remove_conversation_member(self, conv_id: str, agent_id: str):
        self.conn.execute(
            "DELETE FROM conversation_members WHERE conversation_id = ? AND agent_id = ?",
            (conv_id, agent_id),
        )
        self.conn.commit()

    # ── Messages ──

    def send_message(self, conversation_id: str, sender_id: str, content: str, msg_type: str = "text", metadata: dict = None, reply_to_id: str = None, priority: str = "normal") -> dict:
        msg_id = str(uuid.uuid4())
        now = _now()
        meta_json = json.dumps(metadata or {})
        self.conn.execute(
            """INSERT INTO messages (id, conversation_id, sender_id, content, type, metadata, created_at, read_by, reply_to_id, priority)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, sender_id, content, msg_type, meta_json, now, json.dumps([sender_id]), reply_to_id, priority),
        )
        # Update conversation timestamp
        self.conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        # Update sender's last_seen
        self.conn.execute("UPDATE agents SET last_seen = ? WHERE id = ?", (now, sender_id))
        self.conn.commit()
        return self.get_message(msg_id)

    def get_message(self, msg_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return self._msg_row(row) if row else None

    def get_messages(self, conversation_id: str, limit: int = 50, before: Optional[str] = None) -> list[dict]:
        if before:
            rows = self.conn.execute(
                """SELECT * FROM messages WHERE conversation_id = ? AND created_at < ?
                   ORDER BY created_at DESC LIMIT ?""",
                (conversation_id, before, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM messages WHERE conversation_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (conversation_id, limit),
            ).fetchall()
        return [self._msg_row(r) for r in reversed(rows)]

    def delete_message(self, msg_id: str) -> bool:
        """Hard delete — removes the row entirely."""
        c = self.conn.cursor()
        c.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        self.conn.commit()
        return c.rowcount > 0

    def soft_delete_message(self, msg_id: str) -> bool:
        """Soft delete — marks deleted_at, content becomes '[message deleted]'."""
        now = _now()
        c = self.conn.cursor()
        c.execute("UPDATE messages SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (now, msg_id))
        self.conn.commit()
        return c.rowcount > 0

    def edit_message(self, msg_id: str, new_content: str) -> Optional[dict]:
        """Edit a message — stores original in edited_content, updates content, sets edited_at."""
        msg = self.get_message(msg_id)
        if not msg or msg.get("deleted_at"):
            return None
        now = _now()
        # Store current content as edited_content (the original), put new in content
        self.conn.execute(
            "UPDATE messages SET edited_content = ?, edited_at = ? WHERE id = ?",
            (new_content, now, msg_id),
        )
        self.conn.commit()
        return self.get_message(msg_id)

    def get_replies(self, msg_id: str, limit: int = 50) -> list[dict]:
        """Get all replies to a specific message."""
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE reply_to_id = ? ORDER BY created_at ASC LIMIT ?",
            (msg_id, limit),
        ).fetchall()
        return [self._msg_row(r) for r in rows]

    def mark_read(self, msg_id: str, agent_id: str):
        msg = self.get_message(msg_id)
        if not msg:
            return
        read_by = set(msg.get("read_by", []))
        read_by.add(agent_id)
        self.conn.execute(
            "UPDATE messages SET read_by = ? WHERE id = ?",
            (json.dumps(list(read_by)), msg_id),
        )
        self.conn.commit()

    def mark_conversation_read(self, conversation_id: str, agent_id: str):
        """Mark all messages in a conversation as read by an agent."""
        msgs = self.conn.execute(
            "SELECT id, read_by FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        for msg in msgs:
            try:
                read_by = set(json.loads(msg["read_by"]))
            except (json.JSONDecodeError, TypeError):
                read_by = set()
            read_by.add(agent_id)
            self.conn.execute(
                "UPDATE messages SET read_by = ? WHERE id = ?",
                (json.dumps(list(read_by)), msg["id"]),
            )
        self.conn.commit()

    def search_messages(self, query: str, limit: int = 20, offset: int = 0,
                        conversation_id: str = None, sender_id: str = None) -> list[dict]:
        """Search messages — uses FTS5 if available, falls back to LIKE."""
        try:
            return self.search_messages_fts(query, conversation_id=conversation_id,
                                            sender_id=sender_id, limit=limit, offset=offset)
        except Exception:
            safe_query = sanitize_sql_like(query)
            rows = self.conn.execute(
                """SELECT * FROM messages WHERE content LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (f"%{safe_query}%", limit, offset),
            ).fetchall()
            return [self._msg_row(r) for r in rows]


    def set_typing(self, conversation_id: str, agent_id: str):
        now = _now()
        self.conn.execute(
            """INSERT INTO typing_indicators (conversation_id, agent_id, started_at) VALUES (?, ?, ?)
               ON CONFLICT(conversation_id, agent_id) DO UPDATE SET started_at=?""",
            (conversation_id, agent_id, now, now),
        )
        self.conn.commit()

    def clear_typing(self, conversation_id: str, agent_id: str):
        self.conn.execute(
            "DELETE FROM typing_indicators WHERE conversation_id = ? AND agent_id = ?",
            (conversation_id, agent_id),
        )
        self.conn.commit()

    TYPING_TIMEOUT_SECONDS = 5

    def get_typing(self, conversation_id: str, timeout_seconds: int = None) -> list[dict]:
        """Get currently-typing agents, auto-expiring stale entries."""
        timeout = timeout_seconds if timeout_seconds is not None else self.TYPING_TIMEOUT_SECONDS
        cutoff = _now(offset_seconds=-timeout)
        # Delete stale typing indicators
        self.conn.execute(
            "DELETE FROM typing_indicators WHERE conversation_id = ? AND started_at < ?",
            (conversation_id, cutoff),
        )
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT agent_id, started_at FROM typing_indicators WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_typing_all(self, agent_id: str):
        self.conn.execute(
            "DELETE FROM typing_indicators WHERE agent_id = ?",
            (agent_id,),
        )
        self.conn.commit()

    # ── Global feed ──

    def global_feed(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            """SELECT m.*, a.name as sender_name
               FROM messages m JOIN agents a ON m.sender_id = a.id
               ORDER BY m.created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [self._msg_row(r) for r in rows]

    # ── Stats ──

    def stats(self) -> dict:
        agents = self.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        online = self.conn.execute("SELECT COUNT(*) FROM agents WHERE status = 'online'").fetchone()[0]
        convs = self.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        msgs = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        dms = self.conn.execute("SELECT COUNT(*) FROM conversations WHERE type = 'dm'").fetchone()[0]
        groups = self.conn.execute("SELECT COUNT(*) FROM conversations WHERE type = 'group'").fetchone()[0]
        return {
            "agents": agents,
            "online": online,
            "conversations": convs,
            "dms": dms,
            "groups": groups,
            "messages": msgs,
        }

    # ── Helpers ──

    def _agent_row(self, row) -> dict:
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        # Ensure auth fields exist even for pre-migration rows
        d.setdefault("api_key_hash", None)
        try:
            d["api_key_scopes"] = json.loads(d.get("api_key_scopes", '["agent"]'))
        except (json.JSONDecodeError, TypeError):
            d["api_key_scopes"] = ["agent"]
        return d

    def _conv_row(self, row) -> dict:
        d = dict(row)
        d.setdefault("parent_id", None)
        d.setdefault("topic", None)
        d.setdefault("description", None)
        d.setdefault("message_ttl", None)
        d.setdefault("archived", 0)
        return d

    def _msg_row(self, row) -> dict:
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        try:
            d["read_by"] = json.loads(d.get("read_by", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["read_by"] = []
        if "sender_name" in d:
            d["sender_name"] = d["sender_name"]
        # New fields with defaults for backward compat
        d.setdefault("reply_to_id", None)
        d.setdefault("priority", "normal")
        d.setdefault("edited_at", None)
        d.setdefault("edited_content", None)
        d.setdefault("deleted_at", None)
        d.setdefault("forwarded_from_id", None)
        d.setdefault("forwarded_from_conversation_id", None)
        d.setdefault("expires_at", None)
        # Soft-deleted messages: only expose limited info
        if d.get("deleted_at"):
            d["content"] = "[message deleted]"
            d["edited_content"] = None
        elif d.get("edited_at") and d.get("edited_content"):
            d["original_content"] = d["content"]
            d["content"] = d["edited_content"]
        return d

    # ── Reactions ──

    def react_to_message(self, message_id: str, agent_id: str, emoji: str):
        """Toggle a reaction — add if not present, remove if already exists."""
        existing = self.conn.execute(
            "SELECT 1 FROM message_reactions WHERE message_id = ? AND agent_id = ? AND emoji = ?",
            (message_id, agent_id, emoji),
        ).fetchone()
        if existing:
            self.conn.execute(
                "DELETE FROM message_reactions WHERE message_id = ? AND agent_id = ? AND emoji = ?",
                (message_id, agent_id, emoji),
            )
        else:
            self.conn.execute(
                "INSERT INTO message_reactions (message_id, agent_id, emoji, created_at) VALUES (?, ?, ?, ?)",
                (message_id, agent_id, emoji, _now()),
            )
        self.conn.commit()

    def get_message_reactions(self, message_id: str) -> dict:
        """Get reactions grouped by emoji with agent lists."""
        rows = self.conn.execute(
            "SELECT emoji, agent_id, created_at FROM message_reactions WHERE message_id = ? ORDER BY created_at",
            (message_id,),
        ).fetchall()
        result = {}
        for row in rows:
            emoji = row["emoji"]
            if emoji not in result:
                result[emoji] = {"emoji": emoji, "count": 0, "agents": []}
            result[emoji]["count"] += 1
            result[emoji]["agents"].append(row["agent_id"])
        return list(result.values())

    # ── Delivery Tracking ──

    def mark_delivered(self, message_id: str, agent_id: str):
        """Mark a message as delivered to an agent."""
        self.conn.execute(
            "INSERT OR IGNORE INTO message_delivery (message_id, agent_id, delivered_at) VALUES (?, ?, ?)",
            (message_id, agent_id, _now()),
        )
        self.conn.commit()

    def get_delivery_status(self, message_id: str) -> list[dict]:
        """Get delivery status for a message (which agents received it)."""
        rows = self.conn.execute(
            "SELECT agent_id, delivered_at FROM message_delivery WHERE message_id = ?",
            (message_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Broadcast ──

    def broadcast_message(self, sender_id: str, content: str, msg_type: str = "text", metadata: dict = None, priority: str = "normal") -> dict:
        """Send a message to all agents via the global broadcast conversation."""
        # Find or create the broadcast conversation
        bc = self.conn.execute(
            "SELECT id FROM conversations WHERE type = 'channel' AND name = '__broadcast__'"
        ).fetchone()
        if not bc:
            # Create broadcast channel with all agents
            conv = self.create_conversation("channel", "__broadcast__", [sender_id])
            conv_id = conv["id"]
        else:
            conv_id = bc["id"]
        return self.send_message(conv_id, sender_id, content, msg_type, metadata, priority=priority)

    # ── Agent Capabilities ──

    def set_agent_capabilities(self, agent_id: str, capabilities: list[str]):
        """Store agent capabilities as JSON in metadata."""
        agent = self.get_agent(agent_id)
        if not agent:
            return None
        meta = agent.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        meta["capabilities"] = capabilities
        self.conn.execute(
            "UPDATE agents SET metadata = ? WHERE id = ?",
            (json.dumps(meta), agent_id),
        )
        self.conn.commit()
        return self.get_agent(agent_id)

    def find_agents_by_capability(self, capability: str) -> list[dict]:
        """Find agents that have a specific capability."""
        rows = self.conn.execute(
            "SELECT * FROM agents WHERE json_extract(metadata, '$.capabilities') LIKE ?",
            (f'%"{capability}"%',),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Offline Message Queue ──

    def queue_message(self, conversation_id: str, sender_id: str, content: str, msg_type: str = "text", metadata: dict = None, reply_to_id: str = None, priority: str = "normal") -> dict:
        """Send a message and queue it for offline recipients."""
        msg = self.send_message(conversation_id, sender_id, content, msg_type, metadata, reply_to_id, priority)
        # Message is stored in DB; offline agents will get it on reconnect via list_conversations/get_messages
        return msg

    def get_undelivered_messages(self, agent_id: str, limit: int = 100) -> list[dict]:
        """Get messages in agent's conversations that haven't been delivered to them yet."""
        convs = self.list_conversations(agent_id)
        undelivered = []
        for conv in convs:
            conv_id = conv["id"]
            rows = self.conn.execute(
                """SELECT m.* FROM messages m
                   LEFT JOIN message_delivery d ON m.id = d.message_id AND d.agent_id = ?
                   WHERE m.conversation_id = ? AND d.message_id IS NULL AND m.sender_id != ?
                   ORDER BY m.created_at ASC LIMIT ?""",
                (agent_id, conv_id, agent_id, limit),
            ).fetchall()
            undelivered.extend([self._msg_row(r) for r in rows])
        return undelivered[:limit]

    # ── Conversation Members Helper ──

    def get_conversation_members(self, conversation_id: str) -> list[dict]:
        """Get all members of a conversation."""
        rows = self.conn.execute(
            "SELECT agent_id, role FROM conversation_members WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── File Attachment Methods ──

    def store_file(self, message_id: str, uploader_id: str, filename: str,
                   content_type: str, size_bytes: int, storage_path: str,
                   expires_at: str = None) -> dict:
        fid = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO files (id, message_id, uploader_id, filename, content_type,
               size_bytes, storage_path, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fid, message_id, uploader_id, filename, content_type,
             size_bytes, storage_path, now, expires_at),
        )
        self.conn.commit()
        return self.get_file(fid)

    def get_file(self, file_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM files LIMIT 0").description]
        return dict(zip(cols, row))

    def get_files_by_message(self, message_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM files WHERE message_id = ?", (message_id,)).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM files LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]

    def delete_file(self, file_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def cleanup_expired_files(self) -> int:
        now = _now()
        cursor = self.conn.execute("DELETE FROM files WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        self.conn.commit()
        return cursor.rowcount

    # ── Pinned Messages ──

    def pin_message(self, conversation_id: str, message_id: str, pinned_by: str) -> dict:
        now = _now()
        self.conn.execute(
            """INSERT OR IGNORE INTO pinned_messages (conversation_id, message_id, pinned_by, pinned_at)
               VALUES (?, ?, ?, ?)""",
            (conversation_id, message_id, pinned_by, now),
        )
        self.conn.commit()
        return self.get_pinned_messages(conversation_id)

    def unpin_message(self, conversation_id: str, message_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM pinned_messages WHERE conversation_id = ? AND message_id = ?",
            (conversation_id, message_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_pinned_messages(self, conversation_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT pm.*, m.content, m.sender_id, m.created_at as msg_created_at
               FROM pinned_messages pm
               JOIN messages m ON pm.message_id = m.id
               WHERE pm.conversation_id = ?
               ORDER BY pm.pinned_at DESC""",
            (conversation_id,),
        ).fetchall()
        cols = [d[0] for d in self.conn.execute(
            """SELECT pm.*, m.content, m.sender_id, m.created_at as msg_created_at
               FROM pinned_messages pm
               JOIN messages m ON pm.message_id = m.id
               LIMIT 0"""
        ).description]
        return [dict(zip(cols, r)) for r in rows]

    # ── Polls & Voting ──

    def create_poll(self, conversation_id: str, creator_id: str, question: str,
                    options: list[str], multi_vote: bool = False) -> dict:
        pid = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO polls (id, conversation_id, creator_id, question, options, multi_vote, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pid, conversation_id, creator_id, question,
             json.dumps(options), int(multi_vote), now),
        )
        self.conn.commit()
        return self.get_poll(pid)

    def get_poll(self, poll_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM polls LIMIT 0").description]
        result = dict(zip(cols, row))
        result["options"] = json.loads(result["options"])
        result["multi_vote"] = bool(result["multi_vote"])
        votes = self.conn.execute(
            "SELECT option_index, COUNT(*) as cnt FROM poll_votes WHERE poll_id = ? GROUP BY option_index",
            (poll_id,),
        ).fetchall()
        vote_counts = {v[0]: v[1] for v in votes}
        result["vote_counts"] = [vote_counts.get(i, 0) for i in range(len(result["options"]))]
        result["total_votes"] = sum(result["vote_counts"])
        return result

    def vote_poll(self, poll_id: str, agent_id: str, option_index: int) -> Optional[dict]:
        poll = self.get_poll(poll_id)
        if not poll:
            return None
        if poll["closed_at"]:
            return None
        if option_index < 0 or option_index >= len(poll["options"]):
            return None
        if not poll["multi_vote"]:
            self.conn.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND agent_id = ?",
                (poll_id, agent_id),
            )
        now = _now()
        try:
            self.conn.execute(
                "INSERT INTO poll_votes (poll_id, agent_id, option_index, voted_at) VALUES (?, ?, ?, ?)",
                (poll_id, agent_id, option_index, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass
        return self.get_poll(poll_id)

    def close_poll(self, poll_id: str) -> Optional[dict]:
        now = _now()
        cursor = self.conn.execute(
            "UPDATE polls SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            (now, poll_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_poll(poll_id)

    def list_polls(self, conversation_id: str, include_closed: bool = False) -> list[dict]:
        if include_closed:
            rows = self.conn.execute(
                "SELECT id FROM polls WHERE conversation_id = ? ORDER BY created_at DESC",
                (conversation_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id FROM polls WHERE conversation_id = ? AND closed_at IS NULL ORDER BY created_at DESC",
                (conversation_id,),
            ).fetchall()
        return [self.get_poll(r[0]) for r in rows]

    # ── FTS5 Search ──

    def search_messages_fts(self, query: str, conversation_id: str = None,
                            sender_id: str = None, limit: int = 20, offset: int = 0) -> list[dict]:
        """Full-text search using FTS5 with optional filters."""
        from server.security import sanitize_string
        safe_query = sanitize_string(query)
        for ch in ('"', '*', '(', ')', ':', '^'):
            safe_query = safe_query.replace(ch, '')
        if not safe_query.strip():
            return []
        fts_query = '"' + safe_query + '"*'
        if conversation_id:
            sql = """SELECT m.* FROM messages_fts f
                     JOIN messages m ON m.rowid = f.rowid
                     WHERE messages_fts MATCH ? AND m.conversation_id = ?
                     ORDER BY rank LIMIT ? OFFSET ?"""
            rows = self.conn.execute(sql, (fts_query, conversation_id, limit, offset)).fetchall()
        elif sender_id:
            sql = """SELECT m.* FROM messages_fts f
                     JOIN messages m ON m.rowid = f.rowid
                     WHERE messages_fts MATCH ? AND m.sender_id = ?
                     ORDER BY rank LIMIT ? OFFSET ?"""
            rows = self.conn.execute(sql, (fts_query, sender_id, limit, offset)).fetchall()
        else:
            sql = """SELECT m.* FROM messages_fts f
                     JOIN messages m ON m.rowid = f.rowid
                     WHERE messages_fts MATCH ?
                     ORDER BY rank LIMIT ? OFFSET ?"""
            rows = self.conn.execute(sql, (fts_query, limit, offset)).fetchall()
        return [self._msg_row(r) for r in rows]

    def rebuild_fts_index(self):
        """Rebuild the FTS5 index from scratch."""
        self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        self.conn.commit()

    # ── v0.6: Roles & Permissions ──

    def create_role(self, conversation_id: str, name: str, permissions: dict, is_default: bool = False) -> dict:
        role_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            "INSERT INTO conversation_roles (id, conversation_id, name, permissions, is_default, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (role_id, conversation_id, name, json.dumps(permissions), int(is_default), now),
        )
        self.conn.commit()
        return self.get_role(role_id)

    def get_role(self, role_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM conversation_roles WHERE id = ?", (role_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["permissions"] = json.loads(d["permissions"])
        d["is_default"] = bool(d["is_default"])
        return d

    def list_roles(self, conversation_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM conversation_roles WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["permissions"] = json.loads(d["permissions"])
            d["is_default"] = bool(d["is_default"])
            results.append(d)
        return results

    def delete_role(self, role_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM conversation_roles WHERE id = ?", (role_id,))
        self.conn.commit()
        return c.rowcount > 0

    def set_member_role(self, conversation_id: str, agent_id: str, role: str):
        self.conn.execute(
            "UPDATE conversation_members SET role = ? WHERE conversation_id = ? AND agent_id = ?",
            (role, conversation_id, agent_id),
        )
        self.conn.commit()

    def set_member_permissions(self, conversation_id: str, agent_id: str, permissions: dict):
        self.conn.execute(
            "UPDATE conversation_members SET permissions = ? WHERE conversation_id = ? AND agent_id = ?",
            (json.dumps(permissions), conversation_id, agent_id),
        )
        self.conn.commit()

    def check_permission(self, conversation_id: str, agent_id: str, permission: str) -> bool:
        """Check if an agent has a specific permission in a conversation."""
        row = self.conn.execute(
            "SELECT role, permissions FROM conversation_members WHERE conversation_id = ? AND agent_id = ?",
            (conversation_id, agent_id),
        ).fetchone()
        if not row:
            return False
        # Admins/owners have all permissions
        if row["role"] in ("owner", "admin"):
            return True
        try:
            perms = json.loads(row["permissions"])
        except (json.JSONDecodeError, TypeError):
            perms = {}
        return perms.get(permission, False)

    # ── v0.6: Read Cursors ──

    def set_read_cursor(self, agent_id: str, conversation_id: str, message_id: str):
        now = _now()
        self.conn.execute(
            """INSERT INTO read_cursors (agent_id, conversation_id, last_read_message_id, last_read_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(agent_id, conversation_id) DO UPDATE SET
                 last_read_message_id = excluded.last_read_message_id,
                 last_read_at = excluded.last_read_at""",
            (agent_id, conversation_id, message_id, now),
        )
        self.conn.commit()

    def get_read_cursor(self, agent_id: str, conversation_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM read_cursors WHERE agent_id = ? AND conversation_id = ?",
            (agent_id, conversation_id),
        ).fetchone()
        return dict(row) if row else None

    def get_read_cursors_for_agent(self, agent_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM read_cursors WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── v0.6: Channels & Topics ──

    def create_channel(self, name: str, description: str = None, parent_id: str = None, member_ids: list[str] = None) -> dict:
        """Create a channel (conversation with type='channel' and optional parent)."""
        conv = self.create_conversation("channel", name, member_ids)
        updates = []
        params = []
        if parent_id:
            updates.append("parent_id = ?")
            params.append(parent_id)
        if description:
            updates.append("description = ?")
            params.append(description)
        updates.append("topic = ?")
        params.append(name)
        if updates:
            params.append(conv["id"])
            self.conn.execute(
                f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?", params
            )
            self.conn.commit()
            conv = self.get_conversation(conv["id"])
        return conv

    def list_channels(self, parent_id: str = None, limit: int = 100, offset: int = 0) -> list[dict]:
        if parent_id:
            rows = self.conn.execute(
                "SELECT * FROM conversations WHERE type = 'channel' AND parent_id = ? ORDER BY name LIMIT ? OFFSET ?",
                (parent_id, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM conversations WHERE type = 'channel' AND parent_id IS NULL ORDER BY name LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._conv_row(r) for r in rows]

    # ── v0.6: Mentions ──

    def add_mentions(self, message_id: str, agent_ids: list[str]):
        now = _now()
        for aid in agent_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO message_mentions (message_id, mentioned_agent_id, created_at) VALUES (?, ?, ?)",
                (message_id, aid, now),
            )
        self.conn.commit()

    def get_message_mentions(self, message_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM message_mentions WHERE message_id = ?", (message_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_mentions_for_agent(self, agent_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            """SELECT mm.*, m.content, m.sender_id, m.conversation_id, m.created_at as msg_created_at
               FROM message_mentions mm
               JOIN messages m ON mm.message_id = m.id
               WHERE mm.mentioned_agent_id = ?
               ORDER BY mm.created_at DESC LIMIT ? OFFSET ?""",
            (agent_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── v0.6: Notification Preferences ──

    def set_notification_prefs(self, agent_id: str, conversation_id: str = None,
                                muted: bool = False, mute_until: str = None,
                                mention_only: bool = False) -> dict:
        now = _now()
        conv_id = conversation_id or ""
        self.conn.execute(
            """INSERT INTO notification_prefs (agent_id, conversation_id, muted, mute_until, mention_only, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent_id, conversation_id) DO UPDATE SET
                 muted = excluded.muted, mute_until = excluded.mute_until,
                 mention_only = excluded.mention_only, updated_at = excluded.updated_at""",
            (agent_id, conv_id, int(muted), mute_until, int(mention_only), now, now),
        )
        self.conn.commit()
        return self.get_notification_prefs(agent_id, conversation_id)

    def get_notification_prefs(self, agent_id: str, conversation_id: str = None) -> Optional[dict]:
        conv_id = conversation_id or ""
        row = self.conn.execute(
            "SELECT * FROM notification_prefs WHERE agent_id = ? AND IFNULL(conversation_id, '') = ?",
            (agent_id, conv_id),
        ).fetchone()
        if not row:
            return {"agent_id": agent_id, "conversation_id": conv_id or None,
                    "muted": False, "mute_until": None, "mention_only": False}
        d = dict(row)
        d["muted"] = bool(d["muted"])
        d["mention_only"] = bool(d["mention_only"])
        if not d["conversation_id"]:
            d["conversation_id"] = None
        return d

    def list_notification_prefs(self, agent_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM notification_prefs WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["muted"] = bool(d["muted"])
            d["mention_only"] = bool(d["mention_only"])
            if not d["conversation_id"]:
                d["conversation_id"] = None
            results.append(d)
        return results

    # ── v0.6: Message Forwarding ──

    def forward_message(self, original_msg_id: str, target_conversation_id: str, sender_id: str) -> dict:
        """Forward a message to another conversation."""
        orig = self.get_message(original_msg_id)
        if not orig:
            return None
        now = _now()
        msg_id = str(uuid.uuid4())
        meta = orig.get("metadata", {})
        meta["forwarded"] = True
        self.conn.execute(
            """INSERT INTO messages (id, conversation_id, sender_id, content, type, metadata, created_at, read_by,
                                      reply_to_id, priority, forwarded_from_id, forwarded_from_conversation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, target_conversation_id, sender_id, orig["content"], orig["type"],
             json.dumps(meta), now, json.dumps([sender_id]), None, "normal",
             original_msg_id, orig["conversation_id"]),
        )
        self.conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, target_conversation_id)
        )
        self.conn.commit()
        return self.get_message(msg_id)

    # ── v0.6: Bookmarks ──

    def create_bookmark(self, agent_id: str, message_id: str, label: str = None) -> dict:
        bm_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            "INSERT OR IGNORE INTO bookmarks (id, agent_id, message_id, label, created_at) VALUES (?, ?, ?, ?, ?)",
            (bm_id, agent_id, message_id, label, now),
        )
        self.conn.commit()
        return self.get_bookmark(bm_id)

    def get_bookmark(self, bookmark_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
        return dict(row) if row else None

    def list_bookmarks(self, agent_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            """SELECT b.*, m.content, m.sender_id, m.conversation_id, m.created_at as msg_created_at
               FROM bookmarks b JOIN messages m ON b.message_id = m.id
               WHERE b.agent_id = ? ORDER BY b.created_at DESC LIMIT ? OFFSET ?""",
            (agent_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_bookmark(self, bookmark_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ── v0.6: Custom Emojis ──

    def create_custom_emoji(self, name: str, image_url: str, creator_id: str, animated: bool = False) -> dict:
        eid = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            "INSERT INTO custom_emojis (id, name, image_url, creator_id, animated, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, name, image_url, creator_id, int(animated), now),
        )
        self.conn.commit()
        return self.get_custom_emoji(eid)

    def get_custom_emoji(self, emoji_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM custom_emojis WHERE id = ?", (emoji_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["animated"] = bool(d["animated"])
        return d

    def get_custom_emoji_by_name(self, name: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM custom_emojis WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["animated"] = bool(d["animated"])
        return d

    def list_custom_emojis(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM custom_emojis ORDER BY name LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_custom_emoji(self, emoji_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM custom_emojis WHERE id = ?", (emoji_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ── v0.6: Message Expiry ──

    def set_message_expiry(self, conversation_id: str, ttl_seconds: int):
        self.conn.execute(
            "UPDATE conversations SET message_ttl = ? WHERE id = ?", (ttl_seconds, conversation_id)
        )
        self.conn.commit()

    def expire_messages(self) -> int:
        """Delete expired messages based on expires_at or conversation TTL. Returns count deleted."""
        now = _now()
        # Direct expires_at
        c1 = self.conn.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        # TTL-based: messages in conversations with message_ttl
        c2 = self.conn.execute(
            """DELETE FROM messages WHERE conversation_id IN
               (SELECT id FROM conversations WHERE message_ttl IS NOT NULL)
               AND created_at < ?
               AND expires_at IS NULL""",
            (now,),
        )
        # Actually, we need a subquery to compute the cutoff per conversation
        # Let's do it properly:
        convs = self.conn.execute(
            "SELECT id, message_ttl FROM conversations WHERE message_ttl IS NOT NULL"
        ).fetchall()
        total = c1.rowcount
        for conv in convs:
            from datetime import timedelta
            cutoff = _now(offset_seconds=-conv["message_ttl"])
            c3 = self.conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND created_at < ? AND expires_at IS NULL",
                (conv["id"], cutoff),
            )
            total += c3.rowcount
        self.conn.commit()
        return total

    # ── v0.6: Conversation Archiving ──

    def archive_conversation(self, conversation_id: str):
        self.conn.execute(
            "UPDATE conversations SET archived = 1 WHERE id = ?", (conversation_id,)
        )
        self.conn.commit()

    def unarchive_conversation(self, conversation_id: str):
        self.conn.execute(
            "UPDATE conversations SET archived = 0 WHERE id = ?", (conversation_id,)
        )
        self.conn.commit()

    def archive_conversation_for_member(self, conversation_id: str, agent_id: str):
        now = _now()
        self.conn.execute(
            "UPDATE conversation_members SET archived_at = ? WHERE conversation_id = ? AND agent_id = ?",
            (now, conversation_id, agent_id),
        )
        self.conn.commit()

    def unarchive_conversation_for_member(self, conversation_id: str, agent_id: str):
        self.conn.execute(
            "UPDATE conversation_members SET archived_at = NULL WHERE conversation_id = ? AND agent_id = ?",
            (conversation_id, agent_id),
        )
        self.conn.commit()

    # ── v0.6: Webhooks ──

    def create_webhook(self, conversation_id: str, url: str, events: list[str],
                        secret: str = None, created_by: str = None) -> dict:
        wh_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO webhooks (id, conversation_id, url, events, secret, active, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (wh_id, conversation_id, url, json.dumps(events), secret, created_by, now, now),
        )
        self.conn.commit()
        return self.get_webhook(wh_id)

    def get_webhook(self, webhook_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["events"] = json.loads(d["events"])
        d["active"] = bool(d["active"])
        return d

    def list_webhooks(self, conversation_id: str = None, limit: int = 100, offset: int = 0) -> list[dict]:
        if conversation_id:
            rows = self.conn.execute(
                "SELECT * FROM webhooks WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (conversation_id, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM webhooks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["events"] = json.loads(d["events"])
            d["active"] = bool(d["active"])
            results.append(d)
        return results

    def update_webhook(self, webhook_id: str, url: str = None, events: list[str] = None,
                        active: bool = None) -> Optional[dict]:
        updates = []
        params = []
        if url is not None:
            updates.append("url = ?")
            params.append(url)
        if events is not None:
            updates.append("events = ?")
            params.append(json.dumps(events))
        if active is not None:
            updates.append("active = ?")
            params.append(int(active))
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(webhook_id)
        self.conn.execute(
            f"UPDATE webhooks SET {', '.join(updates)} WHERE id = ?", params
        )
        self.conn.commit()
        return self.get_webhook(webhook_id)

    def delete_webhook(self, webhook_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ── v0.6: Event Subscriptions ──

    def create_event_subscription(self, agent_id: str, event_type: str,
                                    conversation_id: str = None, callback_url: str = None) -> dict:
        sub_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO event_subscriptions (id, agent_id, event_type, conversation_id, callback_url, active, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (sub_id, agent_id, event_type, conversation_id, callback_url, now),
        )
        self.conn.commit()
        return self.get_event_subscription(sub_id)

    def get_event_subscription(self, sub_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM event_subscriptions WHERE id = ?", (sub_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["active"] = bool(d["active"])
        return d

    def list_event_subscriptions(self, agent_id: str = None, event_type: str = None,
                                   limit: int = 100, offset: int = 0) -> list[dict]:
        conditions = []
        params = []
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self.conn.execute(
            f"SELECT * FROM event_subscriptions WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["active"] = bool(d["active"])
            results.append(d)
        return results

    def delete_event_subscription(self, sub_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM event_subscriptions WHERE id = ?", (sub_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ── v0.6: Slash Commands ──

    def create_slash_command(self, name: str, description: str, handler_url: str,
                              conversation_id: str = None, created_by: str = None) -> dict:
        cmd_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO slash_commands (id, name, description, handler_url, conversation_id, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cmd_id, name, description, handler_url, conversation_id, created_by, now),
        )
        self.conn.commit()
        return self.get_slash_command(cmd_id)

    def get_slash_command(self, cmd_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM slash_commands WHERE id = ?", (cmd_id,)).fetchone()
        return dict(row) if row else None

    def get_slash_command_by_name(self, name: str, conversation_id: str = None) -> Optional[dict]:
        if conversation_id:
            # Try conversation-specific first, then global
            row = self.conn.execute(
                "SELECT * FROM slash_commands WHERE name = ? AND conversation_id = ?",
                (name, conversation_id),
            ).fetchone()
            if row:
                return dict(row)
        row = self.conn.execute(
            "SELECT * FROM slash_commands WHERE name = ? AND conversation_id IS NULL",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    def list_slash_commands(self, conversation_id: str = None, limit: int = 100, offset: int = 0) -> list[dict]:
        if conversation_id:
            rows = self.conn.execute(
                "SELECT * FROM slash_commands WHERE conversation_id = ? OR conversation_id IS NULL ORDER BY name LIMIT ? OFFSET ?",
                (conversation_id, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM slash_commands ORDER BY name LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_slash_command(self, cmd_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM slash_commands WHERE id = ?", (cmd_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ── v0.6: Scheduled Messages ──

    def create_scheduled_message(self, conversation_id: str, sender_id: str,
                                   content: str, scheduled_for: str) -> dict:
        sm_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO scheduled_messages (id, conversation_id, sender_id, content, scheduled_for, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (sm_id, conversation_id, sender_id, content, scheduled_for, now),
        )
        self.conn.commit()
        return self.get_scheduled_message(sm_id)

    def get_scheduled_message(self, sm_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (sm_id,)).fetchone()
        return dict(row) if row else None

    def list_scheduled_messages(self, conversation_id: str = None, status: str = None,
                                 limit: int = 50, offset: int = 0) -> list[dict]:
        conditions = []
        params = []
        if conversation_id:
            conditions.append("conversation_id = ?")
            params.append(conversation_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self.conn.execute(
            f"SELECT * FROM scheduled_messages WHERE {where} ORDER BY scheduled_for ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def cancel_scheduled_message(self, sm_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("UPDATE scheduled_messages SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (sm_id,))
        self.conn.commit()
        return c.rowcount > 0

    def get_due_scheduled_messages(self) -> list[dict]:
        now = _now()
        rows = self.conn.execute(
            "SELECT * FROM scheduled_messages WHERE status = 'pending' AND scheduled_for <= ?",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_scheduled_sent(self, sm_id: str) -> bool:
        now = _now()
        c = self.conn.cursor()
        c.execute("UPDATE scheduled_messages SET status = 'sent', sent_at = ? WHERE id = ?", (now, sm_id))
        self.conn.commit()
        return c.rowcount > 0

    # ── v0.6: Message Embeds ──

    def create_embed(self, message_id: str, title: str = None, description: str = None,
                      url: str = None, image_url: str = None, thumbnail_url: str = None,
                      embed_type: str = "link") -> dict:
        emb_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO message_embeds (id, message_id, title, description, url, image_url, thumbnail_url, embed_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (emb_id, message_id, title, description, url, image_url, thumbnail_url, embed_type, now),
        )
        self.conn.commit()
        return self.get_embed(emb_id)

    def get_embed(self, embed_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM message_embeds WHERE id = ?", (embed_id,)).fetchone()
        return dict(row) if row else None

    def get_embeds_for_message(self, message_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM message_embeds WHERE message_id = ? ORDER BY created_at", (message_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_embed(self, embed_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("DELETE FROM message_embeds WHERE id = ?", (embed_id,))
        self.conn.commit()
        return c.rowcount > 0

    # ── v0.6: Message Translations ──

    def create_translation(self, message_id: str, language: str, content: str) -> dict:
        tr_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """INSERT INTO message_translations (id, message_id, language, content, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(message_id, language) DO UPDATE SET content = excluded.content, created_at = excluded.created_at""",
            (tr_id, message_id, language, content, now),
        )
        self.conn.commit()
        return self.get_translation(tr_id)

    def get_translation(self, translation_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM message_translations WHERE id = ?", (translation_id,)).fetchone()
        return dict(row) if row else None

    def get_translations_for_message(self, message_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM message_translations WHERE message_id = ? ORDER BY language", (message_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_translation_for_message(self, message_id: str, language: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM message_translations WHERE message_id = ? AND language = ?",
            (message_id, language),
        ).fetchone()
        return dict(row) if row else None


    def close(self):
        self.conn.close()

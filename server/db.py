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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                read_by TEXT DEFAULT '[]'
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

    def send_message(self, conversation_id: str, sender_id: str, content: str, msg_type: str = "text", metadata: dict = None) -> dict:
        msg_id = str(uuid.uuid4())
        now = _now()
        meta_json = json.dumps(metadata or {})
        self.conn.execute(
            """INSERT INTO messages (id, conversation_id, sender_id, content, type, metadata, created_at, read_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, sender_id, content, msg_type, meta_json, now, json.dumps([sender_id])),
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
        c = self.conn.cursor()
        c.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        self.conn.commit()
        return c.rowcount > 0

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

    def search_messages(self, query: str, limit: int = 20, offset: int = 0) -> list[dict]:
        """Search messages with proper LIKE escaping."""
        safe_query = sanitize_sql_like(query)
        rows = self.conn.execute(
            """SELECT * FROM messages WHERE content LIKE ? ESCAPE '\\'
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (f"%{safe_query}%", limit, offset),
        ).fetchall()
        return [self._msg_row(r) for r in rows]

    # ── Typing Indicators ──

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

    def get_typing(self, conversation_id: str) -> list[dict]:
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
        return dict(row)

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
        return d

    def close(self):
        self.conn.close()

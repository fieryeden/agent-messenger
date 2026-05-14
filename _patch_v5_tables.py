#!/usr/bin/env python3
"""Patch script: add files, pinned_messages, polls, poll_votes tables + FTS5 to db.py"""
import re

with open("server/db.py", "r") as f:
    content = f.read()

# ── 1. Add new tables + FTS5 in _init_tables ──
# Match the last index line + closing """) with any leading whitespace
old_tables_end = 'idx_msg_delivery ON message_delivery(message_id);\n        """)'
new_tables_end = '''idx_msg_delivery ON message_delivery(message_id);
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
        """)'''

assert old_tables_end in content, f"Could not find tables end block. Searching near: {old_tables_end[:80]}"
content = content.replace(old_tables_end, new_tables_end, 1)

# ── 2. Add migration v3 ──
# Find the end of migration v2 entry
old_mig = '''    (2, "add_message_threading_edit_delete", [
        "ALTER TABLE messages ADD COLUMN reply_to_id TEXT REFERENCES messages(id) ON DELETE SET NULL",
        "ALTER TABLE messages ADD COLUMN priority TEXT DEFAULT 'normal'",
        "ALTER TABLE messages ADD COLUMN edited_at TEXT",
        "ALTER TABLE messages ADD COLUMN edited_content TEXT",
        "ALTER TABLE messages ADD COLUMN deleted_at TEXT",
        "CREATE INDEX IF NOT EXISTS idx_msg_reply ON messages(reply_to_id)",
    ]),
]'''

new_mig = '''    (2, "add_message_threading_edit_delete", [
        "ALTER TABLE messages ADD COLUMN reply_to_id TEXT REFERENCES messages(id) ON DELETE SET NULL",
        "ALTER TABLE messages ADD COLUMN priority TEXT DEFAULT 'normal'",
        "ALTER TABLE messages ADD COLUMN edited_at TEXT",
        "ALTER TABLE messages ADD COLUMN edited_content TEXT",
        "ALTER TABLE messages ADD COLUMN deleted_at TEXT",
        "CREATE INDEX IF NOT EXISTS idx_msg_reply ON messages(reply_to_id)",
    ]),
    (3, "add_files_pinned_polls_fts", [
        """CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
            uploader_id TEXT NOT NULL REFERENCES agents(id),
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            storage_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS pinned_messages (
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            pinned_by TEXT NOT NULL REFERENCES agents(id),
            pinned_at TEXT NOT NULL,
            PRIMARY KEY (conversation_id, message_id)
        )""",
        """CREATE TABLE IF NOT EXISTS polls (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            creator_id TEXT NOT NULL REFERENCES agents(id),
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            multi_vote INTEGER DEFAULT 0,
            closed_at TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id TEXT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            option_index INTEGER NOT NULL,
            voted_at TEXT NOT NULL,
            PRIMARY KEY (poll_id, agent_id, option_index)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_files_message ON files(message_id)",
        "CREATE INDEX IF NOT EXISTS idx_files_uploader ON files(uploader_id)",
        "CREATE INDEX IF NOT EXISTS idx_pinned_conv ON pinned_messages(conversation_id)",
        "CREATE INDEX IF NOT EXISTS idx_polls_conv ON polls(conversation_id)",
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='rowid', tokenize='porter unicode61')",
        "CREATE TRIGGER IF NOT EXISTS messages_fts_ins AFTER INSERT ON messages BEGIN INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END",
        "CREATE TRIGGER IF NOT EXISTS messages_fts_del AFTER DELETE ON messages BEGIN INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content); END",
        "CREATE TRIGGER IF NOT EXISTS messages_fts_upd AFTER UPDATE ON messages BEGIN INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content); INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END",
    ]),
]'''

assert old_mig in content, "Could not find migration v2 end"
content = content.replace(old_mig, new_mig, 1)

# ── 3. Add new DB methods before close() ──
new_methods = '''
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
        # Add vote counts per option
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
            # Remove any existing votes by this agent
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
            pass  # Already voted for this option
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
        # Escape FTS5 special characters
        for ch in ('"', '*', '(', ')', ':', '^'):
            safe_query = safe_query.replace(ch, '')
        if not safe_query.strip():
            return []
        fts_query = '"' + safe_query + '"*'
        if conversation_id:
            sql = """SELECT m.* FROM messages_fts f
                     JOIN messages m ON m.rowid = f.rowid
                     WHERE messages_fts MATCH ? AND m.conversation_id = ?
                     ORDER BY rank
                     LIMIT ? OFFSET ?"""
            rows = self.conn.execute(sql, (fts_query, conversation_id, limit, offset)).fetchall()
        elif sender_id:
            sql = """SELECT m.* FROM messages_fts f
                     JOIN messages m ON m.rowid = f.rowid
                     WHERE messages_fts MATCH ? AND m.sender_id = ?
                     ORDER BY rank
                     LIMIT ? OFFSET ?"""
            rows = self.conn.execute(sql, (fts_query, sender_id, limit, offset)).fetchall()
        else:
            sql = """SELECT m.* FROM messages_fts f
                     JOIN messages m ON m.rowid = f.rowid
                     WHERE messages_fts MATCH ?
                     ORDER BY rank
                     LIMIT ? OFFSET ?"""
            rows = self.conn.execute(sql, (fts_query, limit, offset)).fetchall()
        return [self._msg_row(r) for r in rows]

    def rebuild_fts_index(self):
        """Rebuild the FTS5 index from scratch (for maintenance)."""
        self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        self.conn.commit()
'''

# Insert before close() method
old_close = '\n    def close(self):'
assert old_close in content, "Could not find close() method"
content = content.replace(old_close, new_methods + '\n    def close(self):', 1)

# ── 4. Replace old search_messages with FTS5-backed version ──
old_search = '''    def search_messages(self, query: str, limit: int = 20, offset: int = 0) -> list[dict]:
        """Search messages by content (LIKE query)."""
        safe_query = sanitize_sql_like(query)
        rows = self.conn.execute(
            """SELECT * FROM messages WHERE content LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (f"%{safe_query}%", limit, offset),
        ).fetchall()
        return [self._msg_row(r) for r in rows]'''

new_search = '''    def search_messages(self, query: str, limit: int = 20, offset: int = 0,
                        conversation_id: str = None, sender_id: str = None) -> list[dict]:
        """Search messages — uses FTS5 if available, falls back to LIKE."""
        try:
            return self.search_messages_fts(query, conversation_id=conversation_id,
                                            sender_id=sender_id, limit=limit, offset=offset)
        except Exception:
            # FTS5 not available (e.g. missing table) — fall back to LIKE
            safe_query = sanitize_sql_like(query)
            rows = self.conn.execute(
                """SELECT * FROM messages WHERE content LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (f"%{safe_query}%", limit, offset),
            ).fetchall()
            return [self._msg_row(r) for r in rows]'''

assert old_search in content, "Could not find old search_messages"
content = content.replace(old_search, new_search, 1)

with open("server/db.py", "w") as f:
    f.write(content)

print("OK - added files/pinned/polls tables, FTS5, migration v3, new DB methods")

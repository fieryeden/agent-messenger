#!/usr/bin/env python3
"""Add v0.5.0 tables, migration v3, FTS5, and new DB methods to db.py."""

with open("server/db.py", "r") as f:
    content = f.read()

# ── 1. Add new tables in _init_tables ──
marker = 'CREATE INDEX IF NOT EXISTS idx_msg_delivery ON message_delivery(message_id);'
assert marker in content, "idx_msg_delivery marker not found"

new_tables_sql = """
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
        END;"""

content = content.replace(marker, marker + new_tables_sql, 1)
print("OK - new tables added to _init_tables")

# ── 2. Add migration v3 ──
# Find the migration list closing bracket pattern
# After idx_msg_reply, the exact text is: ,\n            ]),\n        ]\n\n        for version
# We insert v3 entry before the final ]
idx = content.find('idx_msg_reply ON messages(reply_to_id)"')
assert idx > 0, "idx_msg_reply not found"

# Walk forward to find "for version" 
fv_idx = content.find('for version', idx)
assert fv_idx > 0, "'for version' not found after migration"

# The ] just before "for version" is the end of the migrations list
# Back up from fv_idx to find the ]\n
bracket_idx = content.rfind(']', idx, fv_idx)
assert bracket_idx > 0, "Could not find closing ] of migrations list"

v3_entry = """    (3, "add_files_pinned_polls_fts", [
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
"""

# Insert v3 before the final ]
content = content[:bracket_idx] + v3_entry + content[bracket_idx:]
print("OK - migration v3 added")

# ── 3. Update search_messages to use FTS5 with fallback ──
old_search_start = 'def search_messages(self, query: str, limit: int = 20, offset: int = 0) -> list[dict]:'
if old_search_start in content:
    start = content.index(old_search_start)
    # Find end: next "    def " at same indent
    rest = content[start:]
    # Skip 20 chars to avoid matching our own def
    next_def_pos = None
    for m in __import__('re').finditer(r'\n    def \w', rest[20:]):
        next_def_pos = 20 + m.start()
        break
    if next_def_pos:
        end = start + next_def_pos
        new_method = '''def search_messages(self, query: str, limit: int = 20, offset: int = 0,
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

'''
        content = content[:start] + new_method + content[end:]
        print("OK - search_messages updated")

# ── 4. Add new DB methods before close() ──
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

'''

close_marker = '\n    def close(self):'
assert close_marker in content, "close() not found"
content = content.replace(close_marker, new_methods + '    def close(self):', 1)
print("OK - new DB methods added")

with open("server/db.py", "w") as f:
    f.write(content)

print("DONE - all db.py patches applied")

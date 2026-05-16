"""Script to inject v0.6 migrations and methods into db.py."""
import re

with open('server/db.py', 'r') as f:
    content = f.read()

# ── 1. Add new migrations before the closing ] of the migrations list ──
new_migrations = '''        ]),

        # ── v0.6.0 migrations ──
        (4, "v06_roles_and_permissions", [
            "ALTER TABLE conversation_members ADD COLUMN permissions TEXT DEFAULT '{\\"send_messages\\":true,\\"read_messages\\":true,\\"manage_members\\":false,\\"pin_messages\\":false,\\"manage_conversation\\":false}'",
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
            "CREATE TABLE IF NOT EXISTS notification_prefs (agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE, muted INTEGER DEFAULT 0, mute_until TEXT, mention_only INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (agent_id, COALESCE(conversation_id, '')))",
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
            "CREATE TABLE IF NOT EXISTS slash_commands (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, handler_url TEXT NOT NULL, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE, created_by TEXT NOT NULL REFERENCES agents(id), created_at TEXT NOT NULL, UNIQUE(name, COALESCE(conversation_id, '')))",
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
'''

# Replace the line that closes migration 3 + closes the list
# Line 196: ' ]),\n'  Line 197: ']\n'
# We want to replace the ']),\n]\n' with ']),\n<new_migrations>\n'
old = '        ]),\n]\n'
new = new_migrations
content = content.replace(old, new, 1)

# ── 2. Add new DB methods before the close() method ──
new_methods = '''
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
               ON CONFLICT(agent_id, COALESCE(conversation_id, '')) DO UPDATE SET
                 muted = excluded.muted, mute_until = excluded.mute_until,
                 mention_only = excluded.mention_only, updated_at = excluded.updated_at""",
            (agent_id, conv_id, int(muted), mute_until, int(mention_only), now, now),
        )
        self.conn.commit()
        return self.get_notification_prefs(agent_id, conversation_id)

    def get_notification_prefs(self, agent_id: str, conversation_id: str = None) -> Optional[dict]:
        conv_id = conversation_id or ""
        row = self.conn.execute(
            "SELECT * FROM notification_prefs WHERE agent_id = ? AND COALESCE(conversation_id, '') = ?",
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
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
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

'''

# Insert before the close() method
content = content.replace(
    '\n    def close(self):\n        self.conn.close()',
    new_methods + '\n    def close(self):\n        self.conn.close()'
)

with open('server/db.py', 'w') as f:
    f.write(content)

print("db.py updated successfully")

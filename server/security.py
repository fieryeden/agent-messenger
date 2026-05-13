"""Shared security utilities - rate limiting, input sanitization, audit logging."""

import hashlib
import html
import logging
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("security")


# ── Rate Limiting ──

class RateLimiter:
    """Token-bucket rate limiter per client (by IP or agent_id).

    Config:
        requests_per_minute: max requests allowed in a rolling window
        burst: max concurrent requests allowed instantly
    """

    def __init__(self, requests_per_minute: int = 60, burst: int = 10):
        self.rpm = requests_per_minute
        self.burst = burst
        # client_key -> list of request timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)
        # client_key -> current burst tokens
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}

    def _refill_tokens(self, key: str, now: float):
        """Refill burst tokens based on elapsed time."""
        last = self._last_refill.get(key, now)
        elapsed = now - last
        # Refill rate: burst tokens per minute
        refill = elapsed * (self.rpm / 60.0)
        current = self._tokens.get(key, float(self.burst))
        self._tokens[key] = min(float(self.burst), current + refill)
        self._last_refill[key] = now

    def is_allowed(self, client_key: str) -> bool:
        """Check if a request from client_key is allowed. Returns False if rate limited."""
        now = time.time()

        # Rolling window check (requests per minute)
        timestamps = self._requests[client_key]
        # Prune old entries
        cutoff = now - 60.0
        self._requests[client_key] = [t for t in timestamps if t > cutoff]
        timestamps = self._requests[client_key]

        if len(timestamps) >= self.rpm:
            return False

        # Token bucket check (burst)
        self._refill_tokens(client_key, now)
        if self._tokens.get(client_key, float(self.burst)) < 1.0:
            return False

        # Consume token and record timestamp
        self._tokens[client_key] = self._tokens.get(client_key, float(self.burst)) - 1.0
        self._requests[client_key].append(now)
        return True

    def cleanup(self, max_age: float = 120.0):
        """Remove entries older than max_age seconds to prevent memory leak."""
        now = time.time()
        stale = [k for k, v in self._requests.items() if not v or v[-1] < now - max_age]
        for k in stale:
            self._requests.pop(k, None)
            self._tokens.pop(k, None)
            self._last_refill.pop(k, None)


# ── Input Sanitization ──

# Characters that are dangerous in various contexts
_SQL_LIKE_SPECIAL = re.compile(r"[%_\\]")


def sanitize_string(value: str, max_length: int = 10000) -> str:
    """Sanitize a string for safe storage and display.

    - Strips null bytes
    - HTML-escapes to prevent XSS
    - Truncates to max_length
    - Normalizes whitespace
    """
    if not isinstance(value, str):
        value = str(value)
    # Remove null bytes
    value = value.replace("\x00", "")
    # Normalize whitespace (but preserve intentional newlines)
    value = re.sub(r"[^\S\n]+", " ", value).strip()
    # Truncate
    if len(value) > max_length:
        value = value[:max_length]
    # HTML-escape for safe display in dashboard
    value = html.escape(value, quote=True)
    return value


def sanitize_sql_like(value: str) -> str:
    r"""Escape special characters in LIKE queries to prevent LIKE injection.

    Escapes %, _, and \ characters so user input is treated as literal.
    """
    return _SQL_LIKE_SPECIAL.sub(lambda m: "\\" + m.group(0), value)


def sanitize_agent_id(agent_id: str) -> str:
    """Sanitize agent ID - only allow alphanumeric, hyphens, underscores, dots."""
    if not isinstance(agent_id, str):
        agent_id = str(agent_id)
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "", agent_id)
    if not cleaned:
        raise ValueError("Agent ID must contain at least one alphanumeric character")
    if len(cleaned) > 128:
        cleaned = cleaned[:128]
    return cleaned


def sanitize_uuid(value: str) -> str:
    """Validate and return a UUID string."""
    if not isinstance(value, str):
        raise ValueError("UUID must be a string")
    cleaned = value.strip()
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
    )
    if not uuid_pattern.match(cleaned):
        raise ValueError(f"Invalid UUID format: {value[:32]}")
    return cleaned


def sanitize_tags(tags: list) -> list[str]:
    """Sanitize a list of tags."""
    if not isinstance(tags, list):
        return []
    result = []
    for tag in tags[:20]:  # max 20 tags
        if isinstance(tag, str):
            cleaned = re.sub(r"[^a-zA-Z0-9._-]", "", tag)[:64]
            if cleaned:
                result.append(cleaned)
    return result


def sanitize_content(value: str, max_length: int = 100000) -> str:
    """Sanitize message/content text - less aggressive than sanitize_string.

    Preserves newlines and basic formatting but strips null bytes
    and HTML-escapes. Used for message content, memory content, etc.
    """
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\x00", "")
    if len(value) > max_length:
        value = value[:max_length]
    value = html.escape(value, quote=True)
    return value


# ── Audit Logger ──

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLogger:
    """SQLite-backed audit log for traceability.

    Records: who did what, when, from where, with what result.
    """

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_table()

    def _init_table(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_id TEXT,
                client_ip TEXT,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                detail TEXT,
                status TEXT DEFAULT 'success',
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent_id);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
        """)
        self.conn.commit()

    def log(
        self,
        action: str,
        agent_id: Optional[str] = None,
        client_ip: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        detail: Optional[str] = None,
        status: str = "success",
        error: Optional[str] = None,
    ):
        """Record an audit entry."""
        self.conn.execute(
            """INSERT INTO audit_log (timestamp, agent_id, client_ip, action, resource_type, resource_id, detail, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), agent_id, client_ip, action, resource_type, resource_id, detail, status, error),
        )
        self.conn.commit()

    def query(
        self,
        agent_id: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query audit log entries."""
        conditions = []
        params = []
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self.conn.execute(
            f"SELECT * FROM audit_log WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Get audit log statistics."""
        total = self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        last_24h = self.conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE timestamp >= ?",
            (_now()[:14] + "00:00:00",),  # rough 24h window
        ).fetchone()[0]
        by_action = self.conn.execute(
            "SELECT action, COUNT(*) as count FROM audit_log GROUP BY action ORDER BY count DESC LIMIT 10"
        ).fetchall()
        return {
            "total_entries": total,
            "last_24h": last_24h,
            "top_actions": [dict(r) for r in by_action],
        }

    def close(self):
        self.conn.close()


# ── Graceful Shutdown ──

import signal
import asyncio

class GracefulShutdown:
    """Handle SIGTERM/SIGINT gracefully - drain connections, close DBs, flush logs."""

    def __init__(self, shutdown_callback=None):
        self._shutdown_event = asyncio.Event()
        self._callback = shutdown_callback
        self._received = False

    def install(self):
        """Install signal handlers."""
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

    def _handle_signal(self):
        if self._received:
            return  # Only handle once
        self._received = True
        logger.info("Graceful shutdown initiated...")
        if self._callback:
            try:
                self._callback()
            except Exception as e:
                logger.error("Shutdown callback error: %s", e)
        self._shutdown_event.set()

    @property
    def should_shutdown(self) -> bool:
        return self._received

    async def wait(self):
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

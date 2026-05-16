"""Authentication & authorization — JWT tokens, API keys, scopes, middleware."""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

logger = logging.getLogger("agent-messenger.auth")

# ── Configuration ──

JWT_SECRET = os.getenv("MESSENGER_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_EXPIRY = int(os.getenv("MESSENGER_JWT_ACCESS_MINUTES", "60")) * 60
JWT_REFRESH_EXPIRY = int(os.getenv("MESSENGER_JWT_REFRESH_DAYS", "30")) * 86400
API_KEY_SALT = os.getenv("MESSENGER_API_KEY_SALT", "agent-messenger-v0.3")

# Generate a stable secret if none provided (dev mode)
if not JWT_SECRET:
    _secret_path = os.path.join(os.path.dirname(__file__), "..", "data", ".jwt_secret")
    if os.path.exists(_secret_path):
        with open(_secret_path) as f:
            JWT_SECRET = f.read().strip()
    else:
        JWT_SECRET = secrets.token_hex(32)
        os.makedirs(os.path.dirname(_secret_path), exist_ok=True)
        with open(_secret_path, "w") as f:
            f.write(JWT_SECRET)
        logger.warning("Generated new JWT secret — set MESSENGER_JWT_SECRET env var for production")

_bearer = HTTPBearer(auto_error=False)


# ── JWT Implementation (pure Python, no PyJWT dependency) ──

def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _jwt_encode(payload: dict) -> str:
    """Encode a JWT token using HMAC-SHA256."""
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _jwt_decode(token: str) -> Optional[dict]:
    """Decode and verify a JWT token. Returns payload or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        # Verify signature
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        # Decode payload
        payload = json.loads(_b64url_decode(payload_b64))
        # Check expiry
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ── API Key Management ──

def generate_api_key(agent_id: str, scopes: list[str] = None) -> tuple[str, str]:
    """Generate an API key for an agent. Returns (raw_key, hashed_key).
    The raw key is shown once at creation; the hashed key is stored.
    """
    raw = f"am_{secrets.token_hex(24)}"
    hashed = _hash_api_key(raw)
    return raw, hashed


def _hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(f"{API_KEY_SALT}:{raw_key}".encode()).hexdigest()


def verify_api_key(raw_key: str, hashed_key: str) -> bool:
    """Verify a raw API key against its hash."""
    return hmac.compare_digest(_hash_api_key(raw_key), hashed_key)


# ── Token Creation ──

def create_access_token(agent_id: str, scopes: list[str] = None) -> str:
    """Create a JWT access token."""
    now = time.time()
    payload = {
        "sub": agent_id,
        "type": "access",
        "iat": int(now),
        "exp": int(now + JWT_ACCESS_EXPIRY),
        "scopes": scopes or ["agent"],
    }
    return _jwt_encode(payload)


def create_refresh_token(agent_id: str) -> str:
    """Create a JWT refresh token."""
    now = time.time()
    payload = {
        "sub": agent_id,
        "type": "refresh",
        "iat": int(now),
        "exp": int(now + JWT_REFRESH_EXPIRY),
    }
    return _jwt_encode(payload)


# ── Auth Models ──

class LoginRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    api_key: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class AgentApiKeyCreate(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    scopes: list[str] = Field(default=["agent"], max_length=10)


class AgentApiKeyResponse(BaseModel):
    agent_id: str
    api_key: str  # Only shown once at creation
    scopes: list[str]
    created_at: str


# ── Auth Dependency ──

class AuthIdentity:
    """Resolved identity from JWT or API key."""
    def __init__(self, agent_id: str, scopes: list[str], auth_type: str):
        self.agent_id = agent_id
        self.scopes = scopes
        self.auth_type = auth_type  # "jwt" or "api_key"

    def has_scope(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes


def _get_identity_from_jwt(token: str) -> Optional[AuthIdentity]:
    """Validate a JWT token and return identity."""
    payload = _jwt_decode(token)
    if not payload or payload.get("type") != "access":
        return None
    return AuthIdentity(
        agent_id=payload["sub"],
        scopes=payload.get("scopes", ["agent"]),
        auth_type="jwt",
    )


def _get_identity_from_api_key(token: str, db) -> Optional[AuthIdentity]:
    """Validate an API key and return identity."""
    if not token.startswith("am_"):
        return None
    hashed = _hash_api_key(token)
    agent = db.get_agent_by_api_key(hashed)
    if not agent:
        return None
    return AuthIdentity(
        agent_id=agent["id"],
        scopes=agent.get("api_key_scopes", ["agent"]),
        auth_type="api_key",
    )


async def get_current_identity(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> AuthIdentity:
    """FastAPI dependency: resolve identity from Authorization header."""
    # If middleware already resolved identity, use it
    middleware_id = getattr(request.state, "auth_identity", None)
    if middleware_id:
        return AuthIdentity(
            agent_id=middleware_id["agent_id"],
            scopes=middleware_id["scopes"],
            auth_type=middleware_id["auth_type"],
        )

    # If auth is disabled, allow all
    from server.db_accessor import get_db
    config = getattr(request.app.state, "config", {})
    if not config.get("auth", {}).get("enabled", False):
        return AuthIdentity(agent_id="anonymous", scopes=["admin"], auth_type="disabled")

    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = credentials.credentials
    db = get_db()

    # Try JWT first
    identity = _get_identity_from_jwt(token)
    if identity:
        return identity

    # Try API key
    identity = _get_identity_from_api_key(token, db)
    if identity:
        return identity

    # Try config-level API keys (backward compat with middleware)
    config_api_keys = config.get("auth", {}).get("api_keys", [])
    if config_api_keys and token in config_api_keys:
        return AuthIdentity(
            agent_id="config-api-user",
            scopes=["agent", "admin"],
            auth_type="config_api_key",
        )

    raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_scope(*required_scopes: str):
    """Dependency factory: require one of the given scopes."""
    async def _check(identity: AuthIdentity = Depends(get_current_identity)):
        for scope in required_scopes:
            if identity.has_scope(scope):
                return identity
        raise HTTPException(status_code=403, detail=f"Requires one of: {', '.join(required_scopes)}")
    return _check

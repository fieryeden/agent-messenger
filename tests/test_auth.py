"""Tests for JWT auth, API keys, and auth middleware."""

import os
import time
import pytest
from fastapi.testclient import TestClient

# Set JWT secret before importing
os.environ["MESSENGER_JWT_SECRET"] = "test-secret-key-for-testing-only"


class TestJWTCrypto:
    """Test JWT encode/decode and API key generation."""

    def test_jwt_encode_decode_roundtrip(self):
        from server.auth import _jwt_encode, _jwt_decode
        payload = {"sub": "test-agent", "type": "access", "scopes": ["agent"], "exp": int(time.time()) + 900}
        token = _jwt_encode(payload)
        decoded = _jwt_decode(token)
        assert decoded is not None
        assert decoded["sub"] == "test-agent"
        assert decoded["type"] == "access"

    def test_jwt_expired(self):
        from server.auth import _jwt_encode, _jwt_decode
        payload = {"sub": "test-agent", "type": "access", "exp": int(time.time()) - 10}
        token = _jwt_encode(payload)
        decoded = _jwt_decode(token)
        assert decoded is None

    def test_jwt_invalid_signature(self):
        from server.auth import _jwt_decode
        import hmac, hashlib, base64, json
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b'=').decode()
        payload_b64 = base64.urlsafe_b64encode(json.dumps({"sub": "evil", "type": "access", "exp": int(time.time()) + 900}).encode()).rstrip(b'=').decode()
        msg = f"{header}.{payload_b64}"
        sig = base64.urlsafe_b64encode(hmac.digest(b"wrong-secret", msg.encode(), hashlib.sha256)).rstrip(b'=').decode()
        token = f"{msg}.{sig}"
        assert _jwt_decode(token) is None

    def test_api_key_generation(self):
        from server.auth import generate_api_key, _hash_api_key, verify_api_key
        raw, hashed = generate_api_key("test-agent", scopes=["agent", "admin"])
        assert raw.startswith("am_")
        assert len(raw) > 20
        assert verify_api_key(raw, hashed)
        assert not verify_api_key("am_wrong_key", hashed)

    def test_api_key_hash_deterministic(self):
        from server.auth import _hash_api_key
        h1 = _hash_api_key("am_test_key_123")
        h2 = _hash_api_key("am_test_key_123")
        assert h1 == h2
        h3 = _hash_api_key("am_different_key")
        assert h1 != h3


class TestAuthEndpoints:
    """Test auth REST endpoints with a real app."""

    @pytest.fixture
    def client(self):
        from server.main import create_app
        app = create_app({"auth": {"enabled": True, "api_keys": []}, "database": {"path": ":memory:"}, "audit": {"enabled": False}})
        return TestClient(app)

    def _register_and_get_api_key(self, client, agent_id="test-bot", name="Test Bot"):
        """Helper: register agent directly via DB (bypasses auth), create API key."""
        from server.auth import generate_api_key
        from server.db_accessor import get_db
        db = get_db()
        db.register_agent(agent_id, name, "detached")
        raw_key, hashed = generate_api_key(agent_id, scopes=["agent", "admin"])
        db.set_agent_api_key(agent_id, hashed, ["agent", "admin"])
        return raw_key

    def test_login_success(self, client):
        raw_key = self._register_and_get_api_key(client)
        resp = client.post("/api/v1/auth/login", json={"agent_id": "test-bot", "api_key": raw_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_login_unknown_agent(self, client):
        resp = client.post("/api/v1/auth/login", json={"agent_id": "nonexistent", "api_key": "am_some_key"})
        assert resp.status_code in (401, 403)

    def test_login_wrong_api_key(self, client):
        self._register_and_get_api_key(client)
        resp = client.post("/api/v1/auth/login", json={"agent_id": "test-bot", "api_key": "am_wrong_key"})
        assert resp.status_code in (401, 403)

    def test_refresh_token(self, client):
        raw_key = self._register_and_get_api_key(client)
        login = client.post("/api/v1/auth/login", json={"agent_id": "test-bot", "api_key": raw_key}).json()
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

    def test_protected_route_without_auth(self, client):
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 401

    def test_protected_route_with_jwt(self, client):
        raw_key = self._register_and_get_api_key(client)
        login = client.post("/api/v1/auth/login", json={"agent_id": "test-bot", "api_key": raw_key}).json()
        resp = client.get("/api/v1/agents", headers={"Authorization": f"Bearer {login['access_token']}"})
        assert resp.status_code == 200

    def test_api_key_auth(self, client):
        raw_key = self._register_and_get_api_key(client)
        # Use API key directly for auth
        resp = client.get("/api/v1/agents", headers={"Authorization": f"Bearer {raw_key}"})
        assert resp.status_code == 200

    def test_invalid_token_rejected(self, client):
        resp = client.get("/api/v1/agents", headers={"Authorization": "Bearer invalid-token-here"})
        assert resp.status_code == 401

    def test_health_no_auth_required(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200


class TestAuthDisabled:
    """When auth is disabled, routes should work without tokens."""

    @pytest.fixture
    def client(self):
        from server.main import create_app
        app = create_app({"auth": {"enabled": False}, "database": {"path": ":memory:"}, "audit": {"enabled": False}})
        return TestClient(app)

    def test_agents_without_auth(self, client):
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200

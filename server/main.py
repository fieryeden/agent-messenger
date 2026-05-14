"""Agent Messenger — FastAPI server with WebSocket + REST + Dashboard."""

import argparse
import logging
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.db import MessengerDB
from server.db_accessor import get_db, set_db
from server.routes import agents as agent_routes
from server.routes import auth as auth_routes
from server.routes import broadcast as broadcast_routes
from server.routes import conversations as conv_routes
from server.routes import messages as msg_routes
from server.security import (
    AuditLogger,
    GracefulShutdown,
    RateLimiter,
    sanitize_agent_id,
    sanitize_content,
    sanitize_string,
    sanitize_uuid,
)
from server.websocket import manager as ws_manager

logger = logging.getLogger("agent-messenger")


def _default_config() -> dict:
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8096,
        },
        "database": {
            "path": "./data/messenger.db",
        },
        "auth": {
            "enabled": False,
            "api_keys": [],
        },
        "rate_limit": {
            "requests_per_minute": 60,
            "burst": 10,
        },
        "audit": {
            "enabled": True,
            "db_path": "./data/audit.db",
        },
        "cors_origins": ["*"],
        "dashboard": {
            "enabled": True,
        },
    }


def load_config(config_path="config.yaml") -> dict:
    # Allow passing a dict directly (for tests)
    if isinstance(config_path, dict):
        base = _default_config()
        _deep_merge(base, config_path)
        return base

    config = _default_config()
    if isinstance(config_path, (str, os.PathLike)) and os.path.exists(config_path):
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        _deep_merge(config, user_config)
    return config


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ── Globals ──
db: MessengerDB = None
audit: AuditLogger = None
rate_limiter: RateLimiter = None
config: dict = {}


def create_app(config_path: str = "config.yaml") -> FastAPI:
    global db, audit, rate_limiter, config

    config = load_config(config_path)

    app = FastAPI(
        title="Agent Messenger",
        version="0.3.0",
        description="Inter-agent communication platform with JWT auth, real-time messaging, and dashboard",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.get("cors_origins", ["*"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Init components
    db = MessengerDB(config["database"]["path"])
    set_db(db)
    rate_limiter = RateLimiter(
        requests_per_minute=config.get("rate_limit", {}).get("requests_per_minute", 60),
        burst=config.get("rate_limit", {}).get("burst", 10),
    )

    # Audit logger
    if config.get("audit", {}).get("enabled", True):
        audit = AuditLogger(config["audit"]["db_path"])
    else:
        audit = None

    # ── Auth + Rate Limiting Middleware ──
    @app.middleware("http")
    async def auth_and_rate_limit(request: Request, call_next):
        # Skip auth for health/ready/metrics/docs/static endpoints
        skip_auth_paths = ("/health", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc")
        is_skip_path = any(request.url.path.startswith(p) for p in skip_auth_paths) or request.url.path == "/"

        # Rate limiting (always active)
        client_key = request.client.host if request.client else "unknown"
        agent_header = request.headers.get("X-Agent-ID", "")
        if agent_header:
            client_key = f"agent:{agent_header}"
        if not rate_limiter.is_allowed(client_key):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "retry_after_seconds": 60},
            )

        if is_skip_path:
            return await call_next(request)

        # Auth check — when enabled, all /api/ routes require valid JWT or API key
        # Exception: /api/v1/auth/login and /api/v1/auth/refresh (public)
        if config.get("auth", {}).get("enabled", False):
            public_paths = (
                "/api/v1/auth/login", "/api/v1/auth/refresh",
                "/api/auth/login", "/api/auth/refresh",
            )
            if request.url.path not in public_paths and request.url.path.startswith("/api"):
                from server.auth import _jwt_decode, _hash_api_key

                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    return JSONResponse(status_code=401, content={"detail": "Authorization required"})
                token = auth_header[7:]

                identity = None
                # Try JWT first
                payload = _jwt_decode(token)
                if payload and payload.get("type") == "access":
                    identity = {
                        "agent_id": payload["sub"],
                        "scopes": payload.get("scopes", ["agent"]),
                        "auth_type": "jwt",
                    }

                # Try API key
                if not identity and token.startswith("am_"):
                    hashed = _hash_api_key(token)
                    agent = db.get_agent_by_api_key(hashed)
                    if agent:
                        identity = {
                            "agent_id": agent["id"],
                            "scopes": agent.get("api_key_scopes", ["agent"]),
                            "auth_type": "api_key",
                        }

                # Fallback: static API keys from config (backward compat)
                if not identity:
                    config_api_keys = config.get("auth", {}).get("api_keys", [])
                    if config_api_keys and token in config_api_keys:
                        identity = {
                            "agent_id": "config-api-user",
                            "scopes": ["agent", "admin"],
                            "auth_type": "config_api_key",
                        }

                if not identity:
                    return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

                # Store identity on request state for downstream access
                request.state.auth_identity = identity

        response = await call_next(request)
        return response

    # ── Global Exception Handler ──
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
        if audit:
            audit.log("unhandled_exception", detail=str(exc)[:200], status="error", error=str(exc)[:500])
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # ── Health / Readiness ──
    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "version": "0.3.0",
            "ws_connections": len(ws_manager.active),
            "auth_enabled": config.get("auth", {}).get("enabled", False),
        }

    @app.get("/ready")
    async def ready():
        checks = {"db": db is not None, "ws": ws_manager is not None}
        all_ok = all(checks.values())
        return JSONResponse(
            status_code=200 if all_ok else 503,
            content={"ready": all_ok, "checks": checks},
        )

    # ── Metrics ──
    @app.get("/metrics")
    async def metrics():
        return {
            "stats": db.stats(),
            "ws_connections": len(ws_manager.active),
            "auth_enabled": config.get("auth", {}).get("enabled", False),
            "ws_online_agents": ws_manager.online_agents,
        }

    # ── REST Routes (v1 API + backward compat) ──
    v1_prefix = "/api/v1"
    app.include_router(auth_routes.router, prefix=v1_prefix)
    app.include_router(agent_routes.router, prefix=v1_prefix)
    app.include_router(conv_routes.router, prefix=v1_prefix)
    app.include_router(msg_routes.router, prefix=v1_prefix)
    app.include_router(broadcast_routes.router, prefix=v1_prefix)

    # Backward-compat unversioned mount
    app.include_router(auth_routes.router, prefix="/api")
    app.include_router(agent_routes.router, prefix="/api")
    app.include_router(conv_routes.router, prefix="/api")
    app.include_router(msg_routes.router, prefix="/api")
    app.include_router(broadcast_routes.router, prefix="/api")

    # ── Audit API ──
    @app.get("/api/v1/audit")
    async def query_audit(agent_id: str = None, action: str = None, since: str = None, limit: int = 100):
        if not audit:
            return JSONResponse(status_code=404, content={"detail": "Audit logging disabled"})
        safe_agent = sanitize_agent_id(agent_id) if agent_id else None
        safe_action = sanitize_string(action, max_length=64) if action else None
        entries = audit.query(agent_id=safe_agent, action=safe_action, since=since, limit=limit)
        return {"status": "ok", "entries": entries, "count": len(entries)}

    @app.get("/api/v1/audit/stats")
    async def audit_stats():
        if not audit:
            return JSONResponse(status_code=404, content={"detail": "Audit logging disabled"})
        return {"status": "ok", "stats": audit.stats()}

    # Legacy audit paths (backward compat)
    @app.get("/api/audit")
    async def query_audit_legacy(agent_id: str = None, action: str = None, since: str = None, limit: int = 100):
        return await query_audit(agent_id, action, since, limit)

    @app.get("/api/audit/stats")
    async def audit_stats_legacy():
        return await audit_stats()

    # ── WebSocket ──
    @app.websocket("/ws/{agent_id}")
    async def websocket_endpoint(websocket: WebSocket, agent_id: str):
        try:
            safe_agent = sanitize_agent_id(agent_id)
        except ValueError:
            await websocket.close(code=4000, reason="Invalid agent ID")
            return

        # Rate limit WS connections per agent
        if not rate_limiter.is_allowed(f"ws:{safe_agent}"):
            await websocket.close(code=4001, reason="Rate limit exceeded")
            return

        await ws_manager.connect(safe_agent, websocket)

        # Register agent if not already
        if not db.get_agent(safe_agent):
            db.register_agent(safe_agent, safe_agent, "detached")

        # Subscribe to all conversations
        convs = db.list_conversations(safe_agent)
        for conv in convs:
            ws_manager.subscribe(safe_agent, conv["id"])

        # Notify online
        await ws_manager.broadcast({
            "type": "agent_status",
            "agent_id": safe_agent,
            "status": "online",
        }, exclude=safe_agent)
        if audit:
            audit.log("ws_connect", agent_id=safe_agent)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = __import__("json").loads(raw)
                except __import__("json").JSONDecodeError:
                    await ws_manager.send_to_agent(safe_agent, {"type": "error", "message": "Invalid JSON"})
                    continue

                msg_type = data.get("type")

                if msg_type == "send_message":
                    conv_id = data.get("conversation_id", "")
                    content = data.get("content", "")
                    if conv_id and content:
                        safe_content = sanitize_content(content)
                        msg = db.send_message(conv_id, safe_agent, safe_content)
                        await ws_manager.broadcast_to_conversation(conv_id, {
                            "type": "new_message",
                            "conversation_id": conv_id,
                            "message": msg,
                        }, exclude=safe_agent)
                        await ws_manager.send_to_agent(safe_agent, {
                            "type": "message_sent",
                            "message": msg,
                        })
                        if audit:
                            audit.log("send_message", agent_id=safe_agent, resource_id=conv_id)

                elif msg_type == "typing":
                    conv_id = data.get("conversation_id", "")
                    if conv_id:
                        db.set_typing(conv_id, safe_agent)
                        await ws_manager.broadcast_to_conversation(conv_id, {
                            "type": "typing",
                            "conversation_id": conv_id,
                            "agent_id": safe_agent,
                        }, exclude=safe_agent)

                elif msg_type == "stop_typing":
                    conv_id = data.get("conversation_id", "")
                    if conv_id:
                        db.clear_typing(conv_id, safe_agent)
                        await ws_manager.broadcast_to_conversation(conv_id, {
                            "type": "stop_typing",
                            "conversation_id": conv_id,
                            "agent_id": safe_agent,
                        }, exclude=safe_agent)

                elif msg_type == "subscribe":
                    conv_id = data.get("conversation_id", "")
                    if conv_id:
                        ws_manager.subscribe(safe_agent, conv_id)

                elif msg_type == "unsubscribe":
                    conv_id = data.get("conversation_id", "")
                    if conv_id:
                        ws_manager.unsubscribe(safe_agent, conv_id)

                elif msg_type == "ping":
                    await ws_manager.send_to_agent(safe_agent, {"type": "pong"})

        except WebSocketDisconnect:
            ws_manager.disconnect(safe_agent)
            db.update_agent_status(safe_agent, "offline")
            await ws_manager.broadcast({
                "type": "agent_status",
                "agent_id": safe_agent,
                "status": "offline",
            })
            if audit:
                audit.log("ws_disconnect", agent_id=safe_agent)

    # ── Dashboard ──
    if config.get("dashboard", {}).get("enabled", True):
        @app.get("/dashboard/stats")
        async def dashboard_stats():
            try:
                db_ref = get_db()
                stats = db_ref.stats()
                return {"status": "ok", "stats": stats}
            except Exception as e:
                return JSONResponse(status_code=500, content={"detail": str(e)})

        @app.get("/dashboard/feed")
        async def dashboard_feed(limit: int = 100, offset: int = 0):
            try:
                db_ref = get_db()
                messages = db_ref.global_feed(limit, offset)
                return {"status": "ok", "messages": messages, "count": len(messages)}
            except Exception as e:
                return JSONResponse(status_code=500, content={"detail": str(e)})

        @app.get("/dashboard", response_class=HTMLResponse)
        async def dashboard():
            return _dashboard_html()

        @app.get("/", response_class=HTMLResponse)
        async def root():
            return _dashboard_html()

    # ── Graceful Shutdown ──
    shutdown_handler = GracefulShutdown(shutdown_callback=lambda: _cleanup())

    @app.on_event("startup")
    async def startup():
        shutdown_handler.install()

    @app.on_event("shutdown")
    async def shutdown():
        _cleanup()

    return app


def _cleanup():
    global db, audit
    if db:
        try:
            db.close()
        except Exception:
            pass
    if audit:
        try:
            audit.close()
        except Exception:
            pass


def _dashboard_html() -> str:
    """Dark-themed dashboard with agent list, DM windows, global feed."""
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Messenger Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;height:100vh;display:flex;flex-direction:column}
.header{background:#161b22;padding:12px 20px;display:flex;align-items:center;border-bottom:1px solid #30363d}
.header h1{font-size:16px;color:#f0f6fc}
.header .status{margin-left:auto;font-size:12px;color:#8b949e}
.main{display:flex;flex:1;overflow:hidden}
.sidebar{width:260px;background:#161b22;border-right:1px solid #30363d;display:flex;flex-direction:column}
.sidebar-header{padding:12px;border-bottom:1px solid #30363d;font-size:13px;color:#8b949e}
.agent-list{flex:1;overflow-y:auto;padding:8px}
.agent-item{padding:10px 12px;border-radius:6px;cursor:pointer;display:flex;align-items:center;gap:8px;margin-bottom:2px}
.agent-item:hover{background:#1f2937}
.agent-item.active{background:#1f6feb33}
.agent-dot{width:8px;height:8px;border-radius:50%}
.agent-dot.online{background:#3fb950}
.agent-dot.offline{background:#484f58}
.agent-name{font-size:13px;flex:1}
.agent-type{font-size:11px;color:#8b949e}
.chat-area{flex:1;display:flex;flex-direction:column}
.chat-header{padding:12px 20px;border-bottom:1px solid #30363d;background:#161b22}
.chat-header h2{font-size:14px;color:#f0f6fc}
.messages{flex:1;overflow-y:auto;padding:16px 20px}
.msg{margin-bottom:12px}
.msg-sender{font-size:12px;color:#8b949e;margin-bottom:2px}
.msg-content{font-size:14px;line-height:1.5;background:#161b22;padding:8px 12px;border-radius:8px;display:inline-block;max-width:70%}
.msg-time{font-size:11px;color:#484f58;margin-top:2px}
.feed-area{width:320px;background:#161b22;border-left:1px solid #30363d;display:flex;flex-direction:column}
.feed-header{padding:12px;border-bottom:1px solid #30363d;font-size:13px;color:#8b949e}
.feed-content{flex:1;overflow-y:auto;padding:8px}
.feed-item{padding:8px 12px;border-radius:6px;margin-bottom:4px;font-size:12px;background:#0d1117}
.feed-item .from{color:#58a6ff;font-weight:600}
.feed-item .text{color:#c9d1d9;margin-top:2px}
.feed-item .time{color:#484f58;font-size:11px;margin-top:2px}
.empty-state{display:flex;align-items:center;justify-content:center;height:100%;color:#484f58;font-size:14px}
.stats-bar{padding:8px 12px;border-top:1px solid #30363d;font-size:11px;color:#8b949e;display:flex;gap:16px}
.stat-value{color:#f0f6fc;font-weight:600}
</style></head><body>
<div class="header"><h1>📡 Agent Messenger</h1><span class="status" id="connStatus">Connecting...</span></div>
<div class="main">
<div class="sidebar">
<div class="sidebar-header">AGENTS</div>
<div class="agent-list" id="agentList"></div>
<div class="stats-bar" id="statsBar"></div>
</div>
<div class="chat-area">
<div class="chat-header"><h2 id="chatTitle">Select an agent</h2></div>
<div class="messages" id="messageArea"><div class="empty-state">Select an agent to start chatting</div></div>
</div>
<div class="feed-area">
<div class="feed-header">GLOBAL FEED</div>
<div class="feed-content" id="feedContent"></div>
</div>
</div>
<script>
const API='/api';let agents=[],selectedAgent=null,ws=null;
async function loadAgents(){const r=await fetch(API+'/agents');const d=await r.json();agents=d.agents||[];renderAgents();}
async function loadFeed(){const r=await fetch(API+'/messages/feed?limit=50');const d=await r.json();const feed=d.messages||d.results||[];document.getElementById('feedContent').innerHTML=feed.map(m=>'<div class="feed-item"><span class="from">'+(m.sender_name||m.sender_id)+'</span><div class="text">'+m.content+'</div><div class="time">'+m.created_at+'</div></div>').join('');}
async function loadStats(){const r=await fetch('/metrics');const d=await r.json();const s=d.stats||{};document.getElementById('statsBar').innerHTML='<span>Agents: <span class="stat-value">'+s.agents+'</span></span>'+'<span>Online: <span class="stat-value">'+s.online+'</span></span>'+'<span>Messages: <span class="stat-value">'+s.messages+'</span></span>';}
function renderAgents(){document.getElementById('agentList').innerHTML=agents.map(a=>'<div class="agent-item'+(selectedAgent===a.id?' active':'')+'" onclick="selectAgent(\''+a.id+'\')">'+'<div class="agent-dot '+a.status+'"></div>'+'<span class="agent-name">'+a.name+'</span>'+'<span class="agent-type">'+a.type+'</span></div>').join('');}
async function selectAgent(id){selectedAgent=id;const agent=agents.find(a=>a.id===id);document.getElementById('chatTitle').textContent=agent?agent.name:id;renderAgents();const r=await fetch(API+'/conversations?agent_id='+id);const d=await r.json();const convs=d.conversations||[];if(convs.length>0){const conv=convs[0];const mr=await fetch(API+'/messages/conversation/'+conv.id+'?limit=50');const md=await mr.json();const msgs=md.messages||[];document.getElementById('messageArea').innerHTML=msgs.map(m=>'<div class="msg"><div class="msg-sender">'+m.sender_id+'</div>'+'<div class="msg-content">'+m.content+'</div>'+'<div class="msg-time">'+m.created_at+'</div></div>').join('')||'<div class="empty-state">No messages yet</div>';}else{document.getElementById('messageArea').innerHTML='<div class="empty-state">No conversations yet</div>';}}
function connectWS(){const proto=location.protocol==='https:'?'wss':'ws';ws=new WebSocket(proto+'://'+location.host+'/ws/dashboard-viewer');ws.onopen=()=>{document.getElementById('connStatus').textContent='Connected ●';document.getElementById('connStatus').style.color='#3fb950'};ws.onmessage=(e)=>{const d=JSON.parse(e.data);if(d.type==='agent_status')loadAgents();if(d.type==='new_message')loadFeed();if(selectedAgent)selectAgent(selectedAgent)};ws.onclose=()=>{document.getElementById('connStatus').textContent='Disconnected ○';document.getElementById('connStatus').style.color='#f85149';setTimeout(connectWS,3000)};}
async function init(){await loadAgents();await loadFeed();await loadStats();connectWS()}
init();setInterval(()=>{loadAgents();loadStats()},30000);
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Agent Messenger Server")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--host", default=None, help="Override host")
    parser.add_argument("--port", type=int, default=None, help="Override port")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    cfg = load_config(args.config)
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]

    import uvicorn
    app = create_app(args.config)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

# Module-level app for uvicorn (production uses main(), this is for dev/direct run)
app = create_app("config.yaml")

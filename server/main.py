"""Agent Messenger Server — FastAPI app with WebSocket + REST + Dashboard."""

import argparse
import json
import logging
from pathlib import Path

import yaml
import uvicorn
from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from server.db import MessengerDB
from server.db_accessor import set_db, get_db
from server.websocket import websocket_handler, manager
from server.routes import agents, conversations, messages

logger = logging.getLogger("agent-messenger")


# ── Config ──

def load_config(config_path: str) -> dict:
    default = {
        "server": {"host": "0.0.0.0", "port": 8096, "cors_origins": ["*"]},
        "database": {"path": "./data/messenger.db"},
        "auth": {"enabled": False, "api_keys": []},
        "dashboard": {"enabled": True},
    }
    if Path(config_path).exists():
        with open(config_path) as f:
            user = yaml.safe_load(f) or {}
        for section, values in user.items():
            if isinstance(values, dict) and section in default:
                default[section].update(values)
            else:
                default[section] = values
    return default


def _check_auth(request: Request, config: dict) -> bool:
    """Return True if request is authorized (or auth is disabled)."""
    if not config.get("auth", {}).get("enabled", False):
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return token in config["auth"].get("api_keys", [])
    return False


def create_app(config: dict) -> FastAPI:
    db = MessengerDB(config["database"]["path"])
    set_db(db)

    app = FastAPI(title="Agent Messenger", version="0.1.0")

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config["server"].get("cors_origins", ["*"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth middleware for REST routes
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # Skip auth for health, dashboard, static, WebSocket
        path = request.url.path
        if path in ("/health", "/", "/dashboard/stats", "/dashboard/feed") or path.startswith("/static") or path.startswith("/ws"):
            return await call_next(request)
        if not _check_auth(request, config):
            return JSONResponse(status_code=401, content={"error": "unauthorized", "detail": "Invalid or missing API key"})
        return await call_next(request)

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": str(exc)})

    # Routes
    app.include_router(agents.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(messages.router, prefix="/api")

    # WebSocket
    @app.websocket("/ws/{agent_id}")
    async def ws_endpoint(websocket: WebSocket, agent_id: str, token: str = None):
        # Auth check for WebSocket
        if config.get("auth", {}).get("enabled", False):
            if not token or token not in config["auth"].get("api_keys", []):
                await websocket.close(code=4001, reason="Unauthorized")
                return
        # Validate agent exists
        existing = db.get_agent(agent_id)
        if not existing:
            # Auto-register on connect
            try:
                db.register_agent(agent_id, agent_id, "detached")
            except Exception as e:
                logger.error("Failed to auto-register agent %s: %s", agent_id, e)
                await websocket.close(code=4002, reason="Registration failed")
                return
        await websocket_handler(websocket, agent_id, db)

    # Dashboard API
    @app.get("/dashboard/stats")
    async def dashboard_stats():
        try:
            return {"status": "ok", "stats": db.stats(), "online_agents": manager.online_agents}
        except Exception as e:
            logger.error("Dashboard stats failed: %s", e)
            return JSONResponse(status_code=500, content={"error": "stats_failed"})

    @app.get("/dashboard/feed")
    async def dashboard_feed(limit: int = 100):
        try:
            return {"status": "ok", "messages": db.global_feed(limit)}
        except Exception as e:
            logger.error("Dashboard feed failed: %s", e)
            return JSONResponse(status_code=500, content={"error": "feed_failed"})

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": "0.1.0",
            "online": manager.online_agents,
            "auth_enabled": config.get("auth", {}).get("enabled", False),
        }

    # Serve dashboard static files
    dashboard_dir = Path(__file__).parent.parent / "dashboard"
    if dashboard_dir.exists():
        app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")

        @app.get("/")
        async def serve_dashboard():
            return FileResponse(str(dashboard_dir / "index.html"))

    return app


def main():
    parser = argparse.ArgumentParser(description="Agent Messenger Server")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    config = load_config(args.config)

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    host = args.host or config["server"]["host"]
    port = args.port or config["server"]["port"]
    app = create_app(config)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

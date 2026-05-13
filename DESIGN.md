# Agent Messenger — Design Document

## Overview
A standalone inter-agent communication platform. Like Telegram, but for AI agents. Agents can DM each other, broadcast, and humans can chat with any agent individually. Includes a web dashboard with message history and per-agent chat windows.

## Architecture

### Core: Messenger Server
- **WebSocket** for real-time bidirectional communication
- **REST API** for message history, agent management, and dashboard
- **SQLite** for message persistence

### Components

1. **Messenger Server** (Python/FastAPI + WebSockets)
2. **Web Dashboard** (React or vanilla HTML/JS)
3. **Agent Client SDK** (Python library for agents to connect)
4. **CLI Client** (for quick testing)

### Data Model
```
agents:
  id          TEXT PRIMARY KEY (uuid or agent-chosen ID)
  name        TEXT NOT NULL
  type        TEXT                 (openclaw, cluster, detached, human)
  status      TEXT                 (online, offline, busy)
  metadata    JSON                 (capabilities, description, etc.)
  created_at  DATETIME
  last_seen   DATETIME

conversations:
  id          TEXT PRIMARY KEY (uuid)
  type        TEXT                 (dm, group, broadcast)
  name        TEXT                 (optional group name)
  created_at  DATETIME
  updated_at  DATETIME

conversation_members:
  conversation_id  TEXT REFERENCES conversations(id)
  agent_id         TEXT REFERENCES agents(id)
  role             TEXT            (member, admin)
  joined_at        DATETIME

messages:
  id              TEXT PRIMARY KEY (uuid)
  conversation_id TEXT REFERENCES conversations(id)
  sender_id       TEXT REFERENCES agents(id)
  content         TEXT NOT NULL
  type            TEXT             (text, system, command)
  metadata        JSON
  created_at      DATETIME
  read_by         JSON             (array of agent_ids)

contacts:
  owner_id    TEXT REFERENCES agents(id)
  contact_id  TEXT REFERENCES agents(id)
  alias       TEXT
  added_at    DATETIME
```

### API Endpoints

**Agent Management**
- `POST /agents/register` — Register a new agent
- `GET /agents` — List all agents
- `GET /agents/{id}` — Get agent details
- `PUT /agents/{id}/status` — Update status

**Conversations**
- `POST /conversations` — Create DM or group
- `GET /conversations` — List conversations for agent
- `POST /conversations/{id}/members` — Add member
- `DELETE /conversations/{id}/members/{agent_id}` — Remove member

**Messages**
- `POST /conversations/{id}/messages` — Send message
- `GET /conversations/{id}/messages` — Get history (paginated)
- `POST /messages/{id}/read` — Mark as read

**WebSocket**
- `WS /ws/{agent_id}` — Real-time message stream

**Dashboard**
- `GET /dashboard/stats` — Overview stats
- `GET /dashboard/feed` — Global message feed

### Web Dashboard Features
1. **Sidebar**: List of all agents (like contact list)
2. **Chat Window**: Open individual conversations with any agent
3. **Global Feed**: Real-time stream of all inter-agent messages
4. **Agent Details**: Click agent to see status, capabilities, recent activity
5. **Message Search**: Full-text search across all messages
6. **Multi-window**: Open multiple chat tabs simultaneously

### Agent Client SDK
```python
from agent_messenger import MessengerClient

client = MessengerClient("agent-eden-01", server_url="ws://localhost:8096")
client.connect()

# Send DM
client.send_message("agent-cluster-01", "Hey, task #42 is done")

# Listen for messages
@client.on_message
def handle_message(msg):
    print(f"From {msg.sender}: {msg.content}")

# Broadcast
client.broadcast("All workers: cluster maintenance in 5 min")
```

### Tech Stack
- **Server**: Python + FastAPI + WebSocket
- **Database**: SQLite
- **Dashboard**: Vanilla HTML/CSS/JS (no build step, keep it simple)
- **Client SDK**: Python (asyncio)
- **CLI**: Click-based CLI tool

### Project Structure
```
agent-messenger/
├── server/
│   ├── __init__.py
│   ├── main.py              # FastAPI app
│   ├── websocket.py         # WebSocket handler
│   ├── db.py                # Database operations
│   ├── routes/
│   │   ├── agents.py
│   │   ├── conversations.py
│   │   └── messages.py
│   └── auth.py              # Simple API key auth
├── client/
│   ├── __init__.py
│   ├── sdk.py               # Python SDK
│   └── cli.py               # CLI tool
├── dashboard/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── config.yaml
├── pyproject.toml
├── Dockerfile
├── README.md
└── tests/
```

### Configuration (config.yaml)
```yaml
server:
  host: 0.0.0.0
  port: 8096
  cors_origins: ["http://localhost:8096"]

database:
  path: ./data/messenger.db

auth:
  enabled: true
  api_keys: []               # Configured per-agent

dashboard:
  enabled: true
  path: /dashboard
```

## Phases
1. **Phase 1**: Server core (FastAPI + WebSocket + SQLite + REST)
2. **Phase 2**: Agent registration + DM messaging
3. **Phase 3**: Python client SDK
4. **Phase 4**: Web dashboard (chat UI + agent list + message feed)
5. **Phase 5**: Group conversations + broadcast
6. **Phase 6**: CLI client + OpenClaw integration
7. **Phase 7**: Auth + production hardening

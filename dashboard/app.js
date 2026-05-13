// Agent Messenger Dashboard — Vanilla JS, no build step

const API = window.location.origin + '/api';
let currentAgent = null;  // Currently selected agent for chat
let humanAgentId = 'human';  // The human user's agent ID
let conversations = {};   // recipient_id -> conversation_id
let ws = null;

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
    loadAgents();
    loadStats();
    setupEventListeners();
    // Auto-refresh
    setInterval(loadAgents, 30000);
    setInterval(loadStats, 15000);
    setInterval(() => {
        if (currentAgent) loadMessages(currentAgent);
        if (document.getElementById('feed-view').classList.contains('active')) loadFeed();
    }, 5000);
});

// ── WebSocket ──
function connectWebSocket() {
    const wsUrl = window.location.origin.replace('http', 'ws') + `/ws/${humanAgentId}`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connected');
        // Register human agent
        fetch(`${API}/agents/register`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: humanAgentId, name: 'You', type: 'human'}),
        });
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'new_message') {
            const msg = data.message;
            if (msg.sender_id === currentAgent || msg.conversation_id === conversations[currentAgent]) {
                appendMessage(msg, 'received');
            }
        } else if (data.type === 'agent_status') {
            loadAgents();
        }
    };

    ws.onclose = () => {
        console.log('WebSocket closed, reconnecting in 3s...');
        setTimeout(connectWebSocket, 3000);
    };
}

// ── API Calls ──
async function loadAgents() {
    try {
        const resp = await fetch(`${API}/agents`);
        const data = await resp.json();
        renderAgents(data.agents || []);
    } catch (e) {
        console.error('Failed to load agents:', e);
    }
}

async function loadStats() {
    try {
        const resp = await fetch(window.location.origin + '/dashboard/stats');
        const data = await resp.json();
        const s = data.stats || {};
        document.getElementById('stats').innerHTML =
            `${s.agents || 0} agents (${s.online || 0} online)<br>` +
            `${s.conversations || 0} conversations<br>` +
            `${s.messages || 0} messages`;
    } catch (e) {
        console.error('Failed to load stats:', e);
    }
}

async function loadConversations() {
    try {
        const resp = await fetch(`${API}/conversations?agent_id=${humanAgentId}`);
        const data = await resp.json();
        conversations = {};
        for (const conv of data.conversations || []) {
            for (const memberId of conv.members || []) {
                if (memberId !== humanAgentId) {
                    conversations[memberId] = conv.id;
                }
            }
        }
    } catch (e) {
        console.error('Failed to load conversations:', e);
    }
}

async function loadMessages(recipientId) {
    if (!conversations[recipientId]) return;
    try {
        const resp = await fetch(`${API}/messages/conversation/${conversations[recipientId]}?limit=100`);
        const data = await resp.json();
        const container = document.getElementById('messages');
        container.innerHTML = '';
        for (const msg of (data.messages || [])) {
            const direction = msg.sender_id === humanAgentId ? 'sent' : 'received';
            appendMessage(msg, direction, false);
        }
        container.scrollTop = container.scrollHeight;
    } catch (e) {
        console.error('Failed to load messages:', e);
    }
}

async function sendMessage(recipientId, content) {
    // Ensure conversation exists
    if (!conversations[recipientId]) {
        try {
            const resp = await fetch(`${API}/conversations`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({type: 'dm', member_ids: [humanAgentId, recipientId]}),
            });
            const data = await resp.json();
            conversations[recipientId] = data.conversation.id;
        } catch (e) {
            console.error('Failed to create conversation:', e);
            return;
        }
    }

    try {
        const resp = await fetch(`${API}/messages`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                conversation_id: conversations[recipientId],
                sender_id: humanAgentId,
                content: content,
            }),
        });
        const data = await resp.json();
        appendMessage(data.message, 'sent');
    } catch (e) {
        console.error('Failed to send message:', e);
    }
}

async function loadFeed() {
    try {
        const resp = await fetch(window.location.origin + '/dashboard/feed?limit=100');
        const data = await resp.json();
        const container = document.getElementById('feed-messages');
        container.innerHTML = '';
        for (const msg of (data.messages || [])) {
            appendMessageToContainer(msg, container);
        }
        container.scrollTop = container.scrollHeight;
    } catch (e) {
        console.error('Failed to load feed:', e);
    }
}

// ── Rendering ──
function renderAgents(agents) {
    const list = document.getElementById('agent-list');
    list.innerHTML = '';
    for (const agent of agents) {
        if (agent.id === humanAgentId) continue;
        const div = document.createElement('div');
        div.className = `agent-item${agent.id === currentAgent ? ' active' : ''}`;
        div.dataset.agentId = agent.id;
        div.innerHTML = `
            <div class="agent-status-dot ${agent.status}"></div>
            <div class="agent-info">
                <div class="agent-name">${escapeHtml(agent.name)}</div>
                <div class="agent-type">${agent.type} · ${agent.status}</div>
            </div>
        `;
        div.addEventListener('click', () => selectAgent(agent.id, agent.name, agent.status));
        list.appendChild(div);
    }
}

function appendMessage(msg, direction, scroll = true) {
    const container = document.getElementById('messages');
    appendMessageToContainer(msg, container, direction);
    if (scroll) container.scrollTop = container.scrollHeight;
}

function appendMessageToContainer(msg, container, direction = null) {
    const dir = direction || (msg.sender_id === humanAgentId ? 'sent' : 'received');
    const div = document.createElement('div');
    div.className = `msg ${dir}`;

    const senderName = msg.sender_name || msg.sender_id || '?';
    const time = msg.created_at ? msg.created_at.substring(11, 19) : '';

    let html = '';
    if (dir === 'received') {
        html += `<div class="msg-sender">${escapeHtml(senderName)}</div>`;
    }
    html += `<div class="msg-content">${escapeHtml(msg.content)}</div>`;
    html += `<div class="msg-time">${time}</div>`;

    div.innerHTML = html;
    container.appendChild(div);
}

function selectAgent(agentId, agentName, status) {
    currentAgent = agentId;
    document.getElementById('chat-recipient').textContent = agentName;
    document.getElementById('chat-status').textContent = status;
    document.getElementById('chat-input').disabled = false;
    document.getElementById('btn-send').disabled = false;

    // Highlight in sidebar
    document.querySelectorAll('.agent-item').forEach(el => {
        el.classList.toggle('active', el.dataset.agentId === agentId);
    });

    loadConversations().then(() => loadMessages(agentId));

    // Switch to chat tab
    switchTab('chat');
}

// ── Event Listeners ──
function setupEventListeners() {
    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    // Send message
    document.getElementById('btn-send').addEventListener('click', handleSend);
    document.getElementById('chat-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    // Refresh agents
    document.getElementById('btn-refresh-agents').addEventListener('click', loadAgents);
}

function handleSend() {
    const input = document.getElementById('chat-input');
    const content = input.value.trim();
    if (!content || !currentAgent) return;

    sendMessage(currentAgent, content);
    input.value = '';
}

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
    document.getElementById(`${tabName}-view`).classList.add('active');

    if (tabName === 'feed') loadFeed();
}

// ── Helpers ──
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

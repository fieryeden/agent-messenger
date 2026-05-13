FROM python:3.11-slim AS base

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml ./

# Install Python deps
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || \
    pip install --no-cache-dir fastapi "uvicorn[standard]" websockets pyyaml click pytest pytest-asyncio httpx

# Copy source
COPY server/ ./server/
COPY client/ ./client/
COPY config.yaml ./
COPY tests/ ./tests/

# Create data directory
RUN mkdir -p /app/data

# Expose REST + WebSocket port
EXPOSE 8096

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8096/health')" || exit 1

# Run
CMD ["python", "-m", "uvicorn", "server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8096"]

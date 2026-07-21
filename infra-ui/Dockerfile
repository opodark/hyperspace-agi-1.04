# ── HyperSpace-AGI Infra-UI Bridge ──────────────────────────────────────────
# Serves dashboard.html + SSE bridge + REST proxy on :8099
# HTTP only — no TLS needed for LAN/internal use
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# curl richiesto dall'healthcheck in docker-compose
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app files
COPY server.py .
COPY dashboard.html .

EXPOSE 8099

# bind 0.0.0.0 → accessibile da tutta la LAN, HTTP puro
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8099", "--workers", "1"]

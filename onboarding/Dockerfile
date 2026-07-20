# ── HyperSpace-AGI Onboarding Wizard ────────────────────────────────────────
# Serve wizard.html + API di setup (.env, pull modelli Ollama, stato mesh) su :8088
# HTTP only — no TLS needed for LAN/internal use
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app files
COPY server.py .
COPY wizard.html .

EXPOSE 8088

# bind 0.0.0.0 → accessibile da tutta la LAN, HTTP puro
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "1"]

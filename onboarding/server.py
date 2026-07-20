from __future__ import annotations

import json
import os
import re
from threading import Lock

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

_HERE = os.path.dirname(__file__)

# In docker-compose questi puntano a /repo/.env(.example) (repo montato come
# volume). In locale (fuori Docker) default alla repo-root due livelli sopra
# questo file, cosi' il server funziona anche senza le env var del container.
ENV_FILE_PATH    = os.getenv("ENV_FILE_PATH",    os.path.join(_HERE, "..", ".env"))
ENV_EXAMPLE_PATH = os.getenv("ENV_EXAMPLE_PATH", os.path.join(_HERE, "..", ".env.example"))
REGISTRY_URL     = os.getenv("REGISTRY_URL", "http://localhost:8086").rstrip("/")
WIZARD_HTML      = os.path.join(_HERE, "wizard.html")

app = FastAPI(title="HyperSpace-AGI Onboarding Wizard")

_env_lock = Lock()

# Whitelist chiusa: mai accettare un dict libero qui, altrimenti l'endpoint
# diventa un file-write arbitrario esposto in LAN.
_ENV_KEYS = [
    "PUBLIC_ENDPOINT",
    "OLLAMA_URL",
    "OLLAMA_MODEL",
    "REGISTRY_PUBLIC_URL",
    "NODE_SPECIALIZATION",
    "NODE_AVATAR",
]


class EnvUpdate(BaseModel):
    PUBLIC_ENDPOINT: str | None = None
    OLLAMA_URL: str | None = None
    OLLAMA_MODEL: str | None = None
    REGISTRY_PUBLIC_URL: str | None = None
    NODE_SPECIALIZATION: str | None = None
    NODE_AVATAR: str | None = None


class PullRequest(BaseModel):
    model: str
    url: str


def _read_env_text() -> str:
    path = ENV_FILE_PATH if os.path.isfile(ENV_FILE_PATH) else ENV_EXAMPLE_PATH
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _setvar(text: str, key: str, val: str) -> str:
    """Replace-or-append di una riga KEY=VALUE. Mirror di setvar() in
    installer/hyperspace-installer.pyw (stessa logica, qui condivisa dal
    wizard web)."""
    pattern = rf"^{re.escape(key)}=.*$"
    replacement = f"{key}={val}"
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n == 0:
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += replacement + "\n"
    return new_text


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WIZARD_HTML)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/env")
def get_env():
    text = _read_env_text()
    values = {}
    for key in _ENV_KEYS:
        m = re.search(rf"^{re.escape(key)}=(.*)$", text, flags=re.MULTILINE)
        values[key] = m.group(1).strip() if m else ""
    return values


@app.post("/api/env")
def update_env(body: EnvUpdate):
    updates = body.model_dump(exclude_none=True)
    with _env_lock:
        if os.path.isfile(ENV_FILE_PATH):
            with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
                text = f.read()
        elif os.path.isfile(ENV_EXAMPLE_PATH):
            with open(ENV_EXAMPLE_PATH, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            text = ""
        for key, val in updates.items():
            text = _setvar(text, key, val)
        with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    return {"ok": True, "written": updates}


@app.get("/api/ollama/tags")
async def ollama_tags(url: str):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url.rstrip('/')}/api/tags")
            r.raise_for_status()
            data = r.json()
            return {"ok": True, "models": [m.get("name") for m in data.get("models", []) if m.get("name")]}
    except Exception as e:
        return {"ok": False, "error": str(e), "models": []}


@app.post("/api/ollama/pull")
async def ollama_pull(body: PullRequest):
    async def _stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST", f"{body.url.rstrip('/')}/api/pull",
                    json={"name": body.model, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n"
        except Exception as e:
            yield json.dumps({"status": "error", "error": str(e)}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@app.get("/api/mesh-status")
async def mesh_status():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{REGISTRY_URL}/nodes/active")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"nodes": [], "ttl_seconds": 0, "error": str(e)}

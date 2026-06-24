# control-plane/main.py
# HyperSpace AGI v1.02 — Control Plane
# feat: /v1/chat/completions OpenAI-compatible endpoint
# feat: tool calling loop — web_search, omega_query, omega_store, get_mesh_status
# feat: memory sync inter-nodo nell'heartbeat + smart task routing (tier/vram/load)
# feat: memory compression — gzip + TTL/max-entries pruning
# feat: OMEGA Obsidian bridge — /health + /mcp JSON-RPC 2.0
# feat: CORS middleware for Open WebUI compatibility
# feat: nodo root locale (Mac) registrato al boot come fallback prioritario
# fix: tool loop robusto — fallback no-tools se modello non supporta function calling
# fix: health check JSON-aware — nodi zombie ngrok marcati unreachable
# fix: _TOOL_HANDLERS definito dopo le funzioni omega (NameError fix)
# fix: DB reload al boot, status recovery, endpoint dedup

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import os, threading, time, requests, json, uuid, gzip, hashlib
from datetime import datetime, timedelta, timezone
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, ".."))

import shared.db as db

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# CONFIG
NODE_ENDPOINTS     = [e.strip() for e in os.getenv("NODE_ENDPOINTS", "node:8084").split(",") if e.strip()]
OLLAMA_URL         = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
DEFAULT_MODEL      = os.getenv("OLLAMA_MODEL", "phi3")
INFERENCE_BACKEND  = os.getenv("INFERENCE_BACKEND", "ollama")
REGISTRY_URL       = os.getenv("REGISTRY_URL", "http://registry:8086")
_AUTHORITY_URL     = os.getenv("AUTHORITY_URL", "http://authority:8080")
_AUTHORITY_ENABLED = os.getenv("AUTHORITY_ENABLED", "false").lower() == "true"
UI_BRIDGE_URL      = os.getenv("UI_BRIDGE_URL", "http://localhost:8099")

MEMORY_FILE_GZ     = os.path.join(BASE_DIR, "memory.json.gz")
MEMORY_TTL_DAYS    = int(os.getenv("MEMORY_TTL_DAYS", "7"))
MEMORY_MAX_ENTRIES = int(os.getenv("MEMORY_MAX_ENTRIES", "200"))

# ── NODO ROOT LOCALE (Mac/host) ──────────────────────────────────────────────────
# LOCAL_NODE_ID: ID stabile del Mac (deriva dall'hostname se non impostato)
# LOCAL_NODE_ENDPOINT: endpoint del nodo locale (il CP stesso espone anche /v1/chat)
# Imposta LOCAL_NODE_ENDPOINT=http://host.docker.internal:8085 nel .env per usare
# il control-plane come nodo fallback locale senza un node container separato.
_LOCAL_NODE_ID       = os.getenv("LOCAL_NODE_ID", "")
_LOCAL_NODE_ENDPOINT = os.getenv("LOCAL_NODE_ENDPOINT", "")  # es. http://host.docker.internal:8085
_LOCAL_NODE_ENABLED  = os.getenv("LOCAL_NODE_ENABLED", "true").lower() == "true"

def _stable_local_id() -> str:
    """Genera un node_id stabile basato su hostname."""
    import socket
    h = socket.gethostname()
    return "local-" + hashlib.sha1(h.encode()).hexdigest()[:16]

# ── TOOL CAPABLE MODELS ────────────────────────────────────────────────────────
_TOOL_CAPABLE_OVERRIDE = os.getenv("TOOL_CAPABLE_MODELS", "")
_TOOL_CAPABLE_PATTERNS = [
    "qwen3", "qwen2.5", "llama3.1", "llama3.2", "llama3.3",
    "mistral-nemo", "mistral-small", "mixtral",
    "command-r", "firefunction", "functionary",
    "hermes", "nexusraven", "gorilla",
    "phi4",
]

def _model_supports_tools(model_name: str) -> bool:
    if _TOOL_CAPABLE_OVERRIDE == "*":
        return True
    if _TOOL_CAPABLE_OVERRIDE:
        for p in _TOOL_CAPABLE_OVERRIDE.split(","):
            if p.strip().lower() in model_name.lower():
                return True
    m = model_name.lower().split(":")[0]
    return any(p in m for p in _TOOL_CAPABLE_PATTERNS)

tasks: dict = {}
_nodes_by_id: dict  = {}
_known_endpoints: set = set()
_synced_memory_keys: set = set()

hb_state = {
    "cycle": 0, "last_tick": None, "last_conn": None,
    "last_memory_sync": None,
    "nodes_seen": [], "running": False,
}

advanced_config = {
    "ollama":     {"url": OLLAMA_URL, "defaultModel": DEFAULT_MODEL},
    "mesh":       {"nodeEndpoints": NODE_ENDPOINTS, "heartbeatEvery": 15},
    "_authority": {"serverUrl": _AUTHORITY_URL, "enabled": _AUTHORITY_ENABLED},
    "security":   {"sharedSecret": "", "secretRotatedAt": None},
}

db.init_db()

# ── HELPERS ───────────────────────────────────────────────────────────────────
def _normalize_endpoint(ep: str) -> str:
    ep = ep.strip().rstrip("/")
    if not ep:
        return ep
    if ep.startswith("http://") or ep.startswith("https://"):
        return ep
    return f"http://{ep}"

def _ep_to_url(ep: str) -> str:
    return _normalize_endpoint(ep)

def _best_endpoint(node_info):
    ep = _normalize_endpoint(node_info.get("endpoint", ""))
    if ep.startswith("https://"): return ep
    public = _normalize_endpoint(node_info.get("public_endpoint", ""))
    if public and public.startswith("https://"): return public
    return ep

def _node_list():
    return list(_nodes_by_id.values())

def _load_nodes_from_db():
    nodes = db.get_all_nodes()
    for n in nodes:
        nid = n.get("node_id", "")
        ep  = _normalize_endpoint(n.get("endpoint", ""))
        if not nid:
            continue
        n["endpoint"] = ep
        _nodes_by_id[nid] = n
        if ep:
            _known_endpoints.add(ep)
    for ep in NODE_ENDPOINTS:
        _known_endpoints.add(_normalize_endpoint(ep))
    print(f"[CP] Loaded {len(_nodes_by_id)} nodes from DB, {len(_known_endpoints)} known endpoints")

def _db_row_to_task(row: dict) -> dict:
    return {
        "id":           row.get("task_id", row.get("id", "")),
        "status":       row.get("status", "created"),
        "node":         row.get("node_id") or None,
        "endpoint":     row.get("endpoint", ""),
        "created_at":   row.get("created_at", ""),
        "completed_at": row.get("completed_at") or None,
        "error":        row.get("error") or None,
        "result":       _try_parse_json(row.get("result", "")),
        "payload":      {"prompt": row.get("prompt", ""), "model": row.get("model", "")},
        "_from_db":     True,
    }

def _try_parse_json(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s

def _load_tasks_from_db():
    rows = db.get_all_tasks()
    loaded = 0
    for row in rows:
        tid = row.get("task_id", "")
        if not tid or tid in tasks:
            continue
        tasks[tid] = _db_row_to_task(row)
        loaded += 1
    print(f"[CP] Loaded {loaded} tasks from DB")

def _ts_sort_key(entry: dict) -> float:
    ts = entry.get("ts") or entry.get("timestamp")
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def _ts_to_iso(ts) -> str:
    if ts is 
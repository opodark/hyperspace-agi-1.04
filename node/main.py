# node/main.py
# HyperSpace AGI v0.2 — Unified Node
#
# Ogni container usa questa stessa immagine.
# Il tier (root/hub/leaf) viene calcolato al boot in base
# alle capabilities e alle risorse disponibili — non è hardcodato.
# Nessuna authority esterna: ogni nodo mantiene un peer registry locale
# e si sincronizza via /peers con gli altri nodi della rete.

from fastapi import FastAPI
import asyncio
import httpx
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.identity import generate_or_load_identity, sign_message, verify_message

app = FastAPI()

# ──────────────────────────────────────────────
# IDENTITÀ CRITTOGRAFICA
# ──────────────────────────────────────────────
_identity    = generate_or_load_identity()
NODE_ID      = _identity["node_id"]
NODE_PUBKEY  = _identity["public_key"]
_private_key = _identity["_private_key"]

# ──────────────────────────────────────────────
# CONFIG (da env — nessun valore critico hardcodato)
# ──────────────────────────────────────────────
NODE_HOSTNAME    = os.getenv("NODE_HOSTNAME", "localhost")
NODE_PORT        = int(os.getenv("NODE_PORT", 8084))
OLLAMA_URL       = os.getenv("OLLAMA_URL", "http://ollama:11434")
DEFAULT_MODEL    = os.getenv("OLLAMA_MODEL", "phi3")
HEARTBEAT_EVERY  = int(os.getenv("HEARTBEAT_EVERY", 15))
# Lista peer iniziali separati da virgola, es: "node-1:8084,node-2:8084"
BOOT_PEERS       = [p.strip() for p in os.getenv("BOOT_PEERS", "").split(",") if p.strip()]

_boot_time = time.time()

# ──────────────────────────────────────────────
# TIER CALCULATION
# Nessun tier hardcodato: calcolato da capabilities + risorse
# ──────────────────────────────────────────────

def detect_vram_gb() -> float:
    """Rileva VRAM disponibile. Restituisce 0.0 se non disponibile o non rilevabile."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            timeout=3, stderr=subprocess.DEVNULL
        ).decode().strip()
        return round(float(out.split("\n")[0]) / 1024, 1)
    except Exception:
        return 0.0


def calculate_tier(vram_gb: float, uptime_s: float, reputation: float = 0.5) -> str:
    root_score = (
        min(uptime_s / 604800, 1.0) * 25 +
        0.5 * 35 +           # reachability placeholder — Fase 2
        reputation  * 40
    )
    if root_score >= 85.0:
        return "root"
    if vram_gb >= 4.0:
        return "hub"
    return "leaf"


VRAM_GB = detect_vram_gb()
NODE_CAPABILITIES = ["execute"]
if VRAM_GB > 0 or os.getenv("OLLAMA_URL"):
    NODE_CAPABILITIES.append("ollama")

NODE_PROFILE = {
    "node_id":      NODE_ID,
    "pubkey":       NODE_PUBKEY,
    "tier":         calculate_tier(VRAM_GB, 0),
    "endpoint":     f"{NODE_HOSTNAME}:{NODE_PORT}",
    "capabilities": NODE_CAPABILITIES,
    "vram_gb":      VRAM_GB,
    "version":      "0.2.0",
}

# ──────────────────────────────────────────────
# PEER REGISTRY LOCALE
# Niente authority centrale: ogni nodo conosce solo i suoi peer
# ──────────────────────────────────────────────
_peers: dict = {}   # node_id -> peer_info


def register_peer(info: dict):
    nid = info.get("node_id")
    if nid and nid != NODE_ID:
        _peers[nid] = {
            **info,
            "last_seen": time.time(),
            "status":    "active",
        }


def prune_stale_peers(max_age_s: int = 60):
    now = time.time()
    stale = [nid for nid, p in _peers.items() if now - p["last_seen"] > max_age_s]
    for nid in stale:
        _peers[nid]["status"] = "stale"


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def build_signed_payload(data: dict) -> dict:
    payload = {**data, "pubkey": NODE_PUBKEY, "node_id": NODE_ID}
    return sign_message(payload, _private_key)


async def ollama_generate(prompt: str, model: str = DEFAULT_MODEL) -> str:
    payload = {"model": model, "prompt": prompt, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            return r.json().get("response", "").strip()
    except Exception as e:
        return f"[OLLAMA ERROR] {e}"


async def ollama_health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return {"ok": True, "models": [m["name"] for m in r.json().get("models", [])]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# PEER DISCOVERY & HEARTBEAT
# ──────────────────────────────────────────────

async def announce_to_peer(endpoint: str):
    """Invia NODE_ANNOUNCE a un peer e riceve la sua peer list (PEX)."""
    try:
        payload = build_signed_payload({
            "type":         "NODE_ANNOUNCE",
            "endpoint":     NODE_PROFILE["endpoint"],
            "tier":         NODE_PROFILE["tier"],
            "capabilities": NODE_PROFILE["capabilities"],
            "version":      NODE_PROFILE["version"],
            "timestamp":    time.time(),
        })
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"http://{endpoint}/announce", json=payload)
            if r.status_code == 200:
                data = r.json()
                # PEX: registra i peer che il peer conosce
                for peer in data.get("peers", []):
                    register_peer(peer)
                register_peer({
                    "node_id":      data.get("node_id"),
                    "pubkey":       data.get("pubkey", ""),
                    "endpoint":     endpoint,
                    "tier":         data.get("tier", "leaf"),
                    "capabilities": data.get("capabilities", []),
                })
                print(f"[NODE:{NODE_ID[:10]}] announce ok → {endpoint}")
    except Exception as e:
        print(f"[NODE:{NODE_ID[:10]}] announce failed → {endpoint}: {e}")


def heartbeat_loop():
    """Boot: announce a tutti i BOOT_PEERS. Poi heartbeat periodico."""
    # Attende qualche secondo per lasciar partire gli altri container
    time.sleep(5)

    async def _boot():
        for peer_endpoint in BOOT_PEERS:
            await announce_to_peer(peer_endpoint)

    asyncio.run(_boot())

    while True:
        time.sleep(HEARTBEAT_EVERY)
        NODE_PROFILE["tier"] = calculate_tier(
            VRAM_GB,
            time.time() - _boot_time
        )
        prune_stale_peers()

        async def _hb():
            active = [p for p in _peers.values() if p["status"] == "active"]
            for peer in active:
                await announce_to_peer(peer["endpoint"])

        asyncio.run(_hb())


# ──────────────────────────────────────────────
# STARTUP
# ──────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    print(f"[NODE:{NODE_ID[:10]}] started — tier={NODE_PROFILE['tier']} — endpoint={NODE_PROFILE['endpoint']}")


# ──────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "node_id": NODE_ID, "tier": NODE_PROFILE["tier"]}


@app.get("/identity")
def get_identity():
    return {
        "node_id":      NODE_ID,
        "public_key":   NODE_PUBKEY,
        "tier":         NODE_PROFILE["tier"],
        "version":      NODE_PROFILE["version"],
        "capabilities": NODE_PROFILE["capabilities"],
        "endpoint":     NODE_PROFILE["endpoint"],
        "vram_gb":      VRAM_GB,
    }


@app.get("/status")
def status():
    prune_stale_peers()
    return {
        "node_id":      NODE_ID,
        "public_key":   NODE_PUBKEY,
        "tier":         NODE_PROFILE["tier"],
        "version":      NODE_PROFILE["version"],
        "endpoint":     NODE_PROFILE["endpoint"],
        "capabilities": NODE_PROFILE["capabilities"],
        "vram_gb":      VRAM_GB,
        "uptime_s":     int(time.time() - _boot_time),
        "peers_active": len([p for p in _peers.values() if p["status"] == "active"]),
        "peers_total":  len(_peers),
        "running":      True,
    }


@app.get("/peers")
def get_peers():
    """Restituisce la peer list locale — usato per PEX."""
    prune_stale_peers()
    return {
        "node_id": NODE_ID,
        "pubkey":  NODE_PUBKEY,
        "tier":    NODE_PROFILE["tier"],
        "peers": [
            {
                "node_id":      p["node_id"],
                "endpoint":     p["endpoint"],
                "tier":         p.get("tier", "leaf"),
                "capabilities": p.get("capabilities", []),
                "status":       p["status"],
            }
            for p in _peers.values()
        ]
    }


@app.post("/announce")
async def announce(message: dict):
    """Riceve NODE_ANNOUNCE da un peer, verifica firma, registra peer, risponde con peer list."""
    valid = verify_message(message)
    if not valid:
        return {"error": "invalid signature", "accepted": False}

    register_peer({
        "node_id":      message.get("node_id"),
        "pubkey":       message.get("pubkey", ""),
        "endpoint":     message.get("endpoint", ""),
        "tier":         message.get("tier", "leaf"),
        "capabilities": message.get("capabilities", []),
    })

    print(f"[NODE:{NODE_ID[:10]}] accepted announce from {message.get('node_id', '')[:10]}")

    # Risponde con la propria peer list (PEX)
    prune_stale_peers()
    return {
        "accepted":     True,
        "node_id":      NODE_ID,
        "pubkey":       NODE_PUBKEY,
        "tier":         NODE_PROFILE["tier"],
        "capabilities": NODE_PROFILE["capabilities"],
        "peers": [
            {
                "node_id":  p["node_id"],
                "endpoint": p["endpoint"],
                "tier":     p.get("tier", "leaf"),
            }
            for p in _peers.values() if p["status"] == "active"
        ]
    }


@app.post("/verify")
async def verify_incoming(message: dict):
    """Verifica la firma ECDSA di un messaggio ricevuto."""
    valid = verify_message(message)
    return {"valid": valid, "node_id": message.get("node_id")}


@app.post("/execute")
async def execute_task(task: dict):
    task_id = task.get("task_id", "unknown")
    prompt  = task.get("prompt") or task.get("payload", {}).get("prompt") or f"Esegui task: {task_id}"
    model   = task.get("model") or task.get("payload", {}).get("model") or DEFAULT_MODEL
    print(f"[NODE:{NODE_ID[:10]}] execute task={task_id} model={model}")
    response_text = await ollama_generate(prompt, model)
    return {
        "node_id":  NODE_ID,
        "task_id":  task_id,
        "status":   "done",
        "model":    model,
        "response": response_text,
    }


@app.get("/ollama/health")
async def check_ollama():
    return await ollama_health()


@app.get("/ollama/models")
async def list_models():
    h = await ollama_health()
    return {"models": h.get("models", [])} if h["ok"] else {"error": h.get("error")}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=NODE_PORT)

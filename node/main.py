# node/main.py
# HyperSpace AGI v1.03 — Unified Node
# v1.02: middleware firma inter-nodo, /ollama/pull SSE, ollama-proxy, /memory
# v1.03: aggiunto /v1/chat/completions — il control-plane instrada qui i
#        chat completions quando sceglie questo nodo (invece di ollama-direct
#        o di /execute). Prima mancava del tutto: la richiesta cadeva sempre
#        su un 404, mascherato finché il nodo veniva scartato dallo scoring
#        del CP (bug già corretto lato control-plane).
# v1.04: semaforo di concorrenza + timeout di coda su /execute e
#        /v1/chat/completions (stream e non-stream). Chi arriva quando il
#        nodo è saturo aspetta al massimo REQUEST_QUEUE_TIMEOUT_S, poi
#        riceve un 503 esplicito (type=node_busy_timeout) invece di restare
#        appeso indefinitamente. /status ora espone active_requests/
#        queued_requests/max_concurrent così il control-plane può pesare il
#        carico reale nello scoring, non solo tier/vram/uptime statici.
# fix: heartbeat try/except+retry, endpoint normalizzato, peer TTL configurabile
# fix: /execute accetta campo 'task', salva in memory.jsonl, propaga al CP

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import asyncio
import httpx
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.identity import (
    generate_or_load_identity,
    sign_message,
    verify_message,
    make_request_headers,
    verify_request_headers,
)
from shared.engine_profiles import estimate_default_load_units

app = FastAPI()

# ── IDENTITA' ──────────────────────────────────────────────
_identity    = generate_or_load_identity()
NODE_ID      = _identity["node_id"]
NODE_PUBKEY  = _identity["public_key"]
_private_key = _identity["_private_key"]

# ── CONFIG ───────────────────────────────────────────────
NODE_HOSTNAME        = os.getenv("NODE_HOSTNAME", "localhost")
NODE_PORT            = int(os.getenv("NODE_PORT", 8084))
OLLAMA_URL           = os.getenv("OLLAMA_URL", "http://ollama:11434")
# ollama-proxy gira nello stesso container (vedi node/Dockerfile, entrambi
# avviati dallo stesso CMD) sulla porta 11435. /v1/chat/completions instrada
# qui, non direttamente a OLLAMA_URL, cosi' ogni conversazione passa dalla
# strumentazione di ollama-proxy (log su control-plane, memoria condivisa,
# propagazione ai peer) — la stessa che oggi copre gia' /api/generate e
# /api/chat quando qualcuno chiama ollama-proxy direttamente.
OLLAMA_PROXY_URL     = os.getenv("OLLAMA_PROXY_URL", "http://localhost:11435")
DEFAULT_MODEL        = os.getenv("OLLAMA_MODEL", "")
HEARTBEAT_EVERY      = int(os.getenv("HEARTBEAT_EVERY", 15))
PUBLIC_ENDPOINT      = os.getenv("PUBLIC_ENDPOINT", "").strip().rstrip("/")
BOOT_PEERS           = [p.strip().rstrip("/") for p in os.getenv("BOOT_PEERS", "").split(",") if p.strip()]
CONTROL_PLANE_URL    = os.getenv("CONTROL_PLANE_URL", "").strip().rstrip("/")
REGISTRY_URL         = os.getenv("REGISTRY_URL", "http://registry:8086").strip().rstrip("/")
REGISTRY_PUBLIC_URL  = os.getenv("REGISTRY_PUBLIC_URL", "").strip().rstrip("/")
SIGN_REQUESTS        = os.getenv("SIGN_REQUESTS", "true").lower() == "true"
_FORCED_TIER         = os.getenv("NODE_TIER", "").strip().lower()
NODE_SPECIALIZATION  = os.getenv("NODE_SPECIALIZATION", "generalist").strip()
NODE_AVATAR          = os.getenv("NODE_AVATAR", "🤖").strip()

PEER_MAX_AGE_S       = int(os.getenv("PEER_MAX_AGE_S", "120"))

# ── GESTIONE CARICO / CODA — per UNITÀ DI CARICO, non per richiesta ────────
# Prima: ogni richiesta occupava 1 "slot" a prescindere dal modello, quindi
# un task su un modello 0.5B e uno su un 70B pesavano uguale sul semaforo —
# un nodo poteva accettare N richieste "leggere" in parallelo quanto N
# richieste "pesanti", saturando la VRAM molto prima del limite nominale.
# Ora ogni richiesta dichiara un peso in "unità di carico" stimato dal nome
# del modello, e il nodo accetta lavoro finché la somma dei pesi attivi non
# supera MAX_LOAD_UNITS.
#
# MAX_LOAD_UNITS          : capacità totale in unità di carico. Se non
#                           impostata esplicitamente, viene AUTO-DERIVATA da
#                           VRAM_GB + INFERENCE_BACKEND (stesso pattern di
#                           calculate_tier()/NODE_TIER) — vedi la risoluzione
#                           effettiva più sotto, dopo il rilevamento VRAM,
#                           e shared/engine_profiles.py per la formula.
# REQUEST_QUEUE_TIMEOUT_S : secondi massimi che una richiesta aspetta prima
#                           di ricevere un 503 esplicito.
REQUEST_QUEUE_TIMEOUT_S = int(os.getenv("REQUEST_QUEUE_TIMEOUT_S", 60))

# ── MOTORE D'INFERENZA ──────────────────────────────────────────────────
# Usato SOLO dal control-plane per pesare lo scoring di routing (vedi
# ROUTING_WEIGHT_ENGINE lato CP) — NON influenza calculate_tier(): tier
# resta legato a topologia/affidabilità del nodo, non alla capacità di
# serving del motore.
INFERENCE_BACKEND = os.getenv("INFERENCE_BACKEND", "ollama").strip().lower()

# ── PESO DI CARICO PER MODELLO ──────────────────────────────────────────
# Override esplicito via NODE_MODEL_WEIGHTS (JSON {"sottostringa": peso},
# case-insensitive, match per sottostringa sul nome modello) per i casi in
# cui l'euristica sui parametri non è affidabile (modelli custom, GGUF senza
# convenzione di naming standard).
_MODEL_WEIGHT_OVERRIDES: dict = {}
try:
    _MODEL_WEIGHT_OVERRIDES = {
        k.lower(): float(v) for k, v in json.loads(os.getenv("NODE_MODEL_WEIGHTS", "{}")).items()
    }
except Exception:
    _MODEL_WEIGHT_OVERRIDES = {}

DEFAULT_MODEL_WEIGHT = float(os.getenv("DEFAULT_MODEL_WEIGHT", "1.0"))
_PARAM_SIZE_RE = re.compile(r'(\d+(?:\.\d+)?)\s*b\b', re.IGNORECASE)

def estimate_model_load_units(model_name: str) -> float:
    """Stima il peso in unità di carico di un modello dal suo nome. 1 unità
    ~= un modello nella fascia 7B (riferimento comodo, non una misura
    fisica). Ordine: override esplicito per sottostringa -> parsing del
    suffisso parametri (es. '14b', '0.5b') -> default."""
    if not model_name:
        return DEFAULT_MODEL_WEIGHT
    name_l = model_name.lower()
    for substr, weight in _MODEL_WEIGHT_OVERRIDES.items():
        if substr in name_l:
            return weight
    m = _PARAM_SIZE_RE.search(name_l)
    if m:
        try:
            params_b = float(m.group(1))
            return max(round(params_b / 7.0, 2), 0.25)
        except Exception:
            pass
    return DEFAULT_MODEL_WEIGHT

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

_boot_time = time.time()

class _WeightedLoadLimiter:
    """Sostituisce asyncio.Semaphore: invece di N slot fissi, gestisce un
    budget di unità di carico. Una richiesta su un modello piccolo può
    girare in parallelo con altre; una su un modello grande occupa da sola
    una fetta più larga del budget. acquire() aspetta (con timeout) finché
    non c'è abbastanza capacità libera. Una richiesta più pesante della
    capacità totale non resta bloccata per sempre: parte comunque quando il
    nodo è idle (_active == 0), altrimenti aspetterebbe un budget che non
    esisterà mai."""
    def __init__(self, capacity: float):
        self.capacity = capacity
        self._active = 0.0
        self._queued_units = 0.0
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)

    async def acquire(self, weight: float, timeout_s: float) -> bool:
        async with self._lock:
            self._queued_units += weight
            try:
                def _capacity_free():
                    return self._active + weight <= self.capacity or self._active == 0.0
                try:
                    await asyncio.wait_for(self._cond.wait_for(_capacity_free), timeout=timeout_s)
                except asyncio.TimeoutError:
                    return False
            finally:
                self._queued_units -= weight
            self._active += weight
            return True

    async def release(self, weight: float):
        async with self._lock:
            self._active = max(0.0, self._active - weight)
            self._cond.notify_all()

    @property
    def active_units(self) -> float:
        return round(self._active, 2)

    @property
    def queued_units(self) -> float:
        return round(self._queued_units, 2)


async def _try_acquire_slot(weight: float = 1.0, timeout_s: float = None) -> bool:
    """True se le unità sono state acquisite (il chiamante DEVE poi chiamare
    _release_slot(weight) con lo STESSO peso); False se il timeout è scaduto
    — in quel caso nessuna unità è stata presa e non va rilasciato nulla."""
    timeout_s = REQUEST_QUEUE_TIMEOUT_S if timeout_s is None else timeout_s
    return await _load_limiter.acquire(weight, timeout_s)


async def _release_slot(weight: float = 1.0):
    await _load_limiter.release(weight)


def _busy_response():
    return Response(
        content=json.dumps({"error": {
            "message": f"nodo saturo: coda oltre {REQUEST_QUEUE_TIMEOUT_S}s",
            "type": "node_busy_timeout",
        }}),
        status_code=503, media_type="application/json",
    )

# ── TIER ──────────────────────────────────────────────────
def detect_vram_gb() -> float:
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
    if _FORCED_TIER in ("hub", "root", "leaf"):
        return _FORCED_TIER
    root_score = min(uptime_s / 604800, 1.0) * 25 + 0.5 * 35 + reputation * 40
    if root_score >= 85.0: return "root"
    if vram_gb >= 4.0:     return "hub"
    return "leaf"

_vram_env      = float(os.getenv("VRAM_GB", "0.0"))
_vram_detected = detect_vram_gb()
VRAM_GB        = _vram_env if _vram_env > 0.0 else _vram_detected

# Risoluzione di MAX_LOAD_UNITS: se impostata esplicitamente nel .env vince
# quella; altrimenti auto-derivata da VRAM_GB + INFERENCE_BACKEND (stesso
# pattern di calculate_tier()/NODE_TIER — auto salvo override esplicito).
# Va fatta qui, non più in alto: dipende da VRAM_GB appena rilevata sopra.
_max_load_units_env = os.getenv("MAX_LOAD_UNITS", "").strip()
MAX_LOAD_UNITS = (
    float(_max_load_units_env) if _max_load_units_env
    else estimate_default_load_units(VRAM_GB, INFERENCE_BACKEND)
)
_load_limiter = _WeightedLoadLimiter(MAX_LOAD_UNITS)

NODE_CAPABILITIES = ["execute"]
if VRAM_GB > 0 or os.getenv("OLLAMA_URL"):
    NODE_CAPABILITIES.append("ollama")
NODE_CAPABILITIES.append("ollama-proxy")
NODE_CAPABILITIES.append("v1-chat-completions")

def _normalize_endpoint(ep: str) -> str:
    ep = ep.strip().rstrip("/")
    if not ep:
        return ep
    if ep.startswith("http://") or ep.startswith("https://"):
        return ep
    return f"http://{ep}"

_raw_local = f"{NODE_HOSTNAME}:{NODE_PORT}"
if PUBLIC_ENDPOINT:
    NODE_ADVERTISED_ENDPOINT = _normalize_endpoint(PUBLIC_ENDPOINT)
else:
    NODE_ADVERTISED_ENDPOINT = _normalize_endpoint(_raw_local)

NODE_PROFILE = {
    "node_id":      NODE_ID,
    "pubkey":       NODE_PUBKEY,
    "tier":         calculate_tier(VRAM_GB, 0),
    "endpoint":     NODE_ADVERTISED_ENDPOINT,
    "capabilities": NODE_CAPABILITIES,
    "vram_gb":      VRAM_GB,
    "version":      "1.04.0",
    "specialization": NODE_SPECIALIZATION,
    "avatar":         NODE_AVATAR,
}

# ── PEER REGISTRY ─────────────────────────────────────────
_peers: dict = {}

def register_peer(info: dict):
    nid = info.get("node_id")
    ep  = _normalize_endpoint(info.get("endpoint", ""))
    if nid and nid != NODE_ID:
        _peers[nid] = {**info, "endpoint": ep, "last_seen": time.time(), "status": "active"}

def prune_stale_peers(max_age_s: int = None):
    age = max_age_s if max_age_s is not None else PEER_MAX_AGE_S
    now = time.time()
    for nid, p in list(_peers.items()):
        if now - p["last_seen"] > age:
            _peers[nid]["status"] = "stale"

# ── MEMORIA ────────────────────────────────────────────────
import json as _json
from pathlib import Path

_MEMORY_FILE = Path(DATA_DIR) / "memory.jsonl"

def _read_memory(limit: int = 50) -> list:
    if not _MEMORY_FILE.exists():
        return []
    lines = _MEMORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    return [_json.loads(l) for l in lines[-limit:] if l.strip()]

def _save_memory(entry: dict):
    with _MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(_json.dumps(entry, ensure_ascii=False) + "\n")

async def _push_memory_to_cp(entry: dict):
    if not CONTROL_PLANE_URL:
        return
    try:
        payload = {"node_id": NODE_ID, "entry": entry}
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(f"{CONTROL_PLANE_URL}/memory/push", json=payload)
    except Exception as e:
        print(f"[NODE:{NODE_ID[:10]}] memory push to CP failed: {e}")

# ── HELPERS ───────────────────────────────────────────────
def build_signed_payload(data: dict) -> dict:
    payload = {**data, "pubkey": NODE_PUBKEY, "node_id": NODE_ID}
    return sign_message(payload, _private_key)

def peer_to_url(endpoint: str) -> str:
    return _normalize_endpoint(endpoint)

def _signed_headers(body: bytes = b"") -> dict:
    if not SIGN_REQUESTS:
        return {}
    return make_request_headers(NODE_ID, NODE_PUBKEY, _private_key, body)

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

# ── REGISTRAZIONE ─────────────────────────────────────────
async def register_to_registry():
    active_u, queued_u = _load_limiter.active_units, _load_limiter.queued_units
    payload = {
        "node_id":        NODE_ID,
        "public_address": NODE_ADVERTISED_ENDPOINT,
        "role":           NODE_PROFILE["tier"],
        "metadata": {
            "version":      NODE_PROFILE["version"],
            "tier":         NODE_PROFILE["tier"],
            "capabilities": ",".join(NODE_CAPABILITIES),
            "vram_gb":      str(VRAM_GB),
            "uptime_s":     str(int(time.time() - _boot_time)),
            "public_key":   NODE_PUBKEY[:32],
            "specialization": NODE_PROFILE["specialization"],
            "avatar":         NODE_PROFILE["avatar"],
            "engine":         INFERENCE_BACKEND,
            "active_load":      str(active_u),
            "queued_load":      str(queued_u),
            "max_load_units":   str(MAX_LOAD_UNITS),
            "active_requests":  str(active_u),
            "queued_requests":  str(queued_u),
            "max_concurrent":   str(MAX_LOAD_UNITS),
        }
    }
    body = str(payload).encode()
    hdrs = _signed_headers(body)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{REGISTRY_URL}/register", json=payload, headers=hdrs)
            if r.status_code in (200, 201):
                print(f"[NODE:{NODE_ID[:10]}] registered to registry OK")
            else:
                print(f"[NODE:{NODE_ID[:10]}] registry /register HTTP {r.status_code}")
    except Exception as e:
        print(f"[NODE:{NODE_ID[:10]}] registry /register failed: {e}")

async def register_to_control_plane():
    if not CONTROL_PLANE_URL:
        return
    payload = {
        "node_id":      NODE_ID,
        "public_key":   NODE_PUBKEY,
        "tier":         NODE_PROFILE["tier"],
        "endpoint":     NODE_ADVERTISED_ENDPOINT,
        "capabilities": NODE_PROFILE["capabilities"],
        "vram_gb":      VRAM_GB,
        "version":      NODE_PROFILE["version"],
        "uptime_s":     int(time.time() - _boot_time),
        "peers_active": len([p for p in _peers.values() if p["status"] == "active"]),
    }
    body = str(payload).encode()
    hdrs = _signed_headers(body)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{CONTROL_PLANE_URL}/mesh/announce", json=payload, headers=hdrs)
            if r.status_code == 200:
                print(f"[NODE:{NODE_ID[:10]}] registered to control-plane OK")
            else:
                print(f"[NODE:{NODE_ID[:10]}] control-plane announce HTTP {r.status_code}")
    except Exception as e:
        print(f"[NODE:{NODE_ID[:10]}] control-plane announce failed: {e}")


# ── AUTO-DISCOVERY DAL REGISTRY ──────────────────────────
async def _discover_peers_from_registry():
    urls_to_try = []
    if REGISTRY_URL:
        urls_to_try.append(REGISTRY_URL)
    if REGISTRY_PUBLIC_URL and REGISTRY_PUBLIC_URL != REGISTRY_URL:
        urls_to_try.append(REGISTRY_PUBLIC_URL)

    discovered = []
    for base_url in urls_to_try:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{base_url}/nodes/active")
                if r.status_code == 200:
                    data = r.json()
                    for n in data.get("nodes", []):
                        ep = _normalize_endpoint(n.get("public_address", ""))
                        nid = n.get("node_id", "")
                        if ep and nid and nid != NODE_ID:
                            discovered.append(ep)
                    if discovered:
                        print(f"[NODE:{NODE_ID[:10]}] auto-discovery: found {len(discovered)} peers from {base_url}")
                        break
        except Exception as e:
            print(f"[NODE:{NODE_ID[:10]}] auto-discovery failed from {base_url}: {e}")

    for ep in discovered:
        try:
            await announce_to_peer(ep)
            print(f"[NODE:{NODE_ID[:10]}] auto-discovery: announced to {ep}")
        except Exception as e:
            print(f"[NODE:{NODE_ID[:10]}] auto-discovery announce failed -> {ep}: {e}")


# ── PEER DISCOVERY ────────────────────────────────────────
async def announce_to_peer(endpoint: str):
    base_url = peer_to_url(endpoint)
    try:
        payload = build_signed_payload({
            "type":         "NODE_ANNOUNCE",
            "endpoint":     NODE_ADVERTISED_ENDPOINT,
            "tier":         NODE_PROFILE["tier"],
            "capabilities": NODE_PROFILE["capabilities"],
            "version":      NODE_PROFILE["version"],
            "timestamp":    time.time(),
        })
        import json as _j
        body = _j.dumps(payload, sort_keys=True).encode()
        hdrs = _signed_headers(body)
        hdrs["Content-Type"] = "application/json"
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{base_url}/announce", data=body, headers=hdrs)
            if r.status_code == 200:
                data = r.json()
                for peer in data.get("peers", []):
                    register_peer(peer)
                register_peer({
                    "node_id":      data.get("node_id"),
                    "pubkey":       data.get("pubkey", ""),
                    "endpoint":     endpoint,
                    "tier":         data.get("tier", "leaf"),
                    "capabilities": data.get("capabilities", []),
                })
    except Exception as e:
        print(f"[NODE:{NODE_ID[:10]}] announce failed -> {endpoint}: {e}")

# ── HEARTBEAT ─────────────────────────────────────────────
def _run_async_safe(coro_fn, label: str, *args, **kwargs):
    try:
        asyncio.run(coro_fn(*args, **kwargs))
    except Exception as e:
        print(f"[NODE:{NODE_ID[:10]}] [{label}] ERROR: {e}")

def heartbeat_loop():
    time.sleep(5)

    async def _boot():
        for peer_endpoint in BOOT_PEERS:
            try:
                await announce_to_peer(peer_endpoint)
            except Exception as e:
                print(f"[NODE:{NODE_ID[:10]}] boot announce failed -> {peer_endpoint}: {e}")
        await register_to_registry()
        await register_to_control_plane()
        if not BOOT_PEERS:
            await _discover_peers_from_registry()

    boot_ok = False
    for attempt in range(1, 4):
        try:
            asyncio.run(_boot())
            boot_ok = True
            print(f"[NODE:{NODE_ID[:10]}] boot registration OK (attempt {attempt})")
            break
        except Exception as e:
            print(f"[NODE:{NODE_ID[:10]}] boot attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(5)

    if not boot_ok:
        print(f"[NODE:{NODE_ID[:10]}] WARNING: boot registration failed after 3 attempts — will retry in heartbeat")

    while True:
        time.sleep(HEARTBEAT_EVERY)
        NODE_PROFILE["tier"] = calculate_tier(VRAM_GB, time.time() - _boot_time)
        prune_stale_peers()

        async def _hb():
            active = [p for p in _peers.values() if p["status"] == "active"]
            for peer in active:
                try:
                    await announce_to_peer(peer["endpoint"])
                except Exception as e:
                    print(f"[NODE:{NODE_ID[:10]}] hb announce failed -> {peer['endpoint']}: {e}")
            try:
                await register_to_registry()
            except Exception as e:
                print(f"[NODE:{NODE_ID[:10]}] hb registry failed: {e}")
            try:
                await register_to_control_plane()
            except Exception as e:
                print(f"[NODE:{NODE_ID[:10]}] hb control-plane failed: {e}")
            if not active:
                try:
                    await _discover_peers_from_registry()
                except Exception as e:
                    print(f"[NODE:{NODE_ID[:10]}] hb auto-discovery failed: {e}")

        _run_async_safe(_hb, "heartbeat")

# ── STARTUP ───────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    print(f"[NODE:{NODE_ID[:10]}] started v1.04.0")
    print(f"[NODE:{NODE_ID[:10]}] tier={NODE_PROFILE['tier']} (forced={_FORCED_TIER or 'no'})")
    print(f"[NODE:{NODE_ID[:10]}] advertised={NODE_ADVERTISED_ENDPOINT}")
    print(f"[NODE:{NODE_ID[:10]}] vram_gb={VRAM_GB} (env={_vram_env} detected={_vram_detected})")
    print(f"[NODE:{NODE_ID[:10]}] boot_peers={BOOT_PEERS or 'none — will use registry auto-discovery'}")
    print(f"[NODE:{NODE_ID[:10]}] peer_max_age_s={PEER_MAX_AGE_S}")
    print(f"[NODE:{NODE_ID[:10]}] max_load_units={MAX_LOAD_UNITS} ({'esplicito' if _max_load_units_env else 'auto-derivato da vram+engine'}) queue_timeout_s={REQUEST_QUEUE_TIMEOUT_S} engine={INFERENCE_BACKEND}")
    print(f"[NODE:{NODE_ID[:10]}] registry={REGISTRY_URL}")
    print(f"[NODE:{NODE_ID[:10]}] registry_public={REGISTRY_PUBLIC_URL}")
    print(f"[NODE:{NODE_ID[:10]}] ollama -> {OLLAMA_URL}")

# ── MIDDLEWARE firma ───────────────────────────────────────
# /v1/chat/completions NON passa da qui, di proposito: verifica la firma
# da sola, dentro il proprio handler (vedi sotto). @app.middleware("http")
# e' BaseHTTPMiddleware di Starlette, che per rileggere il body a valle usa
# un trucco (request._receive = receive che "ripete" sempre lo stesso
# messaggio). Questo trucco rompe le StreamingResponse: Starlette ascolta
# in background un http.disconnect che il receive fittizio non manda mai,
# causando "RuntimeError: Unexpected message received: http.request".
# Dato che /v1/chat/completions e' l'ultimo handler (nessuno a valle deve
# rileggere il body), verificare inline evita il problema alla radice.
SIGNED_PATHS = {"/announce", "/execute", "/peer/add", "/verify"}

@app.middleware("http")
async def inter_node_auth(request: Request, call_next):
    if SIGN_REQUESTS and request.method == "POST" and request.url.path in SIGNED_PATHS:
        body = await request.body()
        hdrs = dict(request.headers)
        if not verify_request_headers(hdrs, body):
            return Response(
                content='{"error":"invalid or missing node signature"}',
                status_code=401, media_type="application/json",
            )
        async def receive():
            return {"type": "http.request", "body": body}
        request._receive = receive
    return await call_next(request)

# ── ENDPOINTS ───────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "node_id": NODE_ID, "tier": NODE_PROFILE["tier"], "version": NODE_PROFILE["version"]}

@app.get("/status")
def status():
    prune_stale_peers()
    active_u, queued_u = _load_limiter.active_units, _load_limiter.queued_units
    return {
        "node_id":        NODE_ID,
        "public_key":     NODE_PUBKEY,
        "tier":           NODE_PROFILE["tier"],
        "version":        NODE_PROFILE["version"],
        "endpoint":       NODE_ADVERTISED_ENDPOINT,
        "capabilities":   NODE_CAPABILITIES,
        "vram_gb":        VRAM_GB,
        "uptime_s":       int(time.time() - _boot_time),
        "peers_active":   len([p for p in _peers.values() if p["status"] == "active"]),
        "peers_total":    len(_peers),
        "memory_entries": len(_read_memory(9999)),
        "engine":         INFERENCE_BACKEND,
        "active_load":      active_u,
        "queued_load":      queued_u,
        "max_load_units":   MAX_LOAD_UNITS,
        "active_requests":  active_u,
        "queued_requests":  queued_u,
        "max_concurrent":   MAX_LOAD_UNITS,
        "running":        True,
    }

@app.get("/peers")
def get_peers():
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

@app.get("/memory")
def get_memory(limit: int = 50):
    return {"node_id": NODE_ID, "entries": _read_memory(limit)}

@app.post("/memory/push")
async def receive_memory(payload: dict):
    entry = payload.get("entry", {})
    if entry and entry.get("node_id") != NODE_ID:
        entry["_received_from"] = payload.get("node_id", "unknown")
        _save_memory(entry)
    return {"ok": True}

@app.get("/identity")
def get_identity():
    return {
        "node_id":      NODE_ID,
        "public_key":   NODE_PUBKEY,
        "tier":         NODE_PROFILE["tier"],
        "version":      NODE_PROFILE["version"],
        "capabilities": NODE_PROFILE["capabilities"],
        "endpoint":     NODE_ADVERTISED_ENDPOINT,
        "vram_gb":      VRAM_GB,
    }

@app.post("/announce")
async def announce(message: dict):
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
    prune_stale_peers()
    return {
        "accepted":     True,
        "node_id":      NODE_ID,
        "pubkey":       NODE_PUBKEY,
        "tier":         NODE_PROFILE["tier"],
        "capabilities": NODE_PROFILE["capabilities"],
        "peers": [
            {"node_id": p["node_id"], "endpoint": p["endpoint"], "tier": p.get("tier", "leaf")}
            for p in _peers.values() if p["status"] == "active"
        ]
    }

@app.post("/verify")
async def verify_incoming(message: dict):
    valid = verify_message(message)
    return {"valid": valid, "node_id": message.get("node_id")}

@app.post("/execute")
async def execute_task(task: dict):
    task_id = task.get("task_id", "unknown")
    # Accetta sia 'prompt' che 'task' come campo input (friendly per curl diretto)
    prompt = (
        task.get("prompt")
        or task.get("task")
        or task.get("payload", {}).get("prompt")
        or f"Esegui task: {task_id}"
    )
    model = task.get("model") or task.get("payload", {}).get("model") or DEFAULT_MODEL

    weight = estimate_model_load_units(model)
    if not await _try_acquire_slot(weight):
        return _busy_response()
    try:
        response_text = await ollama_generate(prompt, model)
    finally:
        await _release_slot(weight)

    # Salva in memoria locale e propaga al CP, solo se non sono dei task per i
    # titoli (task_id con prefisso "title-"), altrimenti si genera un loop
    # infinito: la generazione del titolo per una entry finirebbe a sua
    # volta in memoria come nuova entry da titolare.
    is_title_task = task_id.startswith("title-")
    if not is_title_task:
        entry = {
            "node_id":   NODE_ID,
            "task_id":   task_id,
            "prompt":    prompt,
            "response":  response_text,
            "model":     model,
            "timestamp": time.time(),
        }
        _save_memory(entry)
        await _push_memory_to_cp(entry)

    return {"node_id": NODE_ID, "task_id": task_id, "status": "done", "model": model, "response": response_text}

# ── /v1/chat/completions ────────────────────────────────────
# Proxy trasparente verso il backend Ollama/LM Studio LOCALE di questo nodo.
# Il control-plane instrada qui i chat completions (streaming e non) quando
# sceglie questo nodo come target. La firma viene verificata QUI dentro,
# non nel middleware generico (vedi nota sopra su SIGNED_PATHS): solo il
# control-plane puo' invocarla, mai Open WebUI direttamente. Dopo la
# verifica, il nodo NON parla piu' con Ollama direttamente: rigira a
# ollama-proxy (stesso container, porta 11435), che si occupa di
# log/memoria condivisa e poi tocca Ollama per ultimo. Catena completa:
# CP (firma) -> nodo (autentica) -> ollama-proxy (strumenta) -> Ollama.
#
# Lo slot di concorrenza viene acquisito PRIMA di aprire la
# StreamingResponse: se lo status 200 fosse già stato inviato non ci sarebbe
# più modo di segnalare "occupato" al chiamante, che resterebbe appeso.
@app.post("/v1/chat/completions")
async def v1_chat_completions_proxy(request: Request):
    body = await request.body()

    if SIGN_REQUESTS:
        hdrs = dict(request.headers)
        if not verify_request_headers(hdrs, body):
            return Response(
                content='{"error":"invalid or missing node signature"}',
                status_code=401, media_type="application/json",
            )

    try:
        payload = json.loads(body or b"{}")
    except Exception:
        payload = {}
    stream = bool(payload.get("stream", False))

    weight = estimate_model_load_units(payload.get("model", ""))

    if stream:
        if not await _try_acquire_slot(weight):
            return _busy_response()

        async def _stream_gen():
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    async with client.stream(
                        "POST", f"{OLLAMA_PROXY_URL}/v1/chat/completions",
                        content=body, headers={"Content-Type": "application/json"},
                    ) as resp:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                yield chunk
            except Exception as e:
                yield f'data: {{"error": "{e}"}}\n\n'.encode()
            finally:
                await _release_slot(weight)
        return StreamingResponse(_stream_gen(), media_type="text/event-stream")

    if not await _try_acquire_slot(weight):
        return _busy_response()
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                f"{OLLAMA_PROXY_URL}/v1/chat/completions",
                content=body, headers={"Content-Type": "application/json"},
            )
            return Response(
                content=r.content, status_code=r.status_code,
                media_type=r.headers.get("content-type", "application/json"),
            )
    except Exception as e:
        return Response(
            content=json.dumps({"error": {"message": str(e), "type": "server_error"}}),
            status_code=503, media_type="application/json",
        )
    finally:
        await _release_slot(weight)
        return StreamingResponse(_stream_gen(), media_type="text/event-stream")

    if not await _try_acquire_slot():
        return _busy_response()
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                f"{OLLAMA_PROXY_URL}/v1/chat/completions",
                content=body, headers={"Content-Type": "application/json"},
            )
            return Response(
                content=r.content, status_code=r.status_code,
                media_type=r.headers.get("content-type", "application/json"),
            )
    except Exception as e:
        return Response(
            content=json.dumps({"error": {"message": str(e), "type": "server_error"}}),
            status_code=503, media_type="application/json",
        )
    finally:
        _release_slot()

@app.get("/ollama/health")
async def check_ollama():
    return await ollama_health()

@app.get("/ollama/models")
async def list_models():
    h = await ollama_health()
    return {"models": h.get("models", [])} if h["ok"] else {"error": h.get("error")}

@app.post("/ollama/pull")
async def ollama_pull(body: dict):
    model = body.get("model", DEFAULT_MODEL)

    async def stream_pull():
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream(
                    "POST", f"{OLLAMA_URL}/api/pull",
                    json={"name": model, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            yield f"data: {line}\n\n"
            yield 'data: {"status":"done"}\n\n'
        except Exception as e:
            yield f'data: {{"error": "{e}"}}\n\n'

    return StreamingResponse(
        stream_pull(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=NODE_PORT)

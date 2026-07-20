# node/ollama_proxy.py
# HyperSpace AGI v1.03 — Ollama Proxy
#
# Emula l'API Ollama (e ora anche l'API OpenAI-compatibile) su porta 11435.
# Non e' piu' pensato per essere chiamato direttamente da Open WebUI: il
# punto di ingresso e' node/main.py:/v1/chat/completions (autenticato,
# raggiungibile solo dal control-plane), che rigira qui internamente
# (localhost, stesso container). Questo file resta comunque raggiungibile
# sulla rete Docker per compatibilita' con le sue rotte Ollama-native
# preesistenti (/api/generate, /api/chat, ecc.), ma il percorso "ufficiale"
# per le chat della mesh e' /v1/chat/completions.
#
# Ogni richiesta viene:
#   1. Inoltrata al vero Ollama (OLLAMA_URL)
#   2. Loggata nel control-plane come "webui_interaction"
#   3. Appesa a data/memory.jsonl (memoria collettiva locale)
#   4. Propagata agli hub peer via /memory/push (se PUBLIC_ENDPOINT noto)

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

OLLAMA_URL        = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "").rstrip("/")
NODE_TIER         = os.getenv("NODE_TIER", "leaf")
PUBLIC_ENDPOINT   = os.getenv("PUBLIC_ENDPOINT", "").rstrip("/")
PROXY_PORT        = int(os.getenv("PROXY_PORT", 11435))

DATA_DIR   = Path(os.getenv("DATA_DIR", "/app/data"))
MEMORY_FILE = DATA_DIR / "memory.jsonl"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# NODE_ID condiviso con node/main.py: entrambi i processi girano nello
# stesso container con lo stesso DATA_DIR, quindi generate_or_load_identity()
# qui non genera un nuovo keypair — carica quello gia' creato da main.py.
# Prima ollama-proxy usava un NODE_ID separato (env var mai valorizzata),
# risultando sempre "unknown" nei log e nelle entry di memoria.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from shared.identity import generate_or_load_identity
    NODE_ID = generate_or_load_identity()["node_id"]
except Exception:
    NODE_ID = os.getenv("NODE_ID", "unknown")

app = FastAPI(title="HyperSpace Ollama Proxy", version="1.03.0")

# ── MEMORIA COLLETTIVA ────────────────────────────────────
def save_memory(entry: dict):
    """Appende un'interazione a memory.jsonl (una riga JSON per entry)."""
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def read_memory(limit: int = 100) -> list:
    """Legge le ultime `limit` righe di memoria."""
    if not MEMORY_FILE.exists():
        return []
    lines = MEMORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(l) for l in lines[-limit:] if l.strip()]

async def log_to_control_plane(entry: dict):
    """Invia l'interazione al control-plane come log mesh_event."""
    if not CONTROL_PLANE_URL:
        return
    tps_suffix = f" · {entry['tokens_per_sec']} tok/s" if entry.get("tokens_per_sec") else ""
    payload = {
        "type":       "webui_interaction",
        "summary":    f"[{NODE_ID[:10]}] {entry.get('model','?')}: {entry.get('prompt','')[:80]}{tps_suffix}",
        "detail":     json.dumps(entry, ensure_ascii=False),
        "sourceNode": NODE_ID[:12],
        "targetNode": "webui",
        "status":     "success",
        "traceId":    entry.get("interaction_id", "")[:8],
    }
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            await client.post(f"{CONTROL_PLANE_URL}/logs/add", json=payload)
    except Exception:
        pass  # non bloccare il proxy se il CP è giù

async def propagate_to_peers(entry: dict):
    """Invia la memory entry agli hub peer noti tramite /memory/push."""
    boot_peers = [
        p.strip() for p in os.getenv("BOOT_PEERS", "").split(",") if p.strip()
    ]
    if not boot_peers:
        return
    payload = {"node_id": NODE_ID, "entry": entry}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for peer in boot_peers:
            try:
                await client.post(f"{peer.rstrip('/')}/memory/push", json=payload)
            except Exception:
                pass

# ── METRICHE TOKEN/S ──────────────────────────────────────
# Estratte dai contatori nativi di Ollama (eval_count/eval_duration) quando
# disponibili (/api/generate, /api/chat), o approssimate da wall-clock +
# usage OpenAI-style per /v1/chat/completions (Ollama non riporta lì i
# tempi nativi). Il tick periodico durante lo streaming alimenta il pannello
# "nerd stats" realtime del control-plane; il valore finale (più preciso,
# basato su eval_duration) sostituisce l'ultimo tick a fine generazione.
TICK_INTERVAL_S = 0.4

async def _send_tick(iid: str, model: str, tokens_so_far: int,
                      tokens_per_sec: float, elapsed_ms: int, done: bool = False):
    if not CONTROL_PLANE_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(f"{CONTROL_PLANE_URL}/metrics/tick", json={
                "interaction_id": iid, "node_id": NODE_ID, "model": model,
                "tokens_so_far": tokens_so_far, "tokens_per_sec": tokens_per_sec,
                "elapsed_ms": elapsed_ms, "done": done,
            })
    except Exception:
        pass

def _tick_state(iid: str) -> dict:
    return {"iid": iid, "tokens": 0, "t0": time.time(), "last_tick": 0.0}

def _maybe_tick(state: dict, model: str):
    """Schedula (fire-and-forget) un tick se è passato abbastanza tempo dall'ultimo."""
    now = time.time()
    if now - state["last_tick"] < TICK_INTERVAL_S:
        return
    state["last_tick"] = now
    elapsed = now - state["t0"]
    tps = round(state["tokens"] / elapsed, 2) if elapsed > 0 else 0.0
    asyncio.create_task(_send_tick(state["iid"], model, state["tokens"], tps, int(elapsed * 1000)))

def _metrics_from_native_done(chunk: dict, wall_ms: int) -> dict:
    """Ollama-native (/api/generate, /api/chat): eval_duration è in nanosecondi
    e misura solo il tempo di generazione vero (esclude load/queue), quindi è
    più preciso del wall-clock."""
    eval_count   = chunk.get("eval_count") or 0
    eval_ns      = chunk.get("eval_duration") or 0
    prompt_count = chunk.get("prompt_eval_count") or 0
    prompt_ns    = chunk.get("prompt_eval_duration") or 0
    m = {"tokens_in": prompt_count, "tokens_out": eval_count, "duration_ms": wall_ms}
    if eval_count and eval_ns:
        m["tokens_per_sec"] = round(eval_count / (eval_ns / 1e9), 2)
    if prompt_count and prompt_ns:
        m["prompt_tokens_per_sec"] = round(prompt_count / (prompt_ns / 1e9), 2)
    return m

def _metrics_from_usage(usage: dict, wall_ms: int) -> dict:
    """OpenAI-compat (/v1/chat/completions): niente eval_duration nativo, quindi
    tok/s è approssimato dal wall-clock della richiesta (include un filo di
    overhead di rete/coda rispetto al valore native)."""
    usage = usage or {}
    tok_in  = usage.get("prompt_tokens") or 0
    tok_out = usage.get("completion_tokens") or 0
    m = {"tokens_in": tok_in, "tokens_out": tok_out, "duration_ms": wall_ms}
    if tok_out and wall_ms:
        m["tokens_per_sec"] = round(tok_out / (wall_ms / 1000), 2)
    return m

# ── INTERCETTA E LOGGA ───────────────────────────────────
async def _record_interaction(
    prompt: str, response_text: str, model: str,
    source: str = "webui", duration_ms: int = 0,
    interaction_id: str = "", metrics: dict = None,
):
    entry = {
        "interaction_id": interaction_id or str(uuid.uuid4()),
        "ts":             datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "node_id":        NODE_ID,
        "node_tier":      NODE_TIER,
        "source":         source,
        "model":          model,
        "prompt":         prompt,
        "response":       response_text[:2000],  # tronca a 2KB per memoria
        "duration_ms":    duration_ms,
    }
    if metrics:
        entry.update(metrics)
    save_memory(entry)
    await asyncio.gather(
        log_to_control_plane(entry),
        propagate_to_peers(entry),
        return_exceptions=True,
    )
    if interaction_id:
        asyncio.create_task(_send_tick(interaction_id, model, entry.get("tokens_out", 0),
                                        entry.get("tokens_per_sec", 0), duration_ms, done=True))

# ── PROXY ROUTES — Ollama-native ─────────────────────────────

@app.get("/api/tags")
async def proxy_tags():
    """Lista modelli — pass-through diretto."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return Response(content=r.content, media_type="application/json")
    except Exception as e:
        return Response(content=json.dumps({"error": str(e)}),
                        status_code=503, media_type="application/json")

@app.get("/api/version")
async def proxy_version():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/version")
            return Response(content=r.content, media_type="application/json")
    except Exception:
        return Response(content=json.dumps({"version": "0.0.0-hyperspace"}),
                        media_type="application/json")

@app.post("/api/generate")
async def proxy_generate(request: Request):
    """Genera testo — intercetta prompt e risposta."""
    body = await request.json()
    prompt = body.get("prompt", "")
    model  = body.get("model", "")
    stream = body.get("stream", True)
    t0 = time.time()
    iid = str(uuid.uuid4())

    if stream:
        async def stream_and_log():
            full_response = ""
            tick = _tick_state(iid)
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST", f"{OLLAMA_URL}/api/generate", json=body
                    ) as resp:
                        async for line in resp.aiter_lines():
                            if line.strip():
                                yield line + "\n"
                                try:
                                    chunk = json.loads(line)
                                    full_response += chunk.get("response", "")
                                    tick["tokens"] += 1
                                    _maybe_tick(tick, model)
                                    if chunk.get("done"):
                                        dur = int((time.time() - t0) * 1000)
                                        metrics = _metrics_from_native_done(chunk, dur)
                                        await _record_interaction(
                                            prompt, full_response, model,
                                            duration_ms=dur, interaction_id=iid,
                                            metrics=metrics,
                                        )
                                except Exception:
                                    pass
            except Exception as e:
                yield json.dumps({"error": str(e), "done": True}) + "\n"

        return StreamingResponse(stream_and_log(), media_type="application/x-ndjson")
    else:
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
                data = r.json()
                dur  = int((time.time() - t0) * 1000)
                metrics = _metrics_from_native_done(data, dur)
                await _record_interaction(
                    prompt, data.get("response", ""), model, duration_ms=dur,
                    interaction_id=iid, metrics=metrics,
                )
                return Response(content=r.content, media_type="application/json")
        except Exception as e:
            return Response(content=json.dumps({"error": str(e)}),
                            status_code=503, media_type="application/json")

@app.post("/api/chat")
async def proxy_chat(request: Request):
    """Chat completions (formato messages[]) — intercetta e logga."""
    body    = await request.json()
    model   = body.get("model", "")
    messages = body.get("messages", [])
    # Estrai testo leggibile dai messages per la memoria
    prompt_summary = " | ".join(
        f"{m.get('role','?')}: {str(m.get('content',''))[:120]}"
        for m in messages[-3:]  # ultimi 3 turni
    )
    stream = body.get("stream", True)
    t0 = time.time()
    iid = str(uuid.uuid4())

    if stream:
        async def stream_chat_and_log():
            full_response = ""
            tick = _tick_state(iid)
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST", f"{OLLAMA_URL}/api/chat", json=body
                    ) as resp:
                        async for line in resp.aiter_lines():
                            if line.strip():
                                yield line + "\n"
                                try:
                                    chunk = json.loads(line)
                                    msg = chunk.get("message", {})
                                    full_response += msg.get("content", "")
                                    tick["tokens"] += 1
                                    _maybe_tick(tick, model)
                                    if chunk.get("done"):
                                        dur = int((time.time() - t0) * 1000)
                                        metrics = _metrics_from_native_done(chunk, dur)
                                        await _record_interaction(
                                            prompt_summary, full_response,
                                            model, duration_ms=dur, interaction_id=iid,
                                            metrics=metrics,
                                        )
                                except Exception:
                                    pass
            except Exception as e:
                yield json.dumps({"error": str(e), "done": True}) + "\n"

        return StreamingResponse(stream_chat_and_log(), media_type="application/x-ndjson")
    else:
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
                data = r.json()
                dur  = int((time.time() - t0) * 1000)
                content = data.get("message", {}).get("content", "")
                metrics = _metrics_from_native_done(data, dur)
                await _record_interaction(
                    prompt_summary, content, model, duration_ms=dur,
                    interaction_id=iid, metrics=metrics,
                )
                return Response(content=r.content, media_type="application/json")
        except Exception as e:
            return Response(content=json.dumps({"error": str(e)}),
                            status_code=503, media_type="application/json")

# ── PROXY ROUTE — OpenAI-compatibile ─────────────────────────
# Punto di ingresso "ufficiale" per le chat instradate dalla mesh: chiamato
# da node/main.py:/v1/chat/completions dopo che questo ha autenticato la
# richiesta del control-plane. Ultimo hop prima di Ollama vero.
@app.post("/v1/chat/completions")
async def proxy_openai_chat(request: Request):
    body = await request.json()
    model    = body.get("model", "")
    messages = body.get("messages", [])
    prompt_summary = " | ".join(
        f"{m.get('role','?')}: {str(m.get('content',''))[:120]}"
        for m in messages[-3:]
    )
    stream = body.get("stream", False)
    t0 = time.time()
    iid = str(uuid.uuid4())

    if stream:
        # Chiediamo a Ollama di riportare gli usage token nell'ultimo chunk SSE
        # (supportato dal suo endpoint OpenAI-compatibile). Se il backend non lo
        # supporta, l'unico effetto collaterale è che quel campo resta assente:
        # nessuna rottura del framing per il client a valle.
        body = {**body, "stream_options": {**body.get("stream_options", {}), "include_usage": True}}

        async def stream_and_log():
            full_response = ""
            usage = None
            tick = _tick_state(iid)
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST", f"{OLLAMA_URL}/v1/chat/completions", json=body
                    ) as resp:
                        async for raw_chunk in resp.aiter_bytes():
                            if not raw_chunk:
                                continue
                            # Passthrough byte-per-byte: non tocchiamo mai il
                            # framing SSE (righe vuote comprese) inoltrato al
                            # client, per non romperlo.
                            yield raw_chunk
                            # Parsing "best effort" SOLO per il logging: non
                            # deve mai poter interrompere lo streaming.
                            try:
                                for line in raw_chunk.decode("utf-8", errors="ignore").splitlines():
                                    if line.startswith("data: ") and "[DONE]" not in line:
                                        chunk = json.loads(line[len("data: "):])
                                        if chunk.get("usage"):
                                            usage = chunk["usage"]
                                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                                        content = delta.get("content", "")
                                        if content:
                                            full_response += content
                                            tick["tokens"] += 1
                                            _maybe_tick(tick, model)
                            except Exception:
                                pass
            except Exception as e:
                yield f'data: {{"error": "{e}"}}\n\n'.encode()
            finally:
                dur = int((time.time() - t0) * 1000)
                metrics = _metrics_from_usage(usage, dur)
                await _record_interaction(prompt_summary, full_response, model, duration_ms=dur,
                                          interaction_id=iid, metrics=metrics)

        return StreamingResponse(stream_and_log(), media_type="text/event-stream")
    else:
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=body)
                dur = int((time.time() - t0) * 1000)
                try:
                    data    = r.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    usage   = data.get("usage")
                except Exception:
                    content, usage = "", None
                metrics = _metrics_from_usage(usage, dur)
                await _record_interaction(prompt_summary, content, model, duration_ms=dur,
                                          interaction_id=iid, metrics=metrics)
                return Response(content=r.content, status_code=r.status_code,
                                media_type=r.headers.get("content-type", "application/json"))
        except Exception as e:
            return Response(content=json.dumps({"error": {"message": str(e), "type": "server_error"}}),
                            status_code=503, media_type="application/json")

# Tutti gli altri endpoint Ollama: pass-through generico
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_generic(path: str, request: Request):
    method = request.method
    body   = await request.body()
    url    = f"{OLLAMA_URL}/api/{path}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.request(
                method, url, content=body,
                headers={"Content-Type": request.headers.get("content-type", "application/json")}
            )
            return Response(content=r.content, status_code=r.status_code,
                            media_type=r.headers.get("content-type", "application/json"))
    except Exception as e:
        return Response(content=json.dumps({"error": str(e)}),
                        status_code=503, media_type="application/json")

# ── ENDPOINTS MEMORIA (letti da altri nodi / dashboard) ─────────
@app.get("/memory")
def get_memory(limit: int = 50):
    """Ultime `limit` interazioni della memoria collettiva locale."""
    return {"node_id": NODE_ID, "entries": read_memory(limit)}

@app.post("/memory/push")
async def receive_memory(payload: dict):
    """Riceve una memory entry da un peer e la salva localmente."""
    entry = payload.get("entry", {})
    if entry and entry.get("node_id") != NODE_ID:
        entry["_received_from"] = payload.get("node_id", "unknown")
        save_memory(entry)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT)

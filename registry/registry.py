# registry/registry.py
# HyperSpace AGI v1.02 — Public Registry
# feat: landing page pubblica, /dashboard nodi live, /nodes/active TTL-filtered
# v1.03: _active_nodes() espone anche active_requests/queued_requests/
#        max_concurrent (letti dalla metadata inviata dal nodo in /register),
#        cosi' il control-plane e la dashboard possono leggere il carico
#        corrente senza dover contattare ogni nodo direttamente.

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from time import time
from threading import Lock
import uvicorn
import os

app = FastAPI(title="HyperSpace Registry", version="1.03")
lock = Lock()

TTL_SECONDS        = int(os.getenv("NODE_TTL", "300"))   # default 300s = 20 cicli heartbeat
REGISTRY_PUBLIC_URL = os.getenv("REGISTRY_PUBLIC_URL", "https://sanctuary-mower-plated.ngrok-free.dev")
ONBOARDING_URL      = os.getenv("ONBOARDING_URL", "http://localhost:8088")

class NodeRegistration(BaseModel):
    node_id: str
    public_address: str
    role: Optional[str] = "worker"
    metadata: Dict[str, str] = Field(default_factory=dict)

class NodeRecord(NodeRegistration):
    last_seen: float

nodes: Dict[str, NodeRecord] = {}


def prune_stale_nodes():
    now = time()
    stale = [nid for nid, rec in nodes.items() if now - rec.last_seen > TTL_SECONDS]
    for nid in stale:
        del nodes[nid]


def _safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _active_nodes() -> list:
    with lock:
        prune_stale_nodes()
        return [
            {
                "node_id":        n.node_id,
                "public_address": n.public_address,
                "role":           n.role,
                "metadata":       n.metadata,
                "last_seen":      n.last_seen,
                "uptime_s":       _safe_int(n.metadata.get("uptime_s", "0")),
                "vram_gb":        _safe_float(n.metadata.get("vram_gb", "0")),
                "tier":           n.metadata.get("tier", "leaf"),
                "version":        n.metadata.get("version", ""),
                "specialization": n.metadata.get("specialization", "generalist"),
                "avatar":         n.metadata.get("avatar", "🤖"),
                # Carico corrente, inviato dal nodo a ogni /register (vedi
                # node/main.py:register_to_registry). Assenti su nodi con
                # versione precedente: default a 0/1 per non rompere il
                # calcolo dello score lato dashboard/control-plane.
                "active_requests": _safe_int(n.metadata.get("active_requests", "0")),
                "queued_requests": _safe_int(n.metadata.get("queued_requests", "0")),
                "max_concurrent":  _safe_int(n.metadata.get("max_concurrent", "1"), default=1),
            }
            for n in nodes.values()
        ]


# ── API ENDPOINTS ─────────────────────────────────────────────────────────────

@app.post("/register", summary="Registra o aggiorna un nodo")
def register(node: NodeRegistration):
    with lock:
        nodes[node.node_id] = NodeRecord(**node.model_dump(), last_seen=time())
    return {"ok": True, "node_id": node.node_id}


@app.post("/heartbeat", summary="Aggiorna last_seen del nodo")
def heartbeat(node_id: str):
    with lock:
        if node_id not in nodes:
            raise HTTPException(status_code=404, detail=f"Nodo '{node_id}' non trovato")
        nodes[node_id].last_seen = time()
    return {"ok": True, "node_id": node_id}


@app.delete("/nodes/{node_id}", summary="Deregistra un nodo")
def deregister(node_id: str):
    with lock:
        if node_id not in nodes:
            raise HTTPException(status_code=404, detail=f"Nodo '{node_id}' non trovato")
        del nodes[node_id]
    return {"ok": True, "node_id": node_id}


@app.get("/nodes", response_model=List[NodeRecord], summary="Lista nodi attivi")
def list_nodes():
    with lock:
        prune_stale_nodes()
        return list(nodes.values())


@app.get("/nodes/active", summary="Lista nodi attivi per auto-discovery (usata dai nodi al boot) e dalla dashboard")
def list_nodes_active():
    """Ritorna solo i nodi vivi con TTL, in forma flat (incluso il carico
    corrente) — usato da node/main.py al boot per auto-discovery e dal
    control-plane/dashboard per /registry/nodes."""
    return {"nodes": _active_nodes(), "ttl_seconds": TTL_SECONDS}


@app.get("/health")
def health():
    with lock:
        prune_stale_nodes()
    return {"status": "ok", "nodes_count": len(nodes), "ttl_seconds": TTL_SECONDS}


# ── LANDING PAGE ──────────────────────────────────────────────────────────────
# La landing page/wizard di primo avvio ora vive nel servizio "onboarding"
# (porta 8088, vedi onboarding/) — qui restiamo solo API + /dashboard operativa.

@app.get("/", include_in_schema=False)
def landing():
    return RedirectResponse(url=ONBOARDING_URL, status_code=307)


# ── DASHBOARD PUBBLICA ────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    active = _active_nodes()
    rows = ""
    for n in active:
        uptime_h = n["uptime_s"] // 3600
        vram_str = f"{n['vram_gb']:.0f} GB" if n["vram_gb"] > 0 else "CPU"
        rows += f"<tr><td>{n['node_id'][:20]}…</td><td>{n['tier'].upper()}</td><td>{vram_str}</td><td>{uptime_h}h</td><td>{n['public_address']}</td><td>{n['version']}</td></tr>"
    if not rows:
        rows = '<tr><td colspan="6" style="text-align:center;color:#6b6b7a">No active nodes</td></tr>'
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>HyperSpace — Node Dashboard</title>
  <link href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700&display=swap" rel="stylesheet">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Satoshi',sans-serif;background:#0d0d10;color:#e2e2e8;padding:2rem}}
    h1{{font-size:1.4rem;margin-bottom:.3rem}}p{{color:#6b6b7a;font-size:.85rem;margin-bottom:1.5rem}}
    table{{width:100%;border-collapse:collapse;background:#15151a;border-radius:12px;overflow:hidden}}
    th{{background:#1c1c23;padding:.7rem 1rem;text-align:left;font-size:.78rem;color:#6b6b7a;text-transform:uppercase;letter-spacing:.05em}}
    td{{padding:.75rem 1rem;border-top:1px solid rgba(255,255,255,.06);font-size:.88rem}}
    .refresh{{font-size:.75rem;color:#00c896;margin-top:.8rem}}
  </style>
</head>
<body>
  <h1>⬡ HyperSpace Node Dashboard</h1>
  <p>Auto-refresh every 10s &nbsp;·&nbsp; {len(active)} active nodes &nbsp;·&nbsp; TTL {TTL_SECONDS}s</p>
  <table>
    <thead><tr><th>Node ID</th><th>Tier</th><th>VRAM</th><th>Uptime</th><th>Endpoint</th><th>Version</th></tr></thead>
    <tbody id="tbody">{rows}</tbody>
  </table>
  <div class="refresh" id="ts">Last refresh: now</div>
  <script>
    async function refresh(){{
      const r=await fetch('/nodes/active');
      const {{nodes}}=await r.json();
      const tc={{root:'#a78bfa',hub:'#38bdf8',leaf:'#4ade80'}};
      document.getElementById('tbody').innerHTML=nodes.length
        ?nodes.map(n=>`<tr><td>${{n.node_id.slice(0,20)}}…</td><td>${{n.tier.toUpperCase()}}</td><td>${{n.vram_gb>0?n.vram_gb.toFixed(0)+' GB':'CPU'}}</td><td>${{Math.floor(n.uptime_s/3600)}}h</td><td>${{n.public_address}}</td><td>${{n.version}}</td></tr>`).join('')
        :'<tr><td colspan="6" style="text-align:center;color:#6b6b7a">No active nodes</td></tr>';
      document.getElementById('ts').textContent='Last refresh: '+new Date().toLocaleTimeString();
    }}
    setInterval(refresh,10000);
  </script>
</body></html>""")


if __name__ == "__main__":
    port = int(os.getenv("REGISTRY_PORT", "8086"))
    uvicorn.run("registry:app", host="0.0.0.0", port=port, reload=False)

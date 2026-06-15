from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
import asyncio

app = FastAPI()

nodes = {}

# -------- MODELS --------

class RegisterRequest(BaseModel):
    node_id: str
    host: str = ""
    port: int = 8084
    capabilities: list = []

class HeartbeatRequest(BaseModel):
    node_id: str


# -------- HELPERS --------

def mark_stale():
    """Nodi che non mandano heartbeat da >60s passano a stale."""
    cutoff = datetime.utcnow() - timedelta(seconds=60)
    for nid, n in nodes.items():
        last = n.get("last_seen")
        if isinstance(last, datetime) and last < cutoff:
            n["status"] = "stale"
        elif isinstance(last, datetime):
            n["status"] = "active"


# -------- ENDPOINTS --------

@app.post("/register")
def register(req: RegisterRequest):
    nodes[req.node_id] = {
        "node_id":      req.node_id,
        "host":         req.host or req.node_id,
        "port":         req.port,
        "capabilities": req.capabilities,
        "status":       "active",
        "registered_at": datetime.utcnow().isoformat(),
        "last_seen":    datetime.utcnow(),
    }
    print(f"[AUTHORITY] registered: {req.node_id}")
    return {"status": "registered", "node_id": req.node_id}


@app.post("/heartbeat")
def heartbeat(req: HeartbeatRequest):
    if req.node_id in nodes:
        nodes[req.node_id]["last_seen"] = datetime.utcnow()
        nodes[req.node_id]["status"]    = "active"
    return {"status": "ok"}


@app.get("/nodes")
def get_nodes():
    mark_stale()
    # serializza datetime -> str per JSON
    out = {}
    for nid, n in nodes.items():
        out[nid] = {
            "node_id":       n["node_id"],
            "host":          n["host"],
            "port":          n["port"],
            "capabilities":  n["capabilities"],
            "status":        n["status"],
            "registered_at": n["registered_at"],
            "last_seen":     n["last_seen"].isoformat() if isinstance(n["last_seen"], datetime) else str(n["last_seen"]),
        }
    return out


@app.get("/nodes/active")
def get_active_nodes():
    mark_stale()
    return {nid: n for nid, n in nodes.items() if n["status"] == "active"}


@app.delete("/nodes/{node_id}")
def deregister(node_id: str):
    removed = nodes.pop(node_id, None)
    if removed:
        return {"status": "deregistered", "node_id": node_id}
    return {"status": "not_found", "node_id": node_id}


@app.get("/health")
def health():
    return {"status": "ok", "nodes": len(nodes)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

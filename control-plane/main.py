# control-plane/main.py
# HyperSpace AGI v1.03 — Control Plane
# ... (resto del file invariato per ora)

# ── LOG ───────────────────────────────────────────────────────────────────────
LOG_TYPES = {"connection_test", "inter_node_message", "system", "mesh_event", "memory_sync", "auth"}

def push_log(type_, summary, detail="", source="control-plane", target="", status="info", trace_id=""):
    entry = {
        "id":         str(uuid.uuid4()),
        "ts":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type":       type_ if type_ in LOG_TYPES else "system",
        "sourceNode": source,
        "targetNode": target,
        "status":     status,
        "traceId":    trace_id or str(uuid.uuid4())[:8],
        "summary":    summary,
        "detail":     detail,
    }
    db.insert_log(entry)
    return entry

# Helper per logging header di autenticazione web-node
def _log_auth_headers(request, action: str, extra: dict = None):
    """Logga header x-hyperspace-* in modo strutturato per observability."""
    headers = {
        "node_id": request.headers.get("x-hyperspace-node-id"),
        "install_id": request.headers.get("x-hyperspace-install-id"),
        "session_id": request.headers.get("x-hyperspace-session-id"),
        "ts": request.headers.get("x-hyperspace-ts"),
        "nonce": request.headers.get("x-hyperspace-nonce"),
        "client": request.headers.get("x-hyperspace-client"),
        "sig": request.headers.get("x-hyperspace-sig"),
    }
    headers = {k: v for k, v in headers.items() if v}  # rimuovi None

    if extra:
        headers.update(extra)

    push_log(
        "auth",
        f"{action}",
        detail=json.dumps(headers, ensure_ascii=False),
        source=headers.get("node_id", "web-node"),
        status="info"
    )

# (resto del file invariato)
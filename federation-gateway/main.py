# federation-gateway/main.py
# HyperSpace AGI v1.03 — Federation Gateway
#
# Unico componente pensato per essere esposto pubblicamente (via Caddy o tunnel)
# nello scenario di CP confederati. Non contiene NESSUNA logica di verifica
# firma o allowlist: quella vive nel control-plane (/federate/execute),
# perché deve restare un'unica fonte di verità condivisa con la dashboard.
#
# Il gateway fa una cosa sola: inoltra sulla rete Docker interna SOLO le
# rotte esplicitamente whitelisted. Qualsiasi altro path (dashboard, config,
# task manuali, log, gestione peer) riceve 404 senza mai toccare il CP.
# Anche in caso di bug nella verifica firma del CP, chi attacca da qui fuori
# non ha comunque modo di raggiungere endpoint sensibili: non fanno parte
# della superficie che questo componente sa inoltrare.

import os
from flask import Flask, request, Response
import requests

app = Flask(__name__)

CP_URL = os.getenv("CP_URL", "http://control-plane:8085").rstrip("/")

# Whitelist esplicita (metodo, path). Aggiungere qui SOLO endpoint pensati
# per essere pubblici by design — non aggiungere mai /config/*, /task/*,
# /logs/*, /federation/peers* (gestione allowlist, solo dashboard interna).
ALLOWED_ROUTES = {
    ("POST", "/federate/execute"),
    ("GET",  "/federation/identity"),
}

_EXCLUDED_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
def proxy(path):
    route_key = (request.method, f"/{path}")
    if route_key not in ALLOWED_ROUTES:
        return {"error": "not found"}, 404

    try:
        upstream = requests.request(
            method=request.method,
            url=f"{CP_URL}/{path}",
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            data=request.get_data(),
            params=request.args,
            timeout=130,
        )
    except Exception as e:
        return {"error": f"control-plane non raggiungibile: {e}"}, 502

    headers = [
        (k, v) for k, v in upstream.headers.items()
        if k.lower() not in _EXCLUDED_RESPONSE_HEADERS
    ]
    return Response(
        upstream.content,
        status=upstream.status_code,
        headers=headers,
        content_type=upstream.headers.get("Content-Type", "application/json"),
    )


@app.route("/health")
def health():
    """Unico endpoint informativo extra, utile per gli healthcheck Docker
    e per verificare che il gateway sia in piedi senza rivelare nulla sul CP."""
    return {"status": "ok", "service": "federation-gateway", "cp_url": CP_URL}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8095, debug=False)

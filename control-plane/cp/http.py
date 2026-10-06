# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/http.py
# DUE COSE CHE PAREVANO DIVERSE E SONO LA STESSA: la forma delle risposte.
#
# 1. gli header dello stream SSE;
# 2. i guard delle rotte admin e il proxy verso l'host-agent.
#
# Le due stanno insieme perche' condividono la stessa domanda: "che cosa puo'
# uscire da questa risposta, e chi puo' chiederla". Sono funzioni senza stato,
# con i token presi dall'env all'uso, e non toccano il runtime: si possono
# quindi leggere e testare da sole.
#
# I token sono riletti a ogni chiamata, non tenuti in un globale: la tab Setup
# puo' cambiarli a caldo e un valore letto all'import sarebbe gia' vecchio.

import os

from flask import jsonify, request

from shared.network_security import token_authorized

def _sse_headers():
    """Header della risposta SSE.

    NON impostare qui i header hop-by-hop (`Transfer-Encoding`, `Connection`):
    appartengono al server WSGI, che li aggiunge gia' da solo. Impostarli a mano
    produce header DUPLICATI nella risposta — `Transfer-Encoding: chunked` due
    volte e `Connection: keep-alive` seguito da `Connection: close` — cioe' HTTP
    malformato: lo stream viene troncato e il primo chunk puo' andare perso.
    Osservato in sessione di test: SSE da 15 byte con il solo [DONE] su qwen3 e
    connessione chiusa a meta' su qwen2.
    """
    return {
        "Content-Type":      "text/event-stream",
        "Cache-Control":     "no-cache, no-transform",
        "X-Accel-Buffering": "no",   # evita il buffering di un nginx a monte
    }

HOSTCTL_URL   = os.getenv("HOSTCTL_URL", "http://host.docker.internal:8765").rstrip("/")
HOSTCTL_TOKEN = os.getenv("HOSTCTL_TOKEN", "")
NETWORK_ADMIN_TOKEN = os.getenv("NETWORK_ADMIN_TOKEN", "")
_NETWORK_ADMIN_HEADER = "X-Hyperspace-Network-Token"


def _hostctl_configured() -> bool:
    return len(HOSTCTL_TOKEN) >= 32


def _hostctl_headers() -> dict:
    return {"Authorization": f"Bearer {HOSTCTL_TOKEN}"}


def _network_admin_error():
    """Restituisce una risposta Flask se la route admin non è autorizzata."""
    if len(NETWORK_ADMIN_TOKEN) < 32:
        return jsonify({"ok": False, "configured": False,
                        "error": "NETWORK_ADMIN_TOKEN assente o troppo corto — azioni di rete disabilitate"}), 503
    provided = request.headers.get(_NETWORK_ADMIN_HEADER, "")
    if not token_authorized(provided, NETWORK_ADMIN_TOKEN):
        return jsonify({"ok": False, "configured": True,
                        "error": "token amministrativo di rete mancante o non valido"}), 401
    return None


def _is_valid_json_response(r) -> bool:
    """True solo se la risposta è 200 E JSON parsabile. Prima controllava
    solo il Content-Type: un 404/500 con corpo JSON (es. il 404 di default
    di FastAPI, {"detail":"Not Found"}) veniva classificato come risposta
    valida, mascherando un endpoint mancante o rotto come "ping OK".

    Vive qui e non in `main.py` perche' e' un predicato su una risposta HTTP, non
    un pezzo di dominio: lo usano il ping dei nodi, il poll delle metriche e il
    battito, e i tre non hanno niente in comune tranne la risposta che guardano.
    """
    if r.status_code != 200:
        return False
    ct = r.headers.get("Content-Type", "")
    if "text/html" in ct or "text/plain" in ct:
        return False
    try:
        r.json()
        return True
    except Exception:
        return False

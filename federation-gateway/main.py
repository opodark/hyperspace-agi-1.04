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
#
# BOTTLES: /bottles/publish e /bottles/list sono l'unico bootstrap pubblico
# indipendente del progetto (vedi docs/network-panel.md) — un nodo isolato,
# senza ancora accesso alla rete privata Tailscale, deve poterle raggiungere
# da qui. /bottles/announce resta FUORI dalla whitelist di proposito: è
# un'azione admin (richiede NETWORK_ADMIN_TOKEN lato CP) pensata per la
# dashboard interna, non per chiamanti pubblici anonimi.
#
# Rispetto alle route bottle "nude", questo gateway aggiunge due cose:
#   1. un rate-limit di soglia (oltre al proof-of-work e al rate-limit
#      per-IP già nel CP — vedi shared/bottle.py: difesa in profondità, non
#      l'unica barriera);
#   2. la propagazione AUTENTICATA dell'IP pubblico reale del chiamante:
#      senza, il CP vedrebbe solo l'IP Docker interno DI QUESTO GATEWAY per
#      ogni richiesta pubblica, collassando il suo rate-limit per-IP su un
#      unico contatore condiviso da tutto il traffico esterno.
# La firma (sign_client_ip) vive in shared/network_security.py insieme a
# verify_client_ip, usata solo dal CP: stessa primitiva, stessa disciplina
# del resto del progetto — il gateway sa solo firmare, mai validare.

import os
import threading
import time

from flask import Flask, request, Response
import requests

from shared.network_security import sign_client_ip

app = Flask(__name__)

CP_URL = os.getenv("CP_URL", "http://control-plane:8085").rstrip("/")

# Whitelist esplicita (metodo, path). Aggiungere qui SOLO endpoint pensati
# per essere pubblici by design — non aggiungere mai /config/*, /task/*,
# /logs/*, /federation/peers* (gestione allowlist, solo dashboard interna),
# né /bottles/announce (azione admin, vedi commento in cima al file).
ALLOWED_ROUTES = {
    ("POST", "/federate/execute"),
    ("GET",  "/federation/identity"),
    ("POST", "/bottles/publish"),
    ("GET",  "/bottles/list"),
}

_EXCLUDED_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}

# Header con cui questo gateway attesta al CP l'IP pubblico reale del
# chiamante. Riservati: un client esterno non deve MAI poterli impostare lui
# stesso (altrimenti potrebbe far credere al CP di chiamare da un IP
# arbitrario e aggirare il suo rate-limit per-IP) — vengono sempre rimossi
# da quanto arriva dal client prima di inoltrare, su OGNI rotta, non solo
# quelle bottle.
_CLIENT_IP_HEADERS = ("X-Hs-Client-Ip", "X-Hs-Client-Ts", "X-Hs-Client-Sig")
_RESERVED_HEADER_NAMES = {h.lower() for h in _CLIENT_IP_HEADERS} | {"host"}

# Solo le route bottle hanno un rate-limit dedicato qui: sono le uniche
# pensate per traffico pubblico anonimo. path -> (richieste, secondi).
_RATE_LIMITS = {
    "/bottles/publish": (int(os.getenv("GATEWAY_BOTTLE_PUBLISH_MAX_PER_HOUR", "20")), 3600),
    "/bottles/list":    (int(os.getenv("GATEWAY_BOTTLE_LIST_MAX_PER_MINUTE", "30")), 60),
}
_RATE_MAX_TRACKED_KEYS = int(os.getenv("GATEWAY_BOTTLE_RATE_MAX_IPS", "4096"))

_rate_lock = threading.Lock()
_rate_hits: dict[tuple[str, str], list[float]] = {}


def _client_ip() -> str:
    """IP del chiamante da usare per il rate-limit e da attestare al CP.

    Con TRUST_PROXY_HEADERS=true (gateway dietro UN SOLO reverse proxy
    fidato davanti, es. Caddy con `reverse_proxy` di default) legge il primo
    hop di X-Forwarded-For, che quel proxy imposta lui stesso. ATTENZIONE:
    è un'assunzione a un solo hop — con più di un proxy in catena (o dietro
    un tunnel che non normalizza l'header) un client potrebbe falsificare
    gli hop successivi al primo; non abilitare finché la topologia reale
    non è esattamente "un Caddy davanti a questo gateway, niente altro in
    mezzo". Di default (false) usa la connessione TCP diretta, corretta
    quando il gateway è già il primo punto pubblico (es. dietro un tunnel L4
    che non riscrive gli header)."""
    if os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true":
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.remote_addr or "?"


def _rate_check(path: str, ip: str) -> bool:
    limit = _RATE_LIMITS.get(path)
    if not limit:
        return True
    max_hits, window_s = limit
    key = (path, ip)
    now = time.time()
    with _rate_lock:
        for old_key in list(_rate_hits):
            old_path, _ = old_key
            old_window_s = _RATE_LIMITS.get(old_path, (0, window_s))[1]
            kept = [t for t in _rate_hits[old_key] if now - t < old_window_s]
            if kept:
                _rate_hits[old_key] = kept
            else:
                _rate_hits.pop(old_key, None)
        if key not in _rate_hits and len(_rate_hits) >= _RATE_MAX_TRACKED_KEYS:
            return False
        recent = _rate_hits.get(key, [])
        if len(recent) >= max_hits:
            return False
        recent.append(now)
        _rate_hits[key] = recent
        return True


@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
def proxy(path):
    full_path = f"/{path}"
    route_key = (request.method, full_path)
    if route_key not in ALLOWED_ROUTES:
        return {"error": "not found"}, 404

    ip = _client_ip()
    if full_path in _RATE_LIMITS and not _rate_check(full_path, ip):
        return {"error": "troppe richieste, riprova più tardi"}, 429

    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _RESERVED_HEADER_NAMES
    }
    secret = os.getenv("BOTTLE_GATEWAY_SECRET", "")
    if full_path in _RATE_LIMITS and len(secret) >= 32:
        ts = str(time.time())
        forward_headers["X-Hs-Client-Ip"] = ip
        forward_headers["X-Hs-Client-Ts"] = ts
        forward_headers["X-Hs-Client-Sig"] = sign_client_ip(secret, ip, ts)

    try:
        upstream = requests.request(
            method=request.method,
            url=f"{CP_URL}{full_path}",
            headers=forward_headers,
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

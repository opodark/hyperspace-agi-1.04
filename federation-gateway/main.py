# SPDX-License-Identifier: Apache-2.0
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

import json
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
    # /federate/view e' l'unica rotta federata in LETTURA: restituisce a un peer
    # ACCOPPIATO un'istantanea di nodi, modelli, task e log di questo CP
    # (docs/control-plane-sync.md). Sta in whitelist perche' il CP la verifica da
    # solo con firma ECDSA + allowlist, esattamente come /federate/execute: il
    # gateway non aggiunge fiducia, toglie superficie. Nota: condivide solo se
    # FEDERATION_VIEW_ENABLED=true sul CP, che di default e' false — quindi la
    # rotta e' raggiungibile ma risponde 403 finche' l'operatore non lo decide.
    # /federation/views NON e' qui, ed e' deliberato: quella e' la vista
    # aggregata per la dashboard interna, e da fuori diventeremmo una sonda
    # verso i peer federati per chiunque.
    ("GET",  "/federate/view"),
    ("GET",  "/federation/identity"),
    ("POST", "/bottles/publish"),
    ("GET",  "/bottles/list"),
    # Ingresso pubblico dei browser node. Non espone chat, modelli, dashboard o
    # task amministrativi: il browser si registra, tira un task web-safe e ne
    # restituisce l'esito. OPTIONS serve al preflight CORS della pagina HTTPS.
    ("POST", "/web/register"),
    ("POST", "/web/poll"),
    ("POST", "/web/result"),
    ("OPTIONS", "/web/register"),
    ("OPTIONS", "/web/poll"),
    ("OPTIONS", "/web/result"),
    # Esperienza pubblica della mesh: catalogo e chat OpenAI-compatible.
    # Restano escluse dashboard, log, task manuali e configurazione. Il rate
    # limit qui sotto protegge il CP da uso anonimo eccessivo.
    ("GET", "/v1/models"),
    ("OPTIONS", "/v1/models"),
    ("POST", "/v1/chat/completions"),
    ("OPTIONS", "/v1/chat/completions"),
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

# Le route anonime pubbliche hanno un rate-limit dedicato qui. Le bottle hanno
# anche proof-of-work e limiti nel CP; i web node hanno limiti di registro,
# payload e coda nel CP. path -> (richieste, secondi).
_RATE_LIMITS = {
    "/bottles/publish": (int(os.getenv("GATEWAY_BOTTLE_PUBLISH_MAX_PER_HOUR", "20")), 3600),
    "/bottles/list":    (int(os.getenv("GATEWAY_BOTTLE_LIST_MAX_PER_MINUTE", "30")), 60),
    "/web/register":    (int(os.getenv("GATEWAY_WEB_REGISTER_MAX_PER_MINUTE", "12")), 60),
    "/web/poll":        (int(os.getenv("GATEWAY_WEB_POLL_MAX_PER_MINUTE", "30")), 60),
    "/web/result":      (int(os.getenv("GATEWAY_WEB_RESULT_MAX_PER_MINUTE", "30")), 60),
    "/v1/models":       (int(os.getenv("GATEWAY_MODELS_MAX_PER_MINUTE", "30")), 60),
    "/v1/chat/completions": (int(os.getenv("GATEWAY_CHAT_MAX_PER_MINUTE", "12")), 60),
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
    # Il preflight non esegue lavoro applicativo e non deve consumare la quota
    # della chiamata POST che il browser farà subito dopo.
    if request.method != "OPTIONS" and full_path in _RATE_LIMITS and not _rate_check(full_path, ip):
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

    # Il CP puo' restare in attesa del nodo per oltre un minuto prima di inviare
    # gli header. Funnel e alcuni browser interpretano quel silenzio come una
    # connessione morta. Apriamo subito uno stream SSE con un keepalive; se il
    # backend restituisce JSON nativo, lo trasformiamo nello stesso contratto.
    if full_path == "/v1/chat/completions":
        origin = request.headers.get("Origin", "*") or "*"
        method = request.method
        body = request.get_data()
        query = request.args.to_dict(flat=False)

        def chat_stream():
            yield b": gateway connected\n\n"
            try:
                upstream = requests.request(
                    method=method,
                    url=f"{CP_URL}{full_path}",
                    headers=forward_headers,
                    data=body,
                    params=query,
                    timeout=130,
                    stream=True,
                )
                content_type = upstream.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    payload = upstream.json()
                    if not upstream.ok or payload.get("error"):
                        error = payload.get("error") or f"HTTP {upstream.status_code}"
                        if isinstance(error, dict):
                            error = error.get("message") or str(error)
                        yield f"data: {json.dumps({'error': str(error)})}\n\n".encode()
                        return
                    choice = ((payload.get("choices") or [{}])[0])
                    message = choice.get("message") or {}
                    content = message.get("content", "")
                    event = {"choices": [{"delta": {"role": "assistant", "content": content},
                                           "finish_reason": choice.get("finish_reason")} ]}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                    yield b"data: [DONE]\n\n"
                    return
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            except Exception as error:
                yield f"data: {json.dumps({'error': str(error)})}\n\n".encode()

        return Response(
            chat_stream(),
            headers={"Access-Control-Allow-Origin": origin, "Vary": "Origin"},
            content_type="text/event-stream",
        )

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

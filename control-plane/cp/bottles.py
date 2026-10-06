# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/bottles.py
# LE BOTTIGLIE: IL PASSAPORTO DI UN NODO.
#
# Una bottiglia è la promessa firmata di un nodo: "sono io, e questo è il mio
# indirizzo". Non è un semplice registro perché portare con sé la propria identità
# costa. Ogni bottiglia porta dentro una proof-of-work, e verificarne una costa
# circa quanto crearla — che è il motivo per cui il modulo ha due limiti che non
# sono parametri di prestazione ma regole del gioco: quante bottiglie si
# tengono in giro, e quante pubblicazioni si accettano da un IP all'ora.
#
# Il pezzo interessante è `bottles_publish`: verifica. E verifica in un ordine
# preciso, dall'esterno verso l'interno, e ogni ordine è una decisione:
#
#   1. il rate per IP          — prima di guardare il contenuto, perché la
#                                 verifica è la parte costosa e non si fa pagare
#                                 da chi ha già esaurito il turno
#   2. la dimensione            — prima della firma, perché un corpo da 16 MiB non
#                                 si firma e non si verifica: si rifiuta
#   3. la firma                 — la bottiglia è di chi dice di essere
#   4. l'età                    — una bottiglia vecchia non dice niente sul presente
#   5. la proof-of-work         — la prova che è costata qualcosa
#
# E l'annuncio, `bottles_announce`, è l'unico punto dove la chiave privata di
# questo nodo viene usata: sta qui lato server e non reaches mai il browser. Non è
# una scelta di comodo, è la ragione per cui l'annuncio è un'azione server-side e
# non qualcosa che la dashboard potrebbe fare da sé in JavaScript. Per questo
# `endpoint` e `relay_url` nel corpo di una richiesta vengono rifiutati: sono
# configurazione server-side, e accettarli dal client significherebbe annunciare un
# indirizzo scelto da chi chiama.
#
# Sul perché il modulo non ha bisogno di nessun boot: non usa il router, non
# chiama i nodi, non conosce la configurazione avanzata. Usa solo la propria
# identità, e gliela dà `monta()`. È il dominio più staccato fra quelli che sono
# passati qui, e si vede dal fatto che `monta` ha un solo parametro.

import os
import threading
import time

import requests
from flask import Blueprint, jsonify, request

from cp.config import _LOCAL_NODE_ENDPOINT
from cp.http import _network_admin_error
from cp.log import push_log
from shared.bottle import (DEFAULT_DIFFICULTY_BITS as _BOTTLE_DIFFICULTY_BITS,
                           MAX_BOTTLE_BYTES as _MAX_BOTTLE_BYTES,
                           make_bottle, verify_bottle)
from shared.network_security import normalize_http_base, verify_client_ip

_bp = Blueprint("bottles", __name__)

# L'identità di questo nodo: la sola cosa che arriva dal boot. Serve per firmare
# la bottiglia che annuncia, e per metterci dentro l'endpoint pubblico.
_cp_identity: dict = {}


def _serve():
    if not _cp_identity:
        raise RuntimeError(
            "cp.bottles non e' montato: chiama bottles.monta(app, cp_identity=...) "
            "prima di annunciare questo nodo")
    return _cp_identity


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Un intero dall'ambiente, con un soffitto e un pavimento.

    Il pavimento serve perche' un `BOTTLE_MAX_COUNT=0` non significa "nessuna
    bottiglia": significa che il prossimo tentativo fallisce e il messaggio e'
    fuorviante. Il tetto serve perche' un numero scritto male diventa subito un
    denial of service — un rate di un milione di pubblicazioni all'ora non
    protegge niente.
    """
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except (TypeError, ValueError):
        return default


def monta(app, *, cp_identity=None):
    """Registra le tre rotte /bottles/* e tiene l'identita' del nodo."""
    global _cp_identity
    _cp_identity = cp_identity or {}
    app.register_blueprint(_bp)
    return app


def smonta():
    global _cp_identity
    _cp_identity = {}

# ── lo stato: le bottiglie in giro, e il rate per IP ──────────────────────

_bottles: dict = {}


_bottles_lock = threading.Lock()


def _bottles_prune_expired() -> None:
    now = time.time()
    for pubkey in [k for k, b in _bottles.items() if now - b.get("ts", 0) > _BOTTLE_MAX_AGE_S]:
        _bottles.pop(pubkey, None)


_BOTTLE_MAX_COUNT = _bounded_env_int("BOTTLE_MAX_COUNT", 500, 1, 10_000)


_BOTTLE_MAX_AGE_S = _bounded_env_int("BOTTLE_MAX_AGE_S", 3600, 60, 86_400)


_bottle_rate: dict = {}


_bottle_rate_lock = threading.Lock()


def _bottle_rate_check(ip: str) -> bool:
    now = time.time()
    with _bottle_rate_lock:
        # Evita crescita permanente della mappa quando nel tempo cambiano IP.
        for old_ip in list(_bottle_rate):
            kept = [t for t in _bottle_rate[old_ip] if now - t < 3600]
            if kept:
                _bottle_rate[old_ip] = kept
            else:
                _bottle_rate.pop(old_ip, None)
        recent = [t for t in _bottle_rate.get(ip, []) if now - t < 3600]
        if ip not in _bottle_rate and len(_bottle_rate) >= _BOTTLE_RATE_MAX_IPS:
            return False
        if len(recent) >= _BOTTLE_RATE_MAX:
            _bottle_rate[ip] = recent
            return False
        recent.append(now)
        _bottle_rate[ip] = recent
        return True


_BOTTLE_RATE_MAX = _bounded_env_int("BOTTLE_RATE_MAX_PER_HOUR", 20, 1, 10_000)


_BOTTLE_RATE_MAX_IPS = _bounded_env_int("BOTTLE_RATE_MAX_IPS", 4096, 1, 100_000)


def _bottle_client_ip() -> str:
    """IP da usare per il rate-limit dei bottle endpoint.

    Quando la richiesta arriva dal federation-gateway (esposizione pubblica,
    vedi federation-gateway/main.py), request.remote_addr qui sarebbe l'IP
    Docker interno del gateway per OGNI chiamante — collasserebbe il
    rate-limit per-IP sotto su un unico contatore condiviso. Se il gateway
    ha allegato l'attestazione firmata (X-Hs-Client-*, verificata con lo
    stesso BOTTLE_GATEWAY_SECRET), usa quella; altrimenti (rete privata
    diretta, o gateway senza secret configurato) usa la connessione TCP
    reale, corretta in entrambi i casi."""
    ip = request.headers.get("X-Hs-Client-Ip", "")
    ts = request.headers.get("X-Hs-Client-Ts", "")
    sig = request.headers.get("X-Hs-Client-Sig", "")
    if verify_client_ip(os.getenv("BOTTLE_GATEWAY_SECRET", ""), ip, ts, sig):
        return ip
    return request.remote_addr or "?"

# ── pubblicare: la verifica, che e' il punto ──────────────────────────────

@_bp.route('/bottles/publish', methods=['POST'])
def bottles_publish():
    ip = _bottle_client_ip()
    if not _bottle_rate_check(ip):
        return jsonify({"ok": False, "error": "troppe pubblicazioni da questo IP, riprova più tardi"}), 429
    if request.content_length is not None and request.content_length > _MAX_BOTTLE_BYTES:
        return jsonify({"ok": False, "error": "bottiglia troppo grande"}), 413
    bottle = request.get_json(force=True, silent=True) or {}
    valid, reason = verify_bottle(bottle, difficulty_bits=_BOTTLE_DIFFICULTY_BITS, max_age_s=_BOTTLE_MAX_AGE_S)
    if not valid:
        return jsonify({"ok": False, "error": reason}), 400
    with _bottles_lock:
        _bottles_prune_expired()
        pubkey = bottle["pubkey"]
        if pubkey not in _bottles and len(_bottles) >= _BOTTLE_MAX_COUNT:
            oldest = min(_bottles, key=lambda k: _bottles[k].get("ts", 0))
            _bottles.pop(oldest, None)
        _bottles[pubkey] = bottle
    push_log('system', 'Bottle published', f"pubkey={pubkey[:16]}… endpoint={bottle.get('endpoint')}")
    return jsonify({"ok": True})


@_bp.route('/bottles/list')
def bottles_list():
    with _bottles_lock:
        _bottles_prune_expired()
        bottles = list(_bottles.values())
    return jsonify({"count": len(bottles), "difficulty_bits": _BOTTLE_DIFFICULTY_BITS,
                    "max_age_s": _BOTTLE_MAX_AGE_S, "bottles": bottles})

# ── annunciarsi: creare la bottiglia di questo nodo, lato server ──────────

@_bp.route('/bottles/announce', methods=['POST'])
def bottles_announce():
    """Crea e pubblica una bottiglia per QUESTO nodo. La chiave privata vive
    solo qui lato server (_cp_private_key, mai esposta al browser) — è per
    questo che l'annuncio è un'azione server-side e non qualcosa che la
    dashboard potrebbe fare da sola in JS."""
    auth_error = _network_admin_error()
    if auth_error:
        return auth_error
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "il corpo deve essere un oggetto JSON"}), 400
    if "endpoint" in data or "relay_url" in data:
        return jsonify({"ok": False, "error": "endpoint e relay_url sono configurazione server-side; "
                        "usa PUBLIC_ENDPOINT e BOTTLE_RELAY_URL nel .env"}), 400

    endpoint = os.getenv("PUBLIC_ENDPOINT", "") or _LOCAL_NODE_ENDPOINT
    if not endpoint:
        return jsonify({"ok": False, "error": "nessun endpoint da annunciare — imposta PUBLIC_ENDPOINT "
                        "nel .env"}), 400
    try:
        endpoint = normalize_http_base(endpoint)
    except ValueError as error:
        return jsonify({"ok": False, "error": f"PUBLIC_ENDPOINT non valido: {error}"}), 400

    relay_config = os.getenv("BOTTLE_RELAY_URL", "").strip()
    try:
        relay_url = normalize_http_base(relay_config) if relay_config else None
    except ValueError as error:
        return jsonify({"ok": False, "error": f"BOTTLE_RELAY_URL non valido: {error}"}), 400

    # Una sola operazione di mining per processo: impedisce che doppi click o
    # richieste concorrenti saturino tutti i core del control-plane.
    if not _bottle_announce_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "annuncio già in elaborazione"}), 429
    try:
        identita = _serve()
        bottle = make_bottle(identita["public_key"], endpoint,
                             identita["_private_key"],
                             difficulty_bits=_BOTTLE_DIFFICULTY_BITS)
    finally:
        _bottle_announce_lock.release()

    if relay_url is None:
        # nessun relay esterno configurato: pubblica su se stesso, cosi'
        # il pulsante funziona anche in locale senza altre macchine.
        with _bottles_lock:
            _bottles_prune_expired()
            _bottles[bottle["pubkey"]] = bottle
        return jsonify({"ok": True, "relay": "self", "bottle": bottle})

    try:
        r = requests.post(f"{relay_url}/bottles/publish", json=bottle, timeout=10)
        result = r.json() if r.content else {}
        return jsonify({"ok": bool(result.get("ok")), "relay": relay_url, "bottle": bottle,
                        "relay_response": result}), r.status_code
    except requests.RequestException as error:
        return jsonify({"ok": False, "error": f"relay non raggiungibile: {error}", "bottle": bottle}), 502


_bottle_announce_lock = threading.Lock()

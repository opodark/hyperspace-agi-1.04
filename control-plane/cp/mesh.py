# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/mesh.py
# IL REGISTRO DEI NODI E LA SCELTA DI CHI RISPODE.
#
# Il mesh è la parte del control-plane che sa "chi c'è" e sceglie "a chi chiedere".
# Sono due cose distinte che stanno nella stessa sezione per una ragione storica:
# il registro senza il punteggio non sceglierebbe niente, e il punteggio senza il
# registro non avrebbe su che cosa lavorare.
#
# Qui stanno:
#
# - Il registro. `_nodes_by_id`, `_known_endpoints`, `_node_aliases` e l'id del
#   nodo locale. Sono strutture VIVE e mutano in continuamento: ogni nodo che si
#   annuncia ne aggiunge uno, ogni alias ne cambia la riga.
# - L'indirizzamento: come un dizionario-nodo diventa un URL, e come un endpoint
#   diventa un riferimento breve per il formato `modello::ref`.
# - Il punteggio: quanto un nodo è adatto a una richiesta, per backend, per modello
#   e per carico. Ha una cache e un tetto, perché ricalcolarlo a ogni richiesta
#   sarebbe costoso e il risultato cambia poco in poco tempo.
# - Le rotte che l'operatore e il driver leggono: chi c'è, la topologia, gli alias.
#
# Cosa NON c'è, e perché:
#
# - I nodi web (`web_register`, `web_poll` e le altre cinque). Sono worker che
#   girano DENTRO il browser, e il CP non li chiama mai: li cerca e ci mette in
#   coda. È il contrario del mesh, che chiama per nome. Stanno in `cp/webnode.py`.
# - La raccolta delle metriche e il poll dei nodi: sono manutenzione, non
#   indirizzamento, e girano in thread.
# - La federazione fra control-plane: è un altro protocollo, con suoi peer e
#   suoi tempi.
#
# Sullo STATO e sul `global`: `_nodes_by_id` e `_known_endpoints` non vengono mai
# riassegnate, solo mutate in posto, quindi main.py può tenere un riferimento alle
# stesse strutture e continuare a leggerle senza accorgersi di niente. `_node_aliases`
# invece viene riassegnata da `_load_aliases_from_db`, e per quella main.py riceve
# il nuovo valore dalla funzione — la trappola del "salvato ma inerte", chiusa in
# cp/memoria.py e che qui si ripete con un nome diverso.

import json
import re
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import requests
from flask import (Blueprint, Response, jsonify, request, stream_with_context)

import routing as _routing

import shared.db as db
import cp.config as _config
from cp.config import (METRICS_POLL_INTERVAL_S, _LOCAL_NODE_ENABLED,
                       _LOCAL_NODE_ENDPOINT, NODE_ENDPOINTS,
                       REGISTRY_URL, ROUTING_MAX_CANDIDATES,
                       ROUTING_RECENT_PENALTY, ROUTING_RECENT_WINDOW_S,
                       ROUTING_WEIGHT_ENGINE, ROUTING_WEIGHT_GPU,
                       ROUTING_WEIGHT_LATENCY, ROUTING_WEIGHT_LOAD,
                       ROUTING_WEIGHT_TIER, ROUTING_WEIGHT_TPUT,
                       ROUTING_WEIGHT_UPTIME, ROUTING_WEIGHT_VRAM,
)
from cp.http import _sse_headers
from cp.log import push_log
from shared.engine_profiles import all_backend_type_scores as _all_backend_scores
from shared.node_compat import ProtocolWatch

_bp = Blueprint("mesh", __name__)

# Il contesto: cinque oggetti di boot e quattro helper che restano in main.py.
# `_aggregate_mesh_models`, `_latest_metrics`, `_recent_ts` e
# `_score_terms_breakdown` sono ingegneria del nodo (quanti modelli offre, quanto
# è carico, come si scompone il punteggio) e non indirizzamento: stanno in
# main.py perché le usa anche la telemetria e il tool di ricerca.
_contesto = None

# ── lo stato ──────────────────────────────────────────────────────────────────
# Vive qui e viene letto da ventiquattro funzioni che restano in main.py. Non è
# un dettaglio: è il registro della rete, e finché è in due posti la prima
# divergenza è questione di tempo. Il numero è alto perche' metà del
# control-plane chiede "chi c'è" — la selezione dei nodi, i dream, la memoria
# sincronizzata, il doctor, il tool di stato.
#
# L'id del nodo locale NON si calcola qui: lo produce il boot (identita' o
# `LOCAL_NODE_ID` dall'ambiente) e arriva con `monta(local_node_id=...)`.
_LOCAL_NODE_ID = ""
_nodes_by_id: dict = {}
_node_aliases: dict = {}   # node_id -> alias, cache in RAM sincronizzata con SQLite
_known_endpoints: set = set()

# ── la cache del punteggio ────────────────────────────────────────────────────
# Ricalcolare quanto un nodo è adatto costa una richiesta HTTP per ogni nodo, e il
# risultato cambia lentamente: una cache con un tetto breve è la differenza fra
# una scelta di nodo che costa 30 ms e una che costa 2 secondi.
#
# `_PROTOCOL_WATCH` è lo stesso oggetto per cui il resto del control-plane chiede
# "questo nodo parla il protocollo X": se ne crea uno qui, la domanda ha due
# risposte diverse a seconda di chi chiede.
_PROTOCOL_WATCH = ProtocolWatch.from_env()

# I nodi appena scelti dal router (node_id -> istante), per il termine `recent_s`.
# Protetto da lock: la selezione gira su più thread di request.
_recent_routing_picks: dict = {}

# Il punteggio della flotta, calcolato UNA volta e riusato dai due punti che lo
# mostrano (/mesh/nodes e /metrics/nodes). Senza la cache i due calcolavano
# indipendentemente e potevano divergere; con la cache non possono, per costruzione.
_SCORE_CACHE: dict = {}
_SCORE_CACHE_AT = 0.0
_score_cache_lock = threading.Lock()

# Tetto della cache del punteggio: 3/4 dell'intervallo di raccolta metriche, così
# la cache non può essere più vecchia della metrica che dovrebbe contenere.
_SCORE_CACHE_TTL = max(5.0, 0.75 * METRICS_POLL_INTERVAL_S)

# Se e come questo nodo partecipa alla mesh come nodo locale (per l'inferenza
# diretta e per l'annuncio), e l'endpoint con cui si annuncia.


def _serve(*campi):
    if _contesto is None:
        raise RuntimeError(
            "cp.mesh non e' montato: chiama mesh.monta(app, ...) prima di usare "
            "il registro")
    mancanti = [c for c in campi if getattr(_contesto, c, None) is None]
    if mancanti:
        raise RuntimeError("cp.mesh montato senza: " + ", ".join(mancanti))
    return _contesto


def monta(app, *, advanced_config=None, recent_routing_lock=None,
          aggregate_mesh_models=None,
          latest_metrics=None, recent_ts=None, score_terms_breakdown=None,
          local_node_id=None, models_cache=None, rimuovi_campioni=None):
    """Registra le rotte del mesh e tiene i riferimenti al boot."""
    global _contesto, _LOCAL_NODE_ID
    _LOCAL_NODE_ID = local_node_id if local_node_id is not None else ""
    _contesto = SimpleNamespace(
        advanced_config=advanced_config,
        recent_routing_lock=recent_routing_lock,
        aggregate_mesh_models=aggregate_mesh_models,
        latest_metrics=latest_metrics,
        recent_ts=recent_ts,
        score_terms_breakdown=score_terms_breakdown,
        models_cache=models_cache,
        rimuovi_campioni=rimuovi_campioni,
    )
    app.register_blueprint(_bp)
    return app


def riallinea_tetto_cache():
    """Ricalcola il tetto della cache del punteggio dalla frequenza di raccolta.

    Va chiamato quando `METRICS_POLL_INTERVAL_S` cambia. Senza, la cache
    resterebbe valida piu' a lungo della metrica che contiene, e un nodo che
    peggiora continuerebbe a vincere per qualche minuto dopo che e' peggiorato.
    """
    global _SCORE_CACHE_TTL
    _SCORE_CACHE_TTL = max(5.0, 0.75 * _config.METRICS_POLL_INTERVAL_S)


def smonta():
    global _contesto
    _contesto = None

# L'id del nodo locale NON e' qui: lo calcola il boot (identita' o
# `LOCAL_NODE_ID` dall'ambiente) e arriva con `monta(local_node_id=...)`.

# ── HELPERS ───────────────────────────────────────────────────────────────────
def _normalize_endpoint(ep: str) -> str:
    ep = ep.strip().rstrip("/")
    if not ep:
        return ep
    if ep.startswith("http://") or ep.startswith("https://"):
        return ep
    return f"http://{ep}"


def _ep_to_url(ep: str) -> str:
    return _normalize_endpoint(ep)


def _node_ref_for(node_id: str) -> str:
    """Riferimento breve da usare nel formato modello::ref: alias se
    impostato, altrimenti node_id troncato."""
    return _node_aliases.get(node_id) or node_id[:8]


def _node_list():
    out = []
    for n in _nodes_by_id.values():
        nn = dict(n)
        nn["alias"] = _node_aliases.get(nn.get("node_id", ""), "")
        out.append(nn)
    return out


def _load_nodes_from_db():
    nodes = db.get_all_nodes()
    for n in nodes:
        nid = n.get("node_id", "")
        ep  = _normalize_endpoint(n.get("endpoint", ""))
        if not nid:
            continue
        n["endpoint"] = ep
        # Retain old synthetic fallback nodes as history, never as live workers.
        if nid.startswith("local-") and nid != _LOCAL_NODE_ID and not ep:
            n["status"] = "unreachable"
            with db._conn() as con:
                con.execute("UPDATE nodes SET status='unreachable' WHERE node_id=?", (nid,))
        _nodes_by_id[nid] = n
        if ep:
            _known_endpoints.add(ep)
    for ep in NODE_ENDPOINTS:
        _known_endpoints.add(_normalize_endpoint(ep))
    print(f"[CP] Loaded {len(_nodes_by_id)} nodes from DB, {len(_known_endpoints)} known endpoints")


def _load_aliases_from_db():
    global _node_aliases
    _node_aliases = db.get_alias_map()
    print(f"[CP] Loaded {len(_node_aliases)} node aliases from DB")


def _register_local_node():
    """
    Registra la macchina locale come nodo root al boot.
    Se nessun altro nodo e' attivo nella mesh, lo promuove a hub.
    Viene chiamato all'avvio dopo _load_nodes_from_db().
    """
    if not _LOCAL_NODE_ENABLED:
        return

    # Conta nodi attivi (esclude se stesso)
    active_others = [
        n for n in _node_list()
        if n.get("status") == "active" and n.get("node_id") != _LOCAL_NODE_ID
    ]
    # Tier: root se e' l'unico, hub se ci sono altri nodi
    tier = "root" if not active_others else "hub"

    ep = _normalize_endpoint(_LOCAL_NODE_ENDPOINT) if _LOCAL_NODE_ENDPOINT else ""

    # Rileva VRAM approssimativa (macOS unified memory via sysctl)
    vram_gb = 0.0
    try:
        import subprocess
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], stderr=subprocess.DEVNULL
        ).decode().strip()
        vram_gb = round(int(out) / (1024 ** 3), 1)
    except Exception:
        pass

    info = {
        "node_id":      _LOCAL_NODE_ID,
        "endpoint":     ep,
        "tier":         tier,
        "status":       "active",
        "version":      "1.05.0",
        "vram_gb":      vram_gb,
        "peers_active": len(active_others),
        "uptime_s":     0,
        "active_requests": 0,
        "queued_requests": 0,
        "capacity":        1,
        "saturation":      0.0,
        "degraded":        False,
        "backend_type":    "model_manager",
        "last_seen":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capabilities": ["ollama", "control-plane"],
        "is_local":     True,
    }
    _nodes_by_id[_LOCAL_NODE_ID] = info
    if ep:
        _known_endpoints.add(ep)
    db.upsert_node(info)
    print(f"[CP] Local node registered: {_LOCAL_NODE_ID[:20]} tier={tier} vram={vram_gb}GB endpoint={ep or 'ollama-direct'}")
    push_log(
        "mesh_event",
        f"Local node boot: {_LOCAL_NODE_ID[:16]} tier={tier}",
        detail=f"vram={vram_gb}GB active_others={len(active_others)}",
        source=_LOCAL_NODE_ID[:16],
        status="success",
    )
# ── il punteggio: quanto un nodo è adatto a una richiesta ─────────────────────
def _node_score_components(node: dict, model: str = "") -> dict:
    """Breakdown dello score di routing del nodo (fonte unica: _fleet_scores).
    Ritorna il dict con i termini pesati più 'total'. Vuoto se il nodo non è
    un candidato routabile (nessuno score da mostrare, coerente col badge)."""
    e = _fleet_scores().get(node.get("node_id", ""))
    if not e:
        return {}
    return dict(e["breakdown"], total=e["total"])


def _node_score(node: dict, model: str = "") -> float:
    """Score di routing effettivo del nodo. Cerca nella fonte unica
    (_fleet_scores); per contesti fuori flotta (es. topologia che include un
    nodo non eseguibile) ricalcola su contesto ampliato come prima."""
    e = _fleet_scores().get(node.get("node_id", ""))
    if e is not None:
        return e["score"]
    ctx = _active_executable()
    if not any(n.get("node_id") == node.get("node_id") for n in ctx):
        ctx = ctx + [node]
    for _n, score, _b in _routing_scores(ctx, model=model):
        if _n.get("node_id") == node.get("node_id"):
            return score
    return 0.0


def _fleet_scores() -> dict:
    """{node_id: {"score": float, "total": float, "breakdown": dict}} per la
    flotta attiva eseguibile. Ricalcola solo se la cache è scaduta; viene
    invalidata (TTL azzerato) da _record_routing_pick e dal refresh delle
    metriche di un nodo."""
    global _SCORE_CACHE, _SCORE_CACHE_AT
    _serve("latest_metrics")
    now = time.time()
    with _score_cache_lock:
        if _SCORE_CACHE and now - _SCORE_CACHE_AT < _SCORE_CACHE_TTL:
            return _SCORE_CACHE
        out = {}
        for n, score, breakdown in _routing_scores(_active_executable()):
            nid = n.get("node_id", "")
            if not nid:
                continue
            out[nid] = {
                "score": round(score, 4),
                "total": round(_contesto.score_terms_breakdown(breakdown), 4),
                "breakdown": {k: round(v, 4) for k, v in breakdown.items()},
            }
        _SCORE_CACHE = out
        _SCORE_CACHE_AT = now
        return out


def _routing_scores(active_nodes: list, model: str = "") -> list:
    """[(node, score, breakdown)] ordinati per score decrescente. Fonde ogni
    nodo col suo ultimo campione /metrics, normalizza i segnali sul set di
    candidati (control-plane/routing.py, min-max dinamico) e applica la
    penalità "ultimo scelto". E' la funzione centrale del routing v1.05:
    lo score è una funzione delle metriche osservate, non solo di pesi."""
    metrics_map = {n.get("node_id", ""): _contesto.latest_metrics(n.get("node_id", "")) for n in active_nodes}
    sigs = [_routing.extract_signal(n, metrics_map.get(n.get("node_id", "")), model)
            for n in active_nodes]
    _routing.rank_signals(sigs, _config._ROUTING_WEIGHTS, metrics_interval_s=METRICS_POLL_INTERVAL_S)
    now = time.time()
    out = []
    for n, sig in zip(active_nodes, sigs):
        score, breakdown = _routing.compute_score(
            sig, _config._ROUTING_WEIGHTS, _contesto.recent_ts(n.get("node_id", "")), now)
        out.append((n, score, breakdown))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _invalidate_fleet_scores():
    """Azzera il TTL della cache score: il prossimo accesso a _fleet_scores
    ricalcola con i dati correnti (nuovi pick di routing o metriche fresche)."""
    global _SCORE_CACHE_AT
    with _score_cache_lock:
        _SCORE_CACHE_AT = 0.0


def _node_ids_with_model(model: str) -> set:
    """Node id di chi ha davvero 'model' installato, secondo l'ultimo giro di
    _contesto.aggregate_mesh_models(). Usata per filtrare i candidati di routing PRIMA
    dello scoring: senza, un nodo con score alto ma privo del modello
    richiesto vince comunque lo scoring e la richiesta fallisce a valle
    (404 Ollama o risposta vuota) invece di provare un nodo che ce l'ha
    davvero — vedi _select_best_node/_rank_candidate_nodes."""
    agg = _contesto.aggregate_mesh_models()
    return {e["node_id"] for e in agg["per_node"] if e["base_model"] == model}


def _parse_model_node_ref(model: str):
    """Se il model id contiene '::', separa nome modello e riferimento nodo
    (alias, node_id esatto o suo prefisso). Se il riferimento non risolve a
    nessun nodo noto, ritorna comunque il model "pulito" e nessun pin —
    fallback silenzioso allo scoring automatico invece di un errore secco,
    utile se un alias e' stato rimosso dopo che Open WebUI l'ha già
    cachato in una vecchia lista modelli."""
    if "::" not in model:
        return model, None
    base, ref = model.split("::", 1)
    base, ref = base.strip(), ref.strip()
    if not ref:
        return base, None
    nid = db.get_node_id_by_alias(ref)
    if nid:
        return base, nid
    for n in _node_list():
        node_id = n.get("node_id", "")
        if node_id == ref or node_id.startswith(ref):
            return base, node_id
    return base, None


def _record_routing_pick(node_id: str):
    """Registra un tentativo di routing verso il nodo (per il termine
    recent_s). Pruning dei riferimenti scaduti per non far crescere la mappa."""
    now = time.time()
    with _contesto.recent_routing_lock:
        _recent_routing_picks[node_id] = now
        cutoff = now - 3 * ROUTING_RECENT_WINDOW_S
        for k in [k for k, v in _recent_routing_picks.items() if v < cutoff]:
            _recent_routing_picks.pop(k, None)
    # La penalità recent_s deve comparire subito anche nel display (fonte
    # unica _fleet_scores): invalida la cache così il prossimo refresh la
    # ricalcola col nuovo timestamp di routing.
    _invalidate_fleet_scores()


def _active_executable() -> list:
    attivi = [n for n in _node_list() if n.get("status") == "active" and _best_endpoint(n)]
    return _PROTOCOL_WATCH.report(attivi)


def _best_endpoint(node_info):
    """Base HTTP con cui il CP puo' CHIAMARE il nodo, oppure "" se non esiste.

    Un web node non e' indirizzabile: `browser://<id>` e' solo un
    identificativo, non un endpoint. Restituire "" qui lo esclude in un colpo
    solo da OGNI filtro `executable` (routing chat, tool loop, dream, peers...)
    invece di ripetere un controllo `is_web_node` in ogni call-site — che e'
    esattamente il buco che il campo aveva prima di questa guardia: il web node
    finiva fra i candidati del routing chat e il CP provava a chiamare
    `http://browser://<id>/v1/chat/completions`.
    """
    raw = str(node_info.get("endpoint", "") or "").strip()
    if raw.startswith("browser://") or node_info.get("is_web_node"):
        return ""
    ep = _normalize_endpoint(raw)
    if ep.startswith("https://"): return ep
    public = str(node_info.get("public_endpoint", "") or "").strip()
    if public.startswith("browser://"):
        public = ""
    public = _normalize_endpoint(public)
    if public and public.startswith("https://"): return public
    return ep


# ── l'annuncio: la via in cui un nodo si presenta ──────────────────────────────
# Va prima delle altre perche' e' l'unica che AGGIUNGE al registro: le rotte
# sotto leggono e scrivono un registro che qualcuno deve aver riempito prima, e
# se non c'e' nessuno che lo faccia rispondono tutti "vuoto".
#
# Un nodo si annuncia piu' volte, e non ogni annuncio e' la stessa cosa. Sono tre
# casi distinti, ed è qui che si sbagliava piu' volentieri:
#
# 1. Endpoint nuovo o diverso: si aggiorna tutto. E' la registrazione.
# 2. Endpoint identico: NON si aggiorna, ma si rinfresca `status` e `last_seen`.
#    E' il battito, e senza il rinfresco un nodo che ha sopravvissuto a un reboot
#    del control-plane resterebbe "unreachable" per sempre — sparirebbe da
#    /v1/models senza mai diventare un errore.
# 3. Endpoint peggiore (https che scende a http): si rifiuta. Altrimenti un nodo
#    con la configurazione sbagliata continuerebbe a vincere, e non si saprebbe
#    piu' dire perche'.
#
# Un nodo web non ha endpoint in ingresso: se non lo manda, gliene viene dato uno
# sintetico `browser://`, e da li' in poi il registro lo tratta come gli altri.
@_bp.route('/mesh/announce', methods=['POST'])
def mesh_announce():
    data = request.get_json(force=True, silent=True) or {}
    ep   = _normalize_endpoint(data.get("endpoint", ""))
    nid  = data.get("node_id", "")

    if not nid:
        return jsonify({"ok": False, "error": "missing node_id"}), 400

    # Accetta endpoint browser:// per web-nodes (synthetic)
    if not ep:
        ep = f"browser://{nid}"

    existing      = _nodes_by_id.get(nid)
    should_update = True
    if existing:
        existing_ep = _normalize_endpoint(existing.get("endpoint", ""))
        if existing_ep == ep:
            should_update = False
        elif existing_ep.startswith("https://") and not ep.startswith("https://") and not ep.startswith("browser://"):
            should_update = False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if should_update:
        info = {**data, "endpoint": ep, "status": "active", "last_seen": now,
                "is_web_node": ep.startswith("browser://")}
        _nodes_by_id[nid] = info
        _known_endpoints.add(ep)
        db.upsert_node(info)
    else:
        # Endpoint invariato, ma il nodo sta annunciando (heartbeat): rinfresca
        # comunque last_seen e riporta lo stato ad "active". Senza questo, dopo
        # un reboot del CP (_load_nodes_from_db marca tutti i nodi "unreachable"),
        # un nodo che ri-annuncia lo STESSO endpoint resterebbe "unreachable"
        # per sempre e sparirebbe da /v1/models.
        existing["status"] = "active"
        existing["last_seen"] = now
        _nodes_by_id[nid] = existing
        db.upsert_node(existing)
    push_log('mesh_event', f'Node announced: {nid[:12]}',
             f'endpoint={ep} accepted={should_update}', source=nid[:12], status='success')
    return jsonify({"ok": True, "registered": ep, "accepted": should_update})


# ── le rotte che l'operatore e il driver leggono ──
@_bp.route('/mesh/nodes')
def get_mesh_nodes():
    nodes = _node_list()
    # Score reale v1.05 (ibrido qualità+strutturale, normalizzato sul set di
    # candidati + penalità "ultimo scelto") dalla FONTE UNICA _fleet_scores():
    # la stessa usata da /metrics/nodes, così badge e breakdown coincidono.
    # La dashboard non deve ricostruire la formula lato client con i vecchi
    # pesi statici. Nodi non candidati (inattivi, senza endpoint eseguibile)
    # restano senza routing_score: il client usa il fallback strutturale.
    scores = _fleet_scores()
    for n in nodes:
        e = scores.get(n.get("node_id", ""))
        if e:
            n["routing_score"] = e["score"]
    return jsonify(nodes)


@_bp.route('/mesh/nodes/<node_id>', methods=['DELETE'])
def delete_mesh_node(node_id):
    node = _nodes_by_id.get(node_id)
    if not node:
        return jsonify({"ok": False, "error": "nodo non trovato"}), 404
    if node.get("status") == "active" or node.get("is_local") or node_id == _LOCAL_NODE_ID:
        return jsonify({"ok": False, "error": "un nodo attivo o locale non puo essere rimosso"}), 409
    _nodes_by_id.pop(node_id, None)
    endpoint = _normalize_endpoint(node.get("endpoint", ""))
    if endpoint and not any(_normalize_endpoint(n.get("endpoint", "")) == endpoint for n in _nodes_by_id.values()):
        _known_endpoints.discard(endpoint)
    db.delete_node(node_id)
    _node_aliases.pop(node_id, None)
    with _contesto.node_metrics_lock:
        _contesto.rimuovi_campioni(node_id, None)
    push_log('mesh_event', f'Nodo obsoleto rimosso: {node_id[:16]}',
             detail=f'endpoint={endpoint}', status='info')
    return jsonify({"ok": True, "node_id": node_id})


@_bp.route('/nodes/aliases')
def list_node_aliases():
    return jsonify(_node_aliases)


@_bp.route('/nodes/<node_id>/alias', methods=['POST'])
def set_node_alias_route(node_id):
    data  = request.get_json(force=True, silent=True) or {}
    alias = str(data.get("alias", "")).strip()
    if not alias:
        return jsonify({"error": "alias obbligatorio"}), 400
    if not re.match(r'^[a-zA-Z0-9_-]{1,32}$', alias):
        return jsonify({"error": "alias non valido: solo lettere, numeri, - e _ (max 32 caratteri)"}), 400
    if node_id not in _nodes_by_id:
        return jsonify({"error": "nodo non trovato"}), 404
    owner = db.get_node_id_by_alias(alias)
    if owner and owner != node_id:
        return jsonify({"error": f"alias '{alias}' già assegnato al nodo {owner[:16]}…"}), 409
    try:
        db.set_node_alias(node_id, alias)
    except Exception as e:
        return jsonify({"error": f"alias non disponibile: {e}"}), 409
    _load_aliases_from_db()
    _contesto.models_cache["data"] = None  # forza refresh cache modelli col nuovo ref
    push_log('mesh_event', f'Alias impostato: {node_id[:16]} -> {alias}', status='success')
    return jsonify({"ok": True, "node_id": node_id, "alias": alias})


@_bp.route('/nodes/<node_id>/alias', methods=['DELETE'])
def remove_node_alias_route(node_id):
    db.delete_node_alias(node_id)
    _load_aliases_from_db()
    _contesto.models_cache["data"] = None
    push_log('mesh_event', f'Alias rimosso: {node_id[:16]}', status='info')
    return jsonify({"ok": True})


@_bp.route('/mesh/node/<path:endpoint>/status')
def get_node_status(endpoint):
    try:
        return jsonify(requests.get(f"{_ep_to_url(endpoint)}/status", timeout=3).json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@_bp.route('/mesh/node/<path:endpoint>/peers')
def get_node_peers(endpoint):
    try:
        return jsonify(requests.get(f"{_ep_to_url(endpoint)}/peers", timeout=3).json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@_bp.route('/mesh/topology')
def mesh_topology():
    nodes_out, edges_out, seen_edges = [], [], set()
    for nid, node in _nodes_by_id.items():
        nodes_out.append({
            "id": nid, "tier": node.get("tier","leaf"),
            "endpoint": node.get("endpoint",""),
            "peers_active": node.get("peers_active",0),
            "uptime_s": node.get("uptime_s",0),
            "version": node.get("version",""),
            "status": node.get("status","active"),
            "score": round(_node_score(node), 3),
        })
        try:
            r = requests.get(f"{_best_endpoint(node)}/peers", timeout=2)
            for peer in r.json().get("peers", []):
                pid = peer.get("node_id","")
                if not pid or pid == nid: continue
                ek = tuple(sorted([nid, pid]))
                if ek not in seen_edges:
                    seen_edges.add(ek)
                    edges_out.append({"source": nid, "target": pid,
                                      "active": peer.get("status","active")=="active"})
        except Exception:
            pass
    return jsonify({"nodes": nodes_out, "edges": edges_out})


@_bp.route('/mesh/node/<path:endpoint>/pull', methods=['POST'])
def node_pull_model(endpoint):
    data  = request.get_json(force=True, silent=True) or {}
    model = data.get("model", _contesto.advanced_config["ollama"]["defaultModel"])
    def generate():
        try:
            with requests.post(f"{_ep_to_url(endpoint)}/ollama/pull",
                               json={"model": model}, stream=True, timeout=600) as resp:
                for line in resp.iter_lines():
                    if line: yield f"{line.decode()}\n\n"
            yield 'data: {"status":"done"}\n\n'
        except Exception as e:
            yield ('data: ' + json.dumps({'error': str(e)}, ensure_ascii=False) + '\n\n')
    return Response(stream_with_context(generate()), headers=_sse_headers())

# ── REGISTRY PROXY ────────────────────────────────────────────────────────────
@_bp.route('/registry/nodes')
def registry_nodes():
    try:
        # /nodes/active (non /nodes) è già TTL-filtrato e in forma flat —
        # espone anche active_requests/queued_requests/max_concurrent
        # (vedi registry/registry.py:_active_nodes), a differenza del
        # NodeRecord grezzo con metadata annidata che restituiva /nodes.
        r = requests.get(f"{REGISTRY_URL}/nodes/active", timeout=5)
        return jsonify(r.json().get("nodes", []))
    except Exception as e:
        return jsonify({"error": str(e), "registry_url": REGISTRY_URL}), 503

# ── ROUTING WEIGHTS ────────────────────────────────────────────────────────────
# Espone i pesi correnti dello scoring alla dashboard, così il badge
# score/load visualizzato riflette davvero cosa decide il routing invece di
# una formula hardcoded lato client che può disallinearsi silenziosamente
# se questi valori cambiano via .env.
@_bp.route('/config/routing-weights')
def get_routing_weights():
    return jsonify({
        "vram":   ROUTING_WEIGHT_VRAM,
        "load":   ROUTING_WEIGHT_LOAD,
        "tier":   ROUTING_WEIGHT_TIER,
        "uptime": ROUTING_WEIGHT_UPTIME,
        "backend": ROUTING_WEIGHT_ENGINE,
        "latency": ROUTING_WEIGHT_LATENCY,
        "tput":   ROUTING_WEIGHT_TPUT,
        "gpu":    ROUTING_WEIGHT_GPU,
        "recent_penalty": ROUTING_RECENT_PENALTY,
        "recent_window":  ROUTING_RECENT_WINDOW_S,
        "backend_scores": _all_backend_scores(),
        "max_candidates": ROUTING_MAX_CANDIDATES,
    })

# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/metriche.py
# LE METRICHE DEI NODI: IL POLLER E QUELLO CHE SE NE VEDE.
#
# Il mesh sceglie un nodo in base a metriche, ma non le raccoglie: sono un altro
# lavoro, con un altro ritmo e un altro modo di guastarsi. Qui stanno il raccoglitore
# (un thread che interroga ogni nodo), la cache dei campioni, la rotta che li mostra
# e la funzione che il mesh usa per l'ultimo campione di un nodo.
#
# Questa separazione e' anche una correzione: fino alla prima estrazione la cache
# viveva in main.py e il mesh la riceveva per contesto, perche' a scriverla era il
# thread. Il filo attraversava il confine in due direzioni e ogni pezzo ne aveva un
# pezzo suo — il tipo di accordo che regge finche' nessuno tocca niente, e poi si
# rompe quando qualcuno aggiunge un pezzo. Adesso la cache ha un solo proprietario:
# questo modulo. Il mesh non la riceve piu', la chiede con `ultima_metrica`.
#
# Sul lock: `_node_metrics_lock` protegge la cache dei campioni, e i campioni
# vengono letti dal thread del punteggio mentre il raccoglitore li scrive. Senza,
# `/metrics/nodes` puo' vedere una deque a meta' aggiornamento.
#
# Sul perche' `METRICS_WINDOW` non e' qui dentro ma arriva dalla tab Setup: la
# finestra storica e' un parametro, e cambiarlo deve poter succedere a runtime senza
# riavvio. Per questo esiste `ridimensiona_finestra()`.

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify

from cp.config import (METRICS_BACKOFF_BASE_S, METRICS_MAX_BACKOFF_S, METRICS_MAX_WORKERS,
                       METRICS_POLL_INTERVAL_S, METRICS_POLL_TIMEOUT_S, METRICS_WINDOW,
                       NODE_METRICS_SCHEMA_VERSION, _LOCAL_NODE_ENABLED)
from cp.http import _is_valid_json_response
import cp.mesh as mesh
from cp.mesh import (_best_endpoint, _invalidate_fleet_scores,
                     _node_list, _node_score_components)

_bp = Blueprint("metriche", __name__)

# L'id del nodo locale nasce dall'identita', quindi arriva montando. Non e'
# dentro `cp/config.py` perche' non e' una costante di ambiente: e' un hash
# dell'identita' che il boot ha gia' prodotto.
_LOCAL_NODE_ID = ""

# ── lo stato e l'ultimo campione ──────────────────────────────────────────────
_node_metrics_cache: dict = {}
_node_metrics_lock = threading.Lock()


def _latest_metrics(nid: str) -> dict:
    """Ultimo campione /metrics del nodo, senza mutare la cache. None se
    mai raccolto (nodo non ancora pollato o irraggiungibile)."""
    with _node_metrics_lock:
        entry = _node_metrics_cache.get(nid)
        if not entry:
            return None
        samples = entry["samples"]
        return samples[-1] if samples else None


def ultima_metrica(nid: str):
    """Il campione di un nodo, per chi non vuole sapere come e' fatto.

    Il mesh chiama questa invece di ricevere la cache per contesto: la promessa
    "leggi l'ultimo campione" e' molto piu' piccola di "ecco il dizionario, non
    toccarlo", e non si puo' violare per sbaglio.
    """
    return _latest_metrics(nid)


def ridimensiona_finestra(ventaglie: int):
    """Cambia la finestra storica dei campioni, a runtime.

    Chiamata dalla tab Setup quando cambia `METRICS_WINDOW`. La deque va
    ricostruita: `deque(..., maxlen=)` tronca dalla testa, ed e' esattamente
    quello che serve — tenerli tutti e poi tagliare ricostr ammettendo di
    perdere i campioni piu' vecchi.
    """
    global METRICS_WINDOW
    METRICS_WINDOW = max(2, int(ventaglie))
    with _node_metrics_lock:
        for entry in _node_metrics_cache.values():
            entry["samples"] = type(entry["samples"])(entry["samples"],
                                                      maxlen=METRICS_WINDOW)


def monta(app, *, local_node_id=None):
    """Registra `/metrics/nodes`. Lo stato e' gia' pronto; l'unica cosa che
    arriva dal boot e' l'id del nodo locale."""
    global _LOCAL_NODE_ID
    _LOCAL_NODE_ID = local_node_id if local_node_id is not None else ""
    app.register_blueprint(_bp)
    return app

# ── lo stato e l'ultimo campione ────────────────────────────────────────────_node_metrics_cache: dict = {}


_node_metrics_lock = threading.Lock()



def _latest_metrics(nid: str) -> dict:
    """Ultimo campione /metrics del nodo, senza mutare la cache. None se
    mai raccolto (nodo non ancora pollato o irraggiungibile)."""
    with _node_metrics_lock:
        entry = _node_metrics_cache.get(nid)
        if not entry:
            return None
        samples = entry["samples"]
        return samples[-1] if samples else None
# ── la rotta che l'operatore legge ──────────────────────────────────────────
@_bp.route('/metrics/nodes')
def get_metrics_nodes():
    """Metriche backend normalizzate dei nodi (vedi node/backend_metrics.py):
    ultimo campione + finestra storica in-memory (per mini-grafici) +
    breakdown dello score di routing (per spiegare il ranking). I nodi senza
    campioni raccolti (mai pollati o irraggiungibili) restano in lista con
    metrics/history nulli e status coerente con l'ultimo polling."""
    with _node_metrics_lock:
        cache_snapshot = {
            nid: {
                "samples":         list(entry["samples"]),
                "endpoint":        entry["endpoint"],
                "status":          entry["status"],
                "last_at":         entry.get("last_at", 0.0),
                "last_error":      entry.get("last_error"),
                "schema_mismatch": entry.get("schema_mismatch", False),
            }
            for nid, entry in _node_metrics_cache.items()
        }
    now = time.time()
    nodes = []
    for n in _node_list():
        nid    = n.get("node_id", "")
        entry  = cache_snapshot.get(nid)
        samples = entry["samples"] if entry else []
        last    = samples[-1] if samples else None
        sample_age_s = round(max(now - entry["last_at"], 0.0), 1) if entry and entry["last_at"] else None
        breakdown = None
        if n.get("status") == "active":
            # Fonte unica _fleet_scores(): stesso valore di /mesh/nodes
            # (routing_score). Nessun ricalcolo con contesto degenere.
            comp = _node_score_components(n)
            if comp:
                breakdown = comp
        nodes.append({
            "node_id":    nid,
            "alias":      mesh._node_aliases.get(nid, ""),
            "endpoint":   entry["endpoint"] if entry else _best_endpoint(n),
            "status":     entry["status"] if entry else n.get("status", "unknown"),
            # Freschezza: età dell'ultimo campione raccolto e flag stale
            # (età > 2x intervallo di poll = un ciclo saltato o nodo giù).
            "sample_age_s":  sample_age_s,
            "stale":         sample_age_s is not None and sample_age_s > 2 * METRICS_POLL_INTERVAL_S,
            "last_collected_at": (
                datetime.fromtimestamp(entry["last_at"], timezone.utc).isoformat(timespec="seconds")
                if entry and entry["last_at"] else None),
            "last_error":      entry["last_error"] if entry else None,
            "schema_version":  (last or {}).get("schema_version"),
            "schema_mismatch": entry["schema_mismatch"] if entry else None,
            "metrics":    last,
            "history":    [
                {"sampled_at":  s.get("sampled_at"),
                 "collected_at": s.get("collected_at"),
                 "server":     s.get("server", {}),
                 "load":       s.get("load", {}),
                 "runtime":    s.get("runtime", {})}
                for s in samples[-METRICS_WINDOW:]
            ],
            "score_breakdown": breakdown,
        })
    return jsonify({
        "sampled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval_s": METRICS_POLL_INTERVAL_S,
        "window":     METRICS_WINDOW,
        "schema_version": NODE_METRICS_SCHEMA_VERSION,
        "nodes":      nodes,
    })
# ── il raccoglitore, e il thread che lo chiama ──────────────────────────────
def _collect_node_metrics():
    now = time.time()
    # Nodi candidati: attivi, con id ed endpoint eseguibile, non locali, e
    # FUORI dal backoff — un nodo irraggiungibile non va martellato a ogni
    # ciclo (vedi METRICS_BACKOFF_BASE_S / METRICS_MAX_BACKOFF_S).
    candidates = []
    for n in _node_list():
        if n.get("status") != "active":
            continue
        nid = n.get("node_id", "")
        if not nid:
            continue
        if _LOCAL_NODE_ENABLED and nid == _LOCAL_NODE_ID:
            continue
        ep = _best_endpoint(n)
        if not ep:
            continue
        with _node_metrics_lock:
            existing = _node_metrics_cache.get(nid)
            if existing and now < existing.get("next_try_at", 0.0):
                continue
        candidates.append((nid, ep))

    def _fetch(nid, ep):
        try:
            r = requests.get(f"{ep}/metrics", timeout=METRICS_POLL_TIMEOUT_S)
            if r.status_code != 200 or not _is_valid_json_response(r):
                raise ValueError(f"HTTP {r.status_code}")
            payload = r.json()
            # Timbro l'istante di raccolta lato CP: il sampled_at del nodo può
            # restare identico tra poll (cache TTL lato nodo), quindi senza un
            # collected_at locale lo storico apparirebbe con campioni duplicati.
            # Microsecondi: a cadenza breve secondi non basterebbero a rendere
            # distinti due campioni ravvicinati.
            payload["collected_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            return nid, ep, payload, None
        except Exception as e:
            return nid, ep, None, str(e)[:120]

    # Fetch in PARALLELO: con N nodi e timeout l'uno, la versione seriale
    # sforerebbe l'intervallo di poll (requests è thread-safe; l'aggiornamento
    # della cache avviene sotto lock subito dopo).
    results = []
    if candidates:
        with ThreadPoolExecutor(max_workers=METRICS_MAX_WORKERS) as ex:
            results = list(ex.map(lambda c: _fetch(*c), candidates))

    new_sample = False
    for nid, ep, payload, err in results:
        with _node_metrics_lock:
            entry = _node_metrics_cache.setdefault(nid, {
                "samples": deque(maxlen=METRICS_WINDOW),
                "endpoint": "", "status": "unknown", "last_at": 0.0,
                "last_error": None, "error_ts": None,
                "fail_streak": 0, "next_try_at": 0.0, "schema_mismatch": False,
            })
            if err is not None:
                entry["status"]     = "unreachable"
                entry["last_error"] = err
                entry["error_ts"]   = now
                entry["fail_streak"] = entry.get("fail_streak", 0) + 1
                entry["next_try_at"] = now + min(
                    METRICS_BACKOFF_BASE_S * (2 ** max(entry["fail_streak"] - 1, 0)),
                    METRICS_MAX_BACKOFF_S,
                )
            else:
                entry["samples"].append(payload)
                entry["endpoint"]      = ep
                entry["status"]        = "active"
                entry["last_at"]       = now
                entry["last_error"]    = None
                entry["error_ts"]      = None
                entry["fail_streak"]   = 0
                entry["next_try_at"]   = 0.0
                # Deployment eterogeneo: uno schema diverso resta esposto ma
                # marcato, così il consumatore non lo interpreta alla cieca.
                entry["schema_mismatch"] = payload.get("schema_version") != NODE_METRICS_SCHEMA_VERSION
                new_sample = True
    # Campioni freschi -> lo score (fonte unica _fleet_scores) riflette subito
    # carico/saturazione/degradazione, senza aspettare la scadenza del TTL.
    # Chiamata FUORI da _node_metrics_lock: _fleet_scores prende prima
    # _score_cache_lock e poi _node_metrics_lock, l'ordine inverso deadloccerebbe.
    if new_sample:
        _invalidate_fleet_scores()
    # Prune SOLO dei nodi scomparsi dalla mesh. Un nodo temporaneamente giù
    # (in backoff, nessun campione fresco) MANTIENE cache e storico: sono
    # proprio i dati da tenere per capire cosa è successo.
    with _node_metrics_lock:
        live_ids = {n.get("node_id") for n in _node_list() if n.get("node_id")}
        for nid in [k for k in _node_metrics_cache if k not in live_ids]:
            _node_metrics_cache.pop(nid, None)



def metrics_loop():
    time.sleep(5)
    while True:
        cycle_start = time.time()
        _collect_node_metrics()
        elapsed = time.time() - cycle_start
        time.sleep(max(METRICS_POLL_INTERVAL_S - elapsed, 1))
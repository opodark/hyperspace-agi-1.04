# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/memoria.py
# LE VOCI DI MEMORIA: come si ordinano, si potano, si scrivono, e chi le vede.
#
# Qui c'è tutto quello che riguarda una voce di memoria, e nient'altro. Non la
# memoria come dominio — cioè cosa si ricorda e perché, che è un'altra domanda —
# ma la meccanica: il file, il formato, il tetto, l'ordine.
#
# Zero dipendenze dal boot, e non per merito: `memory_sync` è costruito qui dentro
# con `costruisci()`, perché tutto quello che gli serve (`_hermes_memory`,
# `push_log`, `MEMORY_FILE_GZ`) è già in un modulo. Non è un caso: quando la
# memoria era in main.py, `memory_sync` era un oggetto creato lì e ricaricato da
# `_reload_memory_sync`, che lo riassegnava con `global`. Se le funzioni che lo
# usano fossero state in un modulo e l'oggetto qui, il ricaricamento avrebbe
# aggiornato la copia del monolite e quelle funzioni avrebbero letto il vecchio —
# il "salvato ma inerte" che la tab Setup esiste per evitare, identico a quello
# chiuso per `CHANNEL_MODEL`.
#
# Quindi l'oggetto è di questo modulo e `ricarica()` lo restituisce al chiamante.
# Nove funzioni in main.py lo leggono, e nessuna deve tenere una copia.

import gzip
import json
import os
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, jsonify, request

from cp.config import (MEMORY_BACKEND, MEMORY_FILE_GZ, MEMORY_MAX_ENTRIES,
                       MEMORY_TTL_DAYS, UI_BRIDGE_URL, _hermes_memory)
from cp.log import push_log
from shared.hermes_memory import HermesMemoryError
from shared.memory_sync import MemorySync, from_env


_bp = Blueprint("memoria", __name__)


def monta(app):
    """Registra le sei rotte /memory*.

    Nessuna iniezione: il negozio e' gia' costruito qui sotto, e il modulo e'
    quello che lo possiede. Il forge e' l'unico dominio senza niente; questo non ha
    bisogno di un boot, ma ha bisogno di se' stesso — che e' la forma piu' semplice
    di iniettare qualcosa.
    """
    app.register_blueprint(_bp)
    return app


def costruisci() -> MemorySync:
    """Il `MemorySync` del processo. Chiamata una volta all'avvio e a ogni
    salvataggio dalla tab Setup."""
    return from_env(_hermes_memory, log=push_log, memory_file=MEMORY_FILE_GZ)


memory_sync = costruisci()


def ricarica() -> MemorySync:
    """Ricostruisce dopo un salvataggio in tab Setup e restituisce il nuovo.

    Ritornarlo è il punto: un `global` qui dentro aggiornerebbe questo modulo e
    non il namespace di chi chiama, e il chiamante continuerebbe a usare il
    vecchio senza accorgersene.
    """
    global memory_sync
    memory_sync = costruisci()
    return memory_sync


# ── i timestamp: come si ordinano e come si scrivono ──────────────────────────

def _ts_sort_key(entry: dict) -> float:
    ts = entry.get("ts") or entry.get("timestamp")
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def _ts_to_iso(ts) -> str:
    if ts is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(ts)[:20]

# ── le voci: si caricano, si potano, si salvano, si accodano ──────────────────

def _load_memory() -> list:
    if MEMORY_BACKEND == "hermes":
        # Con Hermes spento non si torna vuoti: si legge il mirror locale e la
        # lettura degradata resta scritta nei log da MemorySync.
        return memory_sync.read(MEMORY_MAX_ENTRIES)["entries"]
    if MEMORY_BACKEND != "legacy":
        raise RuntimeError(f"unsupported MEMORY_BACKEND: {MEMORY_BACKEND}")
    if not os.path.exists(MEMORY_FILE_GZ):
        return []
    try:
        with gzip.open(MEMORY_FILE_GZ, "rt", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def _prune_memory(entries: list) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=MEMORY_TTL_DAYS)
    fresh = []
    for e in entries:
        ts_val = e.get("ts") or e.get("timestamp")
        try:
            if isinstance(ts_val, (int, float)):
                ts_dt = datetime.fromtimestamp(float(ts_val), tz=timezone.utc)
            else:
                ts_dt = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
            if ts_dt >= cutoff:
                fresh.append(e)
        except Exception:
            fresh.append(e)
    fresh.sort(key=_ts_sort_key, reverse=True)
    return fresh[:MEMORY_MAX_ENTRIES]

def _save_memory(entries: list) -> None:
    if MEMORY_BACKEND != "legacy":
        raise RuntimeError("legacy memory writes are disabled; Hermes is authoritative")
    with gzip.open(MEMORY_FILE_GZ, "wt", encoding="utf-8") as f:
        json.dump(_prune_memory(entries), f, ensure_ascii=False)

def _memory_append(entry: dict):
    if "ts" not in entry and "timestamp" in entry:
        entry["ts"] = _ts_to_iso(entry["timestamp"])
    if MEMORY_BACKEND == "hermes":
        # Scrittura locale-prima: il mirror si scrive sempre, e se Hermes non
        # risponde la voce va in coda invece di andare persa. Torna `deferred`.
        return memory_sync.write(entry)
    if MEMORY_BACKEND != "legacy":
        raise RuntimeError(f"unsupported MEMORY_BACKEND: {MEMORY_BACKEND}")
    entries = _load_memory()
    ts_key      = entry.get("ts") or entry.get("timestamp", "")
    content_key = str(entry.get("content", "") or entry.get("prompt", ""))[:64]
    dedup_key   = f"{ts_key}:{content_key}"
    existing_keys = {
        f"{e.get('ts') or e.get('timestamp','')}:{str(e.get('content','') or e.get('prompt',''))[:64]}"
        for e in entries
    }
    if dedup_key not in existing_keys:
        entries.append(entry)
        _save_memory(entries)

# ── il bridge: la UI che mostra le voci mentre arrivano ───────────────────────

def _notify_bridge(event_type: str, payload: dict):
    try:
        requests.post(f"{UI_BRIDGE_URL}/push/{event_type}", json=payload, timeout=1.5)
    except Exception:
        pass


def costruisci() -> MemorySync:
    return from_env(_hermes_memory, log=push_log, memory_file=MEMORY_FILE_GZ)# ── il backend, e quale dei due ha risposto ───────────────────────────────

# ── MEMORY ENDPOINTS ──────────────────────────────────────────────────────────
def _memory_effective_backend() -> str:
    """Backend effettivo: `legacy` quando il backend autorevole Hermes è giù.

    Il failsafe è "sticky": dopo un errore di trasporto Hermes resta marcato
    irraggiungibile per HERMES_MEMORY_COOLDOWN_S, e in quel periodo le letture
    leggono il mirror locale (lo stesso file gzip di `MEMORY_BACKEND=legacy`)
    senza pagare il timeout a ogni richiesta. Allo scadere si ritenta Hermes.
    """
    if MEMORY_BACKEND != "hermes":
        return MEMORY_BACKEND
    if _hermes_memory.unavailable():
        return "legacy"
    return "hermes"

# ── la ricerca, con il ripiego sul mirror locale ──────────────────────────

def _memory_search_local(data: dict, *, reason: str = "", entries=None,
                         source: str = "mirror", degraded: bool = True):
    """Browse testuale per mirror degradato e backend legacy.

    Hermes offre una ricerca più ricca, ma la dashboard deve poter leggere la
    memoria anche quando resta soltanto il file locale. I filtri comuni restano
    disponibili; ciò che non esiste localmente non viene inventato.
    """
    query = str(data.get("query", "")).strip().lower()
    limite = max(1, int(data.get("limit", 50)))
    status = str(data.get("status", "")).strip()
    node_id = str(data.get("node_id", "")).strip()
    model = str(data.get("model", "")).strip()
    date_from = str(data.get("date_from", "")).strip()
    date_to = str(data.get("date_to", "")).strip()
    voci = list(entries if entries is not None else memory_sync.read_local(MEMORY_MAX_ENTRIES))

    def matches(voce):
        haystack = " ".join(str(voce.get(key, "")) for key in (
            "content", "prompt", "response", "summary", "detail", "metadata"))
        timestamp = str(voce.get("ts") or voce.get("timestamp") or "")
        entry_node = str(voce.get("node_id") or voce.get("sourceNode") or voce.get("source") or "")
        if query and query not in haystack.lower():
            return False
        if status and str(voce.get("status") or "active") != status:
            return False
        if node_id and node_id.lower() not in entry_node.lower():
            return False
        if model and model.lower() not in str(voce.get("model") or "").lower():
            return False
        if date_from and timestamp[:10] < date_from:
            return False
        if date_to and timestamp[:10] > date_to:
            return False
        return True

    trovate = [voce for voce in voci if isinstance(voce, dict) and matches(voce)][:limite]
    risposta = {"ok": True, "degraded": degraded, "source": source,
                "entries": trovate, "count": len(trovate)}
    if reason:
        risposta["reason"] = reason
    return jsonify(risposta)

# ── le sei rotte ──────────────────────────────────────────────────────────

@_bp.route('/memory')
def get_memory():
    limit   = int(request.args.get("limit", MEMORY_MAX_ENTRIES))
    if _memory_effective_backend() == "hermes":
        try:
            esito = memory_sync.read(limit)
        except HermesMemoryError as exc:
            return jsonify({"error": str(exc), "backend": "hermes"}), 503
        risposta = {"entries": esito["entries"][:limit], "total": len(esito["entries"]),
                    "backend": "hermes", "source": esito["source"],
                    "degraded": esito["degraded"]}
        if esito.get("reason"):
            risposta["reason"] = esito["reason"]
        return jsonify(risposta)
    entries = _load_memory()
    return jsonify({"entries": entries[:limit], "total": len(entries)})


@_bp.route('/memory/push', methods=['POST'])
def push_memory():
    data  = request.get_json(force=True, silent=True) or {}
    entry = data.get("entry")
    if not entry or not isinstance(entry, dict):
        return jsonify({"ok": False, "error": "missing entry"}), 400
    try:
        esito = _memory_append(entry)
    except HermesMemoryError as exc:
        return jsonify({"ok": False, "error": str(exc), "backend": "hermes"}), 503
    if isinstance(esito, dict) and esito.get("ok") is False:
        # Qui non si è salvato niente da nessuna parte: questo sì è un guasto, e il
        # chiamante deve saperlo (una voce in coda invece è al sicuro su disco).
        return jsonify(esito), 503
    return jsonify(esito if isinstance(esito, dict) else {"ok": True})


@_bp.route('/memory/stats')
def memory_stats():
    if _memory_effective_backend() == "hermes":
        # Anche con Hermes giù: la risposta degradata dice cosa c'è in locale e
        # quanto è in coda, che è l'unica cosa da guardare in quel momento. Un 503
        # nasconderebbe proprio quello.
        return jsonify(memory_sync.stats())
    entries    = _load_memory()
    size_bytes = os.path.getsize(MEMORY_FILE_GZ) if os.path.exists(MEMORY_FILE_GZ) else 0
    return jsonify({
        "backend": "legacy", "degraded": False, "source": "legacy",
        "entries": len(entries), "max_entries": MEMORY_MAX_ENTRIES,
        "ttl_days": MEMORY_TTL_DAYS,
        "file_size_bytes": size_bytes,
        "file_size_kb": round(size_bytes/1024, 2),
        "file": MEMORY_FILE_GZ,
    })


@_bp.route('/memory/sync', methods=['POST'])
def sync_memory():
    """Riconsegna a mano la coda a Hermes: "riprova adesso", per il debug."""
    if MEMORY_BACKEND != "hermes":
        return jsonify({"ok": False, "backend": MEMORY_BACKEND,
                        "error": "la riconsegna della coda vale con il backend hermes"}), 409
    esito = memory_sync.flush(force=True)
    esito["pending"] = memory_sync.pending()
    esito["outbox_file"] = str(memory_sync.outbox.path) if memory_sync.outbox else ""
    return jsonify(esito), (200 if esito.get("ok") else 503)


@_bp.route('/memory/search', methods=['POST'])
def search_memory():
    data = request.get_json(force=True, silent=True) or {}
    if MEMORY_BACKEND == "legacy":
        return _memory_search_local(data, entries=_load_memory(), source="legacy", degraded=False)
    if MEMORY_BACKEND != "hermes":
        return jsonify({"ok": False, "error": f"backend memoria non supportato: {MEMORY_BACKEND}"}), 409
    if _hermes_memory.unavailable():
        # Failsafe: Hermes è giù, si serve il mirror senza pagare il timeout.
        return _memory_search_local(data, reason=_hermes_memory.last_error())
    try:
        entries = _hermes_memory.query(
            str(data.get("query", "")), int(data.get("limit", 50)),
            str(data.get("event_type", "")), str(data.get("mode", "browse")),
            node_id=str(data.get("node_id", "")), source=str(data.get("source", "")),
            model=str(data.get("model", "")), status=str(data.get("status", "active")),
            date_from=str(data.get("date_from", "")), date_to=str(data.get("date_to", "")),
            offset=max(0, int(data.get("offset", 0))),
        )
        return jsonify({"ok": True, "backend": "hermes", "source": "hermes",
                        "degraded": False, "entries": entries, "count": len(entries)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "backend": "hermes"}), 503
    except HermesMemoryError as exc:
        return _memory_search_local(data, reason=str(exc))


@_bp.route('/memory/lifecycle', methods=['POST'])
def memory_lifecycle():
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get("ids")
    if MEMORY_BACKEND != "hermes":
        return jsonify({"ok": False, "error": "lifecycle disponibile con Hermes"}), 409
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "ids deve essere una lista"}), 400
    try:
        result = _hermes_memory.lifecycle(ids, str(data.get("action", "")), str(data.get("reason", "")))
        push_log('memory_sync', f'Memory lifecycle: {result.get("action")} ({len(result.get("changed", []))})',
                 detail=str(data.get("reason", "")), status='success')
        return jsonify(result), (200 if result.get("ok") else 207)
    except (HermesMemoryError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "backend": "hermes"}), 503

# ── ricostruire la coda quando cambiano i parametri ───────────────────────

def _reload_memory_sync() -> None:
    """Ricostruisce mirror e coda dopo un salvataggio in Setup.

    Non e' pignoleria: i percorsi e gli interruttori vivono *dentro* MemorySync,
    non in costanti globali, quindi un valore nuovo senza ricostruzione resterebbe
    "salvato ma inerte" — il difetto che la tab Setup esiste per non avere.

    Qui il `global` serve e non e' un errore: il binding di `memory_sync` vive in
    questo modulo. Senza, la riassegnazione creerebbe una variabile locale — cioe'
    una che sparisce all'uscita — e le funzioni che lo leggono continuerebbero a
    usare l'oggetto vecchio, che e' esattamente "salvato ma inerte".
    """
    global memory_sync
    memory_sync = ricarica()

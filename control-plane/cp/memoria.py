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

from cp.config import (MEMORY_BACKEND, MEMORY_FILE_GZ, MEMORY_MAX_ENTRIES,
                       MEMORY_TTL_DAYS, UI_BRIDGE_URL, _hermes_memory)
from cp.log import push_log
from shared.memory_sync import MemorySync, from_env


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
    return from_env(_hermes_memory, log=push_log, memory_file=MEMORY_FILE_GZ)

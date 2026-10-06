# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/battito.py
# IL BATTITO: LA MANUTENZIONE PERIODICA DELLA RETE.
#
# Un thread, e ogni quindici secondi tre cose: chi dei nodi risponde, la memoria
# replicata, e il battito di questo nodo verso gli altri. Non c'è una rotta che lo
# avvia e non c'è modo di fermarlo: parte col server e gira finché il server gira.
# È l'unico pezzo del control-plane con questa forma, ed è il motivo per cui sta
# in un modulo tutto suo — non è una rotta, e i moduli di rotte non hanno thread.
#
# `hb_state` è esposto da `/hb/status` e letto dal tool di stato, dal log e dal
# doctor. Vive qui e viene importato per riferimento: è un dizionario che si
# aggiunge, non una variabile che si riassegna, quindi tutti ne vedono lo stesso.
# Se un giorno diventasse un riassegnamento, quell'import diventerebbe una copia
# morta — e lo scrivo qui perché è la trappola che questo modulo ha già vicino,
# con `_node_aliases` in cp/mesh.py.
#
# Sul perché il ciclo non faccia tutto ogni volta: il battito passa i nodi a ogni
# giro, ma la memoria solo a ogni ciclo pari. La memoria è la parte costosa — è
# una firma per nodo — e dividerla per due non è un ottimismo: è quello che
# succede quando due peer si annunciano a vicenda e ognuno aspetta la risposta
# dell'altro prima di fare il suo turno.

import time
import uuid
from datetime import datetime, timezone

import requests

import shared.db as db
from cp.config import (MEMORY_BACKEND, _LOCAL_NODE_ENABLED, _LOCAL_NODE_ENDPOINT)
from cp.http import _is_valid_json_response
from cp.log import push_log
from cp.memoria import _load_memory, _notify_bridge, _save_memory
from cp.mesh import (_best_endpoint, _known_endpoints, _node_list, _node_score,
                     _nodes_by_id, _normalize_endpoint)

# Lo stato del battito. `last_conn` e `last_memory_sync` sono `None` finché non
# toccano quei rami: "non ancora" e "non è successo" è la stessa risposta, e
# va bene così.
hb_state = {
    "cycle": 0, "last_tick": None, "last_conn": None,
    "last_memory_sync": None,
    "nodes_seen": [], "running": False,
}

# Le chiavi di memoria già replicate, per non rifare la firma di un nodo che ha
# già ricevuto tutto. Un set, non un contatore: conta cosa, non quanto.
_synced_memory_keys: set = set()

# Il modulo non ha bisogno di un boot: non riceve rotte e non ha contesto. La sola
# cosa che serve è la memoria, che è di `cp/memoria.py`.
_memoria = None
_LOCAL_NODE_ID = ""
CP_ID = ""


def collega(memoria, *, local_node_id=None, cp_id=None):
    """Il ciclo ha bisogno di una cosa sola: la memoria da replicare.

    Non e' un `monta`: questo modulo non registra rotte, e dirlo `monta` farebbe
    credere che ci sia un `app` da coinvolgere.
    """
    global _memoria, _LOCAL_NODE_ID, CP_ID
    _memoria = memoria
    _LOCAL_NODE_ID = local_node_id or ""
    CP_ID = cp_id or ""


def smonta():
    global _memoria, _LOCAL_NODE_ID, CP_ID
    _memoria = None
    _LOCAL_NODE_ID = ""
    CP_ID = ""

# ── lo stato del battito ──────────────────────────────────────────────────

hb_state = {
    "cycle": 0, "last_tick": None, "last_conn": None,
    "last_memory_sync": None,
    "nodes_seen": [], "running": False,
}


_synced_memory_keys: set = set()

# ── interrogare i nodi, e marcare chi non risponde ────────────────────────

def _poll_mesh_nodes():
    for ep in list(_known_endpoints):
        if ep and _LOCAL_NODE_ENDPOINT and _normalize_endpoint(ep) == _normalize_endpoint(_LOCAL_NODE_ENDPOINT):
            continue
        try:
            r = requests.get(f"{ep}/status", timeout=3)
            if r.status_code == 200 and _is_valid_json_response(r):
                info = r.json()
                nid  = info.get("node_id", "")
                info.update({
                    "endpoint":  ep,
                    "status":    "active",
                    "last_seen": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
                if nid:
                    existing = _nodes_by_id.get(nid)
                    if not existing or \
                       not _normalize_endpoint(existing.get("endpoint","")).startswith("https://") or \
                       ep.startswith("https://"):
                        _nodes_by_id[nid] = info
                        db.upsert_node(info)
                try:
                    rp = requests.get(f"{ep}/peers", timeout=2)
                    if _is_valid_json_response(rp):
                        for peer in rp.json().get("peers", []):
                            pep = _normalize_endpoint(peer.get("endpoint", ""))
                            if pep and pep not in _known_endpoints:
                                _known_endpoints.add(pep)
                except Exception:
                    pass
            else:
                for nid, n in list(_nodes_by_id.items()):
                    if _normalize_endpoint(n.get("endpoint","")) == ep and n.get("node_id") != _LOCAL_NODE_ID:
                        _nodes_by_id[nid]["status"] = "unreachable"
                        db.upsert_node({**_nodes_by_id[nid], "status": "unreachable"})
                        push_log('mesh_event',
                                 f'Node zombie detected: {nid[:12]}',
                                 f'endpoint={ep} http={r.status_code} content-type={r.headers.get("Content-Type","?")}',
                                 source='heartbeat', status='warn')
        except Exception:
            for nid, n in list(_nodes_by_id.items()):
                if _normalize_endpoint(n.get("endpoint","")) == ep and n.get("node_id") != _LOCAL_NODE_ID:
                    _nodes_by_id[nid]["status"] = "unreachable"
                    db.upsert_node({**_nodes_by_id[nid], "status": "unreachable"})

    if _LOCAL_NODE_ENABLED:
        remote_active = [
            n for n in _node_list()
            if n.get("status") == "active" and n.get("node_id") != _LOCAL_NODE_ID
        ]
        local_node = _nodes_by_id.get(_LOCAL_NODE_ID)
        if local_node and not remote_active and local_node.get("tier") != "root":
            local_node["tier"] = "root"
            db.upsert_node(local_node)
            push_log('mesh_event', f'Local node promoted to root (mesh empty)',
                     source=_LOCAL_NODE_ID[:16], status='info')
        elif local_node and remote_active and local_node.get("tier") == "root":
            local_node["tier"] = "hub"
            db.upsert_node(local_node)
            push_log('mesh_event', f'Local node demoted to hub ({len(remote_active)} remote active)',
                     source=_LOCAL_NODE_ID[:16], status='info')

# ── la memoria, replicata sui nodi ────────────────────────────────────────

# ── MEMORY SYNC ───────────────────────────────────────────────────────────────
def _sync_memory_across_nodes():
    if MEMORY_BACKEND == "hermes":
        # Hermes is the single shared store. Replicating its view back into
        # node-local files would reintroduce dual-write and sync loops. La coda di
        # `shared/memory_sync.py` non è una replica: va solo *verso* Hermes, e il
        # mirror locale è una vista del nodo, non quella di un altro.
        return
    active_nodes = [n for n in _node_list() if n.get("status") == "active"]
    if len(active_nodes) < 2:
        return
    node_memories: dict = {}
    for node in active_nodes:
        nid = node.get("node_id", "")
        ep  = _best_endpoint(node)
        if not ep or node.get("is_local"):
            continue
        try:
            r = requests.get(f"{ep}/memory", params={"limit": 30}, timeout=4)
            if r.status_code == 200:
                node_memories[nid] = r.json().get("entries", [])
        except Exception:
            pass
    if not node_memories:
        return
    pushed_total  = 0
    local_entries = _load_memory()
    local_changed = False
    for src_nid, entries in node_memories.items():
        for entry in entries:
            ts  = entry.get("ts") or entry.get("timestamp", "")
            key = f"{src_nid}:{ts}"
            if key in _synced_memory_keys:
                continue
            _synced_memory_keys.add(key)
            content_key = str(entry.get("content","") or entry.get("prompt",""))[:64]
            dedup_key   = f"{ts}:{content_key}"
            existing_k  = {
                f"{e.get('ts') or e.get('timestamp','')}:{str(e.get('content','') or e.get('prompt',''))[:64]}"
                for e in local_entries
            }
            if dedup_key not in existing_k:
                local_entries.append(entry)
                local_changed = True
            for dst_node in active_nodes:
                if dst_node.get("node_id") == src_nid or dst_node.get("is_local"): continue
                ep_dst = _best_endpoint(dst_node)
                if not ep_dst: continue
                try:
                    requests.post(f"{ep_dst}/memory/push",
                                  json={"node_id": src_nid, "entry": entry}, timeout=4)
                    pushed_total += 1
                except Exception:
                    pass
    if local_changed:
        _save_memory(local_entries)
    if pushed_total > 0:
        push_log('memory_sync', f'Memory sync: {pushed_total} entries su {len(active_nodes)} nodi', status='success')
        _notify_bridge("memory_sync", {"from": "cp", "to": "mesh",
                                        "entries": pushed_total, "label": f"sync {pushed_total}"})

# ── il ciclo ──────────────────────────────────────────────────────────────

def heartbeat_loop():
    time.sleep(3)
    push_log('system', 'Control-plane v1.05 started',
             detail=f'nodes={len(_nodes_by_id)} endpoints={list(_known_endpoints)} federation_id={CP_ID[:16]}',
             status='info')
    hb_state["running"] = True
    while True:
        cycle = hb_state["cycle"] + 1
        hb_state["cycle"]     = cycle
        hb_state["last_tick"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _poll_mesh_nodes()
        hb_state["nodes_seen"] = [
            n.get("node_id", n.get("endpoint","?"))[:12]
            for n in _node_list() if n.get("status") == "active"
        ]
        if cycle % 2 == 0:
            _sync_memory_across_nodes()
            hb_state["last_memory_sync"] = hb_state["last_tick"]
        for node in _node_list():
            if node.get("status") != "active": continue
            if node.get("is_local"): continue
            nid = node.get("node_id", node.get("endpoint","unknown"))[:12]
            ep  = _best_endpoint(node)
            if not ep: continue
            tid = str(uuid.uuid4())[:8]
            try:
                t0  = time.time()
                r   = requests.get(f"{ep}/health", timeout=2)
                lat = int((time.time()-t0)*1000)
                if _is_valid_json_response(r):
                    push_log('connection_test', f'HB#{cycle} ping OK -> {nid}',
                             f'latency: {lat}ms | score: {round(_node_score(node),3)}',
                             source='control-plane', target=nid, status='success', trace_id=tid)
                    hb_state["last_conn"] = hb_state["last_tick"]
                    _notify_bridge("task", {"from": "cp", "to": nid, "type": "heartbeat",
                                            "label": f"HB#{cycle} {lat}ms"})
                else:
                    push_log('connection_test', f'HB#{cycle} zombie -> {nid}',
                             f'HTTP {r.status_code} non-JSON ({r.headers.get("Content-Type","?")})',
                             source='control-plane', target=nid, status='failed', trace_id=tid)
                    node["status"] = "unreachable"
                    db.upsert_node({**node, "status": "unreachable"})
            except Exception as e:
                push_log('connection_test', f'HB#{cycle} FAILED -> {nid}', str(e),
                         source='control-plane', target=nid, status='failed', trace_id=tid)
        time.sleep(15)

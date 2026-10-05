# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/webnode.py
# I WEB NODES: IL LAVORO CHE IL CONTROL-PLANE NON PUO' FARE DA SE'.
#
# Un web node e' una scheda del browser. Si registra, poi TIRA il lavoro con un
# long-poll e pubblica il risultato: il CP non lo chiama mai, e non puo' farlo,
# perche' il browser non ha un endpoint in ingresso. E' la stessa inversione di
# direzione del runner sandbox (shared/code_sandbox.py), e per la stessa ragione
# mette a disagio chi legge: qui il CP e' il cliente.
#
# Perche' stanno separati dal mesh (`cp/mesh.py`), che sembra fare la stessa cosa:
# il mesh sceglie fra server che lui puo' chiamare per nome e parla un protocollo
# che puo' riprovare. Il web node e' l'opposto — e' il CP ad aspettare, e il
# lavoro che torna e' codice eseguito su una macchina che non controlla. Le due
# cose hanno bisogno di garanzie diverse, e metterle nello stesso modulo avrebbe
# fatto sembrare il secondo un caso particolare del primo.
#
# I tre limiti che rendono questo dominio diverso, e che qui sono applicati:
#
# - Solo i task in WEB_SAFE_TASK_TYPES possono essere accodati. Nessuna inferenza
#   pesante puo' finire su una scheda del browser.
# - Il registro NON e' persistito, e non perche' sia scomodo: un web node e' una
#   scheda, e una scheda che si ricorda di una richiesta fatta due giorni fa e' un
#   bug che aspetta. Il riavvio deve dimenticare tutto (vedi shared/web_node.py).
# - Ogni route richiede il token di amministrazione di rete. Sono le stesse rotte
#   che possono mettere in coda codice arbitrario.
#
# Lo stato che si vede da qui (`_nodes_by_id`, `_known_endpoints`) e' del mesh: un
# web node registrato finisce nel registro dei nodi come qualsiasi altro, e deve
# comparire dove l'operatore guarda. Sono importati per nome e non iniettati, e
# va bene perche' il mesh li muta in posto e non li riassegna mai — se un giorno
# li riassegna, questo import diventa una copia morta e va rifatto dal modulo.

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

import shared.db as db
from cp.config import (WEB_NODE_ENABLED, WEB_NODE_HEARTBEAT_S, WEB_NODE_MAX_NODES,
                       WEB_NODE_MAX_PAYLOAD, WEB_NODE_MAX_POLL_S, WEB_NODE_MAX_QUEUE,
                       WEB_NODE_TASK_TTL_S)
from cp.http import _network_admin_error
from cp.log import push_log
from cp.mesh import _known_endpoints, _nodes_by_id
from shared.web_node import (WebNodeError, WebNodeRegistry, WebNodeUnknown,
                             WebTaskRejected)

_bp = Blueprint("webnode", __name__)

# Registry in memoria dei web node e dei task web-safe. Volutamente NON
# persistito: un web node e' una scheda del browser e non deve mai essere
# fonte di verita' (vedi shared/web_node.py).
web_registry = WebNodeRegistry(
    max_nodes=WEB_NODE_MAX_NODES,
    max_queue=WEB_NODE_MAX_QUEUE,
    max_payload_bytes=WEB_NODE_MAX_PAYLOAD,
    task_ttl_s=WEB_NODE_TASK_TTL_S,
    max_poll_s=WEB_NODE_MAX_POLL_S,
)


def monta(app):
    """Registra le rotte /web/*. Lo stato e' gia' pronto: il registro e' tutto
    memoria e non dipende dal boot, quindi non c'e' niente da iniettare."""
    app.register_blueprint(_bp)
    return app# ── WEB NODES — worker nel browser ────────────────────────────────────────────
# Un web node non ha un endpoint in ingresso: il CP non puo' chiamarlo. Si
# registra, poi TIRA il lavoro con un long-poll e pubblica il risultato —
# stessa inversione di direzione del runner sandbox (shared/code_sandbox.py).
# Solo i task in WEB_SAFE_TASK_TYPES possono essere accodati: nessuna inferenza
# pesante puo' finire su una scheda del browser. Richiede che le route /web/*
# girino su un server multi-thread: il long-poll occupa un thread fino a
# WEB_NODE_MAX_POLL_S secondi (vedi docs/web-node.md).
def _web_error(error):
    """Mappa le eccezioni del registry del web node sullo status HTTP corretto."""
    if isinstance(error, WebNodeUnknown):
        status = 404
    elif isinstance(error, WebTaskRejected):
        status = 409
    else:
        status = 400
    return jsonify({"ok": False, "error": str(error)}), status



def _web_node_id(data) -> str:
    return str((data or {}).get("node_id", "") or "").strip()



def _web_capable_node(capability: str):
    """Primo web node (per anzianita' di registrazione) che ha la capability."""
    matches = [n for n in web_registry.nodes() if capability in n["capabilities"]]
    matches.sort(key=lambda n: n.get("registered_at", 0))
    return matches[0]["node_id"] if matches else None



@_bp.route('/web/register', methods=['POST'])
def web_register():
    if not WEB_NODE_ENABLED:
        return jsonify({"ok": False, "error": "web node disattivati su questo control-plane"}), 503
    data = request.get_json(force=True, silent=True) or {}
    node_id = _web_node_id(data)
    if not node_id:
        return jsonify({"ok": False, "error": "missing node_id"}), 400
    try:
        record = web_registry.register(
            node_id,
            capabilities=data.get("capabilities") or [],
            label=data.get("label", ""),
            browser=data.get("browser", ""),
            limits=data.get("limits") or {},
        )
    except WebNodeError as error:
        return _web_error(error)
    # Il web node compare anche nella lista mesh (is_web_node=True) cosi' la
    # dashboard lo mostra, ma _best_endpoint lo tiene fuori dal routing: un
    # browser non e' chiamabile.
    endpoint = f"browser://{node_id}"
    info = {**(_nodes_by_id.get(node_id) or {}), **data, "node_id": node_id,
            "endpoint": endpoint, "status": "active", "is_web_node": True,
            "type": "web-node",
            "last_seen": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _nodes_by_id[node_id] = info
    _known_endpoints.add(endpoint)
    db.upsert_node(info)
    push_log('mesh_event', f'Web node registered: {node_id[:12]}',
             f"caps={','.join(record['capabilities']) or '-'} browser={record['browser'][:40]}",
             source=node_id[:12], status='success')
    return jsonify({"ok": True, "node": record,
                    "heartbeat_interval_s": WEB_NODE_HEARTBEAT_S,
                    "max_poll_s": WEB_NODE_MAX_POLL_S})



@_bp.route('/web/poll', methods=['POST'])
def web_poll():
    if not WEB_NODE_ENABLED:
        return jsonify({"ok": False, "error": "web node disattivati su questo control-plane"}), 503
    data = request.get_json(force=True, silent=True) or {}
    try:
        timeout_s = int(data.get("timeout_s", WEB_NODE_MAX_POLL_S))
    except (TypeError, ValueError):
        timeout_s = WEB_NODE_MAX_POLL_S
    try:
        task = web_registry.poll(_web_node_id(data), timeout_s=timeout_s)
    except WebNodeError as error:
        return _web_error(error)
    if task is None:
        return jsonify({"ok": True, "task": None,
                        "next_poll_s": max(1, WEB_NODE_HEARTBEAT_S // 2)})
    push_log('web_task', f"Web task {task['task_id']} -> {task['node_id'][:12]}",
             f"type={task['type']}", source='control-plane',
             target=task['node_id'][:12], status='pending')
    return jsonify({"ok": True, "task": task})



@_bp.route('/web/result', methods=['POST'])
def web_result():
    if not WEB_NODE_ENABLED:
        return jsonify({"ok": False, "error": "web node disattivati su questo control-plane"}), 503
    data = request.get_json(force=True, silent=True) or {}
    node_id = _web_node_id(data)
    task_id = str(data.get("task_id", "") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "missing task_id"}), 400
    try:
        duration_ms = int(data["duration_ms"]) if data.get("duration_ms") is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    try:
        entry = web_registry.complete(node_id, task_id, ok=bool(data.get("ok")),
                                      result=data.get("result"),
                                      error=data.get("error", ""),
                                      duration_ms=duration_ms)
    except WebNodeError as error:
        return _web_error(error)
    push_log('web_task', f"Web task {task_id} {'done' if entry['ok'] else 'failed'}",
             f"node={node_id[:12]} matched={entry['matched']} err={entry['error'][:80]}",
             source=node_id[:12], target='control-plane',
             status='success' if entry['ok'] else 'warn')
    return jsonify({"ok": True, "result": entry})



@_bp.route('/web/tasks', methods=['POST'])
def web_enqueue():
    """Accoda un task web-safe. Riservato all'operatore (token di rete)."""
    auth_error = _network_admin_error()
    if auth_error:
        return auth_error
    if not WEB_NODE_ENABLED:
        return jsonify({"ok": False, "error": "web node disattivati su questo control-plane"}), 503
    data = request.get_json(force=True, silent=True) or {}
    task_type = str(data.get("type", "") or "").strip()
    node_id = _web_node_id(data) or _web_capable_node(task_type)
    if not node_id:
        available = sorted({c for n in web_registry.nodes() for c in n["capabilities"]})
        return jsonify({"ok": False,
                        "error": f"nessun web node con capability '{task_type}'",
                        "available_capabilities": available}), 409
    try:
        task = web_registry.enqueue(node_id, task_type, data.get("payload") or {},
                                    constraints=data.get("constraints") or {})
    except WebNodeError as error:
        return _web_error(error)
    push_log('web_task', f"Web task enqueued {task['task_id']}",
             f"type={task_type} node={node_id[:12]}",
             source='control-plane', target=node_id[:12], status='pending')
    return jsonify({"ok": True, "task": task}), 202



@_bp.route('/web/status')
def web_status():
    """Istantanea per la dashboard: nodi browser, coda e ultimi esiti."""
    return jsonify({"enabled": WEB_NODE_ENABLED,
                    "heartbeat_interval_s": WEB_NODE_HEARTBEAT_S,
                    **web_registry.status(),
                    "recent_results": web_registry.results(limit=10)})
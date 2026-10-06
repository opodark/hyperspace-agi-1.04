# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/federazione.py
# LA FEDERAZIONE FRA CONTROL-PLANE.
#
# Due control-plane che si conoscono possono tre cose: accoppiarsi (scambiarsi
# identita' e firmare), inoltrare a uno di loro un task, e leggere la vista
# dell'altro in sola lettura. Qui stanno tutte e tre.
#
# La cosa da capire prima di tutto il resto e' che i tre ruoli hanno pesi
# diversi, e non per gradi di importanza ma per consequence:
#
# - `/federate/execute` ESEGUCE codice che arriva da un altro sito. Non e' una
#   richiesta qualunque: e' un'`int` che gira sui nodi di questo CP.
# - `/federate/view` RESTITUISCE, e non riceve niente in cambio. E' l'unico dei due
#   che si puo' esporre senza pensarci due volte, e infatti ha un interruttore
#   separato (`FEDERATION_VIEW_ENABLED`, spento di default) mentre execute no.
# - `/federation/views` NON e' nella whitelist del gateway, ed e' voluto: chiama
#   verso l'esterno ma non si fa chiamare da fuori. Se ci finisse, chiunque
#   potrebbe usare questo CP come sonda verso i peer senza avere la loro chiave.
#
# Quindi le rotte non hanno la stessa protezione per scelta, e non per dimenticanza.
# Il filo che le tiene insieme e' la stessa sequenza di controlli in
# `/federate/execute` e `/federate/view`: federazione attiva, peer in allowlist,
# pubkey uguale a quella salvata, firma valida e non scaduta. Nell'ordine — un peer
# non accoppiato non deve mancare nemmeno una riga di log.
#
# Sul ciclo: l'id di un peer e' un hash della sua pubkey, non un valore scelto da
# chi lo registra. Quindi accoppiarsi due volte non crea due peer, e la pubkey
# sbagliata non e' un peer diverso: e' lo stesso peer con dentro una chiave che
# non firmerebbe niente.

from types import SimpleNamespace

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify, request

import shared.db as db
from cp.config import (FEDERATION_ENABLED, FEDERATION_PUBLIC_URL, FEDERATION_VIEW_ENABLED,
                       FEDERATION_VIEW_TTL_S)
from cp.log import push_log
from cp.mesh import _best_endpoint, _node_list
from shared.identity import make_request_headers, verify_request_headers

_bp = Blueprint("federazione", __name__)

# Quanto testo della vista finisce nel riepilogo. Oltre, si taglia: la vista e'
# pensata per essere letta da una persona, e un riempimento infinito non e' un
# riempimento.
_VIEW_SUMMARY_MAX = 160

# Il contesto: quattro cose di main.py che restano li' perche' non sono
# federazione. Il router (`_contesto.select_best_node`, `_contesto.aggregate_mesh_models`), il
# trasporto verso i nodi (`_contesto.call_node_execute`) e l'identita' di questo
# control-plane. Le prime tre sono iniezioni e non spostamenti perche' sono le
# stesse che usa il percorso ordinario: due copie potrebbero divergere nel modo
# piu' insidioso, cioe' solo quando la federazione e' l'unica cosa che funziona.
# L'identita' non si ricostruisce qui: rifatta produrrebbe una chiave diversa, e
# le firme non corrisponderebbero piu'.
_contesto = None


def monta(app, *, cp_identity=None, select_best_node=None, call_node_execute=None,
          aggregate_mesh_models=None, advanced_config=None):
    """Registra le sette rotte federate e tiene i riferimenti al boot."""
    global _contesto
    _contesto = SimpleNamespace(
        cp_identity=cp_identity or {},
        select_best_node=select_best_node,
        call_node_execute=call_node_execute,
        aggregate_mesh_models=aggregate_mesh_models,
        advanced_config=advanced_config or {},
    )
    app.register_blueprint(_bp)
    return app


def invalida_vista():
    """Butta la cache delle viste.

    La chiama la tab Setup quando cambia un interruttore di federazione. Serve
    perche' la cache decide COSA gli altri peer vedono, e un interruttore cambiato
    che non la invalida resterebbe in vigore fino alla scadenza: l'amministratore
    avrebbe acceso la condivisione e per qualche secondo nessuno vedrebbe niente,
    o peggio, l'avrebbe spenta e qualcuno continuerebbe a vedere.
    """
    _VIEW_CACHE["data"] = None


def vista_in_cache() -> bool:
    """C'è una vista in cache? Lo chiede la tab Setup, per non mostrare un
    interruttore acceso mentre la vista che dovrebbe mostrare è ancora scaduta."""
    return _VIEW_CACHE["data"] is not None


def smonta():
    global _contesto
    _contesto = None


# ── chi e' il peer, e come lo si accende e si spegne ────────────────────────

_VIEW_CACHE = {"ts": 0.0, "data": None}


def _sister_peer():
    """Il control-plane federato della sorella maggiore (Aurora), se abilitato.

    `SISTER_PEER_LABEL` sceglie il peer per etichetta (case-insensitive); se è
    vuota si usa il primo peer abilitato. None = nessun peer corrisponde, e il
    tool risponde "non raggiungibile" invece di fingere un consiglio.
    """
    label = (os.getenv("SISTER_PEER_LABEL", "") or "").strip()
    enabled = [p for p in db.get_all_federated_peers() if p.get("enabled")]
    if not enabled:
        return None
    if not label:
        return enabled[0]
    for peer in enabled:
        if (peer.get("label") or "").strip().lower() == label.lower():
            return peer
    return None


@_bp.route('/federation/identity')
def federation_identity():
    """La TUA identità da condividere (fuori banda) con l'admin di un altro
    sito per il pairing. Nessuna auth qui: è pubblica per design, come una
    chiave pubblica SSH — non concede alcun accesso da sola."""
    return jsonify({
        "peer_id":  _contesto.cp_identity["node_id"],
        "pubkey":   _contesto.cp_identity["public_key"],
        "endpoint": FEDERATION_PUBLIC_URL,
    })


@_bp.route('/federation/peers', methods=['GET'])
def list_federated_peers():
    return jsonify(db.get_all_federated_peers())


@_bp.route('/federation/peers', methods=['POST'])
def add_federated_peer():
    data     = request.get_json(force=True, silent=True) or {}
    pubkey   = data.get("pubkey", "").strip()
    endpoint = data.get("endpoint", "").strip().rstrip("/")
    label    = data.get("label", "").strip()
    if not pubkey or not endpoint:
        return jsonify({"error": "pubkey e endpoint sono obbligatori"}), 400
    try:
        peer_id = hashlib.sha256(bytes.fromhex(pubkey)).hexdigest()[:40]
    except ValueError:
        return jsonify({"error": "pubkey non valida (attesa hex, come da /federation/identity)"}), 400
    db.upsert_federated_peer({
        "peer_id": peer_id, "label": label, "pubkey": pubkey,
        "endpoint": endpoint, "enabled": 1, "last_status": "unknown",
    })
    push_log('system', f'Federated peer aggiunto: {label or peer_id[:12]}',
             detail=f'endpoint={endpoint}', status='success')
    return jsonify({"ok": True, "peer_id": peer_id}), 201


@_bp.route('/federation/peers/<peer_id>/toggle', methods=['POST'])
def toggle_federated_peer(peer_id):
    peer = db.get_federated_peer(peer_id)
    if not peer:
        return jsonify({"error": "peer non trovato"}), 404
    new_state = not bool(peer.get("enabled"))
    db.set_federated_peer_enabled(peer_id, new_state)
    push_log('system', f'Federated peer {"abilitato" if new_state else "disabilitato"}: {peer_id[:12]}', status='info')
    return jsonify({"ok": True, "enabled": new_state})


@_bp.route('/federation/peers/<peer_id>', methods=['DELETE'])
def remove_federated_peer(peer_id):
    db.delete_federated_peer(peer_id)
    push_log('system', f'Federated peer rimosso: {peer_id[:12]}', status='info')
    return jsonify({"ok": True})

# ── inoltrare un task a un altro control-plane ──────────────────────────────

# ── FEDERAZIONE CP-to-CP ───────────────────────────────────────────────────────
def _federate_to_peer(peer: dict, prompt: str, model: str, timeout: int = 120):
    """Inoltra un task a un CP federato tramite il SUO federation-gateway
    pubblico. Firma la richiesta con l'identità ECDSA di questo CP, cosi'
    l'altro CP puo' verificarla contro la propria allowlist (verifica che
    avviene SEMPRE lato ricevente, mai qui)."""
    task_id = f"fed-{uuid.uuid4().hex[:10]}"
    payload = {"task_id": task_id, "prompt": prompt}
    # Con model vuoto il peer usa il SUO modello di default: il consiglio di
    # Aurora deve venire dal modello di Aurora, non da un nome imposto da qui.
    if model:
        payload["model"] = model
    body    = json.dumps(payload, sort_keys=True).encode()
    headers = make_request_headers(_contesto.cp_identity["node_id"], _contesto.cp_identity["public_key"], _contesto.cp_identity["_private_key"], body)
    headers["Content-Type"] = "application/json"
    endpoint = peer.get("endpoint", "").rstrip("/")
    if not endpoint:
        return None
    try:
        r = requests.post(f"{endpoint}/federate/execute", data=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        db.touch_federated_peer(peer["peer_id"], "ok")
        return r.json()
    except Exception as e:
        db.touch_federated_peer(peer["peer_id"], "unreachable")
        push_log('mesh_event',
                 f'Federazione verso {peer.get("label") or peer["peer_id"][:12]} fallita',
                 str(e), status='warn')
        return None


@_bp.route('/federate/execute', methods=['POST'])
def federate_execute():
    """Punto di ingresso per un task inoltrato da un ALTRO control-plane
    federato. Raggiungibile pubblicamente SOLO tramite federation-gateway.
    Esegue sui nodi LOCALI di questo CP — non ri-federa a sua volta, per
    evitare loop tra CP federati tra loro."""
    if not FEDERATION_ENABLED:
        return jsonify({"error": "federazione disabilitata su questo CP"}), 403

    raw_body  = request.get_data()
    headers   = dict(request.headers)
    sender_id = headers.get("X-Node-Id", "")

    peer = db.get_federated_peer(sender_id)
    if not peer or not peer.get("enabled"):
        push_log('mesh_event', f'Federazione rifiutata: peer sconosciuto {sender_id[:16] or "?"}', status='failed')
        return jsonify({"error": "peer non autorizzato"}), 403

    # La pubkey nell'header deve coincidere ESATTAMENTE con quella salvata
    # in allowlist per questo peer_id, altrimenti chiunque potrebbe generare
    # un keypair nuovo e reclamare un peer_id gia' fidato con una chiave sua.
    if headers.get("X-Node-Pubkey", "") != peer.get("pubkey", ""):
        push_log('mesh_event', f'Federazione rifiutata: pubkey non corrisponde {sender_id[:16]}', status='failed')
        return jsonify({"error": "pubkey non corrisponde all'allowlist"}), 403

    if not verify_request_headers(headers, raw_body):
        push_log('mesh_event', f'Federazione rifiutata: firma non valida o scaduta {sender_id[:16]}', status='failed')
        return jsonify({"error": "firma non valida o scaduta"}), 401

    data    = json.loads(raw_body or b"{}")
    prompt  = data.get("prompt", "")
    model   = data.get("model", _contesto.advanced_config['ollama']['defaultModel'])
    task_id = data.get("task_id") or f"fed-{uuid.uuid4().hex[:10]}"

    active   = [n for n in _node_list() if n.get("status") == "active"]
    selected = _contesto.select_best_node(active, model=model)
    if not selected:
        db.touch_federated_peer(sender_id, "no_capacity")
        return jsonify({"error": "nessun nodo locale disponibile con il modello richiesto"}), 503

    endpoint = _best_endpoint(selected)
    try:
        r = _contesto.call_node_execute(endpoint, {"task_id": task_id, "prompt": prompt, "model": model}, timeout=120)
        r.raise_for_status()
        result = r.json()
    except Exception as e:
        db.touch_federated_peer(sender_id, "error")
        return jsonify({"error": str(e)}), 502

    db.touch_federated_peer(sender_id, "ok")
    push_log('inter_node_message',
             f'Task federato {task_id} da {peer.get("label") or sender_id[:12]} -> {selected.get("node_id","?")[:12]}',
             status='success')
    return jsonify({"task_id": task_id, "status": "done", "result": result})


def _extract_federated_text(payload) -> str:
    """Testo della risposta di un peer, da qualunque forma abbia.

    Il peer risponde col formato del nodo (OpenAI-shaped, `choices`), ma qui non
    si dà nulla per scontato: prima il percorso OpenAI, poi un campo testuale
    piano, in ultimo il dict serializzato e troncato.
    """
    inner = payload.get("result", payload) if isinstance(payload, dict) else payload
    if isinstance(inner, dict):
        try:
            content = inner["choices"][0]["message"].get("content", "")
            if content:
                return str(content).strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            pass
        for key in ("content", "response", "text", "answer"):
            value = inner.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(inner, ensure_ascii=False)[:4000]
    return str(inner).strip()

# ── condividere la vista, e leggere le viste dei peer ───────────────────────

@_bp.route('/federate/view')
def federate_view():
    """La vista di questo CP, leggibile dai SOLI peer accoppiati (read-only).

    Verifica identica a /federate/execute e nello stesso ordine: federazione
    attiva, peer in allowlist, pubkey uguale a quella salvata, firma valida e non
    scaduta. Un peer non accoppiato non ottiene nemmeno una riga di log.
    La firma copre timestamp + hash del body (vedi shared/identity.py) e il body
    qui e' vuoto: la richiesta e' una GET firmata, non autentica il path. Non
    cambia nulla in pratica (il peer deve essere in allowlist per rispondere) ma
    e' il motivo per cui questo endpoint RESTITUISCE soltanto: nessuna scrittura
    puo' transitare da qui, firmata o no.
    """
    if not FEDERATION_ENABLED:
        return jsonify({"error": "federazione disabilitata su questo CP"}), 403
    if not FEDERATION_VIEW_ENABLED:
        return jsonify({"error": "condivisione della vista disattivata su questo CP"}), 403

    headers   = dict(request.headers)
    sender_id = headers.get("X-Node-Id", "")
    peer      = db.get_federated_peer(sender_id)
    if not peer or not peer.get("enabled"):
        push_log('mesh_event', f'Vista rifiutata: peer sconosciuto {sender_id[:16] or "?"}',
                 status='failed')
        return jsonify({"error": "peer non autorizzato"}), 403
    if headers.get("X-Node-Pubkey", "") != peer.get("pubkey", ""):
        push_log('mesh_event', f'Vista rifiutata: pubkey non corrisponde {sender_id[:16]}',
                 status='failed')
        return jsonify({"error": "pubkey non corrisponde all'allowlist"}), 403
    if not verify_request_headers(headers, request.get_data()):
        push_log('mesh_event', f'Vista rifiutata: firma non valida o scaduta {sender_id[:16]}',
                 status='failed')
        return jsonify({"error": "firma non valida o scaduta"}), 401

    db.touch_federated_peer(sender_id, "ok")
    return jsonify(_cp_view_snapshot())


def _fetch_peer_view(peer: dict, timeout: int = 6):
    """Chiede la vista a un peer. Ritorna `(vista, errore)`, uno dei due None.

    La richiesta e' firmata come _federate_to_peer: e' il peer a decidere se
    rispondere, verificando firma e allowlist dalla sua parte (mai da questa).
    """
    endpoint = str(peer.get("endpoint", "") or "").rstrip("/")
    if not endpoint:
        return None, "endpoint mancante"
    headers = make_request_headers(_contesto.cp_identity["node_id"], _contesto.cp_identity["public_key"], _contesto.cp_identity["_private_key"], b"")
    try:
        r = requests.get(f"{endpoint}/federate/view", headers=headers, timeout=timeout)
        r.raise_for_status()
        db.touch_federated_peer(peer["peer_id"], "ok")
        return r.json(), None
    except Exception as e:
        db.touch_federated_peer(peer["peer_id"], "unreachable")
        return None, str(e)


def _view_summary(text) -> str:
    """Messaggio di log su una riga e accorciato.

    Nota onesta: questo TRONCA, non maschera. Se un log contiene testo di prompt
    o di risposta (i log di interazione dei nodi), quei 160 caratteri escono. E'
    il motivo per cui la condivisione della vista e' spenta di default: la
    decisione se condividere quel contenuto e' dell'operatore, non del codice.
    """
    flat = " ".join(str(text or "").split())
    return flat[:_VIEW_SUMMARY_MAX] + ("..." if len(flat) > _VIEW_SUMMARY_MAX else "")


def _cp_view_snapshot(log_limit: int = 40, task_limit: int = 40) -> dict:
    """Istantanea read-only di quello che sa questo CP (vedi confine sopra)."""
    warnings = []
    nodes = []
    for node in _node_list():
        nodes.append({
            "node_id":     node.get("node_id", ""),
            "alias":       node.get("alias", ""),
            "label":       node.get("label", ""),
            "tier":        node.get("tier", ""),
            "status":      node.get("status", ""),
            "endpoint":    _best_endpoint(node) or "",
            "vram_gb":     node.get("vram_gb", 0) or 0,
            "uptime_s":    node.get("uptime_s", 0) or 0,
            "is_web_node": bool(node.get("is_web_node")),
            "last_seen":   node.get("last_seen", ""),
        })
    nodes.sort(key=lambda n: (n["status"] != "active", n["node_id"]))

    try:
        agg = _contesto.aggregate_mesh_models()
        models = {"bare": list(agg.get("bare") or []),
                  "per_node": [e.get("id", "") for e in (agg.get("per_node") or [])]}
    except Exception as exc:
        models = {"bare": [], "per_node": []}
        warnings.append(f"modelli non disponibili: {exc}")

    tasks = []
    try:
        for row in (db.get_all_tasks() or [])[:max(0, task_limit)]:
            tasks.append({
                "task_id":      row.get("task_id", ""),
                "status":       row.get("status", ""),
                "node_id":      row.get("node_id", ""),
                "model":        row.get("model", ""),
                "created_at":   row.get("created_at", ""),
                "completed_at": row.get("completed_at", ""),
            })
    except Exception as exc:
        warnings.append(f"task non disponibili: {exc}")

    logs = []
    try:
        for row in (db.query_logs(page=1, per_page=max(1, log_limit)) or []):
            logs.append({
                "ts":      row.get("ts", ""),
                "type":    row.get("type", ""),
                "status":  row.get("status", ""),
                "source":  row.get("source", ""),
                "target":  row.get("target", ""),
                "summary": _view_summary(row.get("summary", "")),
            })
    except Exception as exc:
        warnings.append(f"log non disponibili: {exc}")

    return {
        "cp_id":        _contesto.cp_identity["node_id"],
        "pubkey":       _contesto.cp_identity["public_key"],
        "version":      "1.05",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "federation":   {"enabled": FEDERATION_ENABLED, "public_url": FEDERATION_PUBLIC_URL},
        "nodes":        nodes,
        "models":       models,
        "tasks":        tasks,
        "logs":         logs,
        "warnings":     warnings,
        "counts": {
            "nodes":     len(nodes),
            "active":    len([n for n in nodes if n["status"] == "active"]),
            "web_nodes": len([n for n in nodes if n["is_web_node"]]),
            "models":    len(models.get("bare") or []),
            "tasks":     len(tasks),
            "logs":      len(logs),
        },
    }


def _merge_views(local: dict, peers: list) -> dict:
    """Unisce la vista locale con quelle dei peer, tenendo la provenienza.

    I nodi si deduplicano per node_id (lo stesso nodo compare in piu' viste, una
    per CP che lo vede) ma si conserva CHI lo vede: una dashboard puo' cosi'
    scrivere "visto da entrambi" invece di due righe identiche, e soprattutto
    "visto da uno solo" — che e' la divergenza da guardare.
    I modelli si uniscono. Task e log NON si fondono in un flusso unico: sono
    storie locali di CP diversi, e mescolarle falsificherebbe la loro sequenza.
    """
    sources, seen_by, models = [], {}, set()
    peers_ok = [p for p in (peers or []) if p.get("ok") and p.get("view")]
    items = [{"kind": "local", "label": "locale", "view": local}] + [
        {"kind": "peer", "label": p.get("label") or (p.get("peer_id") or "?")[:8],
         "view": p.get("view")} for p in peers_ok]

    for src in items:
        view = src["view"] or {}
        sources.append({"kind": src["kind"], "label": src["label"],
                        "cp_id": view.get("cp_id", ""), "counts": view.get("counts", {})})
        for node in view.get("nodes") or []:
            nid = node.get("node_id", "")
            if not nid:
                continue
            entry = seen_by.setdefault(nid, {
                "node_id":     nid,
                "alias":       node.get("alias", ""),
                "tier":        node.get("tier", ""),
                "status":      node.get("status", ""),
                "is_web_node": bool(node.get("is_web_node")),
                "seen_by":     [],
            })
            if src["label"] not in entry["seen_by"]:
                entry["seen_by"].append(src["label"])
        for name in (view.get("models") or {}).get("bare") or []:
            models.add(name)

    nodes = sorted(seen_by.values(), key=lambda n: (n["status"] != "active", n["node_id"]))
    return {
        "sources": sources,
        "nodes":   nodes,
        "models":  sorted(models),
        "counts": {
            "sources": len(sources),
            "nodes":   len(nodes),
            "models":  len(models),
            # Un nodo visto da un CP solo e' l'indizio che i due non stanno
            # guardando la stessa mesh: e' il numero da tenere d'occhio.
            "nodes_partial": len([n for n in nodes if len(n["seen_by"]) < len(sources)]),
        },
    }


@_bp.route('/federation/views')
def federation_views():
    """Vista locale + viste dei peer, per la dashboard di QUESTO CP.

    NON e' nella whitelist del federation-gateway, ed e' voluto: e' un endpoint
    da dashboard interna. Chiama verso l'esterno (i peer) ma non si fa chiamare
    da fuori — se ci finisse, chiunque potrebbe usare questo CP come sonda verso
    i peer federati senza avere la loro chiave.

    Il risultato e' in cache per FEDERATION_VIEW_TTL_S secondi, perche' la
    dashboard la interroga in polling: `?refresh=1` la forza (pulsante Aggiorna).
    """
    now = time.time()
    fresh = request.args.get("refresh") not in ("1", "true", "yes")
    if (fresh and _VIEW_CACHE["data"] is not None
            and (now - _VIEW_CACHE["ts"]) < max(0, FEDERATION_VIEW_TTL_S)):
        cached = dict(_VIEW_CACHE["data"])
        cached["cached"] = True
        return jsonify(cached)

    local = _cp_view_snapshot()
    peers = []
    for peer in db.get_all_federated_peers():
        if not peer.get("enabled"):
            continue
        view, error = _fetch_peer_view(peer)
        peers.append({
            "peer_id":     peer.get("peer_id", ""),
            "label":       peer.get("label", ""),
            "endpoint":    peer.get("endpoint", ""),
            "last_status": peer.get("last_status", ""),
            "ok":          bool(view),
            "error":       error,
            "view":        view,
        })

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cached":       False,
        "ttl_s":        FEDERATION_VIEW_TTL_S,
        "view_enabled": FEDERATION_VIEW_ENABLED,
        "local":        local,
        "peers":        peers,
        "merged":       _merge_views(local, peers),
    }
    _VIEW_CACHE.update(ts=now, data=out)
    return jsonify(out)


def _try_federated_execution(prompt: str, model: str):
    """Prova i peer federati abilitati, in ordine, finche' uno risponde.
    Chiamata SOLO quando non ci sono nodi locali attivi disponibili —
    oggi e' un fallback semplice, non ancora integrato nello scoring
    pesato di _node_score (possibile evoluzione futura)."""
    if not FEDERATION_ENABLED:
        return None, None
    for peer in db.get_all_federated_peers():
        if not peer.get("enabled"):
            continue
        result = _federate_to_peer(peer, prompt, model)
        if result:
            return result, peer
    return None, None

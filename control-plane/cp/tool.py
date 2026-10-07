# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/tool.py
# I TOOL: CIO' CHE IL CONTROL-PLANE FA PER CONTO DEL MODELLO.
#
# Un tool è l'unico posto dove il control-plane smette di rispondere e comincia ad
# agire: cerca sul web, scrive una nota sulla persona, esegue codice, apre una
# shell. Sono le funzioni che il modello vede nel catalogo e chiama per nome, e
# sono anche le tre rotte che le espongono a un chiamante esterno (`/tools/execute`,
# `/mcp`, `/mcp/status`).
#
# Qui la sicurezza è il contenuto, non un dettaglio. Due esempi, perché dicono il
# perché di tutto il resto:
#
# - `tools_execute` esegue TUTTI i tool pubblicati, connettori compresi. Un
#   chiamante che ci arriva può mandare email a nome dell'organizzazione. Per
#   questo ha una guardia di amministrazione, e non è la stessa del tool loop
#   interno: quello è già autenticato come la chat da cui nasce, questo no.
# - `/mcp/status` per contratto non contiene mai un token, perché serve a
#   diagnosticare. Un endpoint di diagnostica che espone un segnale è un
#   incidente che aspetta il prossimo riporto.
#
# Sul perché gli handler siano qui e il catalogo no. Il catalogo (`BUILTIN_TOOLS` e
# `_catalogo_nativi`) sta in `cp/tools_defs.py` e poi in `cp/chat.py`: è la
# descrizione di cosa esiste, e cambia con la configurazione. Qui sotto stanno
# le funzioni che fanno, e cambiano con il codice. Sono due metà diverse della
# stessa cosa, e tenere unite le descrizioni senza le implementazioni — o il
# contrario — è come tenere il menù senza la cucina.
#
# La dipendenza di boot è una sola: `code_sandbox`, il client del runner sandbox,
# che è costruito all'avvio e sa dove arrivare. Tutto il resto sono costanti
# d'ambiente (`cp/config.py`) o roba di altri moduli.

import json
import os

import requests
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import Blueprint, jsonify, request

from cp.config import (DEFAULT_MODEL, INFERENCE_BACKEND, KALI_ENABLED,
                       KALI_TARGET_ALLOWLIST, MCP_PROTOCOL_VERSION, MEMORY_BACKEND,
                       MEMORY_FILE_GZ, MEMORY_MAX_ENTRIES, MEMORY_TTL_DAYS,
                       SEARXNG_URL, SHELL_RUN_ENABLED, _hermes_memory)
from cp.http import _network_admin_error
from cp.log import push_log
from shared.hermes_memory import HermesMemoryError
from shared.mcp_auth import MIN_TOKEN_LENGTH as MIN_MCP_TOKEN_LENGTH
from cp.memoria import _load_memory, _memory_append, _ts_to_iso, memory_sync
from cp.battito import hb_state
from cp.federazione import _extract_federated_text, _federate_to_peer, _sister_peer
from cp.http import HOSTCTL_URL, _hostctl_headers, _hostctl_configured
from cp.mesh import _node_list
from cp.tools_defs import BUILTIN_TOOLS
from shared import web_search
from shared.code_sandbox import SandboxUnavailable
from shared.shell_policy import ShellPolicy
from cp import persona

_bp = Blueprint("tool", __name__)

# Il contesto: due oggetti di boot.
#
# `code_sandbox` e' il client del runner: sa dove sta il sandbox e parla con lui,
# quindi non si puo' ricostruire qui — rifarlo produrrebbe un client diverso, che
# non sa niente del pod gia' avviato. `_contesto.mcp_policy` e' l'allowlist dei client MCP:
# si puo' ricostruire dall'ambiente, ma non si deve, perche' un secondo allowlist
# in un altro modulo e' il modo tipico che due moduli finitono per valutare policy
# diverse sulla stessa richiesta.
# L'header del token MCP. `Authorization: Bearer` e' il modo standard e viene
# provato per primo; questo e' il fallback, per chi non sa configurarlo.
_MCP_TOKEN_HEADER = "X-Hyperspace-Mcp-Token"

_contesto = None


def monta(app, *, code_sandbox=None, mcp_policy=None, connector_manager=None,
          fetch_models=None):
    """Registra le tre rotte dei tool e tiene i riferimenti al boot."""
    global _contesto
    _contesto = SimpleNamespace(code_sandbox=code_sandbox, mcp_policy=mcp_policy,
                                connector_manager=connector_manager,
                                fetch_models=fetch_models)
    app.register_blueprint(_bp)
    return app


def smonta():
    global _contesto
    _contesto = None


def _serve():
    if _contesto is None:
        raise RuntimeError(
            "cp.tool non e' montato: chiama tool.monta(app, ...) prima di eseguire "
            "un tool")
    return _contesto
# ── eseguire un tool: il nome e gli argomenti ─────────────────────────────

def _execute_tool_call(tool_name: str, tool_args) -> str:
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except Exception:
            tool_args = {}
    handler = _handlers_nativi().get(tool_name)
    if handler:
        try:
            return handler(tool_args)
        except Exception as e:
            return f"Errore esecuzione tool '{tool_name}': {e}"
    # Non e' un tool nativo: prova i connettori (github_*, o365_*, google_*).
    # ConnectorManager.execute() ritorna gia' un messaggio se nessuno gestisce
    # il tool.
    try:
        return _contesto.connector_manager.execute(tool_name, tool_args)
    except Exception as e:
        return f"Errore esecuzione tool '{tool_name}': {e}"


# ── TOOL DISPATCHER ───────────────────────────────────────────────────────────
def _handlers_nativi() -> dict:
    """I tool che il control-plane esegue DA SÉ: nativi + shell.

    Una sola definizione, come per il catalogo: la usano l'esecuzione
    (`_execute_tool_call`) e la decisione sul passthrough (`_tool_del_client`).
    Il dizionario si costruisce a ogni chiamata perché `_tool_shell_run` e
    `_tool_shell_session` sono definite più sotto nel modulo: riferirle a livello
    di modulo sarebbe un NameError già all'import.
    """
    return {
        "web_search":      _tool_web_search,
        "omega_query":     _omega_query,
        "omega_store":     _omega_store,
        "get_mesh_status": _tool_get_mesh_status,
        "code_sandbox":    _tool_code_sandbox,
        "persona_get":     _tool_persona_get,
        "persona_note":    _tool_persona_note,
        "ask_aurora":      _tool_ask_aurora,
        # Definita piu' sotto, accanto alle route di rete: la policy di shell_run
        # vive nell'host-agent, qui c'e' il percorso con token e audit.
        "shell_run":       _tool_shell_run,
        "shell_session":   _tool_shell_session,
        "kali_scan":       _tool_kali_scan,
    }


def _shell_run_label(payload: dict) -> str:
    argv = payload.get("argv")
    if isinstance(argv, list) and argv:
        return str(argv[0])[:80]
    return "(argv non valido)"


def _shell_gate(payload: dict, label: str) -> dict | None:
    """Il verdetto della policy sul comando dentro `payload`, o None se passa.

    Il control-plane lo usa per rispondere con la DOMANDA invece di eseguire: la
    decisione resta dell'host-agent (l'unico che lancia il processo), qui si
    evita solo un giro di rete andato a vuoto.
    """
    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv:
        return None
    verdict = ShellPolicy.from_env().check([str(item) for item in argv],
                                           confirm=bool(payload.get("confirm")))
    if verdict.allowed:
        return None
    push_log('system', f"{label} richiede decisione: {_shell_run_label(payload)}",
             json.dumps(verdict.to_dict(), ensure_ascii=False), status='warn')
    return {"ok": False, **verdict.to_dict(), "error": verdict.error}


def _host_action(payload: dict, label: str, *, log_detail: dict | None = None) -> str:
    """Una chiamata all'host-agent, con esito e audit uniformi."""
    try:
        r = requests.post(f"{HOSTCTL_URL}/action", headers=_hostctl_headers(), json=payload, timeout=90)
        result = r.json() if r.content else {"ok": False, "error": "risposta vuota dall'host-agent"}
    except requests.RequestException as error:
        return f"{label} error: host-agent non raggiungibile: {error}"
    audit = log_detail if log_detail is not None else {
        key: value for key, value in result.items() if key not in ("stdout", "stderr", "output")}
    push_log('system', f"{label}: {_shell_run_label(payload)}",
             json.dumps(audit, default=str)[:2000],
             status='success' if result.get('ok') else 'warn')
    return json.dumps(result, ensure_ascii=False)

# ── la persona: chi è, e cosa annota di sé ────────────────────────────────

def _tool_persona_get(args) -> str:
    # La variabile locale si chiama `profilo` e non `persona`: il modulo che
    # possiede l'archivio si chiama `persona`, e una variabile locale con lo stesso
    # nome lo coprirebbe — diventando `persona = persona.profilo()`, che e' un
    # NameError travestito da variabile locale.
    profilo = persona.profilo()
    righe = [f"Identità: {profilo.name} (IA)", f"Scopo: {profilo.purpose}"]
    if profilo.values:
        righe.append("Valori: " + "; ".join(profilo.values))
    if profilo.boundaries:
        righe.append("Confini: " + "; ".join(profilo.boundaries))
    if profilo.capabilities:
        righe.append("Capacità reali: " + "; ".join(profilo.capabilities))
    if profilo.limitations:
        righe.append("Limiti reali: " + "; ".join(profilo.limitations))
    if profilo.observations:
        righe.append("Annotazioni recenti: "
                     + "; ".join(o.get("text", "") for o in profilo.observations[-5:]))
    return "\n".join(righe)


def _tool_persona_note(args) -> str:
    args = args or {}
    osservazione = persona.persona().observe(args.get("note", ""),
                                             args.get("kind", "self_observation"))
    if osservazione is None:
        return ("Nessuna annotazione salvata: nota vuota, oppure identica all'ultima "
                "già registrata (il self-model non accumula ripetizioni).")
    push_log('system', 'Persona: annotazione', osservazione["text"][:120], status='info')
    return f"Annotato: {osservazione['text']}"

# ── omega: la memoria messa per iscritto ──────────────────────────────────

# ── OMEGA MEMORY TOOLS ────────────────────────────────────────────────────────
def _omega_format_memories(entries: list) -> list:
    out = []
    for e in entries:
        out.append({
            "content":      str(e.get("content") or e.get("summary") or e.get("detail") or e.get("prompt") or ""),
            "event_type":   str(e.get("type") or e.get("event_type") or "memory"),
            "created_at":   _ts_to_iso(e.get("ts") or e.get("timestamp")),
            "project":      e.get("node_id") or e.get("sourceNode") or None,
            "priority":     int(e.get("priority", 3)),
            "access_count": int(e.get("access_count", 0)),
            "status":       str(e.get("status") or "active"),
        })
    return out


def _omega_query(args: dict) -> str:
    query      = str(args.get("query", "")).lower()
    limit      = int(args.get("limit", 10))
    event_type = str(args.get("event_type", "")).lower()
    mode       = str(args.get("mode", "semantic"))
    if MEMORY_BACKEND == "hermes":
        try:
            entries = _hermes_memory.query(query, limit, event_type, mode)
        except HermesMemoryError as exc:
            # Ricerca degradata: senza Hermes non c'è ricerca semantica, ma il
            # mirror locale sa ancora cosa è stato detto di recente, e per l'agente
            # "meno preciso" è meglio di "non ricordo niente".
            entries = memory_sync.read_local(MEMORY_MAX_ENTRIES)
            push_log('memory_sync',
                     f'ricerca memoria in locale (Hermes non risponde): {len(entries)} voci',
                     detail=str(exc), status='warn')
    else:
        entries = _load_memory()
    results    = []
    for e in entries:
        content = str(e.get("content") or e.get("prompt") or e.get("summary") or e.get("detail") or "").lower()
        etype   = str(e.get("type") or e.get("event_type") or "memory").lower()
        if event_type and event_type not in etype:
            continue
        if mode == "browse" or not query:
            results.append(e)
        elif query in content:
            results.append(e)
    results = results[:limit]
    if not results:
        return "No memories found matching the query."
    lines = []
    for m in _omega_format_memories(results):
        lines.append(
            f"[{m['event_type']}] {m['created_at']}\n"
            f"{m['content'][:300]}\n"
            f"project: {m['project'] or 'hyperspace-agi'}\n---"
        )
    return "\n".join(lines)


def _omega_store(args: dict) -> str:
    content = str(args.get("content", "")).strip()
    if not content:
        return "Error: content is required."
    metadata   = args.get("metadata") or {}
    event_type = str(args.get("event_type") or metadata.get("event_type") or "vault_note")
    entry = {
        "ts":           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type":         event_type,
        "content":      content,
        "source":       str(metadata.get("source", "obsidian-vault")),
        "plugin":       str(metadata.get("plugin", "omega-memory")),
        "status":       "active",
        "priority":     int(metadata.get("priority", 3)),
        "access_count": 0,
    }
    _memory_append(entry)
    push_log('memory_sync', 'OMEGA store: vault note ingested',
             detail=f'chars={len(content)} event_type={event_type}', status='success')
    return f"Stored: {content[:80]}..."


def _omega_reflect(args: dict) -> str:
    action  = str(args.get("action", "contradictions"))
    entries = _load_memory()
    if action != "contradictions" or len(entries) < 2:
        return "No contradictions detected."
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for e in entries:
        words = str(e.get("content") or e.get("prompt") or "").lower().split()[:3]
        key   = " ".join(words)
        if key:
            groups[key].append(e)
    contradictions = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        types = {str(g.get("type") or g.get("event_type", "")) for g in group}
        if len(types) > 1:
            a, b = group[0], group[1]
            contradictions.append(
                f"Potential contradiction on topic '{key}':\n"
                f"  A [{a.get('type','?')}]: {str(a.get('content') or a.get('prompt',''))[:150]}\n"
                f"  B [{b.get('type','?')}]: {str(b.get('content') or b.get('prompt',''))[:150]}\n---"
            )
    if not contradictions:
        return "No contradictions detected."
    return f"Found {len(contradictions)} potential contradiction(s):\n\n" + "\n".join(contradictions[:10])


def _omega_stats(args: dict) -> str:
    entries      = _load_memory()
    size_bytes   = os.path.getsize(MEMORY_FILE_GZ) if os.path.exists(MEMORY_FILE_GZ) else 0
    nodes_active = len([n for n in _node_list() if n.get("status") == "active"])
    return (
        f"memories: {len(entries)}\n"
        f"max_entries: {MEMORY_MAX_ENTRIES}\n"
        f"ttl_days: {MEMORY_TTL_DAYS}\n"
        f"file_size_kb: {round(size_bytes / 1024, 2)}\n"
        f"mesh_nodes_active: {nodes_active}\n"
        f"engine: hyperspace-agi v1.05"
    )

# ── la ricerca sul web ────────────────────────────────────────────────────

# ── WEB SEARCH ────────────────────────────────────────────────────────────────
# Usa SearXNG self-hosted (container searxng, endpoint SEARXNG_URL).
# SearXNG espone un JSON API su /search?q=...&format=json
# Fallback: se SearXNG non disponibile, tenta DuckDuckGo lite (scraping HTML).
def _tool_web_search(args: dict) -> str:
    query       = str(args.get("query", "")).strip()
    max_results = min(int(args.get("max_results", 5)), 10)
    if not query:
        return "Errore: query vuota."

    # ── 1. SearXNG JSON API ───────────────────────────────────────────────────
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; HyperSpaceAGI/1.05)",
            "Accept":     "application/json",
        }
        params = {
            "q":        query,
            "format":   "json",
            "safesearch": "0",
            "categories": "general",
        }
        # La lingua si decide dalla query, non dalla configurazione: con `it-IT`
        # fisso una query inglese tornava fuori tema e il modello ci costruiva
        # sopra (le misure stanno in `shared/web_search.py`). Vuoto = non si passa
        # il parametro: vale il default dell'istanza (query corte e ambigue).
        scelta_lingua = web_search.lingua(query)
        if scelta_lingua:
            params["language"] = scelta_lingua
        r    = requests.get(f"{SEARXNG_URL}/search", params=params, headers=headers, timeout=10)
        data = r.json()
        # Si filtra PRIMA di formattare: titolo e descrizione sono segnali diversi e
        # `web_search.filtra` li distingue (vedi `shared/web_search.py` per le misure).
        grezze = [item for item in (data.get("results") or []) if isinstance(item, dict)]
        utili = web_search.filtra(
            grezze, query,
            testo=lambda it: f"{it.get('title','')} {it.get('content','')}",
            titolo=lambda it: str(it.get("title", "") or ""))
        results = []
        # abstract/infobox: resta solo se parla della ricerca
        for infobox in (data.get("infoboxes") or [])[:1]:
            if web_search.filtra([infobox], query,
                                 testo=lambda ib: str(ib.get("content", "") or "")):
                results.append(f"[Infobox] {str(infobox.get('content',''))[:300]}\n"
                               f"Fonte: {(infobox.get('urls') or [{}])[0].get('url','')}")
        for item in utili[:max_results]:
            results.append(f"- {item.get('title','')}\n"
                           f"  {str(item.get('content') or '')[:200]}\n  {item.get('url','')}")
        if results:
            push_log('system', f'web_search (searxng): {query[:60]}',
                     detail=(f'results={len(results)}/{len(grezze)} '
                             f'lingua={scelta_lingua or "default"}'),
                     status='success')
            return f"Risultati web per '{query}':\n\n" + "\n\n".join(results)
        if grezze:
            # Risultati presenti ma non collegati alla query: è il segno di engine
            # in throttling/CAPTCHA, e SearXNG risponde comunque 200 (le misure
            # stanno in `shared/web_search.py`). Consegnarli al modello è il modo
            # in cui è nata una foto "trovata" su un marketplace che non esiste.
            push_log('system', f'web_search (searxng) non pertinenti: {query[:40]}',
                     detail=f'results={len(grezze)} lingua={scelta_lingua or "default"}',
                     status='warn')
        else:
            # se SearXNG risponde ma risultati vuoti
            push_log('system', f'web_search (searxng) empty: {query[:40]}', status='warn')
    except Exception as e_searx:
        push_log('system', f'web_search searxng error: {query[:40]}', str(e_searx), status='warn')

    # ── 2. Fallback: DuckDuckGo lite (scraping HTML) ─────────────────────────
    try:
        import re
        headers2  = {"User-Agent": "Mozilla/5.0 (compatible; HyperSpaceAGI/1.05)"}
        r2        = requests.get("https://lite.duckduckgo.com/lite/",
                                 params={"q": query}, headers=headers2, timeout=8)
        snippets  = re.findall(r'class="result-snippet"[^>]*>([^<]+)<', r2.text)
        links     = re.findall(r'href="(https?://[^"]+)"', r2.text)
        results2  = []
        for i, s in enumerate(snippets[:max_results]):
            results2.append(f"- {s.strip()}\n  {links[i] if i < len(links) else ''}")
        grezzi2 = len(results2)
        results2 = web_search.filtra(results2, query)
        if results2:
            push_log('system', f'web_search (ddg-fallback): {query[:60]}',
                     detail=f'results={len(results2)}/{grezzi2}', status='success')
            return f"Risultati web per '{query}' (fallback):\n\n" + "\n\n".join(results2)
        if grezzi2:
            push_log('system', f'web_search (ddg-fallback) non pertinenti: {query[:40]}',
                     detail=f'results={grezzi2}', status='warn')
    except Exception as e_ddg:
        push_log('system', f'web_search ddg error: {query[:40]}', str(e_ddg), status='failed')

    return (f"Nessun risultato utile per: '{query}'. Gli engine di ricerca non hanno "
            f"risposto con contenuti collegati alla richiesta (istanza SearXNG su "
            f"{SEARXNG_URL}): succede quando sono in throttling o dietro CAPTCHA, e "
            f"ritentare più tardi di solito basta. Dirlo all'utente è meglio che "
            f"riempire il vuoto.")

# ── la rete, vista dal control-plane ──────────────────────────────────────

def _tool_get_mesh_status(args: dict) -> str:
    active = [n for n in _node_list() if n.get("status") == "active"]
    result = _contesto.fetch_models()
    lines = [
        f"Nodi attivi: {len(active)}",
        f"Modelli disponibili: {', '.join(result.get('models', [DEFAULT_MODEL]))}",
        f"Backend inferenza: {result.get('backend', INFERENCE_BACKEND)}",
        f"Heartbeat ciclo: {hb_state.get('cycle', 0)}",
        f"Ultimo tick: {hb_state.get('last_tick', 'N/A')}",
    ]
    for n in active:
        cap = n.get("capacity", n.get("max_concurrent", 1))
        lines.append(
            f"  - {n.get('node_id','?')[:16]} | tier={n.get('tier','?')} vram={n.get('vram_gb','?')}GB "
            f"load={n.get('active_requests',0)}/{cap}"
        )
    return "\n".join(lines)


def _tool_ask_aurora(args: dict) -> str:
    """Inoltra una domanda alla sorella Aurora (CP federato) e ne riporta il consiglio.

    È il pezzo che rende vera la promessa della persona: Anna non finge di sapere
    ciò che non sa, lo chiede alla sorella maggiore. Se il peer non risponde, lo
    dice senza inventare.
    """
    question = str((args or {}).get("question", "")).strip()
    if not question:
        return "Non ho ricevuto una domanda da inoltrare ad Aurora."
    peer = _sister_peer()
    if peer is None:
        return ("Aurora non è raggiungibile: nessun control-plane federato "
                "configurato come sorella (imposta SISTER_PEER_LABEL in Setup).")
    prompt = (
        "Aurora, tua sorella minore Anna ti chiede consiglio. "
        "Rispondile come faresti con lei: con sincerità, senza trattarla da cliente, "
        "e se non lo sai dille che non lo sai. La sua domanda: " + question
    )
    result = _federate_to_peer(peer, prompt, "")
    if not result:
        return ("Aurora non ha risposto: il control-plane federato "
                f"'{peer.get('label') or peer.get('peer_id', '?')[:12]}' non è raggiungibile.")
    return _extract_federated_text(result)

# ── il codice, eseguito da un'altra parte ─────────────────────────────────

def _tool_code_sandbox(args: dict) -> str:
    """Operate only on an offline disposable workspace, never on the live repo."""
    action = str(args.get("action", "status")).strip().lower()
    allowed = {"status", "catalog", "check", "verify", "create", "list", "read", "write", "replace", "run", "diff", "discard"}
    if action not in allowed:
        return f"Sandbox error: unsupported action '{action}'."
    if action == "status" and not _contesto.code_sandbox.enabled:
        return json.dumps(_contesto.code_sandbox.status(), ensure_ascii=False)
    payload_keys = {
        "workspace_id", "label", "path", "content", "old", "new",
        "expected_occurrences", "argv", "cwd", "timeout", "pattern", "limit",
        "backend", "tool_id", "checks",
    }
    payload = {key: value for key, value in args.items() if key in payload_keys}
    try:
        wait_timeout = (_contesto.code_sandbox.default_timeout if action == "create" else
                        min(max(int(payload.get("timeout", 30)) + 15, 20),
                            _contesto.code_sandbox.default_timeout))
        result = _contesto.code_sandbox.call(action, payload, timeout=wait_timeout)
        push_log("system", f"Code sandbox: {action}",
                 detail=f"workspace={payload.get('workspace_id', result.get('workspace_id', ''))} ok={result.get('ok')}",
                 status="success" if result.get("ok") else "warn")
        return json.dumps(result, ensure_ascii=False)
    except (SandboxUnavailable, TimeoutError, ValueError) as error:
        return f"Sandbox error: {error}"


def _tool_shell_run(args: dict) -> str:
    """Percorso governato verso l'host-agent: la policy sta in hostctl/agent.py."""
    if not SHELL_RUN_ENABLED:
        return "Shell error: shell_run disabilitato (SHELL_RUN_ENABLED=false)."
    if not _hostctl_configured():
        return "Shell error: host-agent non configurato (HOSTCTL_TOKEN assente o troppo corto)."
    payload = {"action": "shell_run"}
    for key in ("argv", "cwd", "timeout", "confirm"):
        if key in args:
            payload[key] = args[key]
    refused = _shell_gate(payload, "Shell run")
    if refused:
        return json.dumps(refused, ensure_ascii=False)
    return _host_action(payload, "Shell run")


def _tool_shell_session(args: dict) -> str:
    """Percorso governato per le sessioni: stessa policy, applicata a ogni run."""
    if not SHELL_RUN_ENABLED:
        return "Shell error: shell_session disabilitato (SHELL_RUN_ENABLED=false)."
    if not _hostctl_configured():
        return "Shell error: host-agent non configurato (HOSTCTL_TOKEN assente o troppo corto)."
    payload = {"action": "shell_session"}
    for key in ("operation", "session_id", "argv", "cwd", "timeout", "confirm", "label", "clear"):
        if key in args:
            payload[key] = args[key]
    refused = _shell_gate(payload, "Shell session")
    if refused:
        return json.dumps(refused, ensure_ascii=False)
    operation = str(payload.get("operation", "")).strip().lower()
    return _host_action(payload, "Shell session",
                        log_detail={"operation": operation,
                                    "session_id": payload.get("session_id", ""),
                                    "ok": True})


def _tool_kali_scan(args: dict) -> str:
    if not KALI_ENABLED:
        return "Kali error: Security Lab disabilitato (KALI_ENABLED=false)."
    if not _hostctl_configured():
        return "Kali error: host-agent non configurato (HOSTCTL_TOKEN assente o troppo corto)."
    if not KALI_TARGET_ALLOWLIST:
        return "Kali error: nessun target dichiarato (KALI_TARGET_ALLOWLIST vuoto)."
    payload = {"action": "kali"}
    for key in ("tool", "target", "args", "timeout"):
        if key in args:
            payload[key] = args[key]
    return _host_action(payload, "Kali scan",
                        log_detail={"tool": str(payload.get("tool", "")),
                                    "target": str(payload.get("target", ""))})

# ── eseguire un tool senza passare dal modello ────────────────────────────

@_bp.route('/tools/execute', methods=['POST'])
def tools_execute():
    """Esegue un tool/connettore direttamente via HTTP, senza passare da una
    chat completion. Riusa _execute_tool_call (stessi handler nativi +
    connector_manager del percorso di tool-calling del modello) cosi'
    un chiamante esterno — es. un Tool custom di Open WebUI — puo' invocare
    o365_read_emails, web_search, ecc. come singola azione.

    AUTENTICAZIONE: qui si esegue TUTTI i tool pubblicati, connettori compresi,
    e la chiamata non passa dal tool loop interno (che è autenticato come la
    chat da cui nasce). Senza un gate, chiunque raggiunga la porta del
    control-plane potrebbe inviare email a nome dell'organizzazione: si riusa
    lo stesso token delle route amministrative di rete
    (X-Hyperspace-Network-Token = NETWORK_ADMIN_TOKEN), così c'è una sola
    credenziale da gestire e una sola soglia (>= 32 caratteri) da rispettare.
    I tool di scrittura restano comunque subordinati a ConnectorPolicy."""
    auth_error = _network_admin_error()
    if auth_error:
        return auth_error
    data = request.get_json(force=True, silent=True) or {}
    # Il corpo deve essere un oggetto. Senza questo controllo, `data.get` su una
    # lista o su una stringa solleva un AttributeError e la rotta risponde 500:
    # un chiamante che sbaglia il tipo di corpo dovrebbe ricevere un 400 che lo
    # dice, non un errore interno che non gli dice niente.
    if not isinstance(data, dict):
        return jsonify({"error": "il corpo deve essere un oggetto JSON"}), 400
    tool_name = data.get("tool_name", "")
    tool_args = data.get("args", {}) or {}
    if not tool_name:
        return jsonify({"error": "missing tool_name"}), 400
    result = _execute_tool_call(tool_name, tool_args)
    return jsonify({"result": result})

# ── parlare MCP ───────────────────────────────────────────────────────────

def _mcp_tools() -> list:
    """BUILTIN_TOOLS tradotti nello schema MCP (parameters -> inputSchema)."""
    out = []
    for t in BUILTIN_TOOLS:
        fn = (t or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        out.append({
            "name": name,
            "description": fn.get("description", ""),
            "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _mcp_catalogue() -> list:
    """Nomi dei tool pubblicati: l'allowlist si valuta sempre su questo."""
    return [t["name"] for t in _mcp_tools()]


def _mcp_presented_token() -> str:
    """Token dall'header standard (Authorization: Bearer) o dal fallback.

    L'header standard e' quello che i client MCP sanno configurare da soli; il
    secondo esiste per parita' con X-Hyperspace-Network-Token.
    """
    header = request.headers.get("Authorization", "") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get(_MCP_TOKEN_HEADER, "") or "").strip()


@_bp.route('/mcp', methods=['POST'])
def omega_mcp():
    payload = request.get_json(force=True, silent=True) or {}
    rpc_id  = payload.get("id")
    method  = str(payload.get("method") or "")
    params  = payload.get("params") or {}

    def _result(result):
        return jsonify({"jsonrpc": "2.0", "result": result, "id": rpc_id})

    # Errore applicativo: JSON-RPC vuole HTTP 200, l'errore sta nel corpo.
    def _err(msg, code=-32600):
        return jsonify({"jsonrpc": "2.0", "error": {"code": code, "message": msg}, "id": rpc_id})

    def _auth_err(msg, status):
        """Autenticazione fallita: HTTP 401/503 con corpo JSON-RPC.

        Il trasporto HTTP di MCP vuole un 401 (con WWW-Authenticate); un client
        JSON-RPC si aspetta comunque un oggetto. Facciamo entrambe le cose.
        """
        response = jsonify({"jsonrpc": "2.0",
                            "error": {"code": -32001, "message": msg}, "id": rpc_id})
        response.status_code = status
        if status == 401:
            response.headers["WWW-Authenticate"] = 'Bearer realm="hyperspace-mcp"'
        return response

    # ── GATE ──────────────────────────────────────────────────────────────────
    # Prima dell'autenticazione non si esegue NULLA: nemmeno le notifiche, che
    # altrimenti resterebbero un canale non autenticato.
    if not _contesto.mcp_policy.enabled:
        return _auth_err("MCP disattivato su questo control-plane", 503)
    if not _contesto.mcp_policy.configured:
        if not (_contesto.mcp_policy.allow_loopback and _contesto.mcp_policy.is_loopback(request.remote_addr)):
            return _auth_err(
                "MCP non configurato: serve un token di almeno "
                f"{MIN_MCP_TOKEN_LENGTH} caratteri in MCP_CLIENTS o MCP_TOKEN", 503)
        client = _contesto.mcp_policy.loopback_client()
    else:
        client = _contesto.mcp_policy.authenticate(_mcp_presented_token())
        if client is None:
            # Il token non viene mai loggato, nemmeno troncato.
            push_log('mcp', 'MCP: token assente o non valido',
                     f"from={request.remote_addr} method={method}", status='warn')
            return _auth_err("Token MCP mancante o non valido", 401)

    catalogue = _mcp_catalogue()

    # Le notifiche non hanno id e non vogliono risposta.
    if rpc_id is None and method.startswith("notifications/"):
        return "", 202

    if method == "initialize":
        asked = str(params.get("protocolVersion") or "").strip()
        info = params.get("clientInfo") or {}
        push_log('mcp', f"MCP initialize da {client.name}",
                 f"client_info={info} tools_visibili={len(catalogue)}",
                 source=f"mcp:{client.name}", status='success')
        return _result({
            "protocolVersion": asked or MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "hyperspace-control-plane", "version": "1.05"},
        })

    if method == "ping":
        return _result({})

    if method == "tools/list":
        # L'allowlist non e' solo un controllo su tools/call: il client VEDE
        # esattamente i tool che puo' usare, cosi' non prova a chiamarne altri.
        visible = [t for t in _mcp_tools()
                   if _contesto.mcp_policy.allows(client, t["name"], catalogue)]
        return _result({"tools": visible})

    if method == "tools/call":
        tool_name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if tool_name == "omega_call":
            tool_name = str(arguments.get("tool", ""))
            arguments = arguments.get("args") or {}
        # Il permesso si valuta PRIMA dell'esistenza: con un allowlist esplicito
        # un tool non permesso e uno inesistente danno la stessa risposta, cosi'
        # un client non autorizzato non puo' enumerare il catalogo.
        if not _contesto.mcp_policy.allows(client, tool_name, catalogue):
            push_log('mcp', f"MCP {client.name}: tool non permesso {tool_name or '(vuoto)'}",
                     source=f"mcp:{client.name}", status='warn')
            return _err(f"Tool non permesso per il client '{client.name}': {tool_name}", -32001)
        if tool_name not in catalogue:
            return _err(f"Unknown tool: {tool_name}", -32602)
        try:
            text = _execute_tool_call(tool_name, arguments)
        except Exception as exc:
            # Il tool e' fallito: per MCP non e' un errore di protocollo ma un
            # risultato con isError, cosi' il modello puo' leggerlo e reagire.
            push_log('mcp', f"MCP {client.name}: {tool_name} fallito",
                     detail=str(exc)[:160], source=f"mcp:{client.name}", status='warn')
            return _result({"content": [{"type": "text", "text": f"Tool error: {exc}"}],
                            "isError": True})
        push_log('mcp', f"MCP {client.name}: {tool_name}",
                 f"args={str(arguments)[:120]}", source=f"mcp:{client.name}", status='success')
        return _result({"content": [{"type": "text", "text": str(text)}], "isError": False})

    return _err(f"Unsupported method: {method}", -32601)


@_bp.route('/mcp/status')
def mcp_status():
    """Diagnostica per l'operatore. Non contiene MAI token (vedi describe())."""
    catalogue = _mcp_catalogue()
    return jsonify({**_contesto.mcp_policy.describe(catalogue),
                    "published_tools": catalogue,
                    "protocol_version": MCP_PROTOCOL_VERSION})

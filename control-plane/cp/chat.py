# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/chat.py
# IL CONTRATTO DI `/v1/chat/completions`, SENZA LA ROTTA.
#
# Qui ci sono dieci funzioni e nessuna route. La rotta è ancora in main.py, e ci
# resta di proposito: è lunga 412 righe e chiama 49 funzioni, fra cui il routing
# dei nodi, la memoria condivisa, la persona e la federazione. Tirarla fuori tutta
# insieme avrebbe significato iniettare in questo modulo venti funzioni che hanno
# anche altri chiamanti, e il risultato sarebbe stato un modulo che importa metà
# di main.py — cioè un mezzo monolith travestito, che è il contrario di quello che
# si voleva.
#
# Queste dieci invece sono un pezzo coerente e sono le più delicate di tutte,
# perché decidono la FORMA di quello che esce. Una `_chunk_finale` sbagliata non
# rompe una richiesta: restituisce 200, il nodo chiama riceve un JSON valido e
# sbagliato, e il difetto si vede mesi dopo, in una conversazione. Nessun test di
# unità sul monolite l'avrebbe preso, perché il monolite rispondeva esattamente
# lo stesso prima e dopo. Per questo sono le prime a muoversi: sono pure, si
# possono provare una per una, e il costo di sbagliarle è alto ma contenuto.
#
# Il gruppo:
#
# - `parse_inference_urls`, `_inference_urls`: gli endpoint di inferenza come
#   lista. Il fallback non deve mai diventare una lista vuota, perché sotto
#   nessun carico il nodo deve poter ancora rispondere.
# - `_chunk_finale`, `_stream_direct`: i pezzi SSE. L'ordine con cui i `delta`
#   arrivano è ciò che il client OpenAI-compatible usa per ricostruire il testo,
#   e un indice fuori posto concatena i pezzi nel punto sbagliato: il risultato
#   resta un testo coerente, solo che inventato.
# - `_requested_thinking`, `_decide_thinking`, `_deadline_exceeded`: la modalità
#   `think`. Il `reasoning` di un modello finisce in `content` quando il client non
#   l'ha chiesto, e lasciare il campo vuoto produce una risposta apparentemente
#   vuota. `_deadline_exceeded` dice al client PERCHÉ la risposta si è interrotta,
#   invece di lasciargli pensare che il modello abbia finito.
# - `_tool_calls_passthrough`, `_tool_del_client`, `_risposta_solo_tool_del_client`:
#   i tool che il CLIENT esegue. Qui la forma conta più che altrove, perché un
#   `tool_calls` con indici sbagliati fa scrivere il risultato del tool dove non
#   va, e il modello prosegue convinto su un'affermazione che nessuno ha fatta.
#
# Le dipendenze delle dieci, misurate e non dedotte: tre oggetti, `db`,
# `push_log` e `jsonify`, ma nessuno è un oggetto di boot — `db` è il modulo
# `shared.db` e `push_log` sta già in `cp/log.py`. L'unica dipendenza da main.py
# è `_handlers_nativi`, che dice quali tool nativi esistono e resta iniettata.
#
# Il primo tentativo di questo modulo dichiarava "zero dipendenze" perché tre
# funzioni su dieci non toccavano nulla: non avevo guardato `_deadline_exceeded`,
# che scrive su `db` e restituisce una risposta Flask. La misura l'ha trovato in
# un secondo, l'affermazione falsa no.
#
# Cosa NON c'è, e perché:
#
# - La rotta `v1_chat_completions` e `_stream_gen`, che è annidata dentro di lei.
#   `_stream_gen` ha bisogno della rotta per l'URL e per il contesto di richiesta,
#   quindi non può precederla. È il passo dopo, non prima.
# - Il routing dei nodi (`_best_endpoint`, `_rank_candidate_nodes`), la memoria
#   condivisa (`_memory_append`, `_load_memory`), la persona (`_with_persona`) e
#   la federazione: sono tutti infrastruttura con molti chiamanti, e vanno
#   estratti come dominio loro, non passati qui come iniettati.

import json
import os
import uuid
from types import SimpleNamespace
import time
from datetime import datetime, timezone

import requests
from flask import (Blueprint, Response, jsonify, request,
                   stream_with_context)

import shared.db as db
from cp import persona as _persona_modulo
from cp.assistant import _assistant_text, _normalize_assistant_message
from cp.budget import RequestDeadline, _respond_result
from cp.config import MESH_MODEL_ICON, OMNIROUTE_MODEL_ID
from cp.federazione import _try_federated_execution
from cp.http import _sse_headers
from cp.inferenza import NodeBusyError, _call_ollama, _local_model_post
from cp.mesh import (_best_endpoint, _node_list, _parse_model_node_ref,
                     _rank_candidate_nodes, _record_routing_pick)
from cp.model_caps import (_model_supports_tools, _tools_requested_off,
                           _use_native_chat_fallback, _warn_tools_stripped)
from cp.persona import _persona_enabled
from shared.forge_skills import attach_skills
from shared.identity import make_request_headers
from shared.persona import should_disclose
from cp.log import push_log
from cp.config import (INFERENCE_BACKEND, OLLAMA_URL, OMNIROUTE_API_KEY,
                       OMNIROUTE_ENABLED, OMNIROUTE_MODEL, OMNIROUTE_URL,
                       PROMPT_COMPRESSION_ENABLED, PROMPT_COMPRESSION_MIN_CHARS,
                       PROMPT_COMPRESSION_MODE, DEFAULT_MODEL)
from cp.memoria import _memory_append, _notify_bridge
from cp.tools_defs import BUILTIN_TOOLS
from cp.budget import _inference_timeout, _is_error_payload
from cp.tool import _execute_tool_call
from shared.persona import identity_tools_hidden

# I tool nativi disponibili: il modello li vede nel catalogo, e un tool che non ha
# un handler non deve essere proposto. Resta in main.py perche' gli handler
# chiamano `connector_manager`, che è un oggetto di boot.
_handlers_nativi = None


_bp = Blueprint("chat", __name__)

# Il contesto: quattro cose di main.py che non sono il protocollo della chat.
#
# `advanced_config` e' la configurazione viva e la tab Setup la modifica in posto —
# iniettarla per riferimento e' quello che la rende viva anche qui; un binding per
# valore, o un import per nome, mostrerebbero a questa rotta la configurazione di
# avvio. `tasks` e' la coda condivisa, e vale lo stesso: la rotta ci scrive un task
# e il battito lo legge, quindi devono vedere lo stesso dizionario. `cp_identity` e'
# la chiave con cui si firma verso i nodi, e non si ricostruisce: rifatta
# produrrebbe una firma diversa. `forge_read_skill` legge un artefatto del forge, e
# resta un callback perche' il forge e' un altro dominio.
_contesto = None


def monta(app, *, advanced_config=None, tasks=None, cp_identity=None,
          forge_read_skill=None):
    """Registra `/v1/chat/completions`, la rotta che il modello usa per parlare."""
    global _contesto
    _contesto = SimpleNamespace(advanced_config=advanced_config or {},
                                tasks=tasks if tasks is not None else {},
                                cp_identity=cp_identity or {},
                                forge_read_skill=forge_read_skill)
    app.register_blueprint(_bp)
    return app


def smonta():
    global _contesto
    _contesto = None


def imposta_handlers(fn) -> None:
    """Registra la funzione che dice quali tool nativi esistono.

    Un solo punto di montaggio, come le tre liste dei canali: se i due moduli
    tenessero ognuno la propria copia, il primo che legge dopo un salvataggio
    dalla tab Setup leggerebbe il catalogo vecchio.
    """
    global _handlers_nativi
    _handlers_nativi = fn


def catalogo_nativi(superficie: str = "") -> list:
    """I tool nativi per quella superficie (se `_handlers_nativi` non c'e' ancora)."""
    if _handlers_nativi is None:
        return []
    return _handlers_nativi()

# ── gli endpoint di inferenza, come lista col fallback ────────────────────────

def parse_inference_urls(value: str, fallback: str) -> list:
    """Endpoint di inferenza diretta come lista.

    `value` (DIRECT_INFERENCE_URLS) è separato da virgole; se vuoto, ricade su
    `fallback` (OLLAMA_URL). Pura: nessun accesso a stato globale, testabile
    senza Flask (vedi tests/test_inference_fallback.py).
    """
    urls = [u.strip().rstrip("/") for u in str(value or "").split(",") if u.strip()]
    fallback = str(fallback or "").strip().rstrip("/")
    return urls or ([fallback] if fallback else [])

def _inference_urls() -> list:
    """Gli endpoint diretti attuali: DIRECT_INFERENCE_URLS oppure OLLAMA_URL."""
    return parse_inference_urls(os.getenv("DIRECT_INFERENCE_URLS", ""), OLLAMA_URL)
# ── i pezzi SSE: la forma di quello che esce ──────────────────────────────────

def _chunk_finale(result_json, model: str, task_id: str) -> dict:
    """Il chunk SSE con cui si chiude un giro di tool loop.

    Se la risposta porta dei tool del client (passthrough) il chunk li porta con
    sé come delta `tool_calls`: è il formato che Open WebUI legge per eseguirli.
    Senza, un client in streaming non vedrebbe mai la chiamata — il ramo
    tool-capable dello stream passa dal loop e riconfeziona solo il testo.
    """
    scelte = (result_json.get("choices") or [{}]) if isinstance(result_json, dict) else [{}]
    messaggio = (scelte[0] or {}).get("message") or {}
    tool_calls = _tool_calls_passthrough(result_json)
    delta = {"role": "assistant", "content": _assistant_text(messaggio)}
    fine = "stop"
    if tool_calls:
        delta["tool_calls"] = [
            {"index": indice,
             "id": tc.get("id") or f"call_{indice}",
             "type": "function",
             "function": {"name": (tc.get("function") or {}).get("name", ""),
                          "arguments": (tc.get("function") or {}).get("arguments") or "{}"}}
            for indice, tc in enumerate(tool_calls)]
        fine = "tool_calls"
    return {
        "id": (result_json or {}).get("id", f"chatcmpl-{task_id}"),
        "object": "chat.completion.chunk",
        "created": (result_json or {}).get("created", int(time.time())),
        "model": (result_json or {}).get("model", model),
        "choices": [{"index": 0, "delta": delta, "finish_reason": fine}],
    }

def _stream_direct(urls, stream_data, model):
    """Streaming diretto con fallback: prova gli URL in ordine; su errore di
    rete passa al successivo. Una risposta HTTP (anche di errore) viene
    restituita com'è (niente fallback: l'endpoint ha risposto)."""
    last_error = None
    for base in (urls if isinstance(urls, (list, tuple)) else [urls]):
        try:
            return requests.post(f"{base}/v1/chat/completions", json=stream_data,
                                 stream=True, timeout=_inference_timeout(model))
        except requests.RequestException as e:
            last_error = e
            continue
    if last_error is not None:
        raise last_error
    raise ValueError("nessun endpoint di inferenza diretto disponibile")
# ── la modalita' think, e cosa succede quando non e' richiesta ────────────────

def _requested_thinking(data: dict, messages: list) -> bool:
    """Legge la richiesta di reasoning ESPLICITA del client (flag JSON `think`
    oppure direttive /think e /no_think nell'ultimo messaggio utente).

    Ritorna None quando il client non ha espresso alcuna preferenza: in quel
    caso la decisione spetta al control-plane (vedi _decide_thinking), non al
    default del backend. Distinguere "non richiesto" da "richiesto False" e'
    essenziale: solo cosi' il CP puo' imporre reasoning OFF quando servono i
    tool senza sovrascrivere una scelta esplicita dell'utente."""
    requested = data.get("think")
    if isinstance(requested, bool):
        return requested
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if "/no_think" in content.lower():
                return False
            if "/think" in content.lower():
                return True
        break
    return None

def _decide_thinking(model: str, data: dict, messages: list, tools: list) -> bool:
    """Decisione UNICA del control-plane su reasoning on/off per questa richiesta.

    Principio: il CP e' l'unico a decidere se il modello deve ragionare, se puo'
    chiamare tool e quali skill esporre. Il backend (Ollama/nodo) non deve mai
    prendere questa decisione da solo.

    Regole, in ordine di priorita':
      1. Se ci sono tool disponibili, il reasoning va SPENTO. Qwen3 (e i modelli
         reasoning in generale) con thinking attivo tende a rispondere a memoria
         invece di emettere tool_calls: reasoning e tool-calling sono di fatto
         mutuamente esclusivi. Se l'utente vuole una ricerca, deve vincere il
         tool-calling.
      2. Altrimenti vale la richiesta esplicita del client (/think, /no_think,
         flag JSON `think`).
      3. Altrimenti reasoning OFF: default prudente e deterministico, coerente
         con "il CP decide", non con il default implicito del backend.
    """
    if tools:
        return False
    explicit = _requested_thinking(data, messages)
    if explicit is not None:
        return explicit
    return False

def _deadline_exceeded(task, task_id, deadline):
    """Interrompe la catena di fallback dicendolo, invece di bruciare minuti.

    HTTP 504: il lavoro non e' stato fatto e non e' colpa del client. Un 200 con
    un corpo d'errore, come accadeva prima, faceva sembrare riuscito un
    fallimento (verificato in sessione di test).
    """
    reason = (f"budget di {deadline.total_s}s esaurito dopo {deadline.elapsed():.0f}s "
              f"senza un esito: agli stadi restanti non resta tempo per un tentativo utile")
    task["status"] = "failed"
    task["error"] = reason
    db.update_task(task_id, "failed", error=reason)
    push_log('inter_node_message', f'task {task_id} interrotto per budget di tempo',
             reason, source='control-plane', target='webui', status='failed')
    return jsonify({"error": {"message": reason, "type": "deadline_exceeded"}}), 504
# ── i tool che il CLIENT esegue ───────────────────────────────────────────────

def _tool_calls_passthrough(result_json) -> list:
    """I tool_calls che una risposta porta al client (lista vuota se non ce ne sono)."""
    if not isinstance(result_json, dict):
        return []
    scelte = result_json.get("choices") or []
    if not scelte or not isinstance(scelte[0], dict):
        return []
    calls = (scelte[0].get("message") or {}).get("tool_calls") or []
    # `or []` copre None e lista vuota, ma non un valore truthy e non iterabile:
    # un `tool_calls: 7` in una risposta malformata farebbe TypeError qui dentro,
    # e da questa funzione un TypeError diventa un 500 su /v1/chat/completions.
    if not isinstance(calls, (list, tuple)):
        return []
    return [tc for tc in calls if isinstance(tc, dict)]

def _tool_del_client(tool_name: str, client_names) -> bool:
    """True se il tool è del CLIENT che l'ha offerto: lo esegue lui, non noi.

    Perché esiste (2026-09-23): Open WebUI 0.11 offre al modello un tool nativo
    `generate_image` che chiama la SUA rotta immagini -> gateway -> ComfyUI. Il
    tool loop del CP però lo eseguiva da sé, e l'unica risposta che poteva dargli
    era "non gestito da nessun connector attivo": la chiamata moriva lì, l'immagine
    non arrivava mai a ComfyUI, e il modello — ricevuto un fallimento — raccontava
    di aver fatto ("File inviato nel canale privato", con nessun file da nessuna
    parte). La regola è quindi esplicita e stretta: **si esegue solo ciò che è
    nostro** (nativi + connettori), e un tool che il client ha offerto e noi non
    abbiamo torna a lui, che sa eseguirlo.
    """
    nome = str(tool_name or "").strip()
    # `_handlers_nativi` e' l'elenco dei tool nativi, non un semplice indicatore:
    # la funzione viene eseguita anche in isolamento dai test, con uno scope che
    # contiene i suoi nomi e nient'altro. Chiamarla attraverso `catalogo_nativi()`
    # aggiungeva un nome che in quello scope non esiste.
    if not nome or nome in (_handlers_nativi() if _handlers_nativi else ()):
        return False
    return nome in {str(nome_cliente or "") for nome_cliente in (client_names or [])}

def _risposta_solo_tool_del_client(resp, tool_calls):
    """La risposta del modello con i SOLI tool che deve eseguire il client.

    Perché si filtrano gli altri: se la stessa risposta contenesse anche un tool
    nostro, i suoi risultati non sarebbero consegnabili al client (il CP non tiene
    stato fra una richiesta e l'altra: li perderebbe). Il modello lo richiederà al
    giro seguente, con in mano il risultato del tool del client — cioè quello che
    l'utente sta aspettando.
    """
    risposta = json.loads(json.dumps(resp, ensure_ascii=False))
    scelte = risposta.get("choices") or [{}]
    messaggio = scelte[0].setdefault("message", {})
    messaggio["tool_calls"] = tool_calls
    if messaggio.get("content") is None:
        messaggio["content"] = ""
    scelte[0]["finish_reason"] = "tool_calls"
    return risposta

# ── chi era l'ultima persona, e se la persona e' attiva ───────────────────

def _last_user_text(messages) -> str:
    """Testo dell'ultimo messaggio utente (le parti multimodali vengono unite)."""
    for message in reversed(list(messages or [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(str(part.get("text", "")) for part in content
                            if isinstance(part, dict) and part.get("type") == "text")
        return str(content or "")
    return ""


def _persona():
    """L'archivio della persona di ADESSO.

    Non un import per nome: `cp/persona.py` ricarica l'archivio quando il nome
    o il file della persona cambiano dalla tab Setup, e un binding tenuto qui
    continuerebbe a usare la persona precedente.
    """
    return _persona_modulo.persona()


def _with_persona(messages, user_text: str = "", surface: str | None = None) -> list:
    """Messaggi con il blocco di identità (e il contesto del mezzo) in testa.

    Se il client ha già un system message il blocco viene APPESO a quello invece
    di sostituirlo: il prompt dell'utente resta suo, l'identità è un'aggiunta.
    """
    blocco = _persona().system_block(user_text, surface=surface)
    out = [dict(m) if isinstance(m, dict) else m for m in (messages or [])]
    for index, message in enumerate(out):
        if (isinstance(message, dict) and message.get("role") == "system"
                and isinstance(message.get("content"), str)):
            out[index] = {**message, "content": message["content"].rstrip() + "\n\n" + blocco}
            return out
    return [{"role": "system", "content": blocco}] + out

# ── il catalogo dei tool che questa superficie puo' vedere ────────────────

def _catalogo_nativi(superficie: str = "") -> list:
    """Il catalogo dei tool nativi che QUESTA superficie può vedere.

    `workbench` (la console usata come banco di lavoro) non riceve i tool
    dell'identità: offrirli invita il modello a chiedere chi è, e la risposta
    arriva con il carattere delle stanze proprio dove non deve. Misurato il
    2026-09-23, la prima prova di `workbench`: "chi sei?" → `tool_call:
    persona_get` → "Sono Aurora, un'IA che tiene compagnia a una cerchia
    ristretta…". La regola (quali tool, e perché) sta in `shared/persona.py`.
    """
    nascosti = identity_tools_hidden(superficie)
    if not nascosti:
        return list(BUILTIN_TOOLS)
    return [tool for tool in BUILTIN_TOOLS
            if tool.get("function", {}).get("name") not in nascosti]

# ── la compressione del prompt e il ripiego su OmniRoute ──────────────────

def _try_omniroute_fallback(data: dict, timeout: int = 60):
    """Ultimo livello di fallback: inoltra la richiesta chat/completions cosi'
    com'e' a OmniRoute (gateway verso 278+ provider esterni, molti free-tier),
    chiamato SOLO quando mesh e federazione hanno gia' fallito entrambe.
    OMNIROUTE_API_KEY e' opzionale: l'immagine ufficiale risponde gia' con
    provider free-tier di default senza alcuna configurazione — la chiave
    va aggiunta solo se/quando l'utente collega provider propri dalla
    dashboard OmniRoute. OMNIROUTE_ENABLED=false disattiva del tutto questo
    livello. Ritorna None su qualunque errore, cosi' il chiamante puo'
    proseguire con l'ultimo fallback locale (ollama diretto) invariato."""
    if not OMNIROUTE_ENABLED:
        return None
    payload = {**data, "model": data.get("model") or OMNIROUTE_MODEL, "stream": False}
    headers = {"Authorization": f"Bearer {OMNIROUTE_API_KEY}"} if OMNIROUTE_API_KEY else {}
    if PROMPT_COMPRESSION_ENABLED:
        headers["x-omniroute-compression"] = PROMPT_COMPRESSION_MODE
    try:
        r = requests.post(
            f"{OMNIROUTE_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        r.raise_for_status()
        result = r.json()
        if isinstance(result, dict) and not result.get("error"):
            return result
    except Exception as e:
        push_log('inter_node_message', 'OmniRoute fallback fallito', str(e), status='warn')
    return None


def _compress_prompt_via_omniroute(text: str) -> str:
    """Comprime un prompt lungo destinato a un nodo della mesh LOCALE (non
    OmniRoute) usando l'engine Caveman reale di OmniRoute (POST
    /api/compression/preview), invece di reimplementarne le regole a mano.
    Chiamata solo se PROMPT_COMPRESSION_ENABLED e il testo supera
    PROMPT_COMPRESSION_MIN_CHARS. Fail-open: qualunque errore (OmniRoute giu',
    endpoint non disponibile, risposta inattesa) ritorna il testo originale
    invariato, mai un'eccezione verso il chiamante."""
    if not (PROMPT_COMPRESSION_ENABLED and OMNIROUTE_ENABLED):
        return text
    if len(text) < PROMPT_COMPRESSION_MIN_CHARS:
        return text
    try:
        r = requests.post(
            f"{OMNIROUTE_URL}/api/compression/preview",
            json={"messages": [{"role": "user", "content": text}], "mode": PROMPT_COMPRESSION_MODE},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()
        compressed = result.get("compressed", "")
        # La preview include il prefisso "user: " del ruolo — lo toglie prima
        # di riusare il testo come prompt vero e proprio verso il nodo.
        if compressed.startswith("user: "):
            compressed = compressed[len("user: "):]
        if compressed and result.get("savingsPct", 0) > 0:
            push_log('system', 'Prompt compresso (Caveman)',
                     f'{result.get("originalTokens")}->{result.get("compressedTokens")} token '
                     f'({result.get("savingsPct")}% risparmio)', status='info')
            return compressed
    except Exception as e:
        push_log('inter_node_message', 'Compressione prompt fallita, invio originale', str(e), status='warn')
    return text

# ── chiudere un task: la risposta che il modello non da' ──────────────────

def _finalize_task(task, task_id, node_id, model, prompt, result_json):
    if _is_error_payload(result_json):
        # Un errore NON e' un completamento: niente memoria, niente log di
        # successo, stato failed. Prima finiva come "done" con HTTP 200, quindi
        # un fallimento era indistinguibile da un successo senza leggere il
        # corpo (osservato in sessione di test: due timeout chiusi come done).
        detail = str(result_json.get("error"))[:300]
        task["status"] = "failed"
        task["error"] = detail
        task["result"] = result_json
        task["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.update_task(task_id, "failed", error=detail)
        push_log('inter_node_message', f'task {task_id} FAILED su {node_id[:12]}',
                 detail, source=node_id[:12], target='webui', status='failed')
        return
    # Normalizza PRIMA di leggere e registrare: i chiamanti fanno
    # `jsonify(result_json)` subito dopo questa funzione, quindi cio' che
    # sistemiamo qui e' anche cio' che riceve il client. Copre in un punto solo
    # tutti i percorsi non-stream (nodo, federazione, omniroute, ollama diretto).
    if isinstance(result_json, dict):
        _normalize_assistant_message(result_json, f"task {task_id}")
    try:
        reply_text = result_json["choices"][0]["message"]["content"]
    except Exception:
        reply_text = json.dumps(result_json)[:300]
    # Quando il modello chiede un tool, `content` è `null` — è lo standard OpenAI,
    # non un modello rotto. Il `try` qui sopra non lo intercetta, perché
    # `["content"]` su una chiave presente che vale None non solleva nulla: il
    # None arriva fino a `reply_text[:500]` e fa TypeError, cioè un 500 su
    # /v1/chat/completions ogni volta che il modello usa un tool. Il testo in quel
    # caso è il nome del tool: è quello che finisce in memoria e nei log, e
    # vuotolo lascerebbe "webui_response" senza contenuto.
    if reply_text is None:
        chiamate = result_json.get("choices", [{}])[0].get("message", {}).get(
            "tool_calls") or []
        reply_text = ", ".join(
            str((c.get("function") or {}).get("name") or c.get("name") or "?")
            for c in chiamate) or "(nessun testo: il modello ha chiesto un tool)"
    task["status"]       = "done"
    task["result"]       = result_json
    task["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.update_task(task_id, "done", result=json.dumps(result_json))
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _memory_append({"ts": ts_now, "type": "webui_prompt", "content": prompt,
                    "model": model, "task_id": task_id, "node_id": node_id, "source": "webui",
                    "status": "active", "priority": 2})
    _memory_append({"ts": ts_now, "type": "webui_response", "content": reply_text[:500],
                    "model": model, "task_id": task_id, "node_id": node_id, "source": "webui",
                    "status": "active", "priority": 2})
    push_log('inter_node_message', f'task {task_id} done', reply_text[:120],
             source=node_id[:12], target='webui', status='success')
    _notify_bridge("task", {"from": node_id[:12], "to": "cp", "type": "task", "label": f"reply: {reply_text[:40]}"})
    _notify_bridge("memory_sync", {"from": node_id[:12], "to": "cp", "entries": 2, "label": "conversation saved"})

# ── il backend diretto, quando non c'e' un nodo ───────────────────────────

def _native_direct_enabled(model):
    # Mixed endpoint lists use the common OpenAI protocol, not Ollama /api/chat.
    return (INFERENCE_BACKEND.strip().lower() == "ollama"
            and len(_inference_urls()) == 1
            and _use_native_chat_fallback(model))

# ── il ciclo dei tool: il modello chiede, noi eseguiamo ─────────────────

def _run_tool_loop(data: dict, ollama_base: str, max_iterations: int = 5, sign: bool = False,
                   node_id: str = "", builtin_tools=None) -> dict:
    messages       = list(data.get("messages", []))
    model          = data.get("model", DEFAULT_MODEL)
    tools_disabled = bool(data.get("_hyperspace_tools_off"))
    backend_data   = {k: v for k, v in data.items()
                      if k not in ("_hyperspace_tools_off", "_hyperspace_surface")}
    supports_tools = _model_supports_tools(model) and not tools_disabled
    push_log('system', f'tool_loop: model={model} tools={supports_tools} signed={sign}', status='info')

    if not supports_tools:
        payload = {**backend_data, "messages": messages, "stream": False}
        payload.pop("tools", None)
        try:
            return _call_ollama(ollama_base, payload, sign=sign, node_id=node_id)
        except NodeBusyError:
            raise
        except Exception as e:
            return {"error": {"message": str(e), "type": "server_error"}}

    client_tools = data.get("tools", [])
    client_names = {t["function"]["name"] for t in client_tools if t.get("function", {}).get("name")}
    offered_builtins = (_catalogo_nativi(str(data.get("_hyperspace_surface", "") or ""))
                        if builtin_tools is None else builtin_tools)
    all_tools    = client_tools + [t for t in offered_builtins if t["function"]["name"] not in client_names]
    last_resp    = None

    def _retry_without_tools(reason):
        push_log('system', f'tool_loop fallback no-tools: {str(reason)[:120]}', status='warn')
        plain = {**backend_data, "messages": messages, "stream": False}
        plain.pop("tools", None)
        try:
            return _call_ollama(ollama_base, plain, sign=sign, node_id=node_id)
        except NodeBusyError:
            raise
        except Exception as e2:
            return {"error": {"message": str(e2), "type": "server_error"}}

    for iteration in range(max_iterations):
        payload = {**backend_data, "messages": messages, "tools": all_tools, "stream": False}
        try:
            resp = _call_ollama(ollama_base, payload, sign=sign, node_id=node_id)
        except NodeBusyError:
            raise
        except ValueError as e:
            if iteration == 0:
                return _retry_without_tools(e)
            return last_resp or {"error": {"message": str(e), "type": "server_error"}}
        except Exception as e:
            return {"error": {"message": str(e), "type": "server_error"}}

        # Un modello non tool-capable non sempre fa fallire la richiesta HTTP
        # (niente ValueError sopra): spesso Ollama risponde 200 con un body
        # JSON {"error": ...} valido, es. "<modello> does not support tools".
        # Stesso fallback del ramo ValueError: ritenta UNA volta senza tools.
        if resp.get("error"):
            if iteration == 0:
                return _retry_without_tools(resp["error"])
            return last_resp or resp

        last_resp = resp
        choice    = resp.get("choices", [{}])[0]
        message   = choice.get("message", {})
        finish    = choice.get("finish_reason", "stop")

        if finish != "tool_calls" or not message.get("tool_calls"):
            return resp

        messages.append(message)
        # Un tool offerto dal client e non nostro lo esegue il client: qui si
        # raccoglie e si torna. Vedi `_tool_del_client` per il perché.
        da_tornare = []
        for tc in message["tool_calls"]:
            tool_id   = tc.get("id", str(uuid.uuid4())[:8])
            tool_name = tc.get("function", {}).get("name", "")
            tool_args = tc.get("function", {}).get("arguments", {})
            if _tool_del_client(tool_name, client_names):
                da_tornare.append(tc)
                continue
            push_log('system', f'tool_call: {tool_name}', detail=f'args={str(tool_args)[:120]}', status='info')
            result = _execute_tool_call(tool_name, tool_args)
            push_log('system', f'tool_result: {tool_name}', detail=f'{result[:120]}', status='success')
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": result})
        if da_tornare:
            nomi = ", ".join(str((tc.get("function") or {}).get("name", "?")) for tc in da_tornare)
            push_log('system', f'tool del client: {nomi}',
                     detail='passthrough: li esegue chi li ha offerti', status='info')
            return _risposta_solo_tool_del_client(resp, da_tornare)

    return last_resp


# ── la rotta ──────────────────────────────────────────────────────────────────
# ── /v1/chat/completions ──────────────────────────────────────────────────────
@_bp.route('/v1/chat/completions', methods=['POST', 'OPTIONS'])
def v1_chat_completions():
    if request.method == 'OPTIONS':
        return '', 204

    data      = request.get_json(force=True, silent=True) or {}
    try:
        data = attach_skills(data, _contesto.forge_read_skill)
    except (ValueError, OSError, TypeError) as error:
        return jsonify({"error": {"message": str(error), "type": "invalid_request_error"}}), 400
    messages  = data.get("messages", [])
    raw_model = data.get("model", _contesto.advanced_config["ollama"]["defaultModel"])

    # Toglie i prefissi cosmetici aggiunti in /v1/models prima di usare il
    # nome per il routing vero — vedi commento su MESH_MODEL_ICON sopra.
    omniroute_direct = (raw_model == OMNIROUTE_MODEL_ID)
    if omniroute_direct:
        model, pinned_node_id = OMNIROUTE_MODEL, None
    else:
        clean_model = raw_model[len(MESH_MODEL_ICON):] if raw_model.startswith(MESH_MODEL_ICON) else raw_model
        model, pinned_node_id = _parse_model_node_ref(clean_model)
    data      = {**data, "model": model}   # a valle il nodo riceve solo il nome modello "pulito"

    # ── IDENTITÀ: un punto solo, prima di tool e thinking ────────────────────
    # Il blocco di identità entra qui e da qui lo ereditano tutti i percorsi
    # (tool loop e streaming, che parte da dict(data)). Il vincolo di disclosure
    # viene deciso sul testo dell'utente e LOGGATO: una decisione che non lascia
    # traccia non è verificabile.
    #
    # La superficie si legge PRIMA della spunta: c'è una superficie che dichiara
    # di non volere l'identità (`workbench`, la console usata come banco di
    # lavoro) e il perché sta in `shared/persona.py`, non qui.
    user_text = _last_user_text(messages)
    superficie = str(data.get("surface", "") or "").strip() \
        or request.headers.get("X-Hyperspace-Surface", "").strip() \
        or "openwebui"
    # La superficie viaggia con la richiesta: la usano l'identità e il catalogo
    # dei tool (i tool dell'identità non si offrono su `workbench`).
    data["_hyperspace_surface"] = superficie
    if _persona_enabled(superficie):
        decisione = should_disclose(user_text)
        messages = _with_persona(messages, user_text, surface=superficie)
        data = {**data, "messages": messages}
        if decisione.required:
            push_log('system', 'Persona: disclosure richiesta',
                     detail=f'regola={decisione.rule} match="{decisione.matched[:60]}"',
                     status='info')

    # ── DECISIONE DEL CONTROL-PLANE: tool e reasoning ────────────────────────
    # Il CP è l'unico a decidere se questa richiesta può chiamare tool e se il
    # modello deve ragionare. Il backend (nodo/Ollama) non deve mai prendere
    # questa decisione da solo: senza un `think` esplicito, Qwen3 attiva il
    # reasoning di default e — con reasoning attivo — NON emette tool_calls,
    # rispondendo a memoria. È il bug per cui "cerca su internet X" non
    # attivava mai web_search.
    #
    # 1. Tool: se il modello è tool-capable, il CP inietta i BUILTIN_TOOLS
    #    (web_search, omega_*, get_mesh_status + connettori) accanto a quelli
    #    eventualmente già passati dal client, senza duplicarli — a meno che il
    #    client abbia chiesto esplicitamente di no (`X-Hyperspace-Tools: off`).
    # 2. Reasoning: deciso da _decide_thinking() — OFF quando ci sono tool
    #    (reasoning e tool-calling sono mutuamente esclusivi), altrimenti
    #    rispetta la richiesta esplicita del client, altrimenti OFF.
    tools_available = []
    client_had_tools = bool(data.get("tools"))
    tools_off = _tools_requested_off(request.headers.get("X-Hyperspace-Tools", ""))
    data["_hyperspace_tools_off"] = tools_off
    if _model_supports_tools(model):
        client_tools = data.get("tools") or []
        client_names = {t.get("function", {}).get("name") for t in client_tools}
        aggiunti = [] if tools_off else [
            tool for tool in _catalogo_nativi(superficie)
            if tool["function"]["name"] not in client_names
        ]
        tools_available = client_tools + aggiunti
        data["tools"] = tools_available
    else:
        data.pop("tools", None)
        # Il client aveva chiesto dei tool e li stiamo togliendo: senza questo
        # avviso un modello nuovo non tool-capable fallirebbe in silenzio. Se a
        # toglierli e' stato il client stesso, invece, non c'e' niente da dire.
        if client_had_tools and not tools_off:
            _warn_tools_stripped(model)

    data["think"] = _decide_thinking(model, data, messages, tools_available)
    push_log('system',
             f'CP decision: model={model} tools={len(tools_available)} think={data["think"]}',
             status='info')

    stream    = data.get("stream", False)

    task_id   = str(uuid.uuid4())[:8]

    prompt = ""
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            c = messages[i].get("content", "")
            prompt = c if isinstance(c, str) else str(c)
            last_user_idx = i
            break
    if not prompt:
        prompt = json.dumps(messages)[:200]

    # Comprime il prompt (Caveman via OmniRoute) solo per la mesh locale —
    # la selezione esplicita di 🌐 OmniRoute riceve compressione via header
    # sulla stessa chiamata, non serve un giro doppio. Salta i contenuti
    # multimodali (liste, es. testo+immagine): comprimerli come stringa
    # romperebbe la struttura del messaggio.
    if not omniroute_direct and last_user_idx is not None:
        raw_content = messages[last_user_idx].get("content")
        if isinstance(raw_content, str):
            compressed = _compress_prompt_via_omniroute(raw_content)
            if compressed != raw_content:
                messages = list(messages)
                messages[last_user_idx] = {**messages[last_user_idx], "content": compressed}
                data = {**data, "messages": messages}

    task = {
        "id": task_id, "status": "created", "node": None,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payload": {"prompt": prompt, "model": model, "source": "webui"},
    }
    _contesto.tasks[task_id] = task
    db.insert_task(task)
    push_log('system', f'WebUI task: {task_id}', detail=f'model={model} stream={stream} prompt={prompt[:80]}')

    active      = [n for n in _node_list() if n.get("status") == "active"]
    ollama_base = _contesto.advanced_config["ollama"]["url"].rstrip("/")

    # ── STREAM ────────────────────────────────────────────────────────────────

    # NOTA: lo streaming oggi resta locale (nodo o ollama-direct). La
    # federazione verso un altro CP entra in gioco solo nel percorso
    # non-stream — proxare uno stream SSE cross-CP e' un passo successivo.
    #
    # Prova in sequenza i migliori nodi candidati (per score): se un nodo
    # risponde 503 node_busy_timeout PRIMA di iniziare a inviare byte, il
    # generatore passa al successivo. Una volta che il primo chunk reale è
    # stato inoltrato al client non si cambia più nodo (l'header 200 è già
    # partito), quindi eventuali errori a metà stream vengono solo segnalati
    # inline, non ritentati su un altro nodo.
    if stream:
        candidates = [] if omniroute_direct else _rank_candidate_nodes(active, pinned_node_id, model=model)
        stream_data = dict(data)
        if _model_supports_tools(model):
            ct = stream_data.get("tools", [])
            cn = {t["function"]["name"] for t in ct if t.get("function", {}).get("name")}
            stream_data["tools"] = ct + [t for t in _catalogo_nativi(superficie)
                                         if t["function"]["name"] not in cn]
        else:
            stream_data.pop("tools", None)

        def _stream_gen():
            if omniroute_direct:
                task["node"] = "omniroute"
                db.update_task(task_id, "assigned", node_id="omniroute", endpoint=OMNIROUTE_URL)
                omni_headers = {"Authorization": f"Bearer {OMNIROUTE_API_KEY}"} if OMNIROUTE_API_KEY else {}
                if PROMPT_COMPRESSION_ENABLED:
                    omni_headers["x-omniroute-compression"] = PROMPT_COMPRESSION_MODE
                try:
                    req = requests.post(f"{OMNIROUTE_URL}/v1/chat/completions",
                                         json=stream_data, headers=omni_headers, stream=True,
                                         timeout=_inference_timeout(stream_data.get("model", "")))
                    with req as resp:
                        for chunk in resp.iter_content(chunk_size=None):
                            if chunk:
                                yield chunk
                    task["status"]       = "done"
                    task["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    db.update_task(task_id, "done")
                    push_log('inter_node_message', f'stream {task_id} done',
                             source='omniroute', target='webui', status='success')
                except Exception as e:
                    yield ('data: ' + json.dumps({'error': str(e)}, ensure_ascii=False) + '\n\n').encode()
                    task["status"] = "failed"
                    db.update_task(task_id, "failed", error=str(e))
                return

            # Il backend Ollama puo' emettere tool_calls nello stream, ma il
            # dispatcher non puo' eseguire il tool dopo aver gia' inoltrato i
            # chunk al client. Per i modelli tool-capable usa quindi il loop
            # non-streaming interno e riconfeziona solo il risultato finale
            # come SSE: web_search viene realmente eseguito anche da WebUI.
            # La scelta e' pattern-driven (_model_supports_tools), non legata a
            # un singolo modello: i distillati in arrivo (qwen3.8, deepseek4.1,
            # ...) si coprono aggiornando _TOOL_CAPABLE_PATTERNS o la env
            # TOOL_CAPABLE_MODELS, senza toccare questo ramo.
            if _model_supports_tools(model):
                for candidate in candidates:
                    node_id_c = candidate.get("node_id", "cp")
                    endpoint_c = _best_endpoint(candidate)
                    _record_routing_pick(node_id_c)
                    try:
                        result_json = _run_tool_loop(
                            stream_data, endpoint_c, sign=True, node_id=node_id_c
                        )
                    except NodeBusyError:
                        continue
                    except Exception:
                        continue
                    if isinstance(result_json, dict) and result_json.get("error"):
                        continue
                    # Il chunk finale lo costruisce `_chunk_finale`: se la risposta
                    # porta un tool del client (passthrough) viaggia con lei, o un
                    # client in streaming non lo vedrebbe mai.
                    chunk = _chunk_finale(result_json, stream_data.get("model", model), task_id)
                    task["node"] = node_id_c
                    db.update_task(task_id, "assigned", node_id=node_id_c, endpoint=endpoint_c)
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    task["status"] = "done"
                    task["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    db.update_task(task_id, "done")
                    return

            served = False
            for candidate in candidates:
                node_id_c  = candidate.get("node_id", "cp")
                endpoint_c = _best_endpoint(candidate)
                _record_routing_pick(node_id_c)
                try:
                    body = json.dumps(stream_data, sort_keys=True).encode()
                    headers = make_request_headers(_contesto.cp_identity.get('node_id', ''),
                                        _contesto.cp_identity.get('public_key', ''),
                                        _contesto.cp_identity.get('_private_key'), body)
                    headers["Content-Type"] = "application/json"
                    req = requests.post(f"{endpoint_c}/v1/chat/completions",
                                        data=body, headers=headers, stream=True,
                                        timeout=_inference_timeout(stream_data.get("model", "")))
                except Exception:
                    continue  # nodo irraggiungibile, prova il prossimo candidato

                if req.status_code == 503:
                    try:
                        if req.json().get("error", {}).get("type") == "node_busy_timeout":
                            push_log('inter_node_message',
                                     f'stream {task_id}: {node_id_c[:12]} occupato, provo il prossimo',
                                     status='warn')
                            continue
                    except Exception:
                        pass

                # Da qui in poi ci impegniamo con questo nodo: nessun altro retry.
                task["node"] = node_id_c
                db.update_task(task_id, "assigned", node_id=node_id_c, endpoint=endpoint_c)
                try:
                    with req as resp:
                        for chunk in resp.iter_content(chunk_size=None):
                            if chunk:
                                yield chunk
                    task["status"]       = "done"
                    task["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    db.update_task(task_id, "done")
                    push_log('inter_node_message', f'stream {task_id} done',
                             source=node_id_c[:12], target='webui', status='success')
                except Exception as e:
                    yield ('data: ' + json.dumps({'error': str(e)}, ensure_ascii=False) + '\n\n').encode()
                    task["status"] = "failed"
                    db.update_task(task_id, "failed", error=str(e))
                served = True
                break

            if served:
                return

            # Nessun nodo locale utilizzabile (assente o tutti occupati/irraggiungibili).
            task["node"] = "ollama-direct"
            db.update_task(task_id, "assigned", node_id="ollama-direct", endpoint=ollama_base)
            try:
                if _native_direct_enabled(model):
                    # Fallback nativo (/api/chat) per i modelli reasoning
                    # (elenco in _NATIVE_CHAT_FALLBACK_PATTERNS, estendibile via
                    # env), usato SOLO quando non c'e' nessun nodo mesh
                    # disponibile. Anche il body nativo accetta i tool nello
                    # stesso formato funzione
                    # dell'API OpenAI, quindi li inoltriamo: senza di essi il
                    # modello non potrebbe mai chiamare web_search in questo
                    # percorso (il vecchio ramo li ometteva del tutto).
                    native = {"model": model, "messages": stream_data.get("messages", []),
                              "stream": False, "think": bool(stream_data.get("think", False))}
                    if stream_data.get("tools"):
                        native["tools"] = stream_data["tools"]
                    native_resp = _local_model_post(f"{_inference_urls()[0]}/api/chat", json=native,
                                                timeout=_inference_timeout(model))
                    native_resp.raise_for_status()
                    native_message = (native_resp.json().get("message") or {})
                    if native_message.get("tool_calls"):
                        # Il modello vuole chiamare un tool. Il percorso nativo
                        # accetta i tool in INGRESSO (stesso formato OpenAI), ma
                        # NON regge il formato OpenAI del secondo giro: il tool
                        # loop rimanda `function.arguments` come STRINGA JSON e
                        # /api/chat risponde 400 ("Value looks like object, but
                        # can't find closing '}' symbol"). Era la causa del bug
                        # "risposta vuota dopo web_search". Per ESEGUIRE davvero
                        # i tool riusiamo quindi il loop del CP, che qui parla
                        # con Ollama diretto (sign=False: nessun nodo da
                        # autenticare). Cosi' anche questo fallback non
                        # restituisce mai un content vuoto dopo una tool call.
                        # Verificato su Ollama 0.34.2.
                        result_json = _run_tool_loop(stream_data, ollama_base)
                        # Anche questo chunk può portare un tool del client: se il
                        # loop l'ha passato indietro, il client deve vederlo.
                        direct_chunk = _chunk_finale(result_json, model, task_id)
                    else:
                        direct_chunk = {
                            "id": f"chatcmpl-{task_id}", "object": "chat.completion.chunk",
                            "created": int(time.time()), "model": model,
                            "choices": [{"index": 0, "delta": {
                                "role": "assistant",
                                "content": native_message.get("content", "")},
                                "finish_reason": "stop"}],
                        }
                    yield f"data: {json.dumps(direct_chunk, ensure_ascii=False)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                else:
                    req = _stream_direct(_inference_urls(), stream_data, model)
                    with req as resp:
                        resp.raise_for_status()
                        for chunk in resp.iter_content(chunk_size=None):
                            if chunk:
                                yield chunk
                task["status"]       = "done"
                task["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                db.update_task(task_id, "done")
                push_log('inter_node_message', f'stream {task_id} done',
                         source='ollama-direct', target='webui', status='success')
            except Exception as e:
                yield ('data: ' + json.dumps({'error': str(e)}, ensure_ascii=False) + '\n\n').encode()
                task["status"] = "failed"
                db.update_task(task_id, "failed", error=str(e))

        return Response(stream_with_context(_stream_gen()), headers=_sse_headers())

    # ── NON-STREAM ────────────────────────────────────────────────────────────
    # Budget TOTALE della richiesta, condiviso da tutta la catena di fallback.
    # Prima ogni stadio aveva il suo timeout e la catena li SOMMAVA: nodo 180s +
    # OmniRoute + ollama-direct 180s = oltre tre minuti prima di ammettere il
    # fallimento. Ora si smette appena il tempo residuo non basta piu' per un
    # tentativo sensato, e lo si dice esplicitamente.
    deadline = RequestDeadline()
    # Scelta esplicita di 🌐 OmniRoute dal menu: salta mesh e federazione,
    # l'utente ha gia' deciso di voler uscire dalla mesh locale.
    if omniroute_direct:
        omni_result = _try_omniroute_fallback(data)
        if omni_result:
            _finalize_task(task, task_id, "omniroute", model, prompt, omni_result)
            push_log('inter_node_message', f'task {task_id} -> omniroute (selezione esplicita)', status='success')
            return _respond_result(omni_result)
        task["status"] = "failed"
        task["error"]  = "OmniRoute non raggiungibile o nessun provider disponibile"
        db.update_task(task_id, "failed", error=task["error"])
        return jsonify({"error": {"message": task["error"], "type": "server_error"}}), 502

    candidates = _rank_candidate_nodes(active, pinned_node_id, model=model)
    for candidate in candidates:
        node_id  = candidate.get("node_id", "cp")
        endpoint = _best_endpoint(candidate)
        _record_routing_pick(node_id)
        task["node"] = node_id
        db.update_task(task_id, "assigned", node_id=node_id, endpoint=endpoint)
        push_log('inter_node_message', f'task {task_id} -> {node_id[:12]}',
                 f'model={model}', source='webui', target=node_id[:12], status='pending')
        try:
            result_json = _run_tool_loop(data, endpoint, sign=True, node_id=node_id)
        except NodeBusyError:
            push_log('inter_node_message', f'task {task_id}: {node_id[:12]} occupato, provo il prossimo',
                     status='warn', target=node_id[:12])
            continue
        except Exception as e:
            push_log('inter_node_message', f'task {task_id} fallback ollama', str(e), status='warn')
            break
        if isinstance(result_json, dict) and result_json.get("error"):
            push_log('inter_node_message', f'task {task_id} nodo {node_id[:12]} errore',
                     str(result_json.get("error"))[:160], status='warn')
            continue
        _finalize_task(task, task_id, node_id, model, prompt, result_json)
        return _respond_result(result_json)

    # Local-first even while workers are still registering after startup.
    # A remote fallback must not delay an available local model by a minute.
    if not deadline.allows():
        return _deadline_exceeded(task, task_id, deadline)
    task["node"] = "ollama-direct"
    db.update_task(task_id, "assigned", node_id="ollama-direct", endpoint=ollama_base)
    try:
        direct_result = _run_tool_loop(data, _inference_urls(), sign=False)
    except Exception as e:
        direct_result = {"error": {"message": str(e), "type": "server_error"}}
    if not _is_error_payload(direct_result):
        _finalize_task(task, task_id, "ollama-direct", model, prompt, direct_result)
        return _respond_result(direct_result)

    # Local inference failed: federation may still serve the request.
    if not deadline.allows():
        return _deadline_exceeded(task, task_id, deadline)
    fed_result, fed_peer = _try_federated_execution(prompt, model)
    if fed_result:
        node_label = f"federated:{fed_peer['peer_id'][:12]}"
        inner_result = fed_result.get("result", fed_result)
        _finalize_task(task, task_id, node_label, model, prompt, inner_result)
        push_log('inter_node_message', f'task {task_id} federato -> {fed_peer.get("label") or node_label}',
                 status='success')
        return _respond_result(inner_result)

    # Mesh, direct inference and federation failed: try the external provider.
    if not deadline.allows():
        return _deadline_exceeded(task, task_id, deadline)
    omni_result = _try_omniroute_fallback(data)
    if omni_result:
        _finalize_task(task, task_id, "omniroute", model, prompt, omni_result)
        push_log('inter_node_message', f'task {task_id} -> omniroute (fallback esterno)', status='success')
        return _respond_result(omni_result)

    _finalize_task(task, task_id, "ollama-direct", model, prompt, direct_result)
    return _respond_result(direct_result)

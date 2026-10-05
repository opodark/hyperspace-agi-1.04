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
import time

import requests
from flask import jsonify

import shared.db as db
from cp.assistant import _assistant_text
from cp.budget import _inference_timeout
from cp.log import push_log
from cp.config import OLLAMA_URL

# I tool nativi disponibili: il modello li vede nel catalogo, e un tool che non ha
# un handler non deve essere proposto. Resta in main.py perche' gli handler
# chiamano `connector_manager`, che è un oggetto di boot.
_handlers_nativi = None


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

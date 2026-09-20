# SPDX-License-Identifier: Apache-2.0
"""Traduzione OpenAI-compatibile <-> nativa Ollama per il percorso nodo.

Perché esiste: l'endpoint OpenAI-compatibile di Ollama IGNORA `think`. Il
control-plane decide il reasoning (`_decide_thinking`) e lo mette nel payload: se
il nodo inoltra quel payload al percorso compatibile, con `think=false` il modello
continua a ragionare, consuma il budget di token e la risposta arriva dopo minuti
(è l'origine dei timeout in chat dal vivo). `/api/chat` rispetta `think`, quindi
quando il CP ha deciso "niente reasoning" il nodo parla nativo e ritraduce la
risposta in forma OpenAI.

Forme verificate contro Ollama reale (qwen3.5:4b, 20 settembre 2026):
  - nativo think=false -> `message`: {content, role}      (nessun campo thinking)
  - compat think=false -> `message`: {content, reasoning, role}   la bandiera è ignorata
  - nativo, tool_calls in uscita: [{id, function: {index, name, arguments: {...OGGETTO...}}}]
  - nativo, secondo giro con `arguments` come STRINGA (formato OpenAI) -> HTTP 400
  - nativo, secondo giro NORMALIZZATO (`arguments` oggetto + `tool_name`) -> HTTP 200

Qui dentro c'è solo traduzione pura: nessuna rete, nessuna dipendenza dal nodo,
così i casi sopra si possono fissare in test che girano anche senza Ollama.
"""
from __future__ import annotations

import json
import time

__all__ = ["needs_native_path", "to_native_chat", "to_openai_chat", "map_finish_reason"]

# Parole chiave OpenAI -> opzioni native. `max_tokens` e `max_completion_tokens`
# sono lo stesso parametro con due nomi (il secondo è quello nuovo).
_OPZIONI = (("max_tokens", "num_predict"), ("max_completion_tokens", "num_predict"),
            ("temperature", "temperature"), ("top_p", "top_p"),
            ("stop", "stop"), ("seed", "seed"))

_DONE_REASON = {"stop": "stop", "length": "length", "load": "stop"}


def needs_native_path(payload) -> bool:
    """True se il payload chiede ESPLICITAMENTE di non ragionare.

    Solo `think: false`: è il caso che il percorso compatibile sbaglia. Con
    `think` assente o true il comportamento resta quello di prima — nessuna
    traduzione, nessun rischio di regressione su un percorso già collaudato.
    """
    return isinstance(payload, dict) and payload.get("think") is False


def _arguments_to_object(raw):
    """`arguments` come stringa JSON -> oggetto. Il nativo non accetta il resto."""
    if isinstance(raw, str):
        try:
            analizzato = json.loads(raw or "{}")
        except ValueError:
            return {"_raw": raw}
        return analizzato if isinstance(analizzato, dict) else {"_value": analizzato}
    return raw if isinstance(raw, dict) else {}


def _messages_to_native(messages):
    """Messaggi in formato OpenAI -> messaggi nativi.

    Due differenze che rompono il nativo (verificate, HTTP 400): nel formato
    OpenAI `function.arguments` è una STRINGA JSON e il messaggio di risposta del
    tool porta `tool_call_id`, mentre il nativo vuole un OGGETTO e `tool_name`.
    La mappa id->nome si costruisce scorrendo i messaggi, perché il nome del tool
    serve DOPO, nel messaggio di risposta.
    """
    fuori = []
    nomi: dict = {}
    for messaggio in messages or []:
        if not isinstance(messaggio, dict):
            continue
        nuovo = {"role": messaggio.get("role", "user")}
        if messaggio.get("content") is not None:
            nuovo["content"] = messaggio["content"]
        for chiamata in messaggio.get("tool_calls") or []:
            if not isinstance(chiamata, dict):
                continue
            funzione = chiamata.get("function") or {}
            nome = funzione.get("name") or ""
            nuovo.setdefault("tool_calls", []).append({
                "type": "function",
                "function": {"name": nome,
                             "arguments": _arguments_to_object(funzione.get("arguments"))},
            })
            if chiamata.get("id"):
                nomi[chiamata["id"]] = nome
        if nuovo["role"] == "tool":
            riferimento = messaggio.get("tool_call_id") or ""
            nuovo["tool_name"] = (messaggio.get("tool_name")
                                  or nomi.get(riferimento) or "")
        fuori.append(nuovo)
    return fuori


def to_native_chat(payload: dict) -> dict:
    """Payload OpenAI non-stream -> payload `/api/chat`.

    Un dict `options` già in formato nativo viene MERGIATO, non ignorato: è il
    modo con cui il control-plane chiede una finestra di contesto esplicita
    (`num_ctx`) senza inventare una traduzione per ogni opzione di Ollama.
    """
    payload = payload if isinstance(payload, dict) else {}
    opzioni = {}
    for chiave_openai, chiave_nativa in _OPZIONI:
        if payload.get(chiave_openai) is not None:
            opzioni.setdefault(chiave_nativa, payload[chiave_openai])
    if isinstance(payload.get("options"), dict):
        # Le chiavi esplicite vincono su quelle tradotte: chi scrive `options`
        # sta già parlando nativo, e i nomi nativi sono quelli giusti.
        opzioni.update({k: v for k, v in payload["options"].items() if v is not None})
    nativo = {"model": payload.get("model", ""), "stream": False,
              "think": bool(payload.get("think", False)),
              "messages": _messages_to_native(payload.get("messages"))}
    if opzioni:
        nativo["options"] = opzioni
    if payload.get("tools"):
        nativo["tools"] = payload["tools"]
    return nativo


def _tool_calls_to_openai(chiamate):
    """tool_calls nativi -> tool_calls OpenAI (`arguments` come stringa)."""
    fuori = []
    for indice, chiamata in enumerate(chiamate or []):
        if not isinstance(chiamata, dict):
            continue
        funzione = chiamata.get("function") or {}
        argomenti = funzione.get("arguments")
        fuori.append({
            "id": chiamata.get("id") or f"call_{indice}",
            "type": "function",
            "function": {"name": funzione.get("name") or "",
                         "arguments": argomenti if isinstance(argomenti, str)
                         else json.dumps(argomenti or {}, ensure_ascii=False)},
        })
    return fuori


def map_finish_reason(done_reason) -> str:
    """`done_reason` nativo -> `finish_reason` OpenAI."""
    return _DONE_REASON.get(str(done_reason or ""), "stop")


def to_openai_chat(native_response: dict, model: str = "") -> dict:
    """Risposta `/api/chat` -> `chat.completion` OpenAI.

    `finish_reason` è "tool_calls" quando il modello vuole chiamare un tool: è il
    segnale che il tool loop del control-plane usa per continuare, quindi va
    tradotto con precisione e non lasciato al default.
    """
    native_response = native_response if isinstance(native_response, dict) else {}
    messaggio = native_response.get("message") or {}
    chiamate = _tool_calls_to_openai(messaggio.get("tool_calls"))
    messaggio_openai = {"role": "assistant", "content": messaggio.get("content") or ""}
    if chiamate:
        messaggio_openai["tool_calls"] = chiamate
    pensiero = messaggio.get("thinking")
    if pensiero:
        # Presente solo se `think` era true: si conserva, perché è il campo che i
        # client OpenAI usano per mostrare il ragionamento.
        messaggio_openai["reasoning"] = pensiero
    prompt_tokens = int(native_response.get("prompt_eval_count") or 0)
    completion_tokens = int(native_response.get("eval_count") or 0)
    return {
        "id": "chatcmpl-native",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or native_response.get("model", ""),
        "choices": [{
            "index": 0,
            "message": messaggio_openai,
            "finish_reason": "tool_calls" if chiamate else map_finish_reason(
                native_response.get("done_reason")),
        }],
        "usage": {"prompt_tokens": prompt_tokens,
                  "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }

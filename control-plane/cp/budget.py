# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/budget.py
# BUDGET DI TEMPO DI UNA RICHIESTA — timeout e deadline della catena di fallback.
#
# Perche' esiste: con un budget fisso, deepseek-r1:8b con 600 token di risposta
# non concludeva entro i 180s (Read timed out, verificato lato server), e un
# 200 con corpo d'errore faceva sembrare riuscito un fallimento.
#
# Vive qui perche' e' logica, non rotte: i test la estragono e la eseguono da
# sola (tests/test_request_budget.py) senza bisogno di Flask, DB o rete. Il resto
# del budget che NON e' puro resta in main.py: `_deadline_exceeded` scrive sul DB
# e chiama push_log, quindi dipende dal runtime.
#
# `jsonify` viene da flask, non da main.py: e' quello che permette a questo
# modulo di restare indipendente senza import circolari.

import os
import time

from flask import jsonify


INFERENCE_TIMEOUT_S           = int(os.getenv("INFERENCE_TIMEOUT_S", "180"))
INFERENCE_TIMEOUT_REASONING_S = int(os.getenv("INFERENCE_TIMEOUT_REASONING_S", "600"))
FALLBACK_MIN_ATTEMPT_S        = int(os.getenv("FALLBACK_MIN_ATTEMPT_S", "60"))
# Il budget TOTALE deve poter contenere almeno un tentativo lungo piu' un
# ripiego, altrimenti il primo tentativo lo sfora e il messaggio di errore
# diventa incoerente ("budget di 300s esaurito dopo 600s", osservato in test).
# Il controllo della deadline avviene FRA gli stadi, non dentro un tentativo:
# un default piu' corto del timeout reasoning non e' un budget piu' severo, e'
# solo un budget che non puo' essere rispettato.
REQUEST_DEADLINE_S = max(
    int(os.getenv("REQUEST_DEADLINE_S", "0") or 0),
    INFERENCE_TIMEOUT_REASONING_S + FALLBACK_MIN_ATTEMPT_S,
)

# Modelli che ragionano: il testo puo' arrivare dopo molti token di thinking.
# Override da env REASONING_MODELS (lista separata da virgole, "*" = tutti):
# stessa semantica di TOOL_CAPABLE_MODELS, cosi' un modello nuovo non richiede
# una patch. deepseek-v4/glm-5/qwen3.8 sono gli alias che usa ds4.
_REASONING_OVERRIDE = os.getenv("REASONING_MODELS", "")
_REASONING_PATTERNS = ["qwen3", "deepseek-r1", "deepseek-v4", "deepseek-r1-pro",
                       "magistral", "glm-5", "qwen3.8"]

def _is_reasoning_model(model_name: str) -> bool:
    override = _REASONING_OVERRIDE.strip()
    if override == "*":
        return True
    if override:
        return any(p.strip().lower() and p.strip().lower() in str(model_name).lower()
                   for p in override.split(","))
    m = str(model_name or "").lower().split(":")[0]
    return any(p in m for p in _REASONING_PATTERNS)

def _inference_timeout(model_name: str) -> int:
    """Secondi concessi a UN tentativo di inferenza, in base al modello."""
    seconds = INFERENCE_TIMEOUT_REASONING_S if _is_reasoning_model(model_name) \
        else INFERENCE_TIMEOUT_S
    return max(10, int(seconds))

class RequestDeadline:
    """Budget totale condiviso da tutta la catena di fallback di una richiesta.

    Il clock e' iniettabile perche' la logica sia testabile senza attese reali.
    """

    def __init__(self, total_s: int = None, clock=time.time):
        self.clock = clock
        self.total_s = max(10, int(REQUEST_DEADLINE_S if total_s is None else total_s))
        self.started_at = clock()
        self.deadline = self.started_at + self.total_s

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def allows(self, min_s: int = None) -> bool:
        """True se resta abbastanza budget perche' un altro tentativo abbia
        senso: sotto la soglia si fallisce subito, invece di sprecare il tempo
        residuo in un tentativo che non potra' completare."""
        threshold = FALLBACK_MIN_ATTEMPT_S if min_s is None else min_s
        return self.remaining() >= max(1, int(threshold))

    def elapsed(self) -> float:
        return self.clock() - self.started_at

def _is_error_payload(payload) -> bool:
    """True se la risposta e' un errore travestito da risposta.

    `_run_tool_loop` e i fallback restituiscono `{"error": {...}}` invece di
    sollevare un'eccezione: senza questo controllo il task veniva marcato `done`
    e il client riceveva HTTP 200 con un corpo d'errore — un fallimento
    indistinguibile da un successo se non leggendo il corpo (osservato in
    sessione di test: due timeout da 180s chiusi come "done").
    """
    return isinstance(payload, dict) and bool(payload.get("error"))

def _respond_result(result_json, status_error: int = 502):
    """Risposta HTTP coerente col payload: un errore non esce come 200."""
    if _is_error_payload(result_json):
        return jsonify(result_json), status_error
    return jsonify(result_json)

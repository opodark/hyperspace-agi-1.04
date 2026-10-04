# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/assistant.py
# IL TESTO CHE ARRIVA DAI MODELLI: come si legge, come si normalizza.
#
# `_assistant_text` sa dove sta il testo in un chunk SSE e in un JSON OpenAI;
# `_normalize_assistant_message` rimette in forma la risposta e segnala il caso
# che non va dimenticato: con `think=false` alcuni modelli (qwen2, gemma4)
# rimandano TUTTO in `reasoning` e lasciano `content` vuoto — cioe' una risposta
# apparentemente vuota. Il rate-limit serve a non ripetere il warning a ogni
# chunk dello stesso stream.
#
# Non ha stato proprio: i due contatori di rate-limit sono globali di modulo ma
# servono solo qui dentro.

import time

from cp.log import push_log

_REASONING_WARN_AT = 0.0
_REASONING_WARN_EVERY_S = 60


# ── TESTO DEI MESSAGGI ASSISTANT ──────────────────────────────────────────────
# I modelli reasoning (qwen3, deepseek-r1) possono consegnare il testo in
# `reasoning`/`reasoning_content` invece che in `content`. Su Ollama 0.34.2 il
# percorso OpenAI-compatibile popola SEMPRE `reasoning`, anche con think=false:
# se il budget di token finisce mentre il modello sta ancora ragionando, il
# `content` resta VUOTO e il client riceverebbe una risposta vuota senza capire
# perche'. Verificato: max_tokens=150 -> content 0 char, reasoning 785 char;
# max_tokens=900 -> content 352 char, reasoning 894 char.
_REASONING_WARN_AT = 0.0
_REASONING_WARN_EVERY_S = 60

def _assistant_text(message) -> str:
    """Il testo utile di un messaggio assistant, qualunque campo l'abbia scritto."""
    if not isinstance(message, dict):
        return ""
    content = str(message.get("content") or "").strip()
    if content:
        return content
    return str(message.get("reasoning") or message.get("reasoning_content") or "").strip()

def _normalize_assistant_message(payload, where: str) -> dict:
    """Sposta `reasoning` in `content` quando `content` e' vuoto (in place).

    Un client OpenAI-compatibile (Open WebUI in testa) mostra `content`: senza
    questo passaggio l'utente vedrebbe una risposta VUOTA pur avendo il modello
    lavorato e consumato token. La mutazione e' in place perche' i chiamanti
    fanno `jsonify(result_json)` subito dopo: cosi' client, memoria e log
    vedono tutti la stessa cosa. Il fallback viene segnalato, con rate-limit,
    perche' questa funzione gira su ogni richiesta.
    """
    global _REASONING_WARN_AT
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return payload
    if not isinstance(message, dict) or str(message.get("content") or "").strip():
        return payload
    fallback = _assistant_text(message)
    if not fallback:
        return payload
    message["content"] = fallback
    message["content_from_reasoning"] = True
    now = time.time()
    if now - _REASONING_WARN_AT > _REASONING_WARN_EVERY_S:
        _REASONING_WARN_AT = now
        push_log('system', f'{where}: content vuoto, mostro il reasoning',
                 'Il modello ha esaurito i token ragionando (max_tokens troppo basso) '
                 'oppure il backend non ha soppresso il thinking: alza max_tokens o usa '
                 'un modello non-reasoning.', status='warn')
    return payload

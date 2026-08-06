# shared/engine_profiles.py
# Profili d'inferenza condivisi tra node e control-plane.
#
# Dopo l'introduzione della concurrency adattativa QoS (v1.05) il punteggio
# non classifica più i singoli prodotti (vllm=1.0, ollama=0.5, ...): serve
# solo a distinguere il PARADIGMA di serving, che è ciò che determina il
# comportamento (limiter adattativo sul nodo, termine "backend" nello
# scoring del CP):
#   inference_server  -> modello persistente, batching continuo, concorrenza
#                        alta (vLLM, TGI, SGLang)        -> punteggio alto
#   model_manager     -> load/unload on-demand, concorrenza nativa minore
#                        (Ollama, LM Studio)             -> punteggio basso
#
# La mappatura motore -> backend_type NON vive qui: è in
# node/backend_metrics.py (_CAPABILITY_PROFILES), unica fonte di verità.

import os
import json

BACKEND_TYPE_SCORE_DEFAULT = float(os.getenv("BACKEND_TYPE_SCORE_DEFAULT", "0.5"))
_BACKEND_TYPE_SCORES = {"inference_server": 1.0, "model_manager": 0.5}
try:
    _BACKEND_TYPE_SCORES.update(
        {k.lower(): float(v) for k, v in json.loads(os.getenv("BACKEND_TYPE_SCORES", "{}")).items()}
    )
except Exception:
    pass


def backend_type_score(backend_type: str) -> float:
    """Punteggio 0..1 per il paradigma di serving. Tipo non riconosciuto ->
    default (model_manager, conservativo: concorrenza incerta)."""
    return _BACKEND_TYPE_SCORES.get((backend_type or "").lower().strip(), BACKEND_TYPE_SCORE_DEFAULT)


def all_backend_type_scores() -> dict:
    """Copia del dizionario punteggi per esposizione esterna (es.
    /config/routing-weights sul control-plane) — non esporre _BACKEND_TYPE_SCORES
    direttamente: è un dettaglio interno di questo modulo."""
    return dict(_BACKEND_TYPE_SCORES)

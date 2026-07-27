# shared/engine_profiles.py
# Profili motore d'inferenza condivisi tra node e control-plane — stessa
# logica di shared/model_weights.py (stima condivisa, non duplicata).
#
# Due usi distinti dello stesso punteggio:
#   1. control-plane: peso nello scoring di routing (ROUTING_WEIGHT_ENGINE)
#   2. node: stima del default di MAX_LOAD_UNITS quando non impostato
#      esplicitamente nel .env (stesso pattern di calculate_tier()/NODE_TIER)
#
# Criterio: capacità di batching continuo/concorrenza reale, non maturità
# del progetto — llama.cpp sta sopra a Ollama perché Ollama ci gira sopra e
# storicamente ne eredita ma limita il parallelismo nativo.

import os
import json

ENGINE_SCORE_DEFAULT = float(os.getenv("ENGINE_SCORE_DEFAULT", "0.5"))
_ENGINE_SCORES = {"vllm": 1.0, "tgi": 0.9, "llama.cpp": 0.7, "ollama": 0.5, "lmstudio": 0.4}
try:
    _ENGINE_SCORES.update({k.lower(): float(v) for k, v in json.loads(os.getenv("ENGINE_SCORES", "{}")).items()})
except Exception:
    pass

# Riferimento per la stima del default di MAX_LOAD_UNITS: quante unità di
# carico per GB di VRAM, al punteggio motore di riferimento (Ollama, 0.5).
# L'UNICO numero davvero "a naso" in questo modulo — va ricalibrato con dati
# reali (vedi mesh-loadtest.py) quando disponibili. Cambiarlo qui si
# propaga a tutta la tabella derivata, senza riscrivere voci singole.
LOAD_UNITS_PER_VRAM_GB = float(os.getenv("LOAD_UNITS_PER_VRAM_GB", "0.125"))  # 1 unità ogni 8GB
MIN_LOAD_UNITS = float(os.getenv("MIN_LOAD_UNITS", "1"))


def engine_score(engine: str) -> float:
    return _ENGINE_SCORES.get((engine or "").lower().strip(), ENGINE_SCORE_DEFAULT)


def all_engine_scores() -> dict:
    """Copia del dizionario punteggi per esposizione esterna (es.
    /config/routing-weights sul control-plane) — non esporre _ENGINE_SCORES
    direttamente: è un dettaglio interno di questo modulo."""
    return dict(_ENGINE_SCORES)


def estimate_default_load_units(vram_gb: float, engine: str) -> float:
    """Default di MAX_LOAD_UNITS quando non impostato esplicitamente nel
    .env — stesso pattern di calculate_tier()/NODE_TIER: auto-rilevato da
    segnali che il nodo già ha (VRAM_GB, INFERENCE_BACKEND), sovrascrivibile
    a mano. Su CPU pura (vram_gb<=0) il collo di bottiglia sono i core, non
    la memoria: concorrenza alta non aiuta, resta sempre a MIN_LOAD_UNITS
    indipendentemente dal motore dichiarato."""
    if vram_gb <= 0:
        return MIN_LOAD_UNITS
    multiplier = engine_score(engine) / 0.5  # normalizzato su Ollama = 1.0x
    return max(MIN_LOAD_UNITS, round(vram_gb * LOAD_UNITS_PER_VRAM_GB * multiplier, 2))

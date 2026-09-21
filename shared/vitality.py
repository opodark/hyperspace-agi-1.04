# SPDX-License-Identifier: Apache-2.0
"""Vitalità di Aurora: quanta "energia" ha, dalla potenza della mesh.

Effetto Tamagotchi: poca mesh → modelli piccoli e risposte essenziali; mesh
ricca → modelli grandi e risposte profonde. Questo modulo è puro (nessuna rete,
nessun Flask): il chiamante passa la lista dei nodi e riceve livello, etichetta
e la riga di contesto da iniettare nel prompt del canale.
"""
from __future__ import annotations

from typing import Any, Dict

# Etichetta leggibile per livello (0..5). L'agente può dichiararla ("oggi sono
# un po' assonnata"): è un fatto sulla RETE, non una scusa.
LEVEL_LABELS = {
    0: "spenta",
    1: "assonnata",
    2: "sveglia a metà",
    3: "sveglia",
    4: "energica",
    5: "in piena forma",
}

# Riga di contesto da iniettare nel prompt del canale, per livello.
LEVEL_CONTEXT = {
    0: "La rete è spenta: rispondi con il minimo indispensabile. Invita chi ti parla ad aprire un tab per aiutarti a svegliarti.",
    1: "La rete è piccola (solo questa macchina): risposte brevi ed essenziali. Invita a unirsi alla mesh — anche un piccolo LLM via WebGPU nel browser — per avere risposte più vive e frequenti.",
    2: "La rete sta crescendo: risposte misurate. Più nodi si uniscono, più puoi essere presente e generosa.",
    3: "La rete è attiva: puoi rispondere con calma e cura.",
    4: "La rete è ricca: puoi essere più profonda e giocare con la voce.",
    5: "La rete è viva e potente: puoi filosofare, approfondire e osare di più.",
}


def mesh_vitality(nodes) -> Dict[str, Any]:
    """Livello di vitalità (0..5) dalla lista dei nodi attivi.

    I web node non contano (un browser non è potenza di calcolo stabile). Il
    nodo locale (il Mac) conta con la sua RAM unificata rilevata via sysctl.
    """
    active = [n for n in (nodes or [])
              if n.get("status") == "active" and not n.get("is_web_node")]
    total_vram = sum(float(n.get("vram_gb") or 0) for n in active)
    count = len(active)
    if count == 0:
        level = 0
    elif count == 1 and total_vram <= 16:
        level = 1
    elif total_vram <= 24:
        level = 2
    elif total_vram <= 48:
        level = 3
    elif total_vram <= 96:
        level = 4
    else:
        level = 5
    return {"level": level, "label": LEVEL_LABELS[level],
            "active_nodes": count, "total_vram_gb": round(total_vram, 1)}


def vitality_context(vitality: dict) -> str:
    """Riga di contesto deterministica per il livello corrente."""
    livello = int((vitality or {}).get("level", 0))
    return LEVEL_CONTEXT.get(livello, LEVEL_CONTEXT[0])

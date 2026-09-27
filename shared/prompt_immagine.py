#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Riconoscimento delle richieste d'immagine: regex veloce + modello piccolo.

La regex (`richiesta_immagine` in `image_jobs.py`) è gratuita e deterministica ma
rigida: capisce solo frasi che COMINCIANO col verbo e un elenco fisso di parole.
Un utente vero scrive "ora fammi un...", "dai disegnami un...", stili arbitrari,
negazioni. Il modello senza censura della persona (`qwen3-8b-abliterated`, 8B) è il secondo passaggio per quelle
frasi: decide se è una richiesta d'immagine e ne estrae il prompt pulito.

La regex resta il primo passaggio (nessuna rete) e il fallback quando il modello
non risponde: il riconoscimento non regredisce mai.
"""
from __future__ import annotations

import json
import os

from shared.image_jobs import richiesta_immagine

# Il modello piccolo e per quanto resta in RAM dopo l'uso. `keep_alive` corto
# perché il Mac ha 16 GB e questo modello vive accanto a SDXL-Turbo e alla
# persona (8B): una volta usato si scarica in fretta.
MODELLO_PROMPT = os.getenv("PROMPT_IMAGE_MODEL", "qwen3-8b-abliterated")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
KEEP_ALIVE_S = 60

_SISTEMA = (
    "Sei il classificatore di un bot di immagini. Ricevi un messaggio di chat e "
    "devi dire se chiede di GENERARE un'immagine (foto, disegno, sketch, ritratto, "
    "illustrazione, close-up, paesaggio, ecc.). Rispondi SOLO con JSON valido, "
    "senza testo intorno:\n"
    '{"vuole_immagine": true|false, "prompt": "..."}\n'
    "Se NON chiede un'immagine: vuole_immagine=false, prompt vuoto.\n"
    "Se la chiede: prompt = descrizione pulita in inglese con soggetto, stile, "
    "tecnica e inquadratura, SENZA le parole della richiesta (\"fammi\", "
    "\"disegnami\", \"per favore\", \"ora\", \"dai\")."
)


def _estrai_json(risposta: str) -> dict | None:
    """Il JSON del modello, anche se arriva avvolto in markdown o con testo intorno."""
    risposta = str(risposta or "").strip()
    if not risposta:
        return None
    try:
        dato = json.loads(risposta)
    except ValueError:
        inizio = risposta.find("{")
        fine = risposta.rfind("}")
        if inizio == -1 or fine <= inizio:
            return None
        try:
            dato = json.loads(risposta[inizio:fine + 1])
        except ValueError:
            return None
    return dato if isinstance(dato, dict) else None


def _prompt_da_modello(risposta: str) -> str:
    """Il prompt pulito, o \"\" se il modello dice che non è una richiesta."""
    dato = _estrai_json(risposta)
    if not dato or not dato.get("vuole_immagine"):
        return ""
    return " ".join(str(dato.get("prompt") or "").split())[:400]


# Il modello iniettato: None nei test (solo regex), una funzione vera in
# produzione. È un globale perché `_channel_immagine` viene estratta con `ast`
# (tests/test_channel_immagine.py) e rieseguita in uno scope che non conosce
# questa funzione: passa di qui.
_MODELLO = None  # callable (testo) -> risposta grezza


def configura_modello(modello) -> None:
    """Installa il callable che interroga il modello piccolo (nessuno = solo regex)."""
    global _MODELLO
    _MODELLO = modello


def richiesta_immagine_smart(testo: str) -> dict | None:
    """Come `richiesta_immagine`, ma con il modello per la coda lunga.

    Restituisce ``{"idea": ..., "regola": ...}`` o None. La regex resta il primo
    passaggio; il modello interviene solo quando lei non ha capito la frase.
    """
    esito = richiesta_immagine(testo)
    if esito is not None:
        return esito
    if _MODELLO is None:
        return None
    try:
        risposta = _MODELLO(testo)
    except Exception:
        return None
    prompt = _prompt_da_modello(risposta)
    if not prompt:
        return None
    return {"idea": prompt, "regola": "modello"}


def chiama_ollama(testo: str, *, base_url: str = OLLAMA_URL,
                  model: str = MODELLO_PROMPT, timeout_s: float = 12.0) -> str:
    """Una chiamata diretta a Ollama: stream off, keep_alive corto.

    `keep_alive` corto è ciò che fa "scaricare" il modello dalla memoria dopo
    l'uso: il Mac resta libero per SDXL-Turbo e per la persona.
    """
    import requests
    risposta = requests.post(
        f"{str(base_url).rstrip('/')}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": _SISTEMA},
                {"role": "user", "content": str(testo)},
            ],
            "stream": False,
            "keep_alive": f"{KEEP_ALIVE_S}s",
            "think": False,
            "options": {"temperature": 0, "num_predict": 160},
        },
        timeout=timeout_s,
    )
    risposta.raise_for_status()
    return (risposta.json().get("message") or {}).get("content", "")

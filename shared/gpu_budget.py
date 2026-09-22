#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Una scheda, un modello: la memoria della GPU si decide, non si spera.

Perché esiste (2026-09-22): generando il ritratto di Aurora, ComfyUI è morto con
`torch.AcceleratorError: CUDA error: unknown error` nel KSampler. La scheda di win11
ha 8151 MiB: ne erano occupati 6170 da `llama-server` (il modello del canale, tenuto
residente da `OLLAMA_KEEP_ALIVE=12h`) e liberi 1730 — Qwen-Image non ci sta. Dopo
l'errore, la coda di ComfyUI è rimasta con `queue_running` vuoto: l'esecutore non
riparte da solo, e il job resta lì per sempre.

Il modello che serve alla chat e il diffusion che serve all'immagine si contendono la
stessa memoria, e i due non si vedono: la contesa si presentava come un errore CUDA
casuale. Qui diventa una decisione dichiarata — prima di accodare un'immagine si
chiede a Ollama di scaricare il modello del canale (`keep_alive: 0`). Ollama lo
ricarica alla prima richiesta: si paga qualche secondo al primo messaggio dopo
un'immagine, non si paga un crash.

Come i canali: la parte decidibile sta qui e si testa senza GPU; il cablaggio (una
chiamata HTTP) sta in `control-plane/main.py`.
"""
from __future__ import annotations

import os

# Spegnibile: su una macchina con una scheda grande non serve, e scaricare il modello
# rallenta la prima risposta dopo ogni immagine.
DISATTIVATO = ("0", "false", "no", "off")

SCARICA_PATH = "/api/generate"


def da_scaricare(env: dict | None = None) -> str:
    """Il modello da scaricare prima di un'immagine. Vuoto = non si scarica niente.

    Vuoto significa: `IMAGE_FREE_GPU` disattivato, oppure nessun modello di canale
    dichiarato (non si inventa un modello da scaricare: si sbaglierebbe macchina).
    """
    valori = os.environ if env is None else env
    if str(valori.get("IMAGE_FREE_GPU", "true")).strip().lower() in DISATTIVATO:
        return ""
    return str(valori.get("CHANNEL_MODEL", "") or "").strip()


def richiesta_scarico(modello: str) -> dict:
    """Il corpo con cui Ollama scarica un modello: `keep_alive: 0`.

    `stream: false` perché la risposta arrivi intera: uno scarico non è una
    conversazione, e non serve leggerla a pezzi.
    """
    return {"model": str(modello or "").strip(), "keep_alive": 0, "stream": False}


def descrivi_esito(status: int, *, errore: str = "", modello: str = "") -> str:
    """Com'è andata, in una riga che si può scrivere nei log."""
    nome = modello or "il modello del canale"
    if status == 200:
        return f"scheda liberata: {nome} scaricato dalla memoria"
    if not status:
        return f"scarico non riuscito ({errore or 'Ollama non raggiungibile'})"
    return f"scarico non riuscito (HTTP {status})"

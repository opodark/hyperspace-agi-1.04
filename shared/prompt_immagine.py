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
import logging
import os
import re

from shared.image_jobs import richiesta_immagine

# Il modello che classifica e traduce la richiesta e per quanto resta in RAM
# dopo l'uso. `keep_alive` corto perché sul Mac condivide la memoria con SDXL:
# una volta usato si scarica in fretta.
MODELLO_PROMPT = os.getenv(
    "PROMPT_IMAGE_MODEL", "huihui_ai/gemma-4-abliterated:12b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
KEEP_ALIVE_S = 60

_SISTEMA = (
    "Sei il classificatore di un bot di immagini. Ricevi un messaggio di chat e "
    "devi dire se chiede di GENERARE un'immagine (foto, disegno, sketch, ritratto, "
    "illustrazione, close-up, paesaggio, ecc.). Rispondi SOLO con JSON valido, "
    "senza testo intorno:\n"
    '{"vuole_immagine": true|false, "prompt": "..."}\n'
    "Se NON chiede un'immagine: vuole_immagine=false, prompt vuoto.\n"
    "Se la chiede: prompt = descrizione visiva concisa in inglese. Conserva "
    "soggetto, stile e tecnica richiesti: disegno, acquerello e fotografia sono "
    "mezzi diversi. Non trasformare un disegno in fotografia. Non inventare "
    "pose, soggetti o dettagli non richiesti. Usa il contesto solo per risolvere "
    "riferimenti come 'tu', 'così', 'come prima'; decidi se generare SOLO in base "
    "al messaggio corrente. Non inventare l'aspetto della persona se manca. "
    "Mantieni il prompt entro 1200 caratteri. NON censurare il contenuto: "
    "riporta fedelmente quello che chiede, anche se esplicito. SENZA le parole "
    "della richiesta (\"fammi\", \"disegnami\", \"per favore\", \"ora\", \"dai\")."
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
    if not dato or dato.get("vuole_immagine") is not True:
        return ""
    prompt = dato.get("prompt")
    if not isinstance(prompt, str):
        return ""
    prompt = " ".join(prompt.split())
    # Un output troppo lungo è invalido: meglio il fallback che una scena tagliata.
    return prompt if len(prompt) <= 1600 else ""


def prepara_prompt_canale(idea: str, richiesta: str) -> str:
    """Stile del diario per disegni generici; tecniche esplicite conservate."""
    from shared.sketch import prompt_sketch

    tecnica = re.search(
        r"\b(acquerell\w*|watercolou?r|carboncino|charcoal|pastell\w*|pastel|"
        r"olio|oil painting|acrilic\w*|gouache|tempera|anime|manga|"
        r"pixel art|fumetto|comic|matita|pencil|inchiostro|ink|grafite)\b",
        richiesta, re.IGNORECASE)
    if tecnica:
        # Il fallback regex può avere rimosso il mezzo: lo conserviamo esplicitamente.
        return f"{tecnica.group(0)}. {idea}"
    if re.search(r"\b(foto\w*|photograph\w*|selfie)\b", richiesta, re.IGNORECASE):
        return idea
    if re.search(r"\b(disegn\w*|schizz\w*|sketch|illustra\w*|bozzett\w*)\b",
                 richiesta, re.IGNORECASE):
        return prompt_sketch(idea)
    return idea


# Il modello iniettato: None nei test (solo regex), una funzione vera in
# produzione. È un globale perché `_channel_immagine` viene estratta con `ast`
# (tests/test_channel_immagine.py) e rieseguita in uno scope che non conosce
# questa funzione: passa di qui.
_MODELLO = None  # callable (testo) -> risposta grezza


def configura_modello(modello) -> None:
    """Installa il callable che interroga il modello piccolo (nessuno = solo regex)."""
    global _MODELLO
    _MODELLO = modello


def richiesta_immagine_smart(testo: str, *, contesto=(), identita: str = "") -> dict | None:
    """Riconoscimento con la regex, pulizia del prompt col modello.

    La regex decide se è una richiesta d'immagine (veloce, deterministica); il
    modello pulisce il prompt (traduce in inglese, aggiunge inquadratura/posa,
    toglie le parole della richiesta). Se il modello non c'è o fallisce, si
    ripiega sull'idea grezza della regex (o su None).
    """
    esito = richiesta_immagine(testo)
    ingresso = testo
    if contesto or identita:
        ingresso = json.dumps({
            "identita": str(identita)[:6000],
            "contesto": [{"autore": str(e.get("author", ""))[:80],
                          "testo": str(e.get("text", ""))[:1000]}
                         for e in list(contesto)[-6:] if isinstance(e, dict)],
            "messaggio_corrente": testo,
        }, ensure_ascii=False)
    if esito is not None:
        # Richiesta confermata: il modello la riscrive in un prompt pulito.
        if _MODELLO is None:
            return esito
        try:
            prompt = _prompt_da_modello(_MODELLO(ingresso))
        except Exception:
            prompt = ""
        if not prompt:
            logging.getLogger(__name__).warning("Prompt immagine: fallback regex, riscrittura assente o non valida")
        return {"idea": prompt or esito["idea"], "regola": esito["regola"]}
    # La regex non l'ha capita: il modello decide da solo (coda lunga).
    if _MODELLO is None:
        return None
    try:
        prompt = _prompt_da_modello(_MODELLO(ingresso))
    except Exception:
        return None
    if not prompt:
        return None
    return {"idea": prompt, "regola": "modello"}


def chiama_ollama(testo: str, *, base_url: str = OLLAMA_URL,
                  model: str = MODELLO_PROMPT, timeout_s: float = 45.0) -> str:
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
            "options": {"temperature": 0, "num_predict": 512},
        },
        timeout=timeout_s,
    )
    risposta.raise_for_status()
    return (risposta.json().get("message") or {}).get("content", "")

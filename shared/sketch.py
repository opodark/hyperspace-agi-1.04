#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sketch dei post: il job immagine leggero per il Mac.

Perché esiste: il loop delle influencer produce un `image_prompt` insieme alla
didascalia, ma su una macchina da 16 GB (il Mac, di notte o quando win11 è
spenta) il modello fotorealistico non ci sta. Lo sketch è l'alternativa che ci
sta: pochi passi, risoluzione bassa, stile dichiaratamente non fotografico —
quindi soddisfa da solo la regola di `shared/showcase.py` ("la figura deve
dichiararsi digitale").

Qui vive la parte decidibile e testabile: il prompt dello sketch, il job che lo
chiede, e il tetto giornaliero. Il modello (SDXL-Turbo, famiglia "sdxl-turbo")
lo sceglie `shared/image_jobs.py` nel grafo.
"""
from __future__ import annotations

from shared.image_jobs import FAMIGLIA_SDXL, nuovo_job
from shared.showcase import DICHIARAZIONE, NEGATIVO_BASE

# Lo stile dello sketch: dichiaratamente non fotografico, così la regola
# "figura digitale" di showcase è soddisfatta senza doverla chiedere ogni volta.
STILE_SKETCH = (
    "schizzo a matita e inchiostro, line art, illustrazione, non fotografico, "
    "tratto veloce, bozza d'artista"
)

# Passi e lato: SDXL-Turbo dà il massimo in 1-4 passi a 512×512. Di più è solo
# spreco di memoria sul Mac.
SKETCH_PASSI = 2
SKETCH_LATO = 512
SKETCH_CANALE = "feed"
SKETCH_PER_DAY = 4


def prompt_sketch(idea: str, *, autore: str = "") -> str:
    """Lo sketch pronto per il modello: stile + idea + dichiarazione.

    La dichiarazione ("si vede che è una costruzione digitale") sta in coda come
    nel ritratto: con Qwen va detto, e con SDXL non fa male.
    """
    pezzi = [STILE_SKETCH]
    testo = " ".join(str(idea or "").split())
    if testo:
        pezzi.append(testo)
    pezzi.append(DICHIARAZIONE)
    return ". ".join(p.strip(".") for p in pezzi if p.strip())


def negativo_sketch() -> str:
    """Le esclusioni dello sketch: le assolute del ritratto, più il no-foto."""
    return NEGATIVO_BASE


def job_sketch(idea: str, *, autore: str = "", post_id: str = "",
               adesso: float | None = None) -> dict:
    """Il job pronto per la coda: leggero, famiglia sdxl-turbo, per il feed.

    `post_id` lega lo sketch al post che illustra (`destinazione`): è il modo in
    cui il diario correla, dopo, la didascalia alla sua immagine.
    """
    return nuovo_job(
        prompt_sketch(idea, autore=autore),
        negativo=negativo_sketch(),
        larghezza=SKETCH_LATO,
        altezza=SKETCH_LATO,
        passi=SKETCH_PASSI,
        richiedente=str(autore or "")[:64],
        canale=SKETCH_CANALE,
        famiglia=FAMIGLIA_SDXL,
        destinazione=str(post_id or "")[:64],
        adesso=adesso,
    )


def puo_generare(conteggi: dict, *, autore: str, tetto: int = SKETCH_PER_DAY) -> bool:
    """True se l'autore non ha ancora superato il tetto giornaliero di sketch.

    `conteggi` è {autore: n_generati_oggi}, tenuto e azzerato dal chiamante
    (control-plane) quando cambia il giorno. Un autore mai visto parte da zero.
    """
    return int((conteggi or {}).get(str(autore or ""), 0)) < max(1, int(tetto))

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sogno notturno delle influencer: una scena onirica + lo sketch che la illustra.

Stesso schema di `shared/post_gen.py` e `shared/persona_dream.py`: il modello è
INIETTATO dal chiamante, qui vive la parte decidibile — il prompt, il parsing
tollerante e il filtro. Il sogno produce una SCENA (il testo della pagina) e un
DISEGNO (l'idea per lo sketch che il Mac renderà): la coppia è la pagina del
diario dei sogni.
"""
from __future__ import annotations

import re

_RIGA_SCENA = re.compile(r"(?i)^\s*scena\s*[:：]\s*(.*)$")
_RIGA_DISEGNO = re.compile(r"(?i)^\s*(?:disegno|immagine|sketch)\s*[:：]\s*(.*)$")

# "NIENTE" è il segnale onesto: il modello dice di non avere nulla da sognare.
_NIENTE = re.compile(r"(?i)^\s*NIENTE\b")

# Meta-rumore: un modello con materiale scarso scrive dell'assenza di materiale
# ("non ho nulla da sognare perché la memoria è vuota") e quella frase non è un
# sogno, è il verbale della riflessione.
_META = re.compile(
    r"\bnon\s+(?:ho|posso|riesco\s+a)\s+(?:nulla|niente|dire|sognare|ricordare)\b"
    r"|\b(?:memoria|registro|materiale)\b[^.!?]{0,40}\b(?:vuot|nul|assent|mancant)\w*\b",
    re.IGNORECASE | re.UNICODE)


def build_dream_prompt(sistema: str, *, memorie=(), feed_recente=()) -> str:
    """Il prompt che chiede alla persona di sognare UN sogno per il diario.

    `sistema` è il blocco di identità già pronto; `memorie` e `feed_recente` sono
    il materiale (recente, in ordine), come nel prompt del post.
    """
    righe = [str(sistema or "").strip(),
             "",
             "Di notte sogni. Scrivi UN sogno breve per il tuo diario:",
             "una scena onirica in una o due frasi, e un'idea per il disegno",
             "che la illustra (uno schizzo, non una fotografia)."]
    if memorie:
        righe += ["", "Ricordi recenti della stanza:",
                  *[f"- {m}" for m in memorie[:5]]]
    if feed_recente:
        righe += ["", "Ultimi post della vetrina (per non ripeterti):",
                  *[f"- [{p.get('author', '?')}] {p.get('caption', '')}"
                    for p in feed_recente[:5]]]
    righe += ["",
              "Rispondi SOLO in questo formato:",
              "SCENA: <il sogno, breve>",
              "DISEGNO: <idea per lo schizzo>",
              "Se non hai nulla da sognare, rispondi esattamente: NIENTE"]
    return "\n".join(righe)


def parse_dream(testo: str) -> dict | None:
    """Risposta del modello -> {scena, disegno} oppure None (NIENTE/illeggibile)."""
    if _NIENTE.search(str(testo or "")):
        return None
    scena, disegno = "", ""
    for riga in str(testo or "").splitlines():
        m = _RIGA_SCENA.match(riga)
        if m and not scena:
            scena = m.group(1).strip()
            continue
        m = _RIGA_DISEGNO.match(riga)
        if m and not disegno:
            disegno = m.group(1).strip()
            continue
    scena = " ".join(scena.split())
    if not scena:
        return None
    return {"scena": scena, "disegno": " ".join(disegno.split())}


def filtra_dream(candidato: dict, *, autore: str, diario=()) -> tuple[bool, str]:
    """(accettato, motivo) — il filtro puro, prima di scrivere nel diario.

    Scarta il meta-rumore e i doppioni della stessa persona (`diario` è la lista
    delle voci già scritte, con `testo` = scena).
    """
    testo = " ".join(str((candidato or {}).get("scena", "")).split())
    if not testo:
        return False, "niente da sognare"
    if _META.search(testo):
        return False, "meta-rumore, non un sogno"
    if any(str(v.get("author", "")) == autore and str(v.get("testo", "")) == testo
           for v in (diario or ())):
        return False, "già sognato"
    return True, ""

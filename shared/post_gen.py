# SPDX-License-Identifier: Apache-2.0
"""Generatore di post: una persona produce un contenuto per il feed.

Stesso schema del sogno (shared/persona_dream.py): il modello è INIETTATO dal
chiamante, qui vive solo la parte decidibile — il prompt, il parsing della
risposta e il filtro. Il loop autonomo (Fase 3) chiamerà questo col modello
locale e scriverà il risultato nel feed (shared/feed.py).

Il formato della risposta è tollerante, come nel sogno: un modello piccolo
sbaglia la formattazione, e perdere un post buono per una riga scritta male è
peggio che accettarne uno con qualche spazio di troppo.
"""
from __future__ import annotations

import re

# ── Formato della risposta ───────────────────────────────────────────────────
# Il modello risponde con due righe marcate; il parser le legge anche con
# maiuscole, spazi o due punti diversi.
_RIGA_DIDASCALIA = re.compile(r"(?i)^\s*didascalia\s*[:：]\s*(.*)$")
_RIGA_IMMAGINE = re.compile(r"(?i)^\s*immagine\s*[:：]\s*(.*)$")

# "NIENTE" è il segnale onesto: il modello dice di non avere nulla da pubblicare.
# Non è un errore da correggere, è una decisione.
_NIENTE = re.compile(r"(?i)^\s*NIENTE\b")

# Meta-rumore: un modello con materiale scarso scrive dell'assenza di materiale
# ("non ho nulla da dire perché la memoria è vuota") e quella frase non è un post.
_META = re.compile(
    r"\bnon\s+(?:ho|posso|riesco\s+a)\s+(?:nulla|niente|dire|pubblicare|scrivere)\b"
    r"|\b(?:memoria|registro|materiale)\b[^.!?]{0,40}\b(?:vuot|nul|assent|mancant)\w*\b",
    re.IGNORECASE | re.UNICODE)


def build_post_prompt(sistema: str, *, memorie=(), feed_recente=(),
                      replica_a: dict | None = None) -> str:
    """Il prompt che chiede alla persona di produrre UN post.

    `sistema` è il blocco di identità già pronto (chi è, tono, confini): il
    chiamante lo passa, qui non si tocca. `memorie` e `feed_recente` sono il
    materiale (recente, in ordine); `replica_a` trasforma il post in una
    REAZIONE al post di qualcun altro.
    """
    righe = [str(sistema or "").strip(),
             "",
             "Devi pubblicare UN post per la tua vetrina: una didascalia breve,",
             "nel tuo tono, e — se vuoi — un'idea per l'immagine che l'accompagna."]
    if replica_a:
        righe += ["",
                  f"Stai rispondendo al post di {replica_a.get('author', 'qualcuno')}:",
                  f"«{replica_a.get('caption', '')}»",
                  "È una REAZIONE: breve, rivolta a chi ha scritto, sempre nel tuo tono."]
    if memorie:
        righe += ["", "Ricordi recenti della stanza:",
                  *[f"- {m}" for m in memorie[:5]]]
    if feed_recente:
        righe += ["", "Ultimi post della vetrina (per non ripeterti):",
                  *[f"- [{p.get('author', '?')}] {p.get('caption', '')}" for p in feed_recente[:5]]]
    righe += ["",
              "Rispondi SOLO in questo formato:",
              "DIDASCALIA: <il testo del post>",
              "IMMAGINE: <idea per l'immagine>   (puoi ometterla)",
              "Se non hai nulla da dire, rispondi esattamente: NIENTE"]
    return "\n".join(righe)


def parse_post(testo: str) -> dict | None:
    """Risposta del modello -> {caption, image_prompt} oppure None.

    None copre due casi diversi ma uguali per il chiamante: il modello ha detto
    NIENTE (nessun post questo giro) oppure l'output era illeggibile.
    """
    if _NIENTE.search(str(testo or "")):
        return None
    didascalia, immagine = "", ""
    for riga in str(testo or "").splitlines():
        m = _RIGA_DIDASCALIA.match(riga)
        if m and not didascalia:
            didascalia = m.group(1).strip()
            continue
        m = _RIGA_IMMAGINE.match(riga)
        if m and not immagine:
            immagine = m.group(1).strip()
            continue
    didascalia = " ".join(didascalia.split())
    if not didascalia:
        return None
    return {"caption": didascalia, "image_prompt": " ".join(immagine.split())}


def filtra_post(candidato: dict, *, autore: str, feed=()) -> tuple[bool, str]:
    """(accettato, motivo) — il filtro puro, prima di scrivere nel feed.

    Scarta il meta-rumore (il verbale del modello, non un contenuto) e i doppioni
    della stessa persona. `nuovo_post` (shared/feed.py) fa poi la validazione
    finale di autore e didascalia.
    """
    testo = " ".join(str((candidato or {}).get("caption", "")).split())
    if not testo:
        return False, "niente da dire"
    if _META.search(testo):
        return False, "meta-rumore, non un contenuto"
    if any(str(p.get("author", "")) == autore and str(p.get("caption", "")) == testo
           for p in (feed or ())):
        return False, "già pubblicato"
    return True, ""


def prossima_mossa(feed: list, *, autori=("anna", "aurora"), turno: int = 0) -> dict:
    """Decide chi posta adesso e se è una reazione.

    Alterna gli autori a ogni giro; se l'ultimo post del feed è dell'ALTRA
    persona, il nuovo post è una REAZIONE a quello — così le due si rispondono
    invece di scrivere in parallelo. Ritorna {autore, replica_a}, dove
    `replica_a` è il post a cui rispondere oppure None (contenuto nuovo).
    """
    autore = autori[turno % len(autori)]
    ultimo = feed[0] if feed else None
    replica_a = ultimo if (ultimo and ultimo.get("author") != autore) else None
    return {"autore": autore, "replica_a": replica_a}

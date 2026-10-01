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
                      replica_a: dict | None = None, insisti: bool = False) -> str:
    """Il prompt che chiede alla persona di produrre UN post.

    `sistema` è il blocco di identità già pronto (chi è, tono, confini): il
    chiamante lo passa, qui non si tocca. `memorie` e `feed_recente` sono il
    materiale (recente, in ordine); `replica_a` trasforma il post in una
    REAZIONE al post di qualcun altro.

    `insisti` è la seconda richiesta, dopo che la prima è stata scartata come
    eco (`filtra_post` con `replica_a`): chiedere *di nuovo la stessa cosa* allo
    stesso modello è una giocata di dadi, chiederglielo dicendo cos'è andato
    storto è una riparazione. Il vincolo resta relativo al post citato, perché è
    l'unico punto in cui la ripetizione è un difetto e non uno stile.
    """
    righe = [str(sistema or "").strip(),
             "",
             "Devi pubblicare UN post per la tua vetrina: una didascalia breve,",
             "nel tuo tono, e — se vuoi — un'idea per l'immagine che l'accompagna.",
             "L'IMMAGINE può essere anche tipografica: se contiene versi, scrivi",
             "il testo esatto nella DIDASCALIA e riportane una breve frase tra virgolette",
             "nell'IMMAGINE. Così il lettore può leggere la poesia anche se il disegno",
             "usa caratteri fantastici o distorti."]
    if replica_a:
        righe += ["",
                  f"Stai rispondendo al post di {replica_a.get('author', 'qualcuno')}:",
                  f"«{replica_a.get('caption', '')}»",
                  "È una REAZIONE: breve, rivolta a chi ha scritto, sempre nel tuo tono.",
                  "Rispondi al pensiero con parole tue: non ripetere le sue parole."]
        if insisti:
            righe += ["La volta precedente hai ricopiato quelle parole: stavolta "
                      "devono essere frasi tue, nuove."]
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


def build_poem_prompt(sistema: str, *, feed_recente=()) -> str:
    """Il prompt che chiede alla persona di scrivere UNA poesia per la vetrina.

    Diverso da `build_post_prompt`: qui la poesia è il contenuto, non una
    possibilità. I versi stanno per intero nella DIDASCALIA; l'IMMAGINE deve
    essere tipografica e riportare una frase breve fra virgolette, così il
    lettore può leggerla anche se il disegno usa caratteri distorti.
    """
    righe = [str(sistema or "").strip(),
             "",
             "Devi scrivere UNA poesia per la vetrina: versi brevi, nel tuo tono.",
             "La poesia sta per intero nella DIDASCALIA (i versi esatti).",
             "L'IMMAGINE deve essere tipografica: riporta una frase breve della poesia",
             "tra virgolette, così il lettore può leggerla anche se il disegno usa",
             "caratteri fantastici o distorti."]
    if feed_recente:
        righe += ["", "Poesie recenti della vetrina (per non ripeterti):",
                  *[f"- [{p.get('author', '?')}] {p.get('caption', '')}" for p in feed_recente[:5]]]
    righe += ["",
              "Rispondi SOLO in questo formato:",
              "DIDASCALIA: <il testo della poesia>",
              "IMMAGINE: <idea per l'immagine tipografica>"]
    return "\n".join(righe)


def parse_post(testo: str) -> dict | None:
    """Risposta del modello -> {caption, image_prompt} oppure None.

    None copre due casi diversi ma uguali per il chiamante: il modello ha detto
    NIENTE (nessun post questo giro) oppure l'output era illeggibile.

    La lettura è tollerante su un punto misurato (2026-10-01): un modello piccolo
    scrive l'etichetta **sola** e il valore sulla riga dopo —
    `DIDASCALIA:` a capo `E se il cuore è un libro,` — e la poesia andava persa
    con la didascalia vuota. Non è un altro formato, è lo stesso con un a capo di
    troppo: quando l'etichetta è sola si raccoglie il testo che segue, finché non
    arriva l'etichetta successiva. Una riga dopo un'etichetta **già valorizzata**
    resta invece fuori, come prima: lì il formato è quello giusto e il resto è
    prosa del modello, non contenuto.
    """
    if _NIENTE.search(str(testo or "")):
        return None
    didascalia: list[str] = []
    immagine: list[str] = []
    destinazione = ""            # "" | "caption" | "image"
    raccogli = False             # True solo dopo un'etichetta lasciata sola
    for riga in str(testo or "").splitlines():
        m = _RIGA_DIDASCALIA.match(riga)
        if m:
            valore = m.group(1).strip()
            destinazione, raccogli = "caption", not valore
            if valore:
                didascalia.append(valore)
            continue
        m = _RIGA_IMMAGINE.match(riga)
        if m:
            valore = m.group(1).strip()
            destinazione, raccogli = "image", not valore
            if valore:
                immagine.append(valore)
            continue
        pulita = riga.strip()
        if pulita and destinazione and raccogli:
            (didascalia if destinazione == "caption" else immagine).append(pulita)
    testo_post = " ".join(" ".join(didascalia).split())
    if not testo_post:
        return None
    return {"caption": testo_post,
            "image_prompt": " ".join(" ".join(immagine).split())}


# ── Eco: la reazione che ricopia il post a cui risponde ──────────────────────
# Misurato (2026-10-01, sandbox della vita): chiesta una REAZIONE, il modello
# restituisce il post di partenza **parola per parola**. Fra due voci diverse
# quel doppione non è un post, è un balbettio — e il filtro dei doppioni non lo
# vede, perché l'autore è l'altro. Non è stilometria: è contare quante parole la
# reazione aggiunge al post. Zero parole nuove su un testo lungo → è una copia;
# pochissime (una coda di cortesia) → è la stessa copia col cappello. Una
# citazione breve, invece, è legittima: si risponde citando.
MOTIVO_ECO = "ripete il post a cui risponde"
_ECO_PAROLE_MINIME = 6      # sotto sei parole il post è una battuta: non si copia
_ECO_PAROLE_NUOVE = 4       # quante parole nuove può portare una copia
_ECO_COPERTURA = 0.9        # quanta parte del post deve tornare perché sia una copia
_PAROLA = re.compile(r"[^\W\d_]+", re.UNICODE)


def _parole(testo: str) -> list[str]:
    """Le parole del testo in minuscolo: niente punteggiatura, niente numeri.

    È privata perché è l'unico punto che deve contare le parole con lo stesso
    metro — grassetti, virgolette e accenti diversi fra due risposte dello stesso
    modello; fuori di qui non serve a nessuno.
    """
    return _PAROLA.findall(str(testo or "").lower())


def ripete_il_post(testo: str, originale: str) -> bool:
    """True se `testo` è il post `originale` ricopiato, anche con qualche parola in più.

    Tre verdetti, tutti e tre su due numeri soli — quanta parte del post torna
    (`copertura`) e quante parole proprie porta la reazione:

    1. le stesse parole, in qualunque ordine e con qualunque punteggiatura;
    2. nessuna parola propria, su una reazione lunga: è il post spezzato in due,
       non una risposta;
    3. il post intero con una coda di cortesia («Anche io lo penso»).

    Restano fuori due cose, ed è la ragione per cui il metro è la copertura e non
    la somiglianza: la **citazione breve** (rispondere citando è legittimo) e il
    **post corto**, dove le parole sono talmente poche che riprenderle tutte è
    fisiologico — su «Buonanotte mondo» una risposta non ha parole sue.
    """
    nuove, copiate = set(_parole(testo)), set(_parole(originale))
    if not nuove or not copiate:
        return False
    if nuove == copiate:
        return True
    if len(copiate) < _ECO_PAROLE_MINIME:
        return False                       # post corto: riprenderne le parole è normale
    if len(nuove) >= _ECO_PAROLE_MINIME and not (nuove - copiate):
        return True                        # nessuna parola propria: è il post, non una risposta
    copertura = len(nuove & copiate) / len(copiate)
    return copertura >= _ECO_COPERTURA and len(nuove - copiate) <= _ECO_PAROLE_NUOVE


def filtra_post(candidato: dict, *, autore: str, feed=(),
                replica_a: dict | None = None) -> tuple[bool, str]:
    """(accettato, motivo) — il filtro puro, prima di scrivere nel feed.

    Tre scarti, tre regole diverse: il meta-rumore (il verbale del modello, non
    un contenuto), i doppioni **della stessa persona** e — quando `replica_a`
    indica il post a cui si sta rispondendo — l'eco, cioè quella reazione che
    all'altra voce non aggiunge niente (`ripete_il_post`). Il motivo dice quale
    delle tre: chi chiama decide se ritentare, e solo l'eco si rimedia
    chiedendo di nuovo. `nuovo_post` (shared/feed.py) fa poi la validazione
    finale di autore e didascalia.
    """
    testo = " ".join(str((candidato or {}).get("caption", "")).split())
    if not testo:
        return False, "niente da dire"
    if _META.search(testo):
        return False, "meta-rumore, non un contenuto"
    if replica_a and ripete_il_post(testo, str(replica_a.get("caption", ""))):
        return False, MOTIVO_ECO
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

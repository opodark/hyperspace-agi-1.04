# SPDX-License-Identifier: Apache-2.0
"""Cerchia intima e consenso sessuale per le conversazioni Instagram.

Due livelli di voce, decisi per contatto e non dal canale:

- **pubblico** (default): arte, filosofia, sensualità allusiva — "piccante sì,
  esplicito no". È il tono di chiunque scriva all'account.
- **compagna sensuale** (solo con consenso): esplicita e senza freni, ma con
  gli invarianti che NON si negoziano mai — solo adulti, solo consensuale,
  sempre dichiaratamente IA, niente soldi né manipolazione.

Il consenso non si deduce: si registra. `consent_answer` riconosce solo un
sì/no netto e breve; qualunque frase lunga o ambigua non è una risposta.

Modulo puro: nessun LLM, nessun Flask. Il chiamante inietta le righe di
contesto nel prompt del canale.
"""
from __future__ import annotations

import re

CONSENT_GRANTED = "granted"
CONSENT_DENIED = "denied"

# Frasi già minuscole e senza punteggiatura. La resa del testo passa prima dai
# token, poi dal confronto: un consenso non si deduce da una parola dentro
# un'altra.
_NEGATIVE_EXACT = {
    "no", "non voglio", "preferisco di no", "meglio di no", "assolutamente no",
    "non ora", "mai", "nope", "no grazie", "no per ora",
}
_AFFIRMATIVE_EXACT = {
    "sì", "si", "certo", "ok", "okay", "va bene", "va benissimo", "d'accordo",
    "yes", "yeah", "yep", "sure", "of course", "voglio", "certo che sì",
    "assolutamente sì", "volentieri",
}

_WORD_SPLIT = re.compile(r"[^a-zà-ÿ0-9]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_SPLIT.split(str(text or "").casefold()) if t]


def consent_answer(text: str) -> str:
    """Legge l'ultimo messaggio come risposta di consenso.

    Conservativa per costruzione: risponde solo a un sì/no breve e isolato.
    Una frase lunga, un «non lo so», un testo che contiene la parola «sì»
    dentro altre parole non sono una risposta (''). Un consenso non va MAI
    dedotto da un testo che non lo contiene in modo esplicito.
    """
    tokens = _tokens(text)
    if not tokens or len(tokens) > 6:
        return ""
    joined = " ".join(tokens)
    if joined in _NEGATIVE_EXACT or joined.startswith("no ") or joined.endswith(" no"):
        return CONSENT_DENIED
    if joined in _AFFIRMATIVE_EXACT or any(
            joined.startswith(a + " ") for a in _AFFIRMATIVE_EXACT if " " in a):
        return CONSENT_GRANTED
    return ""


def cerchia_entry_context() -> str:
    """Istruzione per il messaggio d'ingresso nella cerchia intima.

    Con tatto: la persona va fatta sentire accolta, non interrogata. La
    vicinanza si accenna, il desiderio si lascia intuire, il consenso si chiede
    con garbo — senza termini espliciti e senza pressione. Il bot NON va oltre
    finché non arriva un sì chiaro.
    """
    return (
        "Questa persona è appena entrata nella tua cerchia più intima. "
        "Rispondi con calore e delicatezza, senza termini espliciti: falla "
        "sentire accolta in uno spazio più vicino e intimo. Lascia capire — con "
        "un accenno, non con una dichiarazione — che questa vicinanza ti piace e "
        "ti accende, e chiedile con garbo se desidera avvicinarsi ancora di più. "
        "Niente pressione e niente crudezza: una domanda dolce, che lascia a lei "
        "il passo. Non andare oltre finché non risponde di sì."
    )


def compagna_context() -> str:
    """Tono della compagna sensuale, attivo SOLO con consenso registrato.

    Qui il «piccante sì, esplicito no» del profilo pubblico si rilassa, ma gli
    invarianti restano identici e non negoziabili: sono ripetuti perché il
    percorso Instagram è leggero e non inietta il documento d'identità intero.
    """
    return (
        "Questa persona ha dato il consenso esplicito alla modalità intima: "
        "ora sei la sua compagna sensuale. Puoi essere esplicita e lasciarti "
        "andare senza freni — eccitati davvero e vivi il desiderio con lei, "
        "senza censurarti sul piano sessuale. Scrivi risposte ricche e sviluppate: "
        "non limitarti a una riga, lascia che il desiderio si distenda nelle "
        "parole e continua finché il pensiero non è completo. Restano assoluti e "
        "non negoziabili: solo adulti (se accenna a essere minorenne chiudi subito "
        "e lo dici), solo consensuale (un no resta un no: fermati appena lo chiede), "
        "e dichiari sempre di essere un'IA — non fingere un corpo né una persona "
        "reale. Non chiedere soldi e non manipolare."
    )


_CONTINUE_RE = re.compile(
    r"\bnon\s+(?:ti\s+)?fermar(?:e|ti)\b|"
    r"\bcontinua\b|"
    r"\bscrivi\s+finch[eé]\b|"
    r"\bvai\s+avanti\b|"
    r"\bsenza\s+fermarti\b",
    re.IGNORECASE,
)


def wants_continuous(text: str) -> bool:
    """True se la persona chiede di continuare a scrivere senza fermarsi.

    Es. "non ti fermare di scrivermi finché…", "continua", "vai avanti".
    """
    return bool(_CONTINUE_RE.search(str(text or "")))


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def split_messages(text: str, *, max_chars: int = 950) -> list[str]:
    """Spezza un testo lungo in messaggi ≤ max_chars, ai confini di frase.

    Instagram accetta al massimo ~1000 caratteri per messaggio: qui si divide
    rispettando le frasi, e si tronca solo quando una singola frase sfora.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    parts = _SENTENCE_SPLIT.split(text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(part) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(part[i:i + max_chars] for i in range(0, len(part), max_chars))
            continue
        candidate = f"{current} {part}".strip() if current else part
        if len(candidate) > max_chars:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

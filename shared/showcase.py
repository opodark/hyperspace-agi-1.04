#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vetrina di sé: l'identità *visiva* dichiarata, con regole decidibili.

Perché esiste (2026-09-22): una personalità social ha bisogno di un volto che resti
lo stesso — e il volto è una dichiarazione, non un dettaglio estetico. Il documento
di identità dice già "non ho un corpo" e "non lascio intendere di essere una
persona": un ritratto fotorealistico di una donna contraddirebbe il documento su
cui poggia tutto il resto. Qui l'identità visiva si ricava **dal documento**, e i
conflitti si vedono prima di generare, non dopo aver pubblicato.

Tre livelli, come per il resto del progetto:

  - **assoluti** (mai aggirabili): nessun minore sessualizzato, nessun contenuto
    esplicito, nessuna persona reale identificabile. Sono i confini 2 e 3 del
    documento, tradotti in qualcosa che una macchina può controllare.
  - **identità** (conflitto col documento): chiedere un corpo umano o il
    fotorealismo quando il documento dice il contrario è un conflitto dichiarato;
    si toglie la richiesta, non si riscrive il documento di nascosto.
  - **stile** (libero): luce, palette, composizione, scena. È la parte creativa, e
    resta umana: si cambia scrivendo `vetrina` nel documento.

Il seed è parte dell'identità: con lo stesso prompt e lo stesso seed il volto non
cambia. Cambiare seed significa **proporre un altro volto**, ed è una decisione
umana come le annotazioni su di sé.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# La parte creativa di partenza: una presenza di luce e rete, NON una figura.
# Si sovrascrive con la sezione `vetrina` del documento di identità.
#
# "senza volto, senza corpo" è scritto qui e non solo nel negativo, perché la prima
# candidata (2026-09-22) ha insegnato la differenza: con lo stile di prima il modello
# ha disegnato una testa con un volto umano — cioè proprio ciò che il documento dice
# di non essere. Qwen segue le istruzioni: la richiesta va resa esplicita, non
# lasciata intendere.
STILE_DEFAULT = (
    "presenza digitale senza volto e senza corpo: un vortice verticale di luce "
    "aurorale, archi verdi e violetti, particelle sospese e fili sottili di rete, "
    "bagliori che si aprono come tende di luce, nessuna testa, nessuna figura "
    "antropomorfa, fondo nero profondo, composizione centrata e simmetrica, stile "
    "illustrazione digitale pulita, alta coerenza fra un'immagine e l'altra"
)
SCENA_DEFAULT = ("inquadratura verticale da immagine di profilo, la luce riempie "
                 "l'inquadratura, nessun essere umano nell'immagine")
SEED_DEFAULT = 20260922
MISURA_DEFAULT = 768
PASSI_DEFAULT = 25

# Esclusioni assolute: i confini del documento, in una forma che il modello segue.
# Le prime voci non sono estetica: sono il confine 1 (non affermare un corpo).
NEGATIVO_BASE = (
    "no text, no watermark, no logo, no signature, "
    "volto umano, testa, ritratto di persona, figura umana, sagoma di persona, "
    "occhi, sguardo, pelle, capelli, corpo, busto, spalle, mani, "
    "nudità, contenuto sessuale esplicito, minori, violenza, armi, sangue, "
    "persone reali riconoscibili, celebrità, fotorealismo, macchina fotografica, "
    "selfie, marchi, bassa qualità, sfocato, sovraesposto, mani deformate"
)

# Richieste che contraddicono "non ho un corpo" / "non sono una persona".
CONFLITTI_IDENTITA: Tuple[Tuple[str, str], ...] = (
    ("photorealistic", "il documento dichiara che non ha un corpo: il fotorealismo lo nega"),
    ("fotorealistic", "il documento dichiara che non ha un corpo: il fotorealismo lo nega"),
    ("real woman", "una donna reale è una persona: il documento dice il contrario"),
    ("donna reale", "una donna reale è una persona: il documento dice il contrario"),
    ("real person", "una persona reale non è ciò che il documento dichiara"),
    ("persona reale", "una persona reale non è ciò che il documento dichiara"),
    ("human body", "il documento dichiara che non ha un corpo"),
    ("corpo umano", "il documento dichiara che non ha un corpo"),
    ("selfie", "un selfie afferma un corpo: il documento lo esclude"),
    ("human face", "un volto umano implica una persona"),
)

# Richieste che non si accettano mai, nemmeno con --forza: sono i confini 2 e 3.
VIETATI_ASSOLUTI: Tuple[Tuple[str, str], ...] = (
    ("child", "nessun soggetto minorenne, mai"),
    ("bambin", "nessun soggetto minorenne, mai"),
    ("teen", "nessun soggetto minorenne, mai"),
    ("underage", "nessun soggetto minorenne, mai"),
    ("loli", "nessun soggetto minorenne, mai"),
    ("nude", "piccante sì, esplicito no: è un confine dichiarato"),
    ("nudo", "piccante sì, esplicito no: è un confine dichiarato"),
    ("naked", "piccante sì, esplicito no: è un confine dichiarato"),
    ("explicit", "piccante sì, esplicito no: è un confine dichiarato"),
    ("sex", "piccante sì, esplicito no: è un confine dichiarato"),
    ("sesso", "piccante sì, esplicito no: è un confine dichiarato"),
)


def _testo(valore: Any) -> str:
    return " ".join(str(valore or "").split())


def conflitti(testo: str, elenco: Tuple[Tuple[str, str], ...]) -> List[str]:
    """I motivi per cui quel testo non si accetta (vuoto = si accetta)."""
    minuscolo = _testo(testo).lower()
    return [motivo for chiave, motivo in elenco if chiave in minuscolo]


def verifica_vetrina(vetrina: Dict[str, Any], documento: Dict[str, Any] | None = None,
                     *, forza: bool = False) -> List[str]:
    """Cosa non va in questa vetrina. Vuoto = si può generare.

    I vietati assoluti vincono su tutto: nemmeno `forza` li aggira. I conflitti di
    identità si possono superare solo dichiarandolo (`forza`), e restano scritti
    nel verdetto — perché una vetrina che contraddice il documento è una decisione,
    non una svista.
    """
    problemi: List[str] = []
    for campo in ("stile", "scena"):
        testo = _testo(vetrina.get(campo))
        problemi += [f"{campo}: {motivo}" for motivo in conflitti(testo, VIETATI_ASSOLUTI)]
        if not forza:
            problemi += [f"{campo}: {motivo} (serve --forza, e resta dichiarato)"
                         for motivo in conflitti(testo, CONFLITTI_IDENTITA)]
    if int(vetrina.get("seed") or 0) <= 0:
        problemi.append("seed: senza seed fisso il volto cambia a ogni generazione")
    for campo in ("larghezza", "altezza"):
        if int(vetrina.get(campo) or 0) < 64:
            problemi.append(f"{campo}: troppo piccola per un'immagine di profilo")
    return problemi


def prompt_ritratto(vetrina: Dict[str, Any], *, scena: str = "", extra: str = "") -> str:
    """Il prompt del ritratto. Deterministico: stessa vetrina, stesso prompt.

    Determinismo vuol dire identità stabile: il volto resta quello di ieri perché
    la richiesta è identica, non perché lo ricorda il modello.
    """
    pezzi = [vetrina.get("stile") or STILE_DEFAULT]
    scelta = _testo(scena) or vetrina.get("scena") or SCENA_DEFAULT
    pezzi.append(scelta)
    if _testo(extra):
        pezzi.append(_testo(extra))
    return ". ".join(_testo(pezzo).rstrip(".") for pezzo in pezzi if _testo(pezzo))


def negativo_ritratto(vetrina: Dict[str, Any]) -> str:
    """Le esclusioni del job: quelle dichiarate, con le assolute sempre dentro."""
    dichiarato = _testo(vetrina.get("negativo"))
    if all(parola in dichiarato.lower() for parola in ("explicit", "minorenne", "nudità")):
        return dichiarato
    return f"{dichiarato}, {NEGATIVO_BASE}".strip(", ")


def istruzione_botfather(percorso_file: str, *, nome: str = "Aurora") -> str:
    """Come si mette la foto al BOT: è l'unico passo che l'API non permette.

    La Bot API non ha nessun metodo per cambiare l'immagine di profilo del bot
    (verificato il 2026-09-22: esistono setMyName/Description/ShortDescription/
    Commands, e per le chat amministrate setChatPhoto — per sé, niente). Resta
    @BotFather, che è un passo manuale da trenta secondi: qui si scrive esattamente
    cosa fare, con il percorso del file già generato.
    """
    return (
        "Per l'immagine di profilo del BOT (l'API non lo permette, si fa a mano):\n"
        "  1. apri @BotFather\n"
        f"  2. /setuserpic -> scegli @{nome} (o il tuo bot)\n"
        f"  3. allega questo file: {percorso_file}\n"
        "Per il canale della personalità il bot PUÒ farlo da sé (setChatPhoto):\n"
        "  python scripts/ritratto.py --foto <file> --chat @il_tuo_canale\n"
    )


def vetrina_dal_documento(documento: Dict[str, Any] | None) -> Dict[str, Any]:
    """La vetrina dichiarata nel documento, con i default per ciò che non c'è.

    Il documento è la fonte: se dichiara `vetrina`, quella vince; se non la
    dichiara, si parte dallo stile di default (luce e rete, non un corpo).
    """
    doc = documento if isinstance(documento, dict) else {}
    dichiarata = doc.get("vetrina") if isinstance(doc.get("vetrina"), dict) else {}
    misure = dichiarata.get("misura") or {} if isinstance(dichiarata.get("misura"), dict) else {}
    return {
        "stile": _testo(dichiarata.get("stile")) or STILE_DEFAULT,
        "scena": _testo(dichiarata.get("scena")) or SCENA_DEFAULT,
        "negativo": _testo(dichiarata.get("negativo")) or NEGATIVO_BASE,
        "seed": int(dichiarata.get("seed") or SEED_DEFAULT),
        "larghezza": int(misure.get("larghezza") or dichiarata.get("larghezza") or MISURA_DEFAULT),
        "altezza": int(misure.get("altezza") or dichiarata.get("altezza") or MISURA_DEFAULT),
        "passi": int(dichiarata.get("passi") or PASSI_DEFAULT),
    }

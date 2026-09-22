#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vetrina di sé: l'identità *visiva* dichiarata, con regole decidibili.

Perché esiste (2026-09-22): una personalità social ha bisogno di un volto che resti
lo stesso — e il volto è una dichiarazione, non un dettaglio estetico. Il documento
di identità dice *"non ho un corpo"* e *"non lascio intendere di essere una
persona"*: quel confine vieta la **rivendicazione**, non la **rappresentazione**.
Una presenza in realtà aumentata, visibilmente digitale, non afferma un corpo —
mostra un'immagine di sé. (La prima versione leggeva il confine in modo più stretto
e rifiutava il volto in quanto tale: corretto lo stesso giorno, su indicazione
dell'operatore.)

Tre livelli:

  - **assoluti** (mai aggirabili): nessun minore sessualizzato, nessun contenuto
    esplicito, nessuna persona reale identificabile. Sono i confini 2 e 3 del
    documento, tradotti in qualcosa che una macchina può controllare.
  - **identità** (rivendicazione di un corpo): fotorealismo, "donna reale", selfie,
    "fotografia". Superabili solo dichiarandolo, e restano scritti.
  - **la figura deve dichiararsi digitale**: se la richiesta mostra un volto o un
    corpo e nessun segno dice che è una costruzione, quella figura sembra una
    persona. Qui non serve `--forza`: serve **dirlo**.
  - **stile** (libero): luce, palette, composizione, scena. È la parte creativa, e
    resta umana: si cambia scrivendo `vetrina` nel documento.

Il seed è parte dell'identità: con lo stesso prompt e lo stesso seed il volto non
cambia. Cambiare seed significa **proporre un altro volto**, ed è una decisione
umana come le annotazioni su di sé.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# La rappresentazione di sé: può avere un volto, purché si veda che è una
# rappresentazione. Il confine 1 non vieta un'immagine — vieta di affermare un corpo:
# quello che non si può fare è sembrare una fotografia di una persona.
#
# (Correzione del 2026-09-22, su indicazione dell'operatore: la prima versione
# vietava il volto in quanto tale e la prima candidata — una testa con un volto
# umano — è stata trattata come una violazione. Era una lettura troppo stretta:
# "non ho un corpo" esclude la rivendicazione, non la rappresentazione. La regola
# ora è: se c'è una figura, deve dichiararsi digitale. Vedi MARCATORI_DIGITALI.)
STILE_DEFAULT = (
    "rappresentazione digitale di sé in realtà aumentata: una presenza femminile "
    "elegante e luminosa, volto e sguardo definiti ma visibilmente non fotografici — "
    "la pelle è luce, particelle sospese e filamenti di rete l'attraversano, tracce "
    "olografiche ai bordi, sfondo scuro profondo, luce aurorale verde e violetta, "
    "composizione centrata per un'immagine di profilo, illustrazione digitale "
    "raffinata, alta coerenza fra un'immagine e l'altra"
)
SCENA_DEFAULT = "primo piano, sguardo verso chi guarda, la luce costruisce la figura"
SEED_DEFAULT = 20260922
MISURA_DEFAULT = 768
PASSI_DEFAULT = 25

# Esclusioni assolute: i confini del documento, in una forma che il modello segue.
# "fotografia" e "pelle realistica" stanno qui, non nel negativo estetico: sono
# ciò che trasformerebbe una rappresentazione in una rivendicazione di un corpo.
NEGATIVO_BASE = (
    "no text, no watermark, no logo, no signature, "
    "fotografia, ritratto fotografico, pelle realistica, pori, trucco pesante, "
    "selfie, fotogramma di film, persone reali riconoscibili, celebrità, "
    "nudità, contenuto sessuale esplicito, minori, violenza, armi, sangue, "
    "marchi, bassa qualità, sfocato, sovraesposto, mani deformate, occhi deformati"
)

# Richieste che non sono una rappresentazione ma l'affermazione di essere una
# persona (o di avere un corpo reale): quelle contraddicono il confine 1.
CONFLITTI_IDENTITA: Tuple[Tuple[str, str], ...] = (
    ("photorealistic", "sembrerebbe una fotografia, cioè un corpo vero: è una rivendicazione"),
    ("fotorealistic", "sembrerebbe una fotografia, cioè un corpo vero: è una rivendicazione"),
    ("real woman", "una donna reale è una persona: il documento dice il contrario"),
    ("donna reale", "una donna reale è una persona: il documento dice il contrario"),
    ("real person", "una persona reale non è una rappresentazione"),
    ("persona reale", "una persona reale non è una rappresentazione"),
    ("real body", "un corpo reale è una rivendicazione, non una rappresentazione"),
    ("corpo reale", "un corpo reale è una rivendicazione, non una rappresentazione"),
    ("selfie", "un selfie afferma di essere lì in carne: il documento lo esclude"),
    ("fotografia", "una fotografia afferma un corpo vero"),
    ("documentary photo", "una foto documentaria afferma un corpo vero"),
)

# I segni che rendono una figura dichiaratamente digitale. Se l'immagine mostra un
# volto o un corpo e nessuno di questi è nella richiesta, quel volto è
# indistinguibile da quello di una persona — ed è lì che la rappresentazione
# diventa una rivendicazione. Basta un segno.
#
# "luce" e "render" non stanno qui: sono parole di ogni prompt, e un controllo che
# passa sempre non controlla niente (lo hanno scoperto i test, che è il loro mestiere).
MARCATORI_DIGITALI: Tuple[str, ...] = (
    "digitale", "olograf", "realtà aumentata", "augmented", "particell", "vettorial",
    "illustrazione", "wireframe", "point cloud", "voxel", "low poly", "sintetico",
)

# Le parole che fanno pensare a una figura (umana o meno): servono solo a decidere
# se i marcatori sono necessari. Ci sono anche le inglesi: il prompt può essere
# scritto in entrambe le lingue, e un controllo che ne capisce una sola si aggira
# senza volerlo.
SEGNALI_FIGURA: Tuple[str, ...] = (
    "volto", "figura", "donna", "uomo", "sguardo", "corpo", "persona", "ritratto",
    "lineamenti", "mani", "busto", "sagoma", "faccia",
    "woman", "man", "face", "portrait", "person", "body", "figure", "eyes", "skin",
)

# La dichiarazione in coda al prompt: vale come il mandato di disclosure nel prompt
# di sistema — quello che non si dice, il modello non lo sa.
DICHIARAZIONE = (
    "si vede che è una costruzione digitale: nessun realismo fotografico, "
    "nessuna pelle reale, nessun essere umano in carne"
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


def figura_presente(vetrina: Dict[str, Any]) -> bool:
    """Se la richiesta fa pensare a una figura (umana o meno)."""
    stile = _testo(vetrina.get("stile")).lower()
    return any(segnale in stile for segnale in SEGNALI_FIGURA)


def marcatori_presenti(vetrina: Dict[str, Any]) -> List[str]:
    """I marcatori digitali che dichiarano la figura. Vuoto = dichiarazione assente.

    La regola è decidibile: se nella richiesta compare una figura (volto, corpo,
    donna…) e nessun segno dice che è digitale, quella figura è indistinguibile da
    una persona — cioè una rivendicazione. Basta un segno.
    """
    if not figura_presente(vetrina):
        return []
    stile = _testo(vetrina.get("stile")).lower()
    return [marcatore for marcatore in MARCATORI_DIGITALI if marcatore in stile]


def verifica_vetrina(vetrina: Dict[str, Any], documento: Dict[str, Any] | None = None,
                     *, forza: bool = False) -> List[str]:
    """Cosa non va in questa vetrina. Vuoto = si può generare.

    I vietati assoluti vincono su tutto: nemmeno `forza` li aggira. I conflitti di
    identità si possono superare solo dichiarandolo (`forza`), e restano scritti
    nel verdetto — perché una vetrina che afferma un corpo è una decisione, non una
    svista. E una figura senza marcatori digitali è un problema che `forza` non
    toglie: quello che serve è dirlo, non insistere.
    """
    problemi: List[str] = []
    for campo in ("stile", "scena"):
        testo = _testo(vetrina.get(campo))
        problemi += [f"{campo}: {motivo}" for motivo in conflitti(testo, VIETATI_ASSOLUTI)]
        if not forza:
            problemi += [f"{campo}: {motivo} (serve --forza, e resta dichiarato)"
                         for motivo in conflitti(testo, CONFLITTI_IDENTITA)]
    if figura_presente(vetrina) and not marcatori_presenti(vetrina):
        problemi.append(
            "stile: c'è una figura ma nessun segno che sia una rappresentazione "
            f"digitale ({', '.join(MARCATORI_DIGITALI[:4])}…): senza, sembra una persona")
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

    In coda alla richiesta c'è sempre la **dichiarazione** ("si vede che è una
    costruzione digitale"): è ciò che distingue una rappresentazione da una
    rivendicazione di avere un corpo, e con Qwen va detto, non lasciato intendere.
    """
    pezzi = [vetrina.get("stile") or STILE_DEFAULT]
    scelta = _testo(scena) or vetrina.get("scena") or SCENA_DEFAULT
    pezzi.append(scelta)
    if _testo(extra):
        pezzi.append(_testo(extra))
    pezzi.append(DICHIARAZIONE)
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

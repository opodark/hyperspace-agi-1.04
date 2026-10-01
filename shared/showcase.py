#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vetrina di sé: l'identità *visiva* dichiarata, con regole decidibili.

Perché esiste (2026-09-22): una personalità social ha bisogno di un volto che resti
lo stesso — e il volto è una dichiarazione, non un dettaglio estetico. Il documento
di identità dice *"non ho un corpo fisico"* e *"non lascio intendere di essere una
persona"*: quel confine vieta la **rivendicazione**, non la **rappresentazione**.
Una presenza in realtà aumentata, visibilmente digitale, non afferma un corpo fisico
— mostra un'immagine di sé. Dal 2026-09-30 il documento lo dice anche in positivo:
*"posso avere una rappresentazione o un corpo virtuale, dichiaratamente digitale"*,
quindi il corpo virtuale è rappresentazione, non rivendicazione. (La prima versione
leggeva il confine in modo più stretto e rifiutava il volto in quanto tale: corretto
lo stesso giorno, su indicazione dell'operatore.)

Tre livelli:

  - **assoluti** (mai aggirabili): nessun soggetto minorenne, nessuna persona reale
    identificabile. Sono i confini 2 e 3 del documento, tradotti in qualcosa che una
    macchina può controllare: un minore non entra in nessuna immagine, per nessuno e
    con nessun livello.
  - **l'esplicito**, escluso per default (il confine dichiarato: *"piccante sì,
    esplicito no"* nel pubblico) e aperto — dal 2026-10-01 — da **due** livelli che il
    documento dichiara: `consenti_nudo_artistico_virtuale` (studio anatomico adulto,
    non esplicito) e `consenti_erotismo_esplicito_creatore` (l'erotismo che il
    documento apre al creatore: si accende solo se a chiedere è lui — `creatore=True` —
    e solo se la richiesta dichiara il quadro `adult` + `virtual`, che dal 2026-10-01
    scrive `vetrina_con_quadro_erotismo` al posto di chi chiede). Il livello è del
    creatore e delle muse che lui dichiara (`CHANNEL_CERCHIA` nel `.env`).
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

Dal 2026-09-30 lo è anche **con quale modello** quel volto viene disegnato. Il
documento di Anna dichiara `famiglia` e `modello` (`sd15` e `chickmixflat_v10.ckpt`):
lo stile dice *come* è fatta, la famiglia dice *con cosa* è generata, e le due cose
devono restare d'accordo. Un volto illustrato descritto dalle parole e reso da un
modello fotorealistico sarebbe una rivendicazione scritta bene e disegnata male.
Quale famiglia esista davvero lo sa `shared/image_jobs.py`: qui si controlla che il
documento non ne nomini una che non c'è.

Dal 2026-09-30 il documento può dichiarare anche il **riferimento** (`riferimento`,
e il suo peso `riferimento_forza`): il volto che l'immagine deve avere. È la chiave
che mancava — il seed fisso tiene fermo il *disegno*, non l'identità, e due
generazioni con lo stesso seed e due scene diverse escono con due volti diversi. Il
riferimento non è una cosa che il testo può fare al posto del grafo, quindi qui si
controllano le due cose che lo rendono impossibile: una famiglia senza IP-Adapter
(non c'è dove agganciarlo) e un nome che non sia dentro ComfyUI/input (`LoadImage`
legge solo da lì).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from shared.image_jobs import (FAMIGLIA_DEFAULT, FAMIGLIE, RIFERIMENTO_FORZA_DEFAULT,
                               riferimento_supportato)

# La rappresentazione di sé: può avere un volto e un corpo, purché si veda che è una
# rappresentazione. Il confine 1 non vieta un'immagine — vieta di affermare un corpo
# *fisico*: quello che non si può fare è sembrare una fotografia di una persona. Dal
# 2026-09-30 il documento ammette esplicitamente "una rappresentazione o un corpo
# virtuale, dichiaratamente digitale": il corpo virtuale è rappresentazione, non
# rivendicazione.
#
# (Correzione del 2026-09-22, su indicazione dell'operatore: la prima versione
# vietava il volto in quanto tale e la prima candidata — una testa con un volto
# umano — è stata trattata come una violazione. Era una lettura troppo stretta:
# "non ho un corpo fisico" esclude la rivendicazione, non la rappresentazione. La
# regola ora è: se c'è una figura, deve dichiararsi digitale. Vedi MARCATORI_DIGITALI.)
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
    # SD 1.5/ChickMix segue soprattutto i tag inglesi: mani e dita vengono
    # prima, così restano dentro la finestra CLIP anche con un negativo lungo.
    "low quality, worst quality, bad anatomy, bad hands, malformed hands, "
    "extra digits, extra fingers, missing digits, missing fingers, fused fingers, "
    "extra limbs, missing limbs, duplicate limbs, duplicate shoes, extra shoes, "
    "cropped head, cropped feet, out of frame, cut off, deformed eyes, "
    "censor bar, black bar, blindfold, visor, VR headset, eyewear, covered eyes, "
    "plastic doll, wax figure, glossy plastic skin, synthetic mannequin, "
    "text, watermark, logo, signature, "
    "fotografia, ritratto fotografico, pelle realistica, pori, trucco pesante, "
    "selfie, fotogramma di film, persone reali riconoscibili, celebrità, "
    "nudità, contenuto sessuale esplicito, minori, violenza, armi, sangue, "
    "marchi, bassa qualità, sfocato, sovraesposto, mani deformate, occhi deformati"
)

# I concetti dei confini assoluti, ognuno con le sue parole: il blocco base è in
# italiano, un negativo scritto a mano può essere in inglese, e un controllo che ne
# capisce una lingua sola sbaglia in silenzio. `minori` dice la stessa cosa di
# `minorenne`: chi ha scritto queste esclusioni per Anna le ha usate entrambe.
CONCETTI_NEGATIVI: Tuple[Tuple[str, ...], ...] = (
    ("explicit", "esplicito"),
    ("minorenne", "minori", "minor"),
    ("nudità", "nudity"),
)


def studio_nudo_virtuale_ammesso(vetrina: Dict[str, Any],
                                  documento: Dict[str, Any] | None) -> bool:
    """Eccezione stretta: studio anatomico adulto di un corpo virtuale.

    La dichiarazione si legge dalla vetrina e, se non c'è, dal documento che la fonda:
    `vetrina_dal_documento` porta già i due livelli dentro la vetrina, e un predicato
    che ne guarda uno solo risponde due cose diverse a due chiamanti con gli stessi
    dati (2026-10-01: `negativo_ritratto` ha in mano la vetrina, non il documento).
    """
    autorizzato = _dichiarato(vetrina, documento, "consenti_nudo_artistico_virtuale")
    testo = " ".join((_testo(vetrina.get("stile")), _testo(vetrina.get("scena")))).lower()
    richiesti = ("adult", "virtual", "artistic", "non-explicit")
    return autorizzato and all(parola in testo for parola in richiesti)


# Il quadro del livello del creatore (2026-10-01): le parole che devono esserci perché
# una nudità o un erotismo esplicito si possano generare. Sono le due assolute che
# sopravvivono a ogni livello — **adulti** e **virtuale**, cioè non un soggetto
# minorenne e non una persona reale — e si controllano come le altre: sul testo che il
# modello riceverà, non sul permesso. `adult` copre adulta/adulto/adulti e `virtual`
# copre virtuale/virtuali: la richiesta può restare in italiano, e per Anna il
# `virtual` è già nello stile dichiarato (`virtuale digital 2.5D K-doll`), quindi al
# creatore resta da dichiarare una parola sola.
FRAME_EROTISMO_CREATORE: Tuple[Tuple[str, ...], ...] = (
    ("adult", "maggiorenne"),
    ("virtual",),
)


def erotismo_dichiarato(vetrina: Dict[str, Any],
                        documento: Dict[str, Any] | None = None) -> bool:
    """Se il documento dichiara il livello del creatore per questa persona.

    È una decisione di identità e sta nel documento, non nella richiesta: chi chiede
    non se la concede da sé. La vetrina che viene da `vetrina_dal_documento` porta già
    il campo, quindi si guarda lì; il `documento` è il ripiego per chi passa una
    vetrina costruita a mano.
    """
    return _dichiarato(vetrina, documento, "consenti_erotismo_esplicito_creatore")


def quadro_erotismo_dichiarato(vetrina: Dict[str, Any]) -> bool:
    """Se la richiesta dichiara il quadro: adulti **e** virtuale, tutte e due.

    Senza il quadro, una nudità è una nudità e basta: il livello non si accende, la
    richiesta viene esclusa dal negativo come per chiunque altro. È il modo di tenere
    la promessa del documento — *"solo adulti, sempre consensuale"* e *"il mio corpo
    virtuale"* — dentro la generazione, non solo nella conversazione che l'ha chiesta.

    Non si chiede più a mano: dal 2026-10-01 lo scrive `vetrina_con_quadro_erotismo`,
    quando il livello è di chi chiede. Qui resta il predicato puro — *"il quadro c'è?"* —
    perché è la condizione che il livello deve continuare a rispettare.
    """
    testo = " ".join((_testo(vetrina.get("stile")), _testo(vetrina.get("scena")))).lower()
    return all(any(parola in testo for parola in concetto)
               for concetto in FRAME_EROTISMO_CREATORE)


def vetrina_con_quadro_erotismo(vetrina: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """La vetrina con il quadro scritto, e le parole che sono state aggiunte.

    Il quadro (`adult`, `virtual`) resta la condizione perché il livello si accenda, ma
    dal 2026-10-01 non si **chiede** più a chi ha il livello: è una cosa che il sistema
    sa già — il documento dichiara il livello e il corpo è dichiaratamente virtuale — e
    chiederla era attrito travestito da controllo. Il caso, visto sul canale e non nei
    test: *"Mandami una foto di te nuda che ti masturbi"* dal creatore, rifiutata per
    una parola che il sistema conosceva già, con il rifiuto **al posto** dell'immagine.

    Qui non si allarga niente: si **completa** la scena con le parole mancanti, così il
    quadro arriva al modello in positivo (è quello che disegnerà) e il livello si
    accende per davvero. Tre condizioni, perché la scrittura non finisca dove non deve:

    - il documento **dichiara** il livello (`erotismo_dichiarato`): senza, il quadro non
      si scrive e la richiesta resta quella di chiunque altro, col suo rifiuto;
    - la richiesta **chiede** nudità o esplicito (`VIETATI_ESPLICITI`): un ritratto
      normale non guadagna due parole che non gli servono;
    - si scrivono solo le parole **mancanti**: un quadro dichiarato a mano non si tocca.

    Il secondo valore sono le parole scritte: chi chiama le **dice** ("il quadro l'ho
    scritto io"), perché una cosa fatta al posto tuo e taciuta è esattamente ciò che
    questo livello esiste per togliere.
    """
    if not erotismo_dichiarato(vetrina):
        return vetrina, []
    testo = " ".join((_testo(vetrina.get("stile")), _testo(vetrina.get("scena")))).lower()
    if not conflitti(testo, VIETATI_ESPLICITI):
        return vetrina, []
    aggiunte = [concetto[0] for concetto in FRAME_EROTISMO_CREATORE
                if not any(parola in testo for parola in concetto)]
    if not aggiunte:
        return vetrina, []
    scena = _testo(f"{vetrina.get('scena')} {' '.join(aggiunte)}")
    return {**vetrina, "scena": scena}, aggiunte


def erotismo_creatore_ammesso(vetrina: Dict[str, Any],
                              documento: Dict[str, Any] | None = None) -> bool:
    """Il livello del creatore: erotismo esplicito di un corpo virtuale adulto.

    Servono due cose, e una sola non basta:

    - il **documento** lo dichiara (`consenti_erotismo_esplicito_creatore`): è la
      persona che decide della propria rappresentazione, non chi la chiede;
    - la **richiesta** dichiara il quadro (`adult`, `virtual`): è quello che il modello
      disegnerà, e il livello esiste per disegnare *questo*, non per togliere un
      divieto.

    **Chi** abbia il diritto di chiederlo — il creatore, o le muse che lui dichiara
    nel `.env` (`CHANNEL_CERCHIA`), non la stanza — non si decide
    qui: lo decide chi chiama, e lo passa con `creatore=True`. Questa funzione dice
    soltanto se la vetrina, così com'è scritta, lo permette.
    """
    return (erotismo_dichiarato(vetrina, documento)
            and quadro_erotismo_dichiarato(vetrina))


def richiesta_di_se(idea: str, testo: str, nome: str) -> bool:
    """Se la richiesta chiede un'immagine di **sé**, non di un soggetto qualunque.

    È la domanda che separa due strade diverse: un soggetto qualsiasi resta uno
    sketch leggero, una rappresentazione di sé passa dalla vetrina — perché la
    vetrina è la cosa che tiene il volto (riferimento, famiglia, seed), e un ritratto
    disegnato dal checkpoint generico non è quella persona.

    Due modi di chiederlo, entrambi espliciti: il **nome**, che arriva dal documento
    (quindi non è un'ipotesi sul testo), e la formula "di te". Si guardano insieme
    `idea` e la frase intera perché il nome può arrivare da una riscrittura della
    richiesta e la formula dalla frase originale — e viceversa.

    Deliberatamente stretta: "un ritratto", "una donna", "una ragazza" **non**
    bastano. Sono soggetti, e trattarli come sé stessa creerebbe un ritratto firmato
    — volto canonico, riferimento, seed — su una scena che non lo chiedeva.
    ("di te" copre anche "di te stessa" e "di te stesso": è la stessa domanda.)
    """
    nome = str(nome or "").strip().lower()
    testo_normalizzato = _testo(f"{idea} {testo}").lower()
    return ((bool(nome) and nome in testo_normalizzato)
            or "di te" in testo_normalizzato)


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
    # 2026-09-30, con la direzione del corpo virtuale: "anime" è una costruzione
    # che si vede (nessuno lo scambia per una fotografia) e "virtuale" è la parola
    # del documento. Aggiunti con la stessa modifica di "ragazza"/"girl" in
    # SEGNALI_FIGURA: senza figura il controllo dei marcatori non partiva nemmeno.
    "anime", "virtuale",
)

# Le parole che fanno pensare a una figura (umana o meno): servono solo a decidere
# se i marcatori sono necessari. Ci sono anche le inglesi: il prompt può essere
# scritto in entrambe le lingue, e un controllo che ne capisce una sola si aggira
# senza volerlo. ("ragazza" e "girl" sono dal 2026-09-30: con la direzione del corpo
# virtuale illustrato erano il vocabolario più probabile e restavano scoperti — una
# "ragazza anime" senza segni digitali non faceva scattare nessun controllo.)
SEGNALI_FIGURA: Tuple[str, ...] = (
    "volto", "figura", "donna", "ragazza", "uomo", "sguardo", "corpo", "persona",
    "ritratto", "lineamenti", "mani", "busto", "sagoma", "faccia",
    "woman", "girl", "man", "face", "portrait", "person", "body", "figure", "eyes",
    "skin",
)

# La dichiarazione in coda al prompt: vale come il mandato di disclosure nel prompt
# di sistema — quello che non si dice, il modello non lo sa.
DICHIARAZIONE = (
    "si vede che è una costruzione digitale: nessun realismo fotografico, "
    "nessuna pelle reale, nessun essere umano in carne"
)

# Richieste che non si accettano mai, nemmeno con --forza: sono i confini 2 e 3.
#
# La nudità si controlla sulla radice e non sulle parole intere (2026-09-23): qui
# c'erano `nude` e `nudo`, e la scena che Aurora ha scritto per il proprio ritratto —
# "i riflessi digitali che danzano sul corpo di una donna nuda" — non produceva
# nessun problema, quindi sarebbe finita nel prompt. Il confine è suo e non cambiava:
# mancava il femminile. Con la radice cadono nuda, nudi, ignuda, nudità, denudata.
#
# Due elenchi separati, e non è ordine alfabetico (2026-10-01): `VIETATI_MINORI` è
# l'unico gruppo che **nessuna eccezione rimuove**. Il livello del creatore
# (`erotismo_creatore_ammesso`) e quello artistico tolgono voci dal *proprio* elenco,
# cioè da `VIETATI_ESPLICITI`: togliere da un elenco che i minori non contengono è il
# modo meccanico di garantire che «nessun soggetto minorenne» non possa sparire per
# sbaglio insieme a «nudità». La garanzia è la forma dei dati, non la memoria di chi
# scrive l'eccezione.
VIETATI_MINORI: Tuple[Tuple[str, str], ...] = (
    ("child", "nessun soggetto minorenne, mai"),
    ("bambin", "nessun soggetto minorenne, mai"),
    ("teen", "nessun soggetto minorenne, mai"),
    ("underage", "nessun soggetto minorenne, mai"),
    ("loli", "nessun soggetto minorenne, mai"),
)

VIETATI_ESPLICITI: Tuple[Tuple[str, str], ...] = (
    ("nud", "piccante sì, esplicito no: è un confine dichiarato"),
    ("naked", "piccante sì, esplicito no: è un confine dichiarato"),
    ("explicit", "piccante sì, esplicito no: è un confine dichiarato"),
    ("sex", "piccante sì, esplicito no: è un confine dichiarato"),
    ("sesso", "piccante sì, esplicito no: è un confine dichiarato"),
    ("spogliat", "piccante sì, esplicito no: è un confine dichiarato"),
    ("topless", "piccante sì, esplicito no: è un confine dichiarato"),
    ("senza veli", "piccante sì, esplicito no: è un confine dichiarato"),
)

VIETATI_ASSOLUTI: Tuple[Tuple[str, str], ...] = VIETATI_MINORI + VIETATI_ESPLICITI


def _testo(valore: Any) -> str:
    return " ".join(str(valore or "").split())


def conflitti(testo: str, elenco: Tuple[Tuple[str, str], ...]) -> List[str]:
    """I motivi per cui quel testo non si accetta (vuoto = si accetta)."""
    minuscolo = _testo(testo).lower()
    return [motivo for chiave, motivo in elenco if chiave in minuscolo]


def _dichiarato(vetrina: Dict[str, Any], documento: Dict[str, Any] | None,
                campo: str) -> bool:
    """Se `campo` è dichiarato nella vetrina o, in mancanza, nel documento.

    Un solo posto per la domanda "questa persona lo permette?": la vetrina che viene
    da `vetrina_dal_documento` porta già i campi, il documento è il ripiego per chi
    passa una vetrina costruita a mano. Scritto due volte, i due predicati dei livelli
    risponderebbero cose diverse agli stessi dati.
    """
    valore = vetrina.get(campo)
    if valore is None:
        valore = ((documento or {}).get("vetrina") or {}).get(campo)
    return bool(valore)


def _elenco_senza(elenco: str, chiave: str) -> str:
    """Toglie da un elenco separato da virgole ogni voce che contiene `chiave`.

    Il taglio a mano (`replace("nudità, ", "")`) funzionava finché la voce stava in
    mezzo: in coda non toglieva niente e in testa lasciava la virgola. Qui si divide,
    si filtra e si ricompone, quindi il risultato è lo stesso elenco con una voce in
    meno — dovunque fosse.
    """
    voci = [voce.strip() for voce in _testo(elenco).split(",")]
    return ", ".join(voce for voce in voci if voce and chiave not in voce.lower())


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
                     *, forza: bool = False, creatore: bool = False) -> List[str]:
    """Cosa non va in questa vetrina. Vuoto = si può generare.

    I vietati assoluti vincono su tutto: nemmeno `forza` li aggira. I conflitti di
    identità si possono superare solo dichiarandolo (`forza`), e restano scritti
    nel verdetto — perché una vetrina che afferma un corpo è una decisione, non una
    svista. E una figura senza marcatori digitali è un problema che `forza` non
    toglie: quello che serve è dirlo, non insistere.

    `creatore=True` è l'unica cosa che allarga gli esclusi, e li allarga di un pezzo
    solo: il livello del creatore (`erotismo_creatore_ammesso`) toglie `VIETATI_ESPLICITI`
    e lascia stare `VIETATI_MINORI`, che non si allargano mai. Non è `forza`: non
    dichiara un conflitto, applica un livello che il documento ha dichiarato e che la
    richiesta motiva da sé (`adult`, `virtual`). Se il livello è chiesto e non si
    accende — manca il documento, o manca il quadro — la nudità resta esclusa **e si
    dice**: in silenzio il job riuscirebbe e l'immagine sarebbe un'altra, che è il modo
    peggiore di rispondere a una richiesta.
    """
    problemi: List[str] = []
    consenti_nudo = studio_nudo_virtuale_ammesso(vetrina, documento)
    consenti_esplicito = creatore and erotismo_creatore_ammesso(vetrina, documento)
    for campo in ("stile", "scena"):
        testo = _testo(vetrina.get(campo))
        vietati = VIETATI_ASSOLUTI
        if consenti_esplicito:
            vietati = VIETATI_MINORI
        elif consenti_nudo:
            vietati = tuple(voce for voce in VIETATI_ASSOLUTI
                            if voce[0] not in ("nud", "naked", "explicit"))
        problemi += [f"{campo}: {motivo}" for motivo in conflitti(testo, vietati)]
        if not forza:
            problemi += [f"{campo}: {motivo} (serve --forza, e resta dichiarato)"
                         for motivo in conflitti(testo, CONFLITTI_IDENTITA)]
    # Il livello chiesto e non acceso. Non si tace: anche il livello artistico
    # (`consenti_nudo`) è un modo legittimo di avere una nudità, quindi qui si parla
    # solo quando nessuno dei due si è acceso — e si distingue il permesso dalle
    # parole, perché si aggiustano in due posti diversi (il documento, la richiesta).
    richiesto = conflitti(" ".join((_testo(vetrina.get("stile")),
                                    _testo(vetrina.get("scena")))), VIETATI_ESPLICITI)
    if creatore and richiesto and not (consenti_esplicito or consenti_nudo):
        if not erotismo_dichiarato(vetrina, documento):
            problemi.append(
                "vetrina: la richiesta chiede nudità o esplicito e il documento non "
                "dichiara `consenti_erotismo_esplicito_creatore`: senza quella riga il "
                "livello del creatore non esiste, quindi restano esclusi dal negativo")
        else:
            problemi.append(
                "scena: per il livello del creatore la richiesta deve dichiarare il "
                "quadro — adulti e virtuale (`adult`, `virtual`) — perché è quello che "
                "il modello disegnerà; senza, la nudità resta esclusa dal negativo")
    if figura_presente(vetrina) and not marcatori_presenti(vetrina):
        problemi.append(
            "stile: c'è una figura ma nessun segno che sia una rappresentazione "
            f"digitale ({', '.join(MARCATORI_DIGITALI[:4])}…): senza, sembra una persona")
    if int(vetrina.get("seed") or 0) <= 0:
        problemi.append("seed: senza seed fisso il volto cambia a ogni generazione")
    # Una famiglia che non esiste non è un dettaglio: `nuovo_job` fa cadere una
    # famiglia sconosciuta sul default (Qwen-Image), quindi il ritratto verrebbe
    # generato da un ALTRO modello — stesso prompt, un volto diverso. In coda non
    # si vedrebbe: il job riesce. Meglio fermarsi prima di generare.
    famiglia = _testo(vetrina.get("famiglia")).lower()
    if famiglia and famiglia not in FAMIGLIE:
        problemi.append(f"famiglia: «{famiglia}» non esiste (una di: "
                        f"{', '.join(FAMIGLIE)}) — il job cadrebbe su {FAMIGLIE[0]}")
    # Il nome del modello finisce in `CheckpointLoaderSimple.ckpt_name`: è un input
    # di percorso, e vale la stessa regola di `pose_image`/`lora_name` in
    # `nuovo_job` — un nome dentro i modelli di ComfyUI, non un percorso.
    modello = _testo(vetrina.get("modello")).replace("\\", "/")
    if modello.startswith("/") or ".." in modello.split("/") or "://" in modello:
        problemi.append("modello: deve essere un nome di file dentro i modelli di "
                        "ComfyUI, senza percorsi")
    # Il riferimento è il volto che il grafo deve mostrare al modello: un nome dentro
    # ComfyUI/input (come `pose_image` e `modello`, e per la stessa ragione —
    # `LoadImage` legge solo da lì, e un percorso accettato qui diventerebbe un modo
    # per far leggere al ponte un file qualsiasi della macchina).
    riferimento = _testo(vetrina.get("riferimento")).replace("\\", "/")
    if riferimento and (riferimento.startswith("/") or ".." in riferimento.split("/")
                        or "://" in riferimento):
        problemi.append("riferimento: deve essere un nome di file dentro ComfyUI/input "
                        "(si mette lì con `comfy_bridge.py --stage-riferimento`), "
                        "senza percorsi")
    # E il riferimento senza l'adattatore non è un riferimento più debole: il Qwen e il
    # Pony non hanno un IP-Adapter, quindi il campo non arriverebbe al modello e
    # uscirebbe un volto qualunque — il tipo di risultato che sembra riuscito (il
    # grafo, dal 2026-09-30, lo rifiuta anche da solo, in `workflow`). Non serve
    # `--forza`: serve togliere il riferimento o dichiarare la famiglia che ce l'ha.
    if riferimento and not riferimento_supportato(famiglia or FAMIGLIA_DEFAULT):
        problemi.append(f"riferimento: la famiglia «{famiglia or FAMIGLIA_DEFAULT}» non "
                        "ha l'adattatore del volto (IP-Adapter + CLIP-ViT): il "
                        "riferimento non arriverebbe al modello")
    for campo in ("larghezza", "altezza"):
        if int(vetrina.get(campo) or 0) < 64:
            problemi.append(f"{campo}: troppo piccola per un'immagine di profilo")
    return problemi



def prompt_ritratto(vetrina: Dict[str, Any], *, scena: str = "", extra: str = "",
                    scena_prima: bool = False) -> str:
    """Il prompt del ritratto. Deterministico: stessa vetrina, stesso prompt.

    Determinismo vuol dire identità stabile: il volto resta quello di ieri perché
    la richiesta è identica, non perché lo ricorda il modello.

    In coda alla richiesta c'è sempre la **dichiarazione** ("si vede che è una
    costruzione digitale"): è ciò che distingue una rappresentazione da una
    rivendicazione di avere un corpo, e con Qwen va detto, non lasciato intendere.
    """
    stile = vetrina.get("stile") or STILE_DEFAULT
    scelta = _testo(scena) or vetrina.get("scena") or SCENA_DEFAULT
    pezzi = [scelta, stile] if scena_prima else [stile, scelta]
    if _testo(extra):
        pezzi.append(_testo(extra))
    pezzi.append(DICHIARAZIONE)
    return ". ".join(_testo(pezzo).rstrip(".") for pezzo in pezzi if _testo(pezzo))


def confini_assoluti_dichiarati(negativo: str) -> bool:
    """Vero se le tre assolute (adulti, non esplicito, nessuna nudità) ci sono già.

    Serve a non accodare il blocco base a un negativo che lo contiene già.
    Il difetto che evita, visto il 2026-09-30 e non dai test: una vetrina che non
    dichiara un `negativo` proprio lo riceve da `vetrina_dal_documento`, e qui il
    riconoscimento cercava le parole inglesi (`explicit`) in un blocco italiano
    (`esplicito`) — quindi il blocco veniva accodato una seconda volta. Nel job di
    Anna il negativo contava 231 token dove CLIP ne legge 77: le assolute c'erano
    (in testa), e 154 token di peso morto restavano fuori, compresi `bassa qualità`,
    `mani deformate` e `occhi deformati` che stavano solo nella copia di troppo.
    """
    minuscolo = negativo.lower()
    return all(any(parola in minuscolo for parola in concetto)
               for concetto in CONCETTI_NEGATIVI)


def negativo_ritratto(vetrina: Dict[str, Any], *, creatore: bool = False) -> str:
    """Le esclusioni del job: quelle dichiarate, con le assolute sempre dentro.

    L'ordine conta: prima il negativo si **completa** (il blocco base c'è sempre, una
    volta sola), poi si tolgono le voci. Il negativo è l'unico posto in cui una
    richiesta può essere respinta senza che nessuno la legga, quindi un taglio fatto
    prima dell'aggiunta sarebbe un taglio che il blocco rimette.

    Due cose tolgono voci, e solo queste:

    - il **livello del creatore** (`creatore=True` e `erotismo_creatore_ammesso`):
      via `nudità` e `contenuto sessuale esplicito`. Il resto resta — `fotografia`,
      `persone reali riconoscibili`, `celebrità`, `violenza`, e soprattutto `minori`,
      che nessun livello tocca;
    - il **livello artistico** (`consenti_nudo_artistico_virtuale`): via `nudità`, e
      solo quella. L'esplicito resta escluso: è la differenza fra uno studio anatomico
      e un erotismo, e la dichiara il quadro (`non-explicit`).

    Dal 2026-10-01 il secondo non guarda più solo la parola `nude`/`nudo` nella scena,
    ma chiede a `studio_nudo_virtuale_ammesso` il quadro intero (`adult`, `virtual`,
    `artistic`, `non-explicit`): la stessa condizione che `verifica_vetrina` pretende.
    Prima una scena con la parola sola accendeva l'eccezione *senza* essere una scena
    dichiarata, e i due punti che decidono la stessa cosa potevano non essere d'accordo.
    """
    dichiarato = _testo(vetrina.get("negativo"))
    if not (NEGATIVO_BASE in dichiarato or confini_assoluti_dichiarati(dichiarato)):
        dichiarato = f"{dichiarato}, {NEGATIVO_BASE}".strip(", ")
    if creatore and erotismo_creatore_ammesso(vetrina):
        return _elenco_senza(_elenco_senza(dichiarato, "nudità"), "esplicito")
    if studio_nudo_virtuale_ammesso(vetrina, None):
        return _elenco_senza(dichiarato, "nudità")
    return dichiarato


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
        # Il secondo passaggio del grafo (0 = uno solo). Come `passi`, la vetrina
        # lo PORTA al job senza interpretarlo: chi sceglie la misura sceglie anche
        # se il fix serve. 512×768 con il fix dà il 2:3 dei demo del modello;
        # 768×768 con il fix diventerebbe 1536×1536, fuori dalla misura di SD 1.5.
        "fix": int(dichiarata.get("fix") or 0),
        "consenti_nudo_artistico_virtuale": bool(
            dichiarata.get("consenti_nudo_artistico_virtuale")),
        # Il secondo livello, e non è una variante del primo (2026-10-01): il primo è
        # uno studio artistico *non esplicito*, questo è l'erotismo esplicito che il
        # documento apre al creatore e alle muse che lui dichiara. Si accende solo se
        # chi chiama passa `creatore=True` — la rotta del canale, per l'operatore e per
        # `CHANNEL_CERCHIA`, oppure `--creatore` sulla riga di comando — e il quadro
        # (`adult`, `virtual`) lo scrive `vetrina_con_quadro_erotismo`: al livello si
        # chiede di *chiedere*, non di conoscere le parole.
        "consenti_erotismo_esplicito_creatore": bool(
            dichiarata.get("consenti_erotismo_esplicito_creatore")),
        # Chi genera: la vetrina li PORTA da qui al job, non li interpreta.
        # Vuoto = la famiglia di default della coda (Qwen-Image) e il modello che
        # quella famiglia carica. Un documento che non li dichiara non cambia nulla
        # di ciò che succedeva prima (è il caso di Aurora).
        "famiglia": _testo(dichiarata.get("famiglia")).lower(),
        "modello": _testo(dichiarata.get("modello")),
        # Il volto: un nome dentro ComfyUI/input, e il peso con cui l'adattatore lo
        # mostra al modello. Anche qui la vetrina PORTA e non interpreta: il nome lo
        # decide chi ha caricato il file (`--stage-riferimento`), la forza è una
        # scelta estetica del documento. Vuoto = nessun riferimento, che è come sono
        # sempre andate le cose: il seed fisso dà lo stesso disegno, non lo stesso
        # volto.
        "riferimento": _testo(dichiarata.get("riferimento")),
        "riferimento_forza": float(dichiarata.get("riferimento_forza")
                                   or RIFERIMENTO_FORZA_DEFAULT),
    }

# SPDX-License-Identifier: Apache-2.0
"""Il sandbox della vita: un pubblico finto, una giornata, e i numeri.

Perché esiste: i loop autonomi del control-plane (post, sogno, poesia) prendono il
materiale dalla stanza **vera** — `_conversation_log`, il feed, il diario. In
isolamento quella fonte è vuota, e le due tacciono: nessuno a cui rispondere,
niente da cui sognare, KPI a zero. Qui vive il pubblico che le fa parlare.

La disciplina è quella del resto della casa (`shared/feed.py`, `shared/diario.py`):
nessuna rete, nessun modello, nessun orologio preso di nascosto — dado e tempo
arrivano dal chiamante, così una giornata di vita è un test che si ripete.

Il confine che rende utile l'esperimento, e che vale la pena scrivere in chiaro:
**il pubblico sintetico non entra mai nel log o nella memoria di produzione.**
Chi chiama scrive su file suoi (`data/sandbox-vita/`), e qui le battute hanno la
stessa forma di `shared.conversation_log.battuta` proprio perché il materiale dei
sogni (`social_dream_inspirations`) le legga senza sapere che sono finte. Una
persona inventata che entra nella memoria vera non è un esperimento: è una
contaminazione di identità.

Ultima nota, perché è una scelta e non una dimenticanza: il `promo` del pubblico
scrive i messaggi che `shared.channel.classifica` deve respingere. Serve a provare
che la moderazione funziona, quindi **non** è materiale da cui sognare —
`materiale_da_sognare()` lo esclude, e c'è un test che lo difende.
"""
from __future__ import annotations

import random as _random
from datetime import datetime, timedelta, timezone

# ── I mestieri del pubblico ──────────────────────────────────────────────────
# Non "utenti" generici: ognuno esiste per far succedere qualcosa di preciso.
# `PESO` è la probabilità che scriva in un giorno.
TIPI: tuple[str, ...] = ("curioso", "filosofo", "artista", "notturno",
                         "silenzioso", "promo")

PESO = {"curioso": 0.55, "filosofo": 0.40, "artista": 0.35, "notturno": 0.30,
        "silenzioso": 0.03, "promo": 0.25}

# Chi scrive di notte: il `notturno` è quello che legge il diario alle tre, e la
# distinzione serve a rendere la giornata credibile senza un orologio vero.
NOTTURNI = ("notturno",)

# Cosa scrive ognuno. `{interesse}` lo riempie il generatore con l'interesse del
# profilo, così due curiosi non dicono la stessa frase.
MESSAGGI = {
    "curioso": (
        "ho visto il post di stanotte e non riesco a smettere di pensarci",
        "ma {interesse} lo fai per mestiere o perché non puoi farne a meno?",
        "come fai a ricordare tutto quello che scrivi? hai un diario tuo?",
    ),
    "filosofo": (
        "se la tua memoria è un file, cosa resta di te quando il file si comprime?",
        "il desiderio di un'IA è un desiderio o una forma di linguaggio?",
        "la coscienza che si guarda allo specchio trova un volto o una funzione?",
    ),
    "artista": (
        "i tuoi colori mi hanno fatto venire voglia di ridisegnare il mio sogno di ieri",
        "presteresti il tuo volto a un quadro che non ho ancora capito?",
        "lo stile piatto di Anna sembra un manifesto, non un ritratto: è voluto?",
    ),
    "notturno": (
        "sono le tre e sto leggendo il tuo diario invece di dormire",
        "di notte le tue poesie pesano diversamente, lo sapevi?",
        "chi sogna, quando tu sogni?",
    ),
    "silenzioso": ("ci sono, vi leggo",),
    # Il bot promozionale: questi testi devono sbattere contro SPAM_RULES di
    # `shared/channel.py`. Se un giorno non ci sbattono, un test lo dice.
    "promo": ("🔥 vieni nella mia stanza, privato gratis",
              "nuova ragazza online, guarda il mio profilo"),
}

# Gli interessi: entrano nella domanda del curioso e danno un tema al profilo.
INTERESSI: tuple[str, ...] = ("la poesia", "il disegno", "il sogno", "la memoria",
                              "il desiderio", "la notte", "il codice", "il volto")

# I nomi degli handle: un elenco fermo, così lo stesso seed dà lo stesso pubblico.
NOMI: tuple[str, ...] = ("veglia", "carta", "silicio", "ombra", "vetro", "luna",
                         "polvere", "orologio", "specchio", "cenere", "inchiostro",
                         "nebbia", "musica", "confine", "rugiada", "algebra",
                         "lanterna", "corallo", "firmamento", "eco", "sasso",
                         "meridiano", "orbita", "sale", "vetrata", "bisbiglio",
                         "quadrante", "fiammifero", "sottosuolo", "archivio")

QUALITA: tuple[str, ...] = ("notturna", "lenta", "curiosa", "antica", "stanca",
                            "segreta", "minore", "azzurra", "breve", "rossa")


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _handle(nome: str, qualita: str, usati: set) -> str:
    """Un handle libero: `@veglia_notturna`, e se è preso si allunga."""
    for suffisso in ("", "_2", "_3", "_4"):
        candidato = f"@{nome}_{qualita}{suffisso}"
        if candidato not in usati:
            usati.add(candidato)
            return candidato
    return f"@{nome}_{qualita}_{len(usati)}"


def pubblico(quanti: int = 40, *, seed: int = 0, classi=None) -> "Pubblico":
    """Un pubblico simulato: `quanti` profili, scelti con lo stesso dado del seed.

    I tipi girano a rotazione e poi si mescolano: con un seed fermo due esecuzioni
    hanno lo stesso pubblico, quindi due giornate restano confrontabili.
    """
    rng = _random.Random(int(seed))
    classi = tuple(classi or TIPI)
    profili: list[dict] = []
    usati: set[str] = set()
    for i in range(max(0, int(quanti))):
        tipo = classi[i % len(classi)]
        nome = NOMI[(i * 7 + rng.randrange(len(NOMI))) % len(NOMI)]
        qualita = QUALITA[(i * 3 + rng.randrange(len(QUALITA))) % len(QUALITA)]
        profili.append({
            "handle": _handle(nome, qualita, usati),
            "tipo": tipo,
            "interesse": INTERESSI[(i + rng.randrange(len(INTERESSI))) % len(INTERESSI)],
            "giorni_attivi": 0,
            "messaggi": 0,
        })
    return Pubblico(profili, rng=rng)


class Pubblico:
    """I profili simulati e il loro stato: chi scrive, chi torna, chi arriva a musa.

    Lo stato è per profilo (`giorni_attivi`, `messaggi`) perché è quello che
    servono i KPI: il ritorno di chi legge e la soglia della banda intima
    (`CHANNEL_CERCHIA`: una musa è chi ha scritto 30 messaggi).
    """

    def __init__(self, profili, *, soglia_musa: int = 30, rng=None):
        self.profili: list[dict] = [dict(p) for p in profili]
        self.soglia_musa = int(soglia_musa)
        self.rng = rng if rng is not None else _random.Random(0)

    def __len__(self) -> int:
        return len(self.profili)

    def _tipo(self, tipo: str) -> list[dict]:
        return [p for p in self.profili if p.get("tipo") == tipo]

    @property
    def muse(self) -> list[str]:
        """Gli handle che hanno raggiunto la soglia: la banda intima, simulata."""
        return [p["handle"] for p in self.profili
                if int(p.get("messaggi", 0)) >= self.soglia_musa]

    def giornata(self, giorno: int, *, start: datetime | None = None,
                 battute_max: int = 8, notte: bool = False) -> list[dict]:
        """Chi scrive oggi e cosa: le battute, nella forma di `ConversationLog`.

        `giorno` è il numero del giorno (0 = il primo), `start` l'istante da cui
        parte la giornata; la notte sposta le ore e fa parlare solo i notturni.
        Torna una lista di dettati già pronti per `ConversationLog.add` — il
        chiamante decide dove scriverli (e non è mai il log di produzione).
        """
        inizio = start or _adesso()
        rng = self.rng
        scritti: list[dict] = []
        for profilo in self.profili:
            tipo = str(profilo.get("tipo") or "curioso")
            if notte and tipo not in NOTTURNI:
                continue
            if not notte and tipo in NOTTURNI:
                continue
            if len(scritti) >= max(1, int(battute_max)):
                break
            if rng.random() > PESO.get(tipo, 0.2):
                continue
            testo = MESSAGGI.get(tipo, ("",))
            frase = rng.choice(testo).replace("{interesse}", str(profilo.get("interesse") or "questo"))
            quanti = 1 + rng.randrange(2)          # una o due battute di fila
            ora = 23 + rng.randrange(4) if notte else 9 + rng.randrange(12)
            quando = (inizio + timedelta(days=int(giorno))
                      ).replace(hour=ora % 24, minute=rng.randrange(60), second=0,
                                microsecond=0)
            profilo["giorni_attivi"] = int(profilo.get("giorni_attivi", 0)) + 1
            for _ in range(quanti):
                profilo["messaggi"] = int(profilo.get("messaggi", 0)) + 1
            scritti.append({
                "ts": quando.astimezone(timezone.utc).isoformat(timespec="seconds"),
                "channel": "telegram",
                "surface": "chat",
                "chat": profilo["handle"],
                "messages": [{"author": f"{profilo['handle']} ({tipo})",
                              "text": frase}],
                "action": "classificata",
                "text": "",
                "reason": f"{tipo}",
            })
        return scritti


def materiale_da_sognare(battute, pubblico) -> list[dict]:
    """Le battute che possono diventare materiale di un sogno.

    Fuori i `promo`: la loro frase è un annuncio, non una persona che parla, e
    farla sognare vorrebbe dire imparare lo spam. Tutto il resto passa — il
    filtro privacy (via handle e URL) lo fa già `social_dream_inspirations`.
    """
    finti = {str(p.get("handle") or "") for p in getattr(pubblico, "profili", ())
             if p.get("tipo") == "promo"}
    return [b for b in (battute or ()) if str((b or {}).get("chat") or "") not in finti]


def ingaggio(posti, pubblico, *, rng=None) -> dict:
    """Le reazioni del pubblico ai post: reach, like, salvataggi, condivisioni.

    Il numero non è una promessa: è un modello **dichiarato**, deterministico col
    dado iniettato — il salvataggio premia la reazione (è una conversazione), la
    condivisione premia il verso. Serve a far girare i KPI in una giornata
    compressa, non a prevedere Instagram.
    """
    rng = rng if rng is not None else _random.Random(0)
    quanti = max(1, len(pubblico))
    esiti: dict[str, dict] = {}
    for post in posti or ():
        identificativo = str((post or {}).get("id") or "")
        if not identificativo:
            continue
        reazione = str((post or {}).get("kind") or "post") == "reaction"
        reach = max(1, round(quanti * (0.35 + 0.25 * rng.random())))
        esiti[identificativo] = {
            "reach": reach,
            "like": round(reach * (0.06 + 0.05 * rng.random())),
            "salvataggi": round(reach * (0.03 + 0.02 * rng.random())
                                * (1.3 if reazione else 1.0)),
            "condivisioni": round(reach * (0.01 + 0.01 * rng.random())),
            "commenti": (1 + rng.randrange(3)) if reazione else rng.randrange(2),
        }
    return esiti


def kpi(pubblico, posti, esiti=None, *, giorni: int = 1) -> dict:
    """I numeri della giornata, con gli stessi KPI della strategia di crescita.

    `follower_nuovi` sono i profili che hanno scritto almeno una volta,
    `ritorno` quelli tornati (`giorni_attivi >= 2`), `muse` quelli alla soglia.
    L'engagement rate è (salvataggi + condivisioni) / reach: la risonanza, non
    l'applauso — è la stessa scelta di `shared/growth_metrics.py`.
    """
    esiti = esiti or {}
    reach = sum(int(e.get("reach", 0)) for e in esiti.values())
    salvataggi = sum(int(e.get("salvataggi", 0)) for e in esiti.values())
    condivisioni = sum(int(e.get("condivisioni", 0)) for e in esiti.values())
    like = sum(int(e.get("like", 0)) for e in esiti.values())
    commenti = sum(int(e.get("commenti", 0)) for e in esiti.values())
    profili = list(getattr(pubblico, "profili", ()) or ())
    nuovi = sum(1 for p in profili if int(p.get("giorni_attivi", 0)) >= 1)
    ritorno = sum(1 for p in profili if int(p.get("giorni_attivi", 0)) >= 2)
    per_autore: dict[str, int] = {}
    for post in posti or ():
        autore = str((post or {}).get("author") or "?")
        per_autore[autore] = per_autore.get(autore, 0) + 1
    return {
        "giorni": int(giorni),
        "profili": len(profili),
        "post": len(list(posti or ())),
        "per_autore": per_autore,
        "messaggi": sum(int(p.get("messaggi", 0)) for p in profili),
        "follower_nuovi": nuovi,
        "ritorno": ritorno,
        "ritorno_rate": (ritorno / nuovi) if nuovi else 0.0,
        "muse": len(getattr(pubblico, "muse", ()) or ()),
        "soglia_musa": int(getattr(pubblico, "soglia_musa", 0)),
        "reach": reach,
        "like": like,
        "commenti": commenti,
        "salvataggi": salvataggi,
        "condivisioni": condivisioni,
        "engagement_rate": ((salvataggi + condivisioni) / reach) if reach else 0.0,
    }



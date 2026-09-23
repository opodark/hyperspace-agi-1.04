#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""La lingua di una ricerca web: si decide dalla query, non dalla configurazione.

Perché esiste (2026-09-23): il tool `web_search` chiedeva i risultati a SearXNG con
`language=it-IT` **fisso**. Su una query italiana va bene; su una query inglese gli
engine cercano parole italiane dentro la frase e restituiscono quello che capita.
Misurato sull'istanza di questo repo:

    'best russian nude wallpaper sites 2024'
      it-IT  -> "best - Dizionario inglese-italiano WordReference", "I Beati Paoli
                (romanzo) - Wikipedia"                                     (fuori tema)
      en-US  -> "russian Nude Pics and Porn - Cherry Nudes"                 (pertinente)
    'Unbridled Market dark web marketplace'
      it-IT  -> "Sovranità e sicurezza alimentare", LinkedIn, GATM          (fuori tema)
      en-US  -> "AlphaBay Marketplace Returns - DarkOwl"                    (pertinente)

E vale anche al contrario, che è la ragione per cui non basta un default:

    'chi ha vinto il campionato mondiale di Formula 1 nel 2025'
      it-IT  -> Sky Sport, "Lando Norris campione F1 2025"                  (pertinente)
      en-US  -> "Chi (letter) - Wikipedia", "What is Chi?"                  (fuori tema)

Il guasto non si vede da fuori: SearXNG risponde 200 con dei risultati, quindi il
tool li passa al modello come se fossero la risposta — e il modello ci costruisce
sopra (una chat ha "trovato" la foto su un marketplace del dark web che non esiste).

Perché la decisione sta qui e non nel control-plane: è logica pura — una query
dentro, una lingua fuori — quindi si testa senza rete e senza SearXNG, come
`shared/gpu_budget.py`. Il cablaggio (i parametri della richiesta HTTP) resta in
`control-plane/main.py`, che è l'unico posto che parla con SearXNG.

Nota sull'istanza: `data/searxng/settings.yml` ha `default_lang: "it-IT"`, quindi
**non passare** `language` significa "italiano", non "decidi tu". Da qui il terzo
valore possibile: stringa vuota = non si passa il parametro e vale il default
dell'istanza (che è la scelta giusta per le query corte e ambigue).
"""
from __future__ import annotations

import re

ITALIANO = "it-IT"
INGLESE = "en-US"
# Sotto questa soglia una query non è una frase: "meteo roma" non ha marcatori
# italiani, ma non è nemmeno inglese. Lì si lascia decidere all'istanza.
MIN_PAROLE_SENTENZA = 3

# Marcatori italiani: articoli, preposizioni (anche elise), congiunzioni, pronomi e
# le parole che aprono una domanda o una ricerca di tutti i giorni. Non è una lista
# di stopword esaustiva: è un rivelatore. Sbagliare costa una ricerca nella lingua
# sbagliata — cioè il difetto che questo modulo esiste per evitare — quindi le voci
# si aggiungono quando servono, e ogni aggiunta ha il suo test.
PAROLE_ITALIANE = frozenset("""
a ad al alla alle allo anche avete c che chi ci coi col come con contro cosa così
da dai dal dalla dalle dallo degli dei del della delle dello di dimmi dove
e ed era erano essere fa gli ha hai hanno ho i il in io la le lei li lo loro
ma me mi mia mie miei mio ne nei nel nella nelle nello no non notizie o od oggi
ognuno orari per perché piu più prezzo prezzi puo può qual quale quali quando
quanto quasi quello questa queste questi questo ricetta ricette se sei semmai si
sia siamo siete sono sopra sotto sta stato su sua sue sui sul sulla sulle sullo
suo te telefono ti tra trova tu tua tue tuoi tuo un una uno vi voi ieri domani
meteo previsioni cercami spiegami
""".split())

ACCENTATE = frozenset("àèéìòù")


def lingua(query: str, *, default: str = ITALIANO) -> str:
    """La `language` da chiedere a SearXNG per questa query.

    Tre esiti, e il terzo conta quanto gli altri:
      - `it-IT`   c'è almeno un marcatore italiano (o una lettera accentata);
      - `en-US`   nessun marcatore e abbastanza parole per essere una frase: una
                  frase senza parole funzione italiane non è italiana;
      - `""`      query corta e senza marcatori: non si passa `language` e vale il
                  default dell'istanza (decidere al posto suo qui sarebbe un tiro
                  al buio su una parola sola).

    Una query vuota restituisce `default`, che è il comportamento di un chiamante
    che non ha niente da dire.
    """
    testo = str(query or "").strip()
    if not testo:
        return default
    if any(carattere in ACCENTATE for carattere in testo):
        return ITALIANO
    parole = re.findall(r"[a-zà-ÿ0-9']+", testo.lower())
    if not parole:
        return default
    if any(parola in PAROLE_ITALIANE for parola in parole):
        return ITALIANO
    if len(parole) >= MIN_PAROLE_SENTENZA:
        return INGLESE
    return ""


# Parole inglesi che aprono una domanda o una ricerca: servono a non contare come
# "termine significativo" quello che in realtà è una parola funzione.
PAROLE_INGLESI = frozenset("""
a about after all also an and any are as at be been best but by can could did do
does for from had has have how i if in into is it its just like may more most my
not of on or our out over should so some such than that the their then there these
they this to under up us was we were what when where which who why will with would
you your
""".split())

# Il termine deve dire qualcosa: sotto quattro caratteri è quasi sempre una
# preposizione, un articolo o un acronimo generico ("web", "chi", "il").
MIN_CARATTERI_TERMINE = 4
# I numeri sono termini solo se distinguono: "2025" sì, "1" no — una cifra sola
# compare in quasi qualsiasi pagina, e da sola faceva passare il guardiano su un
# risultato che parlava d'altro (misurato: "Chi Magazine" passava per via dell'1).
MIN_CIFRE_TERMINE = 2


def termini_significativi(query: str) -> list[str]:
    """Le parole della query che dovrebbero comparire in un risultato pertinente.

    Si tolgono le parole funzione (italiane e inglesi) e le parole cortissime; i
    numeri restano, perché in una ricerca "2025" o "5060" sono il termine che
    distingue un risultato da un altro. L'ordine di apparizione si conserva e i
    duplicati no: serve una lista leggibile nei log, non una statistica.
    """
    testo = str(query or "").lower()
    fuori: list[str] = []
    for parola in re.findall(r"[a-zà-ÿ0-9']+", testo):
        pulita = parola.strip("'")
        if not pulita or pulita in PAROLE_ITALIANE or pulita in PAROLE_INGLESI:
            continue
        if pulita.isdigit():
            if len(pulita) < MIN_CIFRE_TERMINE:
                continue
        elif len(pulita) < MIN_CARATTERI_TERMINE:
            continue
        if pulita not in fuori:
            fuori.append(pulita)
    return fuori


def _senza_accenti(testo: str) -> str:
    tabella = str.maketrans("àèéìòù", "aeeiou")
    return str(testo or "").lower().translate(tabella)


def filtra(voci, query: str, *, testo=None, titolo=None) -> list:
    """Le sole voci che parlano della query.

    Il guardiano a livello di insieme (`pertinenti`) intercetta la risposta tutta
    sbagliata; questo copre il caso **misto**, che è il più frequente: un engine
    bloccato che infila due annunci mentre gli altri rispondono bene. Misurato il
    2026-09-23: "Noleggio Auto Low Cost - Rentalcars.com" e "iNoleggio.it - Auto a
    Noleggio" fra i risultati di "chi ha vinto il campionato mondiale di Formula 1
    nel 2025". Al modello quei due finiscono nella stessa evidenza delle fonti
    vere, e il rumore si legge come risposta.

    Se si passa anche `titolo`, e qualche voce ha il termine **nel titolo**, si
    tengono solo quelle: il titolo è il segnale forte (una pagina che parla di Roma
    lo dice nel titolo), mentre una parola che compare solo nella descrizione o
    nell'indirizzo può essere un caso — misurato: per "meteo Roma" passava anche
    un articolo sul clima che di Roma parlava solo nell'URL, e il modello ha citato
    quello invece delle previsioni.

    Senza termini significativi non si toglie niente: non ci sarebbe un criterio.
    """
    voci = list(voci or [])
    termini = termini_significativi(query)
    if not termini:
        return voci
    chiavi = [_senza_accenti(termine) for termine in termini]
    testo_di = testo or (lambda voce: str(voce or ""))
    pertinenti = [voce for voce in voci
                  if any(chiave in _senza_accenti(testo_di(voce)) for chiave in chiavi)]
    if titolo is None:
        return pertinenti
    nel_titolo = [voce for voce in pertinenti
                  if any(chiave in _senza_accenti(titolo(voce)) for chiave in chiavi)]
    return nel_titolo or pertinenti


def pertinenti(testi, query: str) -> bool:
    """I risultati parlano della query, o sono la spazzatura di un engine bloccato?

    Perché esiste (2026-09-23): quando un engine è in throttling o dietro CAPTCHA
    — `CAPTCHA (it-it)` e `suspended_time=180` nei log di SearXNG — l'istanza
    risponde comunque 200 con **contenuti non collegati**: "Chi Magazine" per una
    domanda sul mondiale di Formula 1, "WhatsApp Web" per un marketplace del dark
    web, "Sovranità e sicurezza alimentare" per la stessa. Il tool li passava al
    modello come se fossero la risposta, e il modello ci costruiva sopra: è così
    che una chat ha "trovato" una foto che non esiste.

    Una ricerca senza un solo termine significativo in comune non è una risposta:
    è meglio dirlo che consegnarla. Il criterio è deliberatamente permissivo (un
    termine basta, su tutto l'insieme): serve a intercettare il caso "niente a
    che vedere", non a giudicare la qualità.
    """
    testi = list(testi or [])
    if not testi and not termini_significativi(query):
        return True     # niente da verificare: si lascia passare
    return bool(filtra(testi, query))



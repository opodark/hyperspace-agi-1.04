# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/persona.py
# L'ARCHIVIO DELLA PERSONA: UN SOLO PROPRIETARIO.
#
# Un modulo di quattro righe, e non per eleganza: per un bug.
#
# `persona_store` è stato riassegnato dal boot fin dall'inizio — `_reload_persona`
# lo ricrea quando il nome o il file della persona cambiano dalla tab Setup. Un
# modulo che lo riceve per contesto ne tiene una copia, e quella copia non si
# aggiorna: dopo un salvataggio in Setup, il control-plane e i canali avrebbero
# due persone diverse, e quella giusta dipenderebbe da quale modulo ha chiesto per
# primo. Non e' un errore che si vede subito — e' un errore che si vede quando
# qualcuno cambia nome alla persona e si chiede perche' i canali non lo sappiano.
#
# Quindi l'archivio vive qui, e chi lo vuole chiama `persona()` e ottiene quello di
# adesso. Non si importa il nome: un import per nome tenerebbe l'oggetto di quando
# e' stato importato, che e' la trappola — la stessa di `_node_aliases` nel mesh,
# la stessa di `memory_sync` in cp/memoria.py. E' un'istruzione che in questo
# repository si ripete piu' volte di quanto sarebbe ragionevole, il che vuol dire
# che e' una trappola vera e non una paranoia.

from shared.persona import PersonaStore

_persona_store: PersonaStore | None = None


def monta(store) -> None:
    """Registra l'archivio iniziale. Non c'è un `app`: questo modulo non ha rotte."""
    global _persona_store
    _persona_store = store


def persona() -> PersonaStore:
    """L'archivio di ADESSO.

    Se non è ancora stato montato ne crea uno dal disco: meglio una persona
    appena caricata che un `None` che fa fallire una richiesta con un errore che
    non spiega niente.
    """
    global _persona_store
    if _persona_store is None:
        _persona_store = PersonaStore.load()
    return _persona_store


def profilo() -> PersonaStore:
    """Il documento della persona: nome, osservazioni, sezioni.

    Distinto da `persona()` perche' `persona.persona().persona` non si legge: e'
    due `persona` di fila che non hanno niente a che fare. Il profilo e' il
    documento, l'archivio e' la cosa che lo contiene e che sa salvarlo.
    """
    return _persona_store.persona if _persona_store is not None else persona().persona


def ricarica() -> PersonaStore:
    """Rilegge identità e annotazioni dal disco, dopo un salvataggio in Setup.

    Riassegna, e non aggiorna: è il punto in cui la copia vecchia muore.
    """
    global _persona_store
    _persona_store = PersonaStore.load()
    return _persona_store


def smonta() -> None:
    global _persona_store
    _persona_store = None

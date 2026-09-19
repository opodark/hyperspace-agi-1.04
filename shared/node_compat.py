"""Compatibilita' di protocollo fra control-plane e nodi.

Perche' esiste: un nodo che gira un'immagine vecchia risponde a `/health`,
sembra sano, e non ha le rotte che il control-plane si aspetta. E' successo
davvero: la differenza si e' vista solo sondando a mano `/models/capabilities`
(404 su un control-plane, 200 sull'altro). Un numero di versione dichiarato nel
`/status` rende quella differenza immediata invece che oggetto di indagine.

La scelta di fondo e' **additiva e non punitiva**: un nodo che non dichiara la
versione NON viene escluso dal routing. Escluderlo espellerebbe dalla mesh un
nodo che funziona, per un dato che prima semplicemente non esisteva — cioe' si
trasformerebbe un miglioramento diagnostico in un guasto. In modalita' advisory
(default) si segnala una volta per nodo e si lascia passare; la modalita'
`strict` esiste per chi vuole il filtro, ed e' opt-in.

Qui vive solo la POLICY: nessuna dipendenza da Flask/Bottle, nessun accesso alla
rete. Il log e' iniettato, quindi `report()` e' testabile senza catturare stdout.
"""
from __future__ import annotations

import os

# Alza questo quando una modifica al contratto fra CP e nodi rende i nodi
# precedenti incompleti (una rotta nuova, un campo obbligatorio nuovo). Non
# alzarlo per aggiunte che i nodi vecchi possono ignorare.
PROTOCOL_VERSION = 1
MIN_COMPATIBLE = 1

MODE_VAR = "NODE_PROTOCOL_MODE"
MODE_ADVISORY = "advisory"
MODE_STRICT = "strict"

OK = "ok"
OLDER = "older"
NEWER = "newer"
MISSING = "missing"
UNKNOWN = "unknown"


def mode_from_env(environ=None) -> str:
    """Modalita' configurata. Un valore non riconosciuto ricade su advisory."""
    env = os.environ if environ is None else environ
    raw = str(env.get(MODE_VAR, MODE_ADVISORY)).strip().lower()
    return raw if raw in (MODE_ADVISORY, MODE_STRICT) else MODE_ADVISORY


def check(node_info, *, supported: int = PROTOCOL_VERSION, minimum: int = MIN_COMPATIBLE):
    """(stato, motivo). Non solleva mai: un payload inatteso diventa `unknown`."""
    try:
        grezzo = (node_info or {}).get("protocol_version")
    except AttributeError:
        return UNKNOWN, "nodo non leggibile come dizionario"

    if grezzo is None or grezzo == "":
        return MISSING, ("nodo senza protocol_version: immagine piu' vecchia del "
                         "repository, oppure nodo non aggiornato")
    try:
        versione = int(grezzo)
    except (TypeError, ValueError):
        return UNKNOWN, f"protocol_version non numerica: {grezzo!r}"

    if versione == supported:
        return OK, f"protocollo {versione}"
    if versione > supported:
        return NEWER, (f"nodo a protocollo {versione}, questo control-plane ne "
                       f"supporta {supported}: il nodo e' avanti")
    if versione >= minimum:
        return OLDER, (f"nodo a protocollo {versione}, supportato fino a {supported}: "
                       "potrebbe non avere funzioni che il control-plane si aspetta")
    return OLDER, (f"nodo a protocollo {versione}, sotto il minimo {minimum}: "
                   "incompatibile")


def is_usable(state: str, mode: str) -> bool:
    """Se un nodo in questo stato puo' ricevere lavoro, nella modalita' data.

    In advisory tutto passa tranne il caso in cui non si sa proprio cosa sia il
    nodo. In strict passa solo il protocollo esatto.
    """
    if mode == MODE_STRICT:
        return state == OK
    return state != UNKNOWN


class ProtocolWatch:
    """Segnala le incompatibilita' UNA VOLTA per nodo e per stato.

    La costruzione dei candidati avviene a ogni richiesta: senza questa memoria
    lo stesso avviso comparirebbe centinaia di volte al minuto e diventerebbe
    rumore, cioe' esattamente il contrario di quello che serve.
    """

    def __init__(self, *, supported: int = PROTOCOL_VERSION, minimum: int = MIN_COMPATIBLE,
                 mode: str = MODE_ADVISORY):
        self.supported = supported
        self.minimum = minimum
        self.mode = mode
        self._segnalati = set()

    @classmethod
    def from_env(cls, environ=None) -> "ProtocolWatch":
        return cls(mode=mode_from_env(environ))

    def observe(self, node_info):
        """(stato, motivo, da_segnalare). `da_segnalare` e' True solo la prima volta."""
        nodo = node_info or {}
        chiave_nodo = str(nodo.get("node_id") or nodo.get("endpoint") or "?")
        stato, motivo = check(nodo, supported=self.supported, minimum=self.minimum)
        chiave = (chiave_nodo, stato)
        nuovo = chiave not in self._segnalati
        self._segnalati.add(chiave)
        return stato, motivo, nuovo

    def report(self, nodes, log=print) -> list:
        """Logga le novita' e restituisce i nodi ammessi dalla modalita' corrente."""
        ammessi = []
        for nodo in nodes or []:
            stato, motivo, nuovo = self.observe(nodo)
            if nuovo and stato != OK:
                nome = str((nodo or {}).get("node_id") or "?")[:20]
                log(f"[CP] {nome}: {motivo}")
            if is_usable(stato, self.mode):
                ammessi.append(nodo)
        return ammessi

    def describe(self) -> dict:
        return {"supported": self.supported, "minimum": self.minimum,
                "mode": self.mode, "segnalati": len(self._segnalati)}

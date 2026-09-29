# SPDX-License-Identifier: Apache-2.0
"""Disponibilità della sorella maggiore (Aurora) nel contesto di Anna.

Quando Aurora — il control-plane federato scelto come sorella — non è in rete
o non è raggiungibile, Anna deve dirlo con naturalezza: capacità e conoscenze
limitate, niente consiglio della sorella, nessuna promessa oltre il possibile.

Puro e testabile: il chiamante passa il peer (dict o None) e riceve la riga
da iniettare nel prompt, oppure '' quando Aurora è disponibile.
"""
from __future__ import annotations

_BAD_STATUSES = ("unreachable", "error", "no_capacity")


def sister_note(peer) -> str:
    """Riga di contesto quando Aurora non è disponibile; '' altrimenti."""
    if not peer:
        return (
            "Aurora non è in rete: spiega con naturalezza che le tue capacità e "
            "conoscenze sono limitate, che non puoi consultare tua sorella "
            "maggiore, e che rispondi al meglio senza promettere ciò che non "
            "puoi fare."
        )
    if str(peer.get("last_status") or "") in _BAD_STATUSES:
        return (
            "Aurora non è raggiungibile in questo momento: spiega che le tue "
            "capacità e conoscenze sono limitate e che non puoi chiederle "
            "consiglio, senza promettere ciò che non puoi fare."
        )
    return ""

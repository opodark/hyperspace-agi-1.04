# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.sister_status import sister_note  # noqa: E402


def test_nessun_peer_nota_di_limite():
    testo = sister_note(None)
    assert "Aurora non è in rete" in testo
    assert "limitate" in testo


def test_peer_irraggiungibile_nota_di_limite():
    testo = sister_note({"label": "win11-cp", "last_status": "unreachable"})
    assert "non è raggiungibile" in testo
    assert "limitate" in testo


def test_peer_disponibile_nessuna_nota():
    assert sister_note({"label": "win11-cp", "last_status": "ok"}) == ""
    assert sister_note({"label": "win11-cp"}) == ""

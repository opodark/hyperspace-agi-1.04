# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.instagram_vip import InstagramVipStore, level_for  # noqa: E402


def test_levels():
    assert level_for(4) == ""
    assert level_for(5) == "vip"
    assert level_for(15) == "cerchia"
    assert level_for(30) == "musa"


def test_store_promotes_once_and_persists(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("123", "alice") for _ in range(6)]
    assert events[4]["promoted"] is True
    assert events[5]["promoted"] is False
    assert InstagramVipStore(str(path)).list()[0]["messages"] == 6


def test_ingresso_nella_cerchia_intima_e_consenso_persistente(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("42", "bianca") for _ in range(15)]
    assert events[-1]["entered_intimate"] is True
    assert store.consent("42") == ""

    store.set_consent("42", "granted")
    assert store.consent("42") == "granted"
    assert InstagramVipStore(str(path)).consent("42") == "granted"


def test_entered_intimate_non_ripete_al_livello_successivo(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("7", "carla") for _ in range(15)]
    assert events[-1]["entered_intimate"] is True   # vip -> cerchia
    events = [store.record("7", "carla") for _ in range(15)]
    assert events[-1]["entered_intimate"] is False  # cerchia -> musa, già dentro


def test_il_creatore_non_si_degrada_e_non_entra_nel_consenso(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    store.set_creator("99", "papa")
    assert store.list(vip_only=False)[0]["level"] == "creatore"
    for _ in range(40):
        store.record("99", "papa")
    assert store.list(vip_only=False)[0]["level"] == "creatore"
    # il creatore è sopra "musa", non entra mai nel flusso del consenso
    assert store.consent("99") == ""
    assert InstagramVipStore(str(path)).list(vip_only=False)[0]["level"] == "creatore"

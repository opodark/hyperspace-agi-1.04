# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.instagram_vip import (CREATOR_LEVEL, INTIMATE_LEVELS, LEVELS,  # noqa: E402
                                  InstagramVipStore, level_for)


def test_le_bande_sono_tre_piu_il_creatore():
    """La scala è una sola: pubblico/anonimo, VIP, MUSA — più il creatore a mano.

    Il gradino `cerchia` (15 messaggi) è stato tolto il 2026-10-01: era la banda che
    Telegram, che le bande le **dichiara** a mano, non ha mai avuto. Questo test
    tiene ferma la scala: se qualcuno rimettesse un gradino, la divisione del
    pubblico che vive di lei (voce, immagine, consenso) non lo seguirebbe.
    """
    assert LEVELS == ((30, "musa"), (5, "vip"))
    assert INTIMATE_LEVELS == ("musa",)


def test_levels():
    assert level_for(4) == ""
    assert level_for(5) == "vip"
    assert level_for(29) == "vip"
    assert level_for(30) == "musa"


def test_store_promotes_once_and_persists(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("123", "alice") for _ in range(6)]
    assert events[4]["promoted"] is True
    assert events[5]["promoted"] is False
    assert InstagramVipStore(str(path)).list()[0]["messages"] == 6


def test_il_gradino_tolto_non_e_piu_un_traguardo(tmp_path):
    """I traguardi sono le soglie della scala: a 15 messaggi non si promuove nulla."""
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("55", "dora") for _ in range(15)]
    assert events[-1]["promoted"] is False
    assert events[-1]["level"] == "vip"
    assert store.list()[0]["milestones"] == [5]


def test_ingresso_nella_banda_intima_e_consenso_persistente(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("42", "bianca") for _ in range(30)]
    assert events[-1]["entered_intimate"] is True
    assert store.consent("42") == ""

    store.set_consent("42", "granted")
    assert store.consent("42") == "granted"
    assert InstagramVipStore(str(path)).consent("42") == "granted"


def test_entered_intimate_non_ripete_al_livello_successivo(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("7", "carla") for _ in range(30)]
    assert events[-1]["entered_intimate"] is True   # vip -> musa: il varco
    events = [store.record("7", "carla") for _ in range(15)]
    assert events[-1]["entered_intimate"] is False  # già dentro: non si ripete


def test_il_creatore_non_si_degrada_e_non_entra_nel_consenso(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    store.set_creator("99", "papa")
    assert store.list(vip_only=False)[0]["level"] == "creatore"
    # I traguardi del creatore sono le soglie della scala: si muovono con lei.
    assert store.list(vip_only=False)[0]["milestones"] == [5, 30]
    for _ in range(40):
        store.record("99", "papa")
    assert store.list(vip_only=False)[0]["level"] == "creatore"
    # il creatore è sopra "musa", non entra mai nel flusso del consenso
    assert store.consent("99") == ""
    assert InstagramVipStore(str(path)).list(vip_only=False)[0]["level"] == "creatore"


def test_una_banda_tolta_non_resta_appesa_al_contatto(tmp_path):
    """Chi stava nella banda `cerchia` torna alla banda che il conteggio dice.

    I dati scritti prima del 2026-10-01 possono portare un nome che la scala non
    produce più: senza il ricalcolo resterebbe per sempre un livello che non esiste
    (e nel report finirebbe nel secchio degli anonimi). Il creatore è l'unico
    assegnato a mano: lui non si tocca.
    """
    path = tmp_path / "vips.json"
    path.write_text(json.dumps({"people": {
        "1": {"level": "cerchia", "messages": 20, "username": "tizia",
              "milestones": [5, 15]},
        "2": {"level": "cerchia", "messages": 40, "username": "tizia2"},
        "3": {"level": CREATOR_LEVEL, "messages": 3, "username": "papa",
              "milestones": [5, 15, 30]},
    }}), encoding="utf-8")

    store = InstagramVipStore(str(path))
    righe = {row["messages"]: row for row in store.list(vip_only=False)}
    assert righe[20]["level"] == "vip"
    assert righe[40]["level"] == "musa"
    assert righe[3]["level"] == CREATOR_LEVEL
    # Anche il diario dei traguardi segue la scala: il 15 non è più una tappa.
    assert righe[20]["milestones"] == [5]
    assert righe[3]["milestones"] == [5, 30]

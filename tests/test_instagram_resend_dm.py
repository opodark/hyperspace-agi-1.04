# SPDX-License-Identifier: Apache-2.0
"""Il reinvio dei disegni non consegnati sceglie i job giusti e l'URL giusto.

Il controllo che conta: l'URL usa il percorso relativo *completo*
(`HyperSpace/bridge_00048_.jpg`), perché la rotta `/instagram/media/<token>/<nome>`
serve il file dentro il volume del diario. Col solo basename la richiesta finisce
su un 404 e Instagram risponde "400 Caricamento non riuscito": nessun disegno
consegnato, in coda per sempre.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "instagram_resend_dm.py"


def _load():
    """Importa il driver (senza eseguire main) per testarne le funzioni pure."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("instagram_resend_dm_sotto_test", DRIVER)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


mod = _load()


@pytest.fixture
def immagini(tmp_path, monkeypatch):
    (tmp_path / "HyperSpace").mkdir()
    (tmp_path / "HyperSpace" / "bridge_00048_.jpg").write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(mod, "IMMAGINI_DIR", str(tmp_path))
    return tmp_path


def test_percorso_servibile_mantiene_la_cartella(immagini):
    assert mod.percorso_servibile("HyperSpace/bridge_00048_.jpg") == \
        "HyperSpace/bridge_00048_.jpg"


def test_percorso_servibile_rifiuta_file_assente(immagini):
    assert mod.percorso_servibile("HyperSpace/assente.jpg") == ""
    assert mod.percorso_servibile("") == ""
    assert mod.percorso_servibile(None) == ""


def test_percorso_servibile_rifiuta_la_fuga_dal_volume(immagini):
    assert mod.percorso_servibile("../segreto.jpg") == ""
    assert mod.percorso_servibile("/etc/passwd") == ""


def test_url_pubblico_mantiene_le_sottocartelle(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_PUBLIC_BASE_URL", "https://media.example.test/")
    monkeypatch.setenv("INSTAGRAM_MEDIA_TOKEN", "tok123")
    assert mod.url_pubblico("HyperSpace/bridge_00048_.jpg") == (
        "https://media.example.test/instagram/media/tok123/HyperSpace/bridge_00048_.jpg")


def test_url_pubblico_si_ferma_senza_base(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("INSTAGRAM_MEDIA_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        mod.url_pubblico("HyperSpace/bridge_00048_.jpg")


def _job(**extra):
    job = {"id": "2119819cfb8f", "canale": "instagram",
           "destinazione": "2029617404424222", "stato": "done", "consegnato": False,
           "esito": {"file": "HyperSpace/bridge_00048_.jpg"}}
    job.update(extra)
    return job


def test_da_reinviare_solo_i_dm_conclusi_e_non_consegnati():
    jobs = {"a": _job(id="a"), "b": _job(id="b", consegnato=True),
            "c": _job(id="c", canale="telegram"), "d": _job(id="d", stato="running"),
            "e": _job(id="e", destinazione="")}
    assert [j["id"] for j in mod.da_reinviare(jobs, [])] == ["a"]


def test_da_reinviare_rispetta_gli_id_richiesti():
    jobs = {"a": _job(id="a"), "b": _job(id="b")}
    assert [j["id"] for j in mod.da_reinviare(jobs, ["b"])] == ["b"]
    assert mod.da_reinviare(jobs, ["zzz"]) == []
    assert mod.da_reinviare({}, []) == []


def test_token_canale_prende_il_primo_token(monkeypatch):
    monkeypatch.setenv("CHANNEL_CLIENTS", "cli=aaa;web=bbb")
    assert mod.token_canale() == "aaa"


def test_token_canale_vuoto_senza_clienti(monkeypatch):
    monkeypatch.delenv("CHANNEL_CLIENTS", raising=False)
    assert mod.token_canale() == ""

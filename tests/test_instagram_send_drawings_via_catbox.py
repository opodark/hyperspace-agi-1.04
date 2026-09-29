# SPDX-License-Identifier: Apache-2.0
"""Il ponte catbox carica il JPEG giusto e sceglie i disegni giusti.

Meta rifiuta gli allegati serviti dagli host tunnel (subcode 2018007) senza
nemmeno scaricarli: l'unico modo di consegnare un DM immagine è dare a Meta un
URL su un host accettato. Qui si controlla che la scelta dei job, il percorso
del file e il contratto di upload (multipart `reqtype=fileupload`) siano quelli
attesi, senza toccare la rete.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "instagram_send_drawings_via_catbox.py"


def _load():
    """Importa il driver (senza eseguire main) per testarne le funzioni pure."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("instagram_catbox_sotto_test", DRIVER)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


mod = _load()


def _job(**extra):
    job = {"id": "2119819cfb8f", "canale": "instagram",
           "destinazione": "2029617404424222", "stato": "done", "consegnato": False,
           "esito": {"file": "HyperSpace/bridge_00048_.jpg"}}
    job.update(extra)
    return job


def test_da_consegnare_solo_i_dm_conclusi_e_non_consegnati():
    jobs = {"a": _job(id="a"), "b": _job(id="b", consegnato=True),
            "c": _job(id="c", canale="telegram"), "d": _job(id="d", stato="running"),
            "e": _job(id="e", destinazione="")}
    assert [j["id"] for j in mod.da_consegnare(jobs)] == ["a"]
    assert mod.da_consegnare({}) == []


def test_percorso_usa_il_file_dell_esito(tmp_path, monkeypatch):
    (tmp_path / "HyperSpace").mkdir()
    (tmp_path / "HyperSpace" / "bridge_00048_.jpg").write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(mod, "IMMAGINI_DIR", tmp_path)
    assert mod.percorso(_job()).is_file()
    assert mod.percorso(_job(esito={})) == tmp_path  # nessun file: resta la radice


class _Risposta:
    def __init__(self, testo, stato=200):
        self.text = testo
        self.status_code = stato

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_carica_manda_multipart_e_restituisce_l_url(tmp_path, monkeypatch):
    file = tmp_path / "bridge_00048_.jpg"
    file.write_bytes(b"\xff\xd8\xff")
    chiamate = {}

    def finto_post(url, data=None, files=None, timeout=None):
        chiamate.update({"url": url, "data": data, "files": list((files or {}).keys()),
                         "nome": (files or {}).get("fileToUpload", ("",))[0]})
        return _Risposta("https://files.catbox.moe/abc123.jpg")

    monkeypatch.setattr(mod.requests, "post", finto_post)
    assert mod.carica(file) == "https://files.catbox.moe/abc123.jpg"
    assert chiamate["url"] == mod.CATBOX_API
    assert chiamate["data"] == {"reqtype": "fileupload"}
    assert chiamate["files"] == ["fileToUpload"]
    assert chiamate["nome"] == "bridge_00048_.jpg"


def test_carica_rifiuta_una_risposta_non_url(tmp_path, monkeypatch):
    file = tmp_path / "bridge_00048_.jpg"
    file.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: _Risposta("File too large"))
    with pytest.raises(RuntimeError):
        mod.carica(file)


def test_token_canale_prende_il_primo_token(monkeypatch):
    monkeypatch.setenv("CHANNEL_CLIENTS", "cli=aaa;web=bbb")
    assert mod.token_canale() == "aaa"


def test_token_canale_vuoto_senza_clienti(monkeypatch):
    monkeypatch.delenv("CHANNEL_CLIENTS", raising=False)
    assert mod.token_canale() == ""

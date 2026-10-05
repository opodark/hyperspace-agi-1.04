# SPDX-License-Identifier: Apache-2.0
"""Il disegno in DM viaggia col percorso relativo *completo*.

Regressione: l'URL usava `os.path.basename`, così `HyperSpace/bridge_00048_.jpg`
diventava `/instagram/media/<token>/bridge_00048_.jpg` -> 404 dalla rotta media,
e Instagram rispondeva "400 Caricamento non riuscito": nessun disegno consegnato.
"""
import ast

from tests import cp_source
import os
import re
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# `image_result` chiama queste due: la prenotazione della memoria unificata del
# Mac si trattiene fra due job a checkpoint, e la decisione sta in
# `shared/image_jobs.py` — non in una copia scritta qui.
from shared.image_jobs import FAMIGLIE_CHECKPOINT, usa_checkpoint  # noqa: E402

ENV = {"INSTAGRAM_PUBLIC_BASE_URL": "https://media.example.test/",
       "INSTAGRAM_MEDIA_TOKEN": "tok123"}


def _functions(*names):
    # fresco=True: qui sotto si azzerano i decorator, e la vista in cache di
    # cp_source e' condivisa. Senza, la modifica resterebbe nel nodo in cache e
    # i test che vengono dopo leggerebbero una rotta senza @app.route.
    tree = cp_source.albero(fresco=True)
    trovati = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            trovati[node.name] = node
    return trovati


class Queue:
    def __init__(self, job):
        self.job = job
        self.consegnati = []

    def concludi(self, job_id, ok, file="", errore="", durata_ms=0):
        return self.job

    def consegnato(self, job_id):
        self.consegnati.append(job_id)
        return True

    def ha_in_coda(self, famiglie=()):
        # Il job di questo test non e' di una famiglia a checkpoint (non dichiara
        # `famiglia`), quindi la coda non trattiene la memoria del Mac.
        return False


def _run(tmp_path, file_esito, sent='{"message_id": "img.1"}'):
    job = {"id": "2119819cfb8f", "stato": "done", "canale": "instagram",
           "destinazione": "2029617404424222", "esito": {"file": file_esito}}
    queue = Queue(job)
    calls, logs = [], []
    funzioni = _functions("_percorso_disegno_servibile", "image_result")
    scope = {
        "os": os, "re": re, "quote": quote, "threading": threading,
        "DIARIO_IMMAGINI_DIR": str(tmp_path),
        "request": SimpleNamespace(get_json=lambda **kw: {"id": job["id"], "ok": True,
                                                          "file": file_esito}),
        "_channel_error": lambda: None,
        "usa_checkpoint": usa_checkpoint,
        "FAMIGLIE_CHECKPOINT": FAMIGLIE_CHECKPOINT,
        # coda, gate, connettori e diario arrivano dal boot attraverso il
        # contesto di cp/immagini.py: prima erano quattro nomi piatti qui.
        "_serve": lambda *campi: None,
        "_contesto": SimpleNamespace(
            image_memory_gate=SimpleNamespace(release_image=lambda *a, **k: None,
                                              continue_image_queue=lambda *a, **k: False),
            image_queue=queue,
            connector_manager=SimpleNamespace(
                execute=lambda tool, args: (calls.append((tool, args)), sent)[1]),
            diario=SimpleNamespace(get=lambda *a: None, save=lambda *a: None,
                                   aggiorna_file=lambda *a: False)),
        "file_da_job": lambda _job: None,
        "push_log": lambda *a, **kw: logs.append((a, kw)),
        "jsonify": lambda payload: payload,
    }
    body = [funzioni["_percorso_disegno_servibile"], funzioni["image_result"]]
    exec(compile(ast.Module(body=body, type_ignores=[]), "cp", "exec"), scope)
    with mock.patch.dict(os.environ, ENV, clear=False):
        scope["image_result"]()
    return calls, logs, queue, scope["_percorso_disegno_servibile"]


def _immagine(tmp_path, relative="HyperSpace/bridge_00048_.jpg") -> None:
    pieno = tmp_path / relative
    pieno.parent.mkdir(parents=True, exist_ok=True)
    pieno.write_bytes(b"\xff\xd8\xff\xe0finto jpeg")


def test_il_dm_usa_il_percorso_completo_e_consegna(tmp_path):
    _immagine(tmp_path)
    calls, logs, queue, _ = _run(tmp_path, "HyperSpace/bridge_00048_.jpg")
    assert len(calls) == 1
    tool, args = calls[0]
    assert tool == "instagram_send_image"
    assert args["recipient_id"] == "2029617404424222"
    assert args["image_url"] == ("https://media.example.test/instagram/media/tok123/"
                                 "HyperSpace/bridge_00048_.jpg")
    assert queue.consegnati == ["2119819cfb8f"]
    assert any("consegnato" in (a[1] if len(a) > 1 else "") for a, _ in logs)


def test_file_assente_non_consegna_e_lo_dice(tmp_path):
    calls, logs, queue, _ = _run(tmp_path, "HyperSpace/assente.jpg")
    assert calls == []
    assert queue.consegnati == []
    assert any("non servibile" in str(kw.get("detail", "")) for _, kw in logs)


def test_traversal_rifiutato(tmp_path):
    fuori = tmp_path.parent / "segreti.jpg"
    fuori.write_bytes(b"\xff\xd8\xff\xe0finto jpeg")
    calls, logs, queue, percorso = _run(tmp_path, "../segreti.jpg")
    assert percorso("../segreti.jpg") == ""
    assert calls == []
    assert queue.consegnati == []
    assert any("non servibile" in str(kw.get("detail", "")) for _, kw in logs)


def test_percorso_servibile_normalizzato(tmp_path):
    _immagine(tmp_path)
    _, _, _, percorso = _run(tmp_path, "HyperSpace/bridge_00048_.jpg")
    assert percorso("HyperSpace/bridge_00048_.jpg") == "HyperSpace/bridge_00048_.jpg"
    assert percorso("/HyperSpace/bridge_00048_.jpg") == "HyperSpace/bridge_00048_.jpg"
    assert percorso("") == ""
    assert percorso(None) == ""


def test_dm_non_consegnato_resta_in_coda(tmp_path):
    _immagine(tmp_path)
    errore = ("Tool 'instagram_send_image': il connettore che lo espone ha fallito — "
              "instagram (RuntimeError: Instagram HTTP 400: Caricamento non riuscito)")
    calls, logs, queue, _ = _run(tmp_path, "HyperSpace/bridge_00048_.jpg", sent=errore)
    assert len(calls) == 1
    assert queue.consegnati == []
    assert any("non consegnato" in (a[1] if len(a) > 1 else "") for a, _ in logs)


if __name__ == "__main__":
    unittest.main()

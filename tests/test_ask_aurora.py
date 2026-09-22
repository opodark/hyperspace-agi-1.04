# SPDX-License-Identifier: Apache-2.0
import ast
import json
import os
import unittest
from pathlib import Path

SRC = Path(__file__).parents[1] / "control-plane/main.py"
FUNCS = {"_sister_peer", "_extract_federated_text", "_tool_ask_aurora"}


class _FakeDB:
    def __init__(self, peers):
        self._peers = peers

    def get_all_federated_peers(self):
        return self._peers


def _load(peers=(), federate_result=None):
    """Estrae dal VERO sorgente del control-plane le tre funzioni del tool
    `ask_aurora` e le esegue in isolamento, iniettando un `db` finto e un
    `_federate_to_peer` finto (che cattura l'inoltro). Stesso approccio di
    tests/test_model_patterns.py: nessuna copia della logica."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in FUNCS]
    captured = {}

    def _fake_federate(peer, prompt, model):
        captured["peer"] = peer
        captured["prompt"] = prompt
        captured["model"] = model
        return federate_result

    scope = {
        "os": os,
        "json": json,
        "db": _FakeDB(list(peers)),
        "_federate_to_peer": _fake_federate,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SRC), "exec"), scope)
    missing = FUNCS - set(scope)
    if missing:
        raise RuntimeError(f"funzioni non trovate in {SRC.name}: {sorted(missing)}")
    scope["_captured"] = captured
    return scope


def _peer(label="win11-cp", enabled=1, peer_id="797a87a3c54be273"):
    return {"label": label, "enabled": enabled, "peer_id": peer_id,
            "endpoint": "http://100.64.31.18:8095"}


class SisterPeerTests(unittest.TestCase):
    def test_etichetta_corrispondente_vincente(self):
        s = _load(peers=[_peer("win11-cp"), _peer("altro")])
        os.environ["SISTER_PEER_LABEL"] = "win11-cp"
        try:
            self.assertEqual(s["_sister_peer"]()["label"], "win11-cp")
        finally:
            os.environ.pop("SISTER_PEER_LABEL", None)

    def test_senza_etichetta_usa_il_primo(self):
        s = _load(peers=[_peer("win11-cp"), _peer("altro")])
        os.environ["SISTER_PEER_LABEL"] = ""
        try:
            self.assertEqual(s["_sister_peer"]()["label"], "win11-cp")
        finally:
            os.environ.pop("SISTER_PEER_LABEL", None)

    def test_nessun_peer_abilitato(self):
        s = _load(peers=[_peer(enabled=0)])
        self.assertIsNone(s["_sister_peer"]())

    def test_etichetta_non_trovata(self):
        s = _load(peers=[_peer("altro")])
        os.environ["SISTER_PEER_LABEL"] = "win11-cp"
        try:
            self.assertIsNone(s["_sister_peer"]())
        finally:
            os.environ.pop("SISTER_PEER_LABEL", None)


class ExtractFederatedTextTests(unittest.TestCase):
    def test_formato_openai(self):
        s = _load()
        text = s["_extract_federated_text"](
            {"result": {"choices": [{"message": {"content": "  consiglio  "}}]}})
        self.assertEqual(text, "consiglio")

    def test_campo_testuale_piano(self):
        s = _load()
        self.assertEqual(s["_extract_federated_text"]({"result": {"content": "ok"}}), "ok")

    def test_stringa_grezza(self):
        s = _load()
        self.assertEqual(s["_extract_federated_text"]("  testo  "), "testo")


class AskAuroraToolTests(unittest.TestCase):
    def test_domanda_vuota(self):
        s = _load()
        self.assertIn("Non ho ricevuto", s["_tool_ask_aurora"]({"question": "  "}))

    def test_nessun_peer(self):
        s = _load(peers=[])
        self.assertIn("SISTER_PEER_LABEL", s["_tool_ask_aurora"]({"question": "chi sono?"}))

    def test_peer_irraggiungibile(self):
        s = _load(peers=[_peer()], federate_result=None)
        self.assertIn("non è raggiungibile", s["_tool_ask_aurora"]({"question": "chi sono?"}))

    def test_consiglio_riportato(self):
        s = _load(peers=[_peer()],
                  federate_result={"result": {"choices": [{"message": {"content": "sii te stessa"}}]}})
        out = s["_tool_ask_aurora"]({"question": "che faccio?"})
        self.assertEqual(out, "sii te stessa")
        # Il prompt inoltrato inquadra Aurora e Anna e riporta la domanda.
        self.assertIn("Aurora", s["_captured"]["prompt"])
        self.assertIn("Anna", s["_captured"]["prompt"])
        self.assertIn("che faccio?", s["_captured"]["prompt"])
        # model vuoto = il peer usa il SUO modello di default.
        self.assertEqual(s["_captured"]["model"], "")


class RegistrationTests(unittest.TestCase):
    def test_il_tool_e_nel_catalogo_nativo_e_nel_dispatcher(self):
        source = SRC.read_text(encoding="utf-8")
        self.assertIn('"name": "ask_aurora"', source)
        self.assertIn('"ask_aurora":      _tool_ask_aurora,', source)

    def test_la_config_e_in_setup(self):
        source = SRC.read_text(encoding="utf-8")
        self.assertIn('"key": "SISTER_PEER_LABEL"', source)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC = ROOT / "control-plane" / "main.py"


class ConversationDiaryTests(unittest.TestCase):
    def test_le_rotte_esistono(self):
        source = SRC.read_text(encoding="utf-8")
        self.assertIn("@app.route('/conversations')", source)
        self.assertIn("@app.route('/conversations/data')", source)

    def test_il_diario_e_il_registratore_esistono(self):
        source = SRC.read_text(encoding="utf-8")
        self.assertIn("_conversation_log", source)
        self.assertIn("def _record_conversation", source)

    def test_il_reply_registra_le_battute(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        corpo = next(ast.unparse(n) for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == "channel_reply")
        self.assertIn("_record_conversation", corpo)
        # Quattro uscite registrano: reply, wait, presentazione, immagine.
        self.assertGreaterEqual(corpo.count("_record_conversation"), 4)

    def test_la_pagina_html_e_copiata_nel_container(self):
        dockerfile = (ROOT / "control-plane" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("conversations.html", dockerfile)
        self.assertTrue((ROOT / "control-plane" / "conversations.html").exists())


if __name__ == "__main__":
    unittest.main()

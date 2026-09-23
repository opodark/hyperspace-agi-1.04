# SPDX-License-Identifier: Apache-2.0
"""Le rotte del diario: la pagina e i dati, senza importare Flask."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


class DiarioRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cp = functions(ROOT / "control-plane" / "main.py")

    def test_la_pagina_serve_diario_html(self):
        fonte = ast.unparse(self.cp["diario_page"])
        self.assertIn("diario.html", fonte)
        self.assertIn("send_from_directory", fonte)

    def test_i_dati_espongono_le_voci_del_diario(self):
        fonte = ast.unparse(self.cp["diario_data"])
        self.assertIn("diario.list", fonte)
        self.assertIn("voci", fonte)


if __name__ == "__main__":
    unittest.main()

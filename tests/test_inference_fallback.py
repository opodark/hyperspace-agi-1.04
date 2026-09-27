# SPDX-License-Identifier: Apache-2.0
"""Lo switch automatico Ollama↔LM Studio: parse_inference_urls.

La funzione vive in control-plane/main.py (app Flask), quindi la si carica col
solito trucco AST di test_model_patterns.py: si estrae dal VERO sorgente, senza
copiare la logica. È pura (solo `str`), non serve iniettare nulla.
"""
import ast
import unittest
from pathlib import Path

SRC = Path(__file__).parents[1] / "control-plane/main.py"
FUNCS = {"parse_inference_urls"}


def _load():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in FUNCS]
    scope = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SRC), "exec"), scope)
    missing = FUNCS - set(scope)
    if missing:
        raise RuntimeError(f"funzioni non trovate in {SRC.name}: {sorted(missing)}")
    return scope


class ParseInferenceUrlsTests(unittest.TestCase):
    def test_lista_virgola_senza_slash_finali(self):
        f = _load()["parse_inference_urls"]
        self.assertEqual(f("http://a:1, http://b:2/ ,c:3", "x"),
                         ["http://a:1", "http://b:2", "c:3"])

    def test_vuoto_ricade_sul_fallback_singolo(self):
        f = _load()["parse_inference_urls"]
        self.assertEqual(f("", "http://fallback:11434/"), ["http://fallback:11434"])
        self.assertEqual(f("   ", "fb"), ["fb"])

    def test_fallback_vuoto_da_lista_vuota(self):
        f = _load()["parse_inference_urls"]
        self.assertEqual(f("", ""), [])
        self.assertEqual(f(", ,", ""), [])

    def test_spazi_e_vuoti_scartati(self):
        f = _load()["parse_inference_urls"]
        self.assertEqual(f(" http://a , , http://b ", "fb"),
                         ["http://a", "http://b"])


if __name__ == "__main__":
    unittest.main()

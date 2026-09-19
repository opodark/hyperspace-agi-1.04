# SPDX-License-Identifier: Apache-2.0
"""Guardia contro il downgrade silenzioso dei log.

push_log() riscrive a "system" qualunque tipo non presente in LOG_TYPES: un
tipo nuovo dimenticato nell'insieme non genera errori, sparisce soltanto nel
tipo sbagliato e non e' piu' filtrabile da /logs?type=. E' successo davvero con
"web_task", introdotto dal web node: questo test lo intercetta.

L'endpoint /logs/add accetta tipi arbitrari per contratto (li valida comunque
con LOG_TYPES lato push_log), quindi qui si controllano solo le chiamate con
primo argomento letterale fatte dal codice del control-plane.
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"


def _log_types(tree):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "LOG_TYPES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("LOG_TYPES non trovato in control-plane/main.py")


def _literal_log_types_used(tree):
    used = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "push_log"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            used.setdefault(first.value, node.lineno)
    return used


class LogTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.declared = _log_types(cls.tree)
        cls.used = _literal_log_types_used(cls.tree)

    def test_every_literal_type_used_is_declared(self):
        unknown = {name: line for name, line in self.used.items() if name not in self.declared}
        self.assertEqual(unknown, {},
                         f"tipi usati ma non in LOG_TYPES (verrebbero declassati a 'system'): {unknown}")

    def test_types_introduced_by_recent_features_are_declared(self):
        self.assertIn("web_task", self.declared)
        self.assertIn("mcp", self.declared)

    def test_system_fallback_is_still_declared(self):
        self.assertIn("system", self.declared)

    def test_push_log_still_downgrades_unknown_types(self):
        """La guardia ha senso solo finche' push_log si comporta cosi'."""
        source = ast.unparse(self.tree)
        self.assertIn("if type_ in LOG_TYPES else", source)


if __name__ == "__main__":
    unittest.main()

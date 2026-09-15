import ast
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"


class NetworkRouteGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.functions = {
            node.name: node for node in cls.tree.body if isinstance(node, ast.FunctionDef)
        }

    def test_privileged_routes_call_network_admin_guard(self):
        for name in ("network_status", "network_action", "bottles_announce"):
            with self.subTest(route=name):
                calls = [
                    node.func.id
                    for node in ast.walk(self.functions[name])
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                ]
                self.assertIn("_network_admin_error", calls)

    def test_public_bottle_routes_do_not_require_admin_token(self):
        for name in ("bottles_publish", "bottles_list"):
            with self.subTest(route=name):
                calls = [
                    node.func.id
                    for node in ast.walk(self.functions[name])
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                ]
                self.assertNotIn("_network_admin_error", calls)

    def test_announce_rejects_client_selected_targets(self):
        source = ast.unparse(self.functions["bottles_announce"])
        self.assertIn("'endpoint' in data", source)
        self.assertIn("'relay_url' in data", source)
        self.assertIn("normalize_http_base", source)


if __name__ == "__main__":
    unittest.main()

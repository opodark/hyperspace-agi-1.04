"""Guardie delle route web node e del confine di indirizzabilita'.

Niente Flask qui: le funzioni vengono estratte dal VERO control-plane/main.py
con ast ed eseguite in isolamento (stesso approccio di tests/test_local_nodes.py).
Cosi' il test gira anche dove flask/cryptography non sono installati, mentre la
verifica end-to-end completa vive in scripts/verify_web_node_e2e.py.
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"
ROOT = Path(__file__).parents[1]

WEB_ROUTES = {
    "web_register": "/web/register",
    "web_poll": "/web/poll",
    "web_result": "/web/result",
    "web_enqueue": "/web/tasks",
    "web_status": "/web/status",
}


class EndpointAddressabilityTests(unittest.TestCase):
    """Il gate `_best_endpoint` e' l'unico posto che decide se un nodo e'
    chiamabile: se un web node lo superasse, il CP proverebbe a fare una POST
    su http://browser://<id>/v1/chat/completions."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        wanted = ("_normalize_endpoint", "_ep_to_url", "_best_endpoint")
        nodes = [n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name in wanted]
        cls.scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"),
             cls.scope)

    def test_web_node_has_no_callable_endpoint(self):
        best = self.scope["_best_endpoint"]
        self.assertEqual(best({"endpoint": "browser://abc"}), "")
        self.assertEqual(best({"endpoint": "browser://abc", "is_web_node": True}), "")
        self.assertEqual(best({"endpoint": "", "is_web_node": True}), "")

    def test_web_public_endpoint_is_ignored_too(self):
        best = self.scope["_best_endpoint"]
        self.assertEqual(best({"endpoint": "", "public_endpoint": "browser://abc"}), "")

    def test_regular_nodes_still_resolve(self):
        best = self.scope["_best_endpoint"]
        self.assertEqual(best({"endpoint": "http://mac:8084"}), "http://mac:8084")
        self.assertEqual(best({"endpoint": "mac:8084"}), "http://mac:8084")
        self.assertEqual(best({"endpoint": "https://node.example"}), "https://node.example")
        self.assertEqual(best({"endpoint": "", "public_endpoint": "https://pub.example"}),
                         "https://pub.example")
        self.assertEqual(best({"endpoint": ""}), "")


class WebRouteGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in cls.tree.body if isinstance(n, ast.FunctionDef)}

    def test_routes_are_defined(self):
        for name, path in WEB_ROUTES.items():
            with self.subTest(route=name):
                self.assertIn(name, self.functions)
                source = ast.unparse(self.functions[name].decorator_list)
                self.assertIn(path, source)

    def test_enqueue_requires_the_operator_token(self):
        calls = [n.func.id for n in ast.walk(self.functions["web_enqueue"])
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("_network_admin_error", calls)

    def test_read_only_routes_do_not_require_the_operator_token(self):
        for name in ("web_register", "web_poll", "web_result", "web_status"):
            with self.subTest(route=name):
                calls = [n.func.id for n in ast.walk(self.functions[name])
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
                self.assertNotIn("_network_admin_error", calls)

    def test_registry_is_built_from_env_limits(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("from shared.web_node import", source)
        self.assertIn("web_registry = WebNodeRegistry(", source)
        for key in ("WEB_NODE_MAX_QUEUE", "WEB_NODE_TASK_TTL_S", "WEB_NODE_MAX_POLL_S"):
            self.assertIn(key, source)

    def test_task_types_come_from_the_shared_whitelist(self):
        shared_source = (ROOT / "shared" / "web_node.py").read_text(encoding="utf-8")
        self.assertIn("WEB_SAFE_TASK_TYPES = {", shared_source)
        # Nessun elenco duplicato nel CP: i tipi ammessi vivono in un solo posto.
        cp_source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("WEB_SAFE_TASK_TYPES = {", cp_source)


if __name__ == "__main__":
    unittest.main()

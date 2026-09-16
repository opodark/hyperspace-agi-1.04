import ast
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "node" / "main.py"


class FakeResponse:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class FakeHttpx:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class HermesMemoryVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_read_memory")
        cls.function_code = compile(
            ast.Module(body=[function], type_ignores=[]), "node-memory", "exec",
        )

    def invoke(self, response, limit=100, control_plane="http://control-plane:8085"):
        httpx = FakeHttpx(response)
        scope = {"CONTROL_PLANE_URL": control_plane, "httpx": httpx}
        exec(self.function_code, scope)
        return scope["_read_memory"](limit), httpx

    def test_reads_authoritative_memory_from_control_plane(self):
        expected = [{"id": "m1", "content": "Hermes owns this"}]
        rows, httpx = self.invoke(FakeResponse({"entries": expected}), limit=12)
        self.assertEqual(rows, expected)
        self.assertEqual(httpx.calls[0][0], "http://control-plane:8085/memory")
        self.assertEqual(httpx.calls[0][1]["params"], {"limit": 12})

    def test_unavailable_backend_fails_closed_without_local_fallback(self):
        rows, _ = self.invoke(FakeResponse({}, RuntimeError("offline")))
        self.assertEqual(rows, [])

    def test_missing_control_plane_has_no_node_local_memory(self):
        rows, httpx = self.invoke(FakeResponse({"entries": [{"content": "unused"}]}), control_plane="")
        self.assertEqual(rows, [])
        self.assertEqual(httpx.calls, [])


if __name__ == "__main__":
    unittest.main()

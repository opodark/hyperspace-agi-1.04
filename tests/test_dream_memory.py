import ast
import json
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "node" / "main.py"


class DerivedMemoryVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_read_memory")
        cls.function_code = compile(
            ast.Module(body=[function], type_ignores=[]), "node-memory", "exec",
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.jsonl"

    def read(self, rows):
        self.path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
        )
        scope = {"_MEMORY_FILE": self.path, "_json": json}
        exec(self.function_code, scope)
        return scope["_read_memory"](100)

    def test_latest_revision_of_derived_memory_is_visible(self):
        rows = [
            {"task_id": "ordinary", "content": "original memory"},
            {"id": "insight-1", "type": "dream_insight", "status": "active", "content": "first"},
            {"id": "insight-1", "type": "dream_insight", "status": "active", "content": "re-promoted"},
        ]
        visible = self.read(rows)
        self.assertEqual([row.get("content") for row in visible],
                         ["original memory", "re-promoted"])

    def test_revocation_tombstone_excludes_insight_without_erasing_log(self):
        rows = [
            {"id": "insight-1", "type": "dream_insight", "status": "active", "content": "candidate"},
            {"id": "insight-1", "type": "dream_insight", "status": "revoked", "content": "candidate"},
        ]
        self.assertEqual(self.read(rows), [])
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)

    def test_corrupt_line_does_not_hide_valid_memories(self):
        self.path.write_text('{bad json}\n{"content":"valid"}\n', encoding="utf-8")
        scope = {"_MEMORY_FILE": self.path, "_json": json}
        exec(self.function_code, scope)
        self.assertEqual(scope["_read_memory"](100), [{"content": "valid"}])


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ForgeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main_source = (ROOT / "control-plane" / "main.py").read_text(encoding="utf-8")
        cls.main_tree = ast.parse(cls.main_source)
        cls.dashboard = (ROOT / "control-plane" / "dashboard.html").read_text(encoding="utf-8")
        cls.compose = (ROOT / "docker-compose.windows.yml").read_text(encoding="utf-8")

    def test_backend_exposes_update_and_ide_config_routes(self):
        routes = {}
        for node in self.main_tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "app.route":
                    routes[ast.literal_eval(decorator.args[0])] = ast.unparse(decorator)
        self.assertIn("methods=['PUT']", routes["/forge/artifacts/<artifact_id>"])
        self.assertIn("/forge/config", routes)

    def test_updates_increment_version_and_return_to_draft(self):
        update = next(node for node in self.main_tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == "forge_update")
        source = ast.unparse(update)
        self.assertIn("int(item.get('version', 1)) + 1", source)
        self.assertIn("'status': 'draft'", source)

    def test_source_sidecar_is_written_for_the_ide(self):
        write = next(node for node in self.main_tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "_forge_write")
        source = ast.unparse(write)
        self.assertIn("{'tool': '.py', 'patch': '.diff'}.get", source)
        self.assertIn("handle.write(item.get('source', ''))", source)
        self.assertIn("${HS_DATA_DIR}/forge:/home/coder/forge:rw", self.compose)

    def test_dashboard_distinguishes_create_from_update(self):
        self.assertIn("window.forgeEditingId=item.id", self.dashboard)
        self.assertIn("method=id?'PUT':'POST'", self.dashboard)
        self.assertIn("function forgeNew()", self.dashboard)
        self.assertIn("forgeRequest('/forge/config')", self.dashboard)

    def test_patch_artifacts_are_inert_unified_diffs(self):
        self.assertIn('_FORGE_TYPES = {"tool", "skill", "patch"}', self.main_source)
        validate = next(node for node in self.main_tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_forge_validate")
        source = ast.unparse(validate)
        self.assertIn("kind == 'patch'", source)
        self.assertIn("patch must be a unified text diff", source)
        generate = next(node for node in self.main_tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "forge_generate")
        self.assertIn("kind not in {'tool', 'skill'}", ast.unparse(generate))
        self.assertIn('<option value="patch">Patch sandbox</option>', self.dashboard)

        namespace = {"ast": ast, "re": re}
        module = ast.fix_missing_locations(ast.Module(body=[validate], type_ignores=[]))
        exec(compile(module, "<forge-validate>", "exec"), namespace)
        valid = namespace["_forge_validate"](
            "patch", "--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-old\n+new\n")
        self.assertTrue(valid["valid"], valid)
        invalid = namespace["_forge_validate"](
            "patch", "Binary files differ: a/image.png and b/image.png\n")
        self.assertFalse(invalid["valid"], invalid)


if __name__ == "__main__":
    unittest.main()

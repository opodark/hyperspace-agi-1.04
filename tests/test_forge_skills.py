# SPDX-License-Identifier: Apache-2.0
import ast
import hashlib
import json
import re
import shutil
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
import os

from flask import Flask, jsonify, request
from shared.forge_skills import ECC_BUNDLE_DIR, attach_skills, load_ecc_bundle, source_hash


def _symlink_creation_available() -> bool:
    """Windows richiede privilegi (o Developer Mode) per creare un symlink.

    Senza, il caso 'symlink' del test non e' verificabile: meglio saltarlo
    dichiarandolo che fingere che sia passato.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            (Path(tmp) / "link").symlink_to(Path(tmp) / "target")
        except (OSError, NotImplementedError):
            return False
    return True


SYMLINKS_AVAILABLE = _symlink_creation_available()


class BundleTests(unittest.TestCase):
    def test_pinned_bundle_has_license_and_two_verified_sources(self):
        items = load_ecc_bundle(ECC_BUNDLE_DIR)
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(source_hash(item["source"]), item["provenance"]["sha256"])
            self.assertIn("Permission is hereby granted", item["provenance"]["license_text"])

    def test_tampering_symlinks_and_unexpected_files_rejected(self):
        for change in ("tamper", "symlink", "manifest"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "ecc"
                shutil.copytree(ECC_BUNDLE_DIR, root)
                target = root / "skills/security-review/SKILL.md"
                if change == "tamper":
                    target.write_text("changed")
                elif change == "symlink":
                    if not SYMLINKS_AVAILABLE:
                        self.skipTest("questo filesystem/utente non puo' creare symlink")
                    target.unlink()
                    target.symlink_to(ECC_BUNDLE_DIR / "skills/security-review/SKILL.md")
                else:
                    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                    manifest["files"]["../../outside"] = "0" * 64
                    (root / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    load_ecc_bundle(root)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.item = {"type": "skill", "status": "approved", "version": 1,
                     "source": "# Example\nInspect the result.",
                     "approved_source_sha256": source_hash("# Example\nInspect the result.")}

    def test_opt_in_only_and_extension_not_forwarded(self):
        data = {"messages": [{"role": "user", "content": "Hello"}]}
        self.assertEqual(attach_skills(data, lambda _: self.fail("unexpected load")), data)
        selected = dict(data, hyperspace_skills=["example"])
        result = attach_skills(selected, lambda _: self.item)
        self.assertNotIn("hyperspace_skills", result)
        self.assertIn(self.item["source"], result["messages"][0]["content"])
        self.assertEqual(result["messages"][-1], data["messages"][0])
        self.assertEqual(len(data["messages"]), 1)

    def test_draft_disabled_modified_and_oversized_skills_rejected(self):
        for field, value in (("status", "draft"), ("status", "disabled"),
                             ("type", "tool"), ("source", "tampered"),
                             ("approved_source_sha256", None)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                attach_skills({"hyperspace_skills": ["example"]},
                              lambda _: dict(self.item, **{field: value}))
        big = "x" * 24000
        with self.assertRaises(ValueError):
            attach_skills({"hyperspace_skills": ["example"]}, lambda _: dict(
                self.item, source=big, approved_source_sha256=source_hash(big)))

    def test_invalid_selection_and_path_traversal(self):
        for ids in (None, "example", ["../secret"], ["a", "a"], ["a", "b", "c"], [3]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                attach_skills({"hyperspace_skills": ids}, lambda _: self.fail("invalid ID read"))

    def test_both_ecc_skills_fit_context_budget(self):
        items = load_ecc_bundle(ECC_BUNDLE_DIR)
        by_id = {item["id"]: dict(item, status="approved", version=1,
                                  approved_source_sha256=source_hash(item["source"])) for item in items}
        result = attach_skills({"hyperspace_skills": list(by_id), "messages": [
            {"role": "system", "content": "System"}, {"role": "user", "content": "Task"}]}, by_id.__getitem__)
        self.assertEqual(result["messages"][0]["content"], "System")
        self.assertEqual(result["messages"][1]["role"], "user")


class ForgeRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        app = Flask(__name__)
        app.testing = True
        self.ns = dict(globals(), app=app, FORGE_DIR=self.tmp.name, FORGE_ADMIN_TOKEN="test-token",
                       _forge_lock=threading.Lock(), _FORGE_TYPES={"tool", "skill"},
                       _FORGE_STATES={"draft", "review", "approved", "disabled"},
                       push_log=lambda *args, **kwargs: None)
        source = Path(__file__).parents[1] / "control-plane/main.py"
        names = {"_forge_path", "_forge_write", "_forge_authorized", "_forge_validate",
                 "_forge_read_skill", "forge_import_ecc", "forge_update", "forge_status",
                 "v1_chat_completions"}
        # encoding esplicito: il file ha caratteri non-ASCII (i banner di sezione)
        # e su Windows la codifica di default del sistema non e' UTF-8.
        nodes = [node for node in ast.parse(source.read_text(encoding="utf-8")).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.ns)
        self.client = app.test_client()
        self.headers = {"X-Hyperspace-Forge-Token": "test-token"}

    def test_import_auth_idempotence_and_reapproval_after_edit(self):
        self.assertEqual(self.client.post("/forge/import/ecc").status_code, 403)
        response = self.client.post("/forge/import/ecc", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        artifact_id = response.json["artifacts"][0]["id"]
        item = self.ns["_forge_read_skill"](artifact_id)
        self.assertEqual(item["status"], "draft")
        approved = self.client.post(f"/forge/artifacts/{artifact_id}/status", headers=self.headers,
                                    json={"status": "approved"})
        self.assertEqual(approved.json["approved_source_sha256"], source_hash(item["source"]))
        self.client.put(f"/forge/artifacts/{artifact_id}", json={"source": "# Local revision\n"})
        again = self.client.post("/forge/import/ecc", headers=self.headers)
        self.assertFalse(any(item["created"] for item in again.json["artifacts"]))
        edited = self.ns["_forge_read_skill"](artifact_id)
        self.assertEqual(edited["source"], "# Local revision\n")
        self.assertEqual(edited["status"], "draft")
        self.assertNotIn("approved_source_sha256", edited)
        self.assertIn("provenance", edited)
        rejected = self.client.post("/v1/chat/completions", json={"hyperspace_skills": [artifact_id]})
        self.assertEqual(rejected.status_code, 400)


if __name__ == "__main__":
    unittest.main()

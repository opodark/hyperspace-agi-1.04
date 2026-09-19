# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from shared.development_dream import DevelopmentDreamJournal, NightlyDevelopmentDream


class FakeSandbox:
    def __init__(self, changed=True, tests_ok=True):
        self.changed, self.tests_ok = changed, tests_ok
        self.created, self.discarded = [], []

    def status(self):
        return {"enabled": True, "available": True, "backend": "fake"}

    def call(self, action, args):
        if action == "create":
            workspace = f"fake:{len(self.created) + 1}"
            self.created.append((workspace, args["label"]))
            return {"ok": True, "workspace_id": workspace, "backend": "fake"}
        if action == "diff":
            return ({"ok": True, "changed_files": ["a.py"],
                     "diff": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n", "truncated": False}
                    if self.changed else {"ok": True, "changed_files": [], "diff": ""})
        if action == "run":
            is_test = args["argv"][:2] == ["python3", "-m"]
            return {"ok": self.tests_ok if is_test else True, "output": "ok", "exit_code": 0}
        if action == "discard":
            self.discarded.append(args["workspace_id"])
            return {"ok": True}
        return {"ok": True}


class NightlyDevelopmentDreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1_700_000_000

    def worker(self, sandbox=None, **kwargs):
        return NightlyDevelopmentDream(
            self.temp.name, sandbox or FakeSandbox(), lambda prompt: "agent done",
            enabled=True, start_hour=0, end_hour=24, idle_seconds=300,
            clock=lambda: self.now, **kwargs)

    def test_due_requires_window_idle_and_one_run_per_day(self):
        worker = self.worker()
        self.assertFalse(worker.due(self.now - 299))
        self.assertTrue(worker.due(self.now - 300))
        worker.run_once()
        self.assertFalse(worker.due(self.now - 1000))

    def test_verified_patch_becomes_candidate_and_keeps_primary(self):
        sandbox = FakeSandbox()
        report = self.worker(sandbox).run_once("Improve a test")
        self.assertEqual(report["status"], "candidate")
        self.assertTrue(report["verification"]["ok"])
        self.assertEqual(len(sandbox.created), 2)
        self.assertEqual(sandbox.discarded, ["fake:2"])
        self.assertTrue(Path(self.temp.name, "development_dreams.jsonl").exists())

    def test_no_change_does_not_create_verifier(self):
        sandbox = FakeSandbox(changed=False)
        report = self.worker(sandbox).run_once()
        self.assertEqual(report["status"], "no_changes")
        self.assertEqual(len(sandbox.created), 1)
        self.assertEqual(sandbox.discarded, ["fake:1"])

    def test_failed_tests_never_become_candidate(self):
        report = self.worker(FakeSandbox(tests_ok=False)).run_once()
        self.assertEqual(report["status"], "failed")


class DevelopmentDreamJournalTests(unittest.TestCase):
    def test_approval_is_attributed_but_does_not_apply_code(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = DevelopmentDreamJournal(directory)
            journal.append({"id": "dev-1", "status": "candidate", "reviews": []})
            row = journal.review("dev-1", "approve", "alice", "tests reviewed", timestamp=1000)
            self.assertEqual(row["status"], "approved")
            self.assertEqual(row["reviews"][0]["reviewer"], "alice")
            self.assertNotIn("applied", row)


if __name__ == "__main__":
    unittest.main()

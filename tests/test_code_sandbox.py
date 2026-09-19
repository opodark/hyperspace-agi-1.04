# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import re
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from shared.code_sandbox import CodeSandboxClient, HybridCodeSandboxClient, SandboxUnavailable


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("sandbox_runner", ROOT / "sandbox" / "runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.seed = root / "seed"
        self.exchange = root / "exchange"
        self.workspaces = root / "workspaces"
        self.seed.mkdir()
        self.exchange.mkdir()
        self.workspaces.mkdir()
        (self.seed / "app.py").write_text("print('old')\n", encoding="utf-8")
        (self.seed / ".env").write_text("SECRET=must-not-copy\n", encoding="utf-8")
        (self.seed / "data").mkdir()
        (self.seed / "data" / "runtime.txt").write_text("private", encoding="utf-8")
        runner.SEED = self.seed
        runner.EXCHANGE = self.exchange
        runner.WORKSPACES = self.workspaces
        runner.MAX_WORKSPACES = 6

    def create(self):
        return runner._create({"label": "test"})["workspace_id"]

    def test_workspace_is_a_sanitized_copy_not_the_source(self):
        workspace_id = self.create()
        repo = self.workspaces / workspace_id / "repo"
        self.assertEqual((repo / "app.py").read_text(encoding="utf-8"), "print('old')\n")
        self.assertFalse((repo / ".env").exists())
        self.assertFalse((repo / "data").exists())
        (repo / "app.py").write_text("changed", encoding="utf-8")
        self.assertEqual((self.seed / "app.py").read_text(encoding="utf-8"), "print('old')\n")

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        workspace_id = self.create()
        with self.assertRaises(ValueError):
            runner._read({"workspace_id": workspace_id, "path": "../../etc/passwd"})
        with self.assertRaises(ValueError):
            runner._write({"workspace_id": workspace_id, "path": "/tmp/escape", "content": "x"})

    def test_replace_is_exact_and_diff_is_reviewable(self):
        workspace_id = self.create()
        result = runner._replace({
            "workspace_id": workspace_id,
            "path": "app.py",
            "old": "old",
            "new": "new",
            "expected_occurrences": 1,
        })
        self.assertTrue(result["ok"])
        diff = runner._diff({"workspace_id": workspace_id})
        self.assertEqual(diff["changed_files"], ["app.py"])
        self.assertIn("-print('old')", diff["diff"])
        self.assertIn("+print('new')", diff["diff"])

    def test_generated_patch_applies_to_a_clean_workspace(self):
        proposal_id = self.create()
        runner._replace({
            "workspace_id": proposal_id,
            "path": "app.py",
            "old": "old",
            "new": "verified",
            "expected_occurrences": 1,
        })
        runner._write({"workspace_id": proposal_id, "path": "new_file.py",
                       "content": "VALUE = 42\n"})
        proposal = runner._diff({"workspace_id": proposal_id})

        verifier_id = self.create()
        runner._write({"workspace_id": verifier_id, "path": ".proposal.patch",
                       "content": proposal["diff"]})
        checked = runner._run({"workspace_id": verifier_id,
                               "argv": ["git", "apply", "--check", ".proposal.patch"]})
        self.assertTrue(checked["ok"], checked.get("output"))
        applied = runner._run({"workspace_id": verifier_id,
                               "argv": ["git", "apply", ".proposal.patch"]})
        self.assertTrue(applied["ok"], applied.get("output"))
        repo = self.workspaces / verifier_id / "repo"
        self.assertEqual((repo / "app.py").read_text(encoding="utf-8"),
                         "print('verified')\n")
        self.assertEqual((repo / "new_file.py").read_text(encoding="utf-8"),
                         "VALUE = 42\n")

    def test_command_allowlist_rejects_shells(self):
        workspace_id = self.create()
        with self.assertRaises(ValueError):
            runner._run({"workspace_id": workspace_id, "argv": ["sh", "-c", "echo unsafe"]})

    def test_allowed_command_runs_without_shell(self):
        workspace_id = self.create()
        # Il runner ammette sia `python` sia `python3` (vedi ALLOWED_EXECUTABLES):
        # nell'immagine sandbox Linux esiste `python`, su macOS solo `python3`.
        # Fissare "python" qui renderebbe il test non portatile fuori container.
        python_exe = "python" if shutil.which("python") else "python3"
        result = runner._run({
            "workspace_id": workspace_id,
            "argv": [python_exe, "-c", "print('sandbox-ok')"],
            "timeout": 5,
        })
        self.assertTrue(result["ok"])
        self.assertIn("sandbox-ok", result["output"])

    def test_workspace_limit_is_enforced(self):
        runner.MAX_WORKSPACES = 1
        self.create()
        with self.assertRaises(RuntimeError):
            self.create()


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.exchange = Path(self.temp.name)
        (self.exchange / "jobs").mkdir()
        (self.exchange / "results").mkdir()
        (self.exchange / "heartbeat").write_text(str(time.time()), encoding="ascii")

    def test_disabled_client_never_submits_jobs(self):
        client = CodeSandboxClient(self.exchange, enabled=False)
        with self.assertRaises(SandboxUnavailable):
            client.call("status")
        self.assertEqual(list((self.exchange / "jobs").iterdir()), [])

    def test_file_queue_round_trip(self):
        client = CodeSandboxClient(self.exchange, enabled=True)

        def respond():
            deadline = time.time() + 2
            while time.time() < deadline:
                jobs = list((self.exchange / "jobs").glob("*.json"))
                if jobs:
                    job = json.loads(jobs[0].read_text(encoding="utf-8"))
                    (self.exchange / "results" / f"{job['job_id']}.json").write_text(
                        json.dumps({"ok": True, "job_id": job["job_id"]}), encoding="utf-8",
                    )
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=respond)
        thread.start()
        result = client.call("status", timeout=2)
        thread.join()
        self.assertTrue(result["ok"])
        self.assertEqual(list((self.exchange / "results").iterdir()), [])


class FakeBackend:
    default_timeout = 30

    def __init__(self, name, available):
        self.name, self.available = name, available
        self.calls = []

    def status(self):
        return {"enabled": True, "available": self.available, "backend": self.name}

    def call(self, action, arguments=None, timeout=None):
        self.calls.append((action, dict(arguments or {})))
        return {"ok": True, "workspace_id": "workspace-1"}


class HybridClientTests(unittest.TestCase):
    def test_prefers_sbx_and_routes_tagged_workspace_back_to_it(self):
        primary, fallback = FakeBackend("sbx", True), FakeBackend("docker", True)
        client = HybridCodeSandboxClient(primary, fallback, enabled=True)
        created = client.call("create", {"label": "nightly"})
        self.assertEqual(created["workspace_id"], "sbx:workspace-1")
        client.call("diff", {"workspace_id": created["workspace_id"]})
        self.assertEqual(primary.calls[-1][1]["workspace_id"], "workspace-1")
        self.assertEqual(fallback.calls, [])

    def test_falls_back_when_creating_a_new_workspace(self):
        primary, fallback = FakeBackend("sbx", False), FakeBackend("docker", True)
        client = HybridCodeSandboxClient(primary, fallback, enabled=True)
        created = client.call("create", {})
        self.assertEqual(created["workspace_id"], "docker:workspace-1")
        self.assertEqual(len(fallback.calls), 1)


class ComposeIsolationTests(unittest.TestCase):
    def test_both_profiles_enforce_offline_container_boundaries(self):
        for filename in ("docker-compose.yml", "docker-compose.windows.yml"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            match = re.search(r"(?ms)^  code-sandbox:\n.*?(?=^  \S|\Z)", text)
            self.assertIsNotNone(match, f"missing code-sandbox service in {filename}")
            section = match.group(0)
            with self.subTest(filename=filename):
                self.assertIn("network_mode: none", section)
                self.assertIn("read_only: true", section)
                self.assertIn("no-new-privileges:true", section)
                self.assertNotIn("docker.sock", section)

    def test_docker_build_context_excludes_runtime_secrets(self):
        text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn(".env*", text)
        self.assertIn("data", text)
        self.assertIn(".git", text)


if __name__ == "__main__":
    unittest.main()

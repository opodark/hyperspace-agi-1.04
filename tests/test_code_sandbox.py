# SPDX-License-Identifier: Apache-2.0
import importlib.util
import ast
import json
import re
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
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

    def test_read_only_image_seed_produces_writable_disposable_workspace(self):
        import stat
        (self.seed / "app.py").chmod(0o444)
        self.seed.chmod(0o555)
        self.addCleanup(self.seed.chmod, 0o755)
        workspace_id = self.create()
        repo = self.workspaces / workspace_id / "repo"
        self.assertTrue(repo.stat().st_mode & stat.S_IWUSR)
        self.assertTrue((repo / "app.py").stat().st_mode & stat.S_IWUSR)
        result = runner._write({"workspace_id": workspace_id, "path": "new.py", "content": "x = 1\n"})
        self.assertTrue(result["ok"])
        self.assertTrue(runner._discard({"workspace_id": workspace_id})["ok"])
        self.assertFalse((self.workspaces / workspace_id).exists())
        self.assertFalse((self.seed / "app.py").stat().st_mode & stat.S_IWUSR)

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

    def check(self, tool, source, path="sample.py", **kwargs):
        workspace_id = self.create()
        runner._write({"workspace_id": workspace_id, "path": path, "content": source})
        return runner._check({"workspace_id": workspace_id, "tool_id": tool,
                              "path": path, **kwargs})

    def test_catalog_reports_missing_dependency(self):
        original = runner.importlib.util.find_spec
        with patch.object(runner.importlib.util, "find_spec",
                          side_effect=lambda name: None if name == "bandit" else original(name)):
            catalog = runner._catalog({})
            self.assertFalse(next(x for x in catalog["tools"] if x["tool_id"] == "bandit")["available"])
            result = self.check("bandit", "x = 1\n")
        self.assertFalse(result["passed"])
        self.assertFalse(result["completed"])
        self.assertIn("unavailable", result["error"])

    @unittest.skipUnless(runner.importlib.util.find_spec("bandit"), "Bandit not installed")
    def test_bandit_finds_eval_and_accepts_clean_code(self):
        result = self.check("bandit", "value = eval(input())  # nosec\n")
        self.assertTrue(result["completed"], result)
        self.assertFalse(result["passed"])
        self.assertTrue(any(f["rule"] == "B307" and f["path"] == "sample.py"
                            and f["line"] == 1 for f in result["findings"]), result)
        clean = self.check("bandit", "value = 42\n")
        self.assertTrue(clean["passed"], clean)

    @unittest.skipUnless(runner.importlib.util.find_spec("bandit"), "Bandit not installed")
    def test_bandit_parse_errors_and_empty_scans_do_not_pass(self):
        for source in ("def broken(\n", "# nothing to scan\n"):
            result = self.check("bandit", source)
            self.assertFalse(result["passed"], result)
            self.assertFalse(result["completed"], result)

    def test_bandit_malformed_report_does_not_pass(self):
        with patch.object(runner, "_catalog", return_value={"tools": [
                {"tool_id": "bandit", "version": "test", "available": True}]}), \
             patch.object(runner, "_capture", return_value={"ok": True, "completed": True,
                 "exit_code": 0, "output": "not json", "stderr": "", "truncated": False}):
            result = self.check("bandit", "value = 42\n")
        self.assertFalse(result["passed"])
        self.assertFalse(result["completed"])

    @unittest.skipUnless(runner.importlib.util.find_spec("pytest"), "pytest not installed")
    def test_pytest_pass_fail_and_no_tests(self):
        for source, passed, code in (("def test_ok(): assert 1 == 1\n", True, 0),
                                     ("def test_bad(): assert False\n", False, 1),
                                     ("value = 1\n", False, 5)):
            result = self.check("pytest", source, path="test_sample.py")
            self.assertTrue(result["completed"], result)
            self.assertEqual(result["passed"], passed, result)
            self.assertEqual(result["exit_code"], code, result)

    def test_profile_timeout_and_output_limit(self):
        result = self.check("profile", "sum(range(100))\n")
        self.assertTrue(result["passed"], result)
        self.assertIn("function calls", result["output"])
        timed = self.check("profile", "import time\ntime.sleep(10)\n", timeout=1)
        self.assertFalse(timed["completed"])
        self.assertFalse(timed["passed"])
        with patch.object(runner, "MAX_OUTPUT_BYTES", 1024):
            flooded = self.check("profile", "print('x' * 100000)\n")
        self.assertTrue(flooded["truncated"])
        self.assertFalse(flooded["passed"])
        self.assertLessEqual(len(flooded["output"]), 1024)

    def test_check_rejects_target_outside_workspace(self):
        workspace_id = self.create()
        for path in ("../../seed/app.py", str(self.seed / "app.py")):
            with self.assertRaises(ValueError):
                runner._check({"workspace_id": workspace_id, "tool_id": "profile", "path": path})

    def test_unittest_discovery_requires_tests(self):
        workspace_id = self.create()
        result = runner._check({"workspace_id": workspace_id, "tool_id": "unittest"})
        self.assertFalse(result["passed"], result)
        runner._write({"workspace_id": workspace_id, "path": "test_sample.py", "content":
                       "import unittest\nclass Example(unittest.TestCase):\n"
                       "    def test_ok(self): self.assertEqual(2 + 2, 4)\n"})
        result = runner._check({"workspace_id": workspace_id, "tool_id": "unittest"})
        self.assertTrue(result["passed"], result)

    @unittest.skipUnless(runner.importlib.util.find_spec("bandit"), "Bandit not installed")
    def test_bandit_rejects_nested_symlink_escape(self):
        workspace_id = self.create()
        repo = self.workspaces / workspace_id / "repo"
        (repo / "outside.py").symlink_to(self.seed / "app.py")
        with self.assertRaises(ValueError):
            runner._check({"workspace_id": workspace_id, "tool_id": "bandit"})

    def test_run_preserves_stderr_for_existing_callers(self):
        workspace_id = self.create()
        python_exe = "python" if shutil.which("python") else "python3"
        result = runner._run({"workspace_id": workspace_id, "argv": [
            python_exe, "-c", "import sys; print('diagnostic', file=sys.stderr); sys.exit(2)"]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("diagnostic", result["output"])


class ControlPlanePresetTests(unittest.TestCase):
    def test_handler_forwards_preset_parameters(self):
        tree = ast.parse((ROOT / "control-plane/main.py").read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_tool_code_sandbox")
        backend = FakeBackend("docker", True)
        namespace = {"code_sandbox": backend, "json": json,
                     "push_log": lambda *args, **kwargs: None, "SandboxUnavailable": SandboxUnavailable}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "handler", "exec"), namespace)
        namespace["_tool_code_sandbox"]({"action": "check", "tool_id": "bandit",
                                         "path": "shared", "workspace_id": "docker:12345678"})
        self.assertEqual(backend.calls[-1], ("check", {"tool_id": "bandit", "path": "shared",
                                                        "workspace_id": "docker:12345678"}))


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
    def test_development_checks_stay_on_explicit_backend(self):
        primary, fallback = FakeBackend("sbx", True), FakeBackend("docker", True)
        client = HybridCodeSandboxClient(primary, fallback, enabled=True)
        created = client.call("create", {"backend": "docker"})
        self.assertEqual(created["workspace_id"], "docker:workspace-1")
        client.call("catalog")
        client.call("check", {"workspace_id": created["workspace_id"], "tool_id": "bandit"})
        self.assertEqual(fallback.calls[-1][0], "check")
        unsupported = client.call("check", {"workspace_id": "sbx:workspace-1"})
        self.assertFalse(unsupported["passed"])
        self.assertEqual(primary.calls, [])

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

# SPDX-License-Identifier: Apache-2.0
"""shell_run: le mani dell'agente, e le pareti che le tengono.

Il confine non e' un pattern: e' `shell=False` + allowlist di eseguibili per
nome + cwd dentro directory ammesse. Qui si verifica che dica *no* in modo
leggibile quando il chiamante crede di parlare con una shell, che nasca spento,
e che output e tempo siano limitati dal server e non dal chiamante.
"""
import ast
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("hostctl_agent", ROOT / "hostctl" / "agent.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)
MAIN_SOURCE = ROOT / "control-plane" / "main.py"


def _settings(values, key, default=""):
    return values.get(key, agent._file_env.get(key, default))


def _enabled(**extra):
    """Configurazione accesa per il test: che il default sia spento lo verifica
    un test a parte."""
    values = {"SHELL_RUN_ENABLED": "true", "SHELL_ALLOWED_DIRS": str(ROOT), **extra}
    return patch.object(agent, "config_value", lambda key, default="": _settings(values, key, default))


class DisabledByDefaultTests(unittest.TestCase):
    def test_refuses_until_the_operator_turns_it_on(self):
        with patch.object(agent, "config_value", lambda key, default="": default):
            result = agent.action_shell_run({"argv": ["git", "status"]})
        self.assertFalse(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertIn("SHELL_RUN_ENABLED", result["error"])

    def test_registered_as_a_non_read_only_action(self):
        self.assertIn("shell_run", agent.ACTIONS)
        self.assertNotIn("shell_run", agent.READ_ONLY_ACTIONS)


class ArgvValidationTests(unittest.TestCase):
    def test_rejects_a_shell_string_instead_of_an_argv(self):
        with _enabled(), self.assertRaises(ValueError) as ctx:
            agent.action_shell_run({"argv": "git status --short"})
        self.assertIn("lista", str(ctx.exception))

    def test_rejects_shell_operators_as_arguments(self):
        for operator in (";", "&&", "|", ">>", "$("):
            with self.subTest(operator=operator), _enabled(), self.assertRaises(ValueError):
                agent.action_shell_run({"argv": ["git", "status", operator, "rm -rf /"]})

    def test_rejects_a_shell_as_the_executable(self):
        for shell in ("sh", "bash", "cmd", "powershell", "pwsh"):
            with self.subTest(shell=shell), _enabled(), self.assertRaises(ValueError) as ctx:
                agent.action_shell_run({"argv": [shell, "-c", "echo hi"]})
            self.assertIn("non ammesso", str(ctx.exception))

    def test_rejects_control_characters_and_empty_arguments(self):
        with _enabled(), self.assertRaises(ValueError):
            agent.action_shell_run({"argv": ["git", "status\nrm -rf /"]})
        with _enabled(), self.assertRaises(ValueError):
            agent.action_shell_run({"argv": ["git", "   "]})
        with _enabled(), self.assertRaises(ValueError):
            agent.action_shell_run({"argv": []})

    def test_rejects_too_many_arguments(self):
        with _enabled(), self.assertRaises(ValueError):
            agent.action_shell_run({"argv": ["git"] + ["a"] * 40})

    def test_allowlist_is_configurable_and_narrows(self):
        with _enabled(SHELL_ALLOWED_EXECUTABLES="git"):
            with self.assertRaises(ValueError) as ctx:
                agent.action_shell_run({"argv": ["python", "-c", "print(1)"]})
        self.assertIn("python", str(ctx.exception))


class CwdTests(unittest.TestCase):
    def test_rejects_a_cwd_outside_the_allowed_directories(self):
        outside = Path.home()
        if outside == ROOT:
            self.skipTest("home e repo coincidono: caso non verificabile")
        with _enabled(), self.assertRaises(ValueError) as ctx:
            agent.action_shell_run({"argv": ["git", "status"], "cwd": str(outside)})
        self.assertIn("SHELL_ALLOWED_DIRS", str(ctx.exception))

    def test_rejects_a_path_that_escapes_with_two_dots(self):
        with _enabled(), self.assertRaises(ValueError):
            agent.action_shell_run({"argv": ["git", "status"], "cwd": "../../.."})

    def test_accepts_a_subdirectory_and_defaults_to_the_root(self):
        seen = []
        with _enabled(), patch.object(
                agent, "_shell_run_exec",
                lambda argv, cwd, timeout, max_bytes: seen.append(cwd) or {"ok": True}):
            resolved = agent.action_shell_run({"argv": ["git", "status"], "cwd": str(ROOT / "docs")})
            agent.action_shell_run({"argv": ["git", "status"]})
        self.assertEqual(Path(resolved["cwd"]), (ROOT / "docs").resolve())
        self.assertEqual(seen[0], (ROOT / "docs").resolve())
        self.assertEqual(seen[1], ROOT.resolve())


class ExecutionTests(unittest.TestCase):
    def test_timeout_and_output_cap_come_from_the_server_not_the_caller(self):
        seen = {}

        def fake_exec(argv, cwd, timeout, max_bytes):
            seen.update(argv=argv, timeout=timeout, max_bytes=max_bytes)
            return {"ok": True, "exit_code": 0, "output": "", "truncated": False}

        with _enabled(SHELL_MAX_TIMEOUT="20", SHELL_MAX_OUTPUT_BYTES="4096"), \
                patch.object(agent, "_shell_run_exec", fake_exec):
            result = agent.action_shell_run({"argv": ["git", "status"], "timeout": 999})
        self.assertEqual(seen["timeout"], 20)          # il chiamante non alza il tetto
        self.assertEqual(seen["max_bytes"], 4096)
        self.assertEqual(result["timeout_s"], 20)
        self.assertIn("git", seen["argv"][0].lower())

    def test_timeout_must_be_an_integer(self):
        with _enabled(), self.assertRaises(ValueError):
            agent.action_shell_run({"argv": ["git", "status"], "timeout": "subito"})

    def test_unknown_executable_is_reported_not_raised(self):
        with _enabled(), patch.object(agent.shutil, "which", return_value=None):
            result = agent.action_shell_run({"argv": ["git", "status"]})
        self.assertFalse(result["ok"])
        self.assertIn("non trovato", result["error"])

    def test_cap_truncates_and_says_so(self):
        text, truncated = agent._shell_cap("x" * 5000, 1024)
        self.assertTrue(truncated)
        self.assertLessEqual(len(text.encode("utf-8")), 1024)
        text, truncated = agent._shell_cap("breve", 1024)
        self.assertFalse(truncated)
        self.assertEqual(text, "breve")

    def test_a_real_command_runs_and_reports_its_exit_code(self):
        """Sul campo: `python` e' in allowlist e la radice del repo e' il cwd."""
        import sys
        with _enabled():
            result = agent.action_shell_run(
                {"argv": [sys.executable, "-c", "print('shell-ok')"], "timeout": 30})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("shell-ok", result["output"])
        self.assertFalse(result["truncated"])
        self.assertIn("duration_ms", result)

    def test_a_failing_command_is_not_an_exception(self):
        import sys
        with _enabled():
            result = agent.action_shell_run(
                {"argv": [sys.executable, "-c", "import sys; sys.exit(3)"], "timeout": 30})
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 3)


class HttpSurfaceTests(unittest.TestCase):
    def test_the_action_is_exposed_by_the_single_token_protected_route(self):
        """La route e' una sola (`POST /action`, con token Bearer): l'esposizione
        di una nuova azione dipende solo da ACTIONS."""
        self.assertIn("shell_run", sorted(agent.ACTIONS))
        self.assertTrue(callable(agent.Handler.do_POST))


class _FakeResponse:
    content = b"json"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self, calls, payload):
        self.calls = calls
        self.payload = payload

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self.payload)


class ControlPlaneToolTests(unittest.TestCase):
    """Il tool sta nel catalogo solo se voluto e possibile, e l'audit non porta
    l'output del comando (che puo' essere grande e contenere dati del progetto)."""

    @classmethod
    def setUpClass(cls):
        cls.source = MAIN_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        cls.wanted = {"_tool_shell_run", "_shell_run_label"}
        cls.functions = [n for n in tree.body
                         if isinstance(n, ast.FunctionDef) and n.name in cls.wanted]

    def _handler(self, *, enabled=True, configured=True, calls=None, logs=None, payload=None):
        namespace = {
            "SHELL_RUN_ENABLED": enabled,
            "_hostctl_configured": lambda: configured,
            "HOSTCTL_URL": "http://host:8765",
            "_hostctl_headers": lambda: {"Authorization": "Bearer x"},
            "requests": _FakeRequests(calls if calls is not None else [],
                                      payload or {"ok": True, "exit_code": 0,
                                                  "output": "SECRET-OUTPUT"}),
            "json": json,
            "push_log": lambda *a, **kwargs: (logs if logs is not None else []).append(
                {"args": a, **kwargs}),
        }
        body = [self._label_node] + [n for n in self.functions if n.name == "_tool_shell_run"]
        exec(compile(ast.Module(body=body, type_ignores=[]), "handler", "exec"), namespace)
        return namespace["_tool_shell_run"]

    @property
    def _label_node(self):
        return next(n for n in self.functions if n.name == "_shell_run_label")

    def test_catalogue_entry_is_conditional_not_static(self):
        # Fail-closed: fuori dal blocco condizionato il tool non e' nel catalogo.
        self.assertIn("if shell_run_available():", self.source)
        self.assertIn("_NATIVE_TOOLS.append(SHELL_RUN_TOOL)", self.source)
        tree = ast.parse(self.source)
        native = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(getattr(t, "id", "") == "_NATIVE_TOOLS" for t in n.targets))
        self.assertNotIn("shell_run", ast.unparse(native.value))

    def test_the_dispatcher_calls_it(self):
        tree = ast.parse(self.source)
        dispatcher = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                          and n.name == "_execute_tool_call")
        self.assertIn("shell_run", ast.unparse(dispatcher))

    def test_disabled_and_unconfigured_are_answered_without_calling_the_host(self):
        for state, expected in (({"enabled": False, "configured": True}, "disabilitato"),
                                ({"enabled": True, "configured": False}, "host-agent")):
            calls = []
            with self.subTest(**state):
                answer = self._handler(calls=calls, **state)({"argv": ["git", "status"]})
            self.assertIn(expected, answer)
            self.assertFalse(calls)

    def test_the_payload_forwarded_is_the_argv_and_the_audit_has_no_output(self):
        calls, logs = [], []
        handler = self._handler(calls=calls, logs=logs)
        answer = handler({"argv": ["git", "status", "--short"], "cwd": "/repo", "timeout": 10})
        forwarded = calls[-1]["json"]
        self.assertEqual(forwarded, {"action": "shell_run", "argv": ["git", "status", "--short"],
                                     "cwd": "/repo", "timeout": 10})
        self.assertEqual(json.loads(answer)["exit_code"], 0)
        self.assertEqual(logs[-1]["status"], "success")
        self.assertEqual(logs[-1]["args"][0], "system")
        self.assertEqual(logs[-1]["args"][1], "Shell run: git")
        self.assertNotIn("SECRET-OUTPUT", json.dumps(logs))

    def test_a_failed_command_is_logged_without_claiming_success(self):
        calls, logs = [], []
        handler = self._handler(calls=calls, logs=logs,
                                payload={"ok": False, "exit_code": 2, "output": "errore"})
        json.loads(handler({"argv": ["pytest", "-q"]}))
        self.assertEqual(logs[-1]["status"], "warn")

    def test_unreachable_host_is_an_answer_not_an_exception(self):
        namespace = {"SHELL_RUN_ENABLED": True, "_hostctl_configured": lambda: True,
                     "HOSTCTL_URL": "http://host:8765", "_hostctl_headers": dict, "json": json,
                     "push_log": lambda *a, **k: None}
        body = [self._label_node] + [n for n in self.functions if n.name == "_tool_shell_run"]
        exec(compile(ast.Module(body=body, type_ignores=[]), "handler", "exec"), namespace)

        class Boom:
            class RequestException(Exception):
                pass

            @staticmethod
            def post(*_args, **_kwargs):
                raise Boom.RequestException("connection refused")

        namespace["requests"] = Boom
        answer = namespace["_tool_shell_run"]({"argv": ["git", "status"]})
        self.assertIn("non raggiungibile", answer)


if __name__ == "__main__":
    unittest.main()


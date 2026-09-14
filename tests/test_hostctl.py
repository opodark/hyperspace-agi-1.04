import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("hostctl_agent", Path(__file__).parents[1] / "hostctl/agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class TokenAuthTests(unittest.TestCase):
    def setUp(self):
        agent.Handler.token = "il-token-giusto"

    def _authorized(self, header_value):
        h = agent.Handler.__new__(agent.Handler)
        h.headers = {"Authorization": header_value} if header_value is not None else {}
        return h._authorized()

    def test_missing_header_rejected(self):
        self.assertFalse(self._authorized(None))

    def test_wrong_scheme_rejected(self):
        self.assertFalse(self._authorized("Basic il-token-giusto"))

    def test_wrong_token_rejected(self):
        self.assertFalse(self._authorized("Bearer sbagliato"))

    def test_correct_token_accepted(self):
        self.assertTrue(self._authorized("Bearer il-token-giusto"))


class ActionWhitelistTests(unittest.TestCase):
    def test_all_actions_are_callables(self):
        for name, fn in agent.ACTIONS.items():
            self.assertTrue(callable(fn), name)

    def test_read_only_actions_are_a_subset_of_actions(self):
        self.assertTrue(agent.READ_ONLY_ACTIONS.issubset(agent.ACTIONS.keys()))


class NgrokStartValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_path = Path(self.temp.name) / "state.json"
        patcher = patch.object(agent, "STATE_PATH", self.state_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch.object(agent.shutil, "which", return_value="/usr/local/bin/ngrok")
    def test_rejects_non_numeric_port(self, _which):
        result = agent.action_ngrok_start({"port": "rm -rf /"})
        self.assertFalse(result["ok"])
        self.assertIn("non valido", result["error"])

    @patch.object(agent.shutil, "which", return_value="/usr/local/bin/ngrok")
    def test_rejects_out_of_range_port(self, _which):
        result = agent.action_ngrok_start({"port": 99999})
        self.assertFalse(result["ok"])

    @patch.object(agent.shutil, "which", return_value=None)
    def test_missing_binary_reported(self, _which):
        result = agent.action_ngrok_start({"port": 8080})
        self.assertFalse(result["ok"])
        self.assertIn("non installato", result["error"])

    @patch.object(agent, "_pid_alive_and_named", return_value=True)
    @patch.object(agent.shutil, "which", return_value="/usr/local/bin/ngrok")
    def test_refuses_second_start_while_one_is_tracked_and_alive(self, _which, _alive):
        agent._write_state({"ngrok_pid": 4242})
        result = agent.action_ngrok_start({"port": 8080})
        self.assertFalse(result["ok"])
        self.assertIn("gia' in esecuzione", result["error"])

    @patch.object(agent, "action_ngrok_status", return_value={"ok": True, "running": True, "tunnels": []})
    @patch("time.sleep", return_value=None)
    @patch.object(agent.subprocess, "Popen")
    @patch.object(agent.shutil, "which", return_value="/usr/local/bin/ngrok")
    def test_start_never_uses_a_shell_string(self, _which, popen, _sleep, _status):
        popen.return_value.pid = 999
        agent.action_ngrok_start({"port": 8080})
        called_argv = popen.call_args.args[0]
        self.assertIsInstance(called_argv, list)
        self.assertEqual(called_argv, ["ngrok", "http", "8080"])
        self.assertNotIn("shell", popen.call_args.kwargs)


class NgrokStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_path = Path(self.temp.name) / "state.json"
        patcher = patch.object(agent, "STATE_PATH", self.state_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stop_with_nothing_tracked(self):
        result = agent.action_ngrok_stop({})
        self.assertFalse(result["ok"])

    @patch.object(agent, "_pid_alive_and_named", return_value=False)
    def test_stale_pid_is_cleared_not_killed(self, _alive):
        agent._write_state({"ngrok_pid": 4242})
        with patch("os.kill") as kill:
            result = agent.action_ngrok_stop({})
            kill.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(agent._read_state(), {})

    @patch.object(agent, "_pid_alive_and_named", return_value=True)
    def test_live_tracked_pid_gets_sigterm(self, _alive):
        agent._write_state({"ngrok_pid": 4242})
        with patch("os.kill") as kill:
            result = agent.action_ngrok_stop({})
            kill.assert_called_once_with(4242, 15)
        self.assertTrue(result["ok"])


class WgInterfaceValidationTests(unittest.TestCase):
    @patch.dict("os.environ", {"WIREGUARD_INTERFACE": "wg0"})
    def test_accepts_normal_name(self):
        self.assertEqual(agent._wg_interface(), "wg0")

    @patch.dict("os.environ", {"WIREGUARD_INTERFACE": "wg0; rm -rf /"})
    def test_rejects_shell_metacharacters_and_falls_back(self):
        self.assertEqual(agent._wg_interface(), "wg0")

    @patch.dict("os.environ", {}, clear=False)
    def test_defaults_when_unset(self):
        import os
        os.environ.pop("WIREGUARD_INTERFACE", None)
        self.assertEqual(agent._wg_interface(), "wg0")


class GenerateTokenTests(unittest.TestCase):
    def test_writes_token_to_fresh_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            token = agent.generate_token(path)
            content = path.read_text(encoding="utf-8")
            self.assertIn(f"HOSTCTL_TOKEN={token}", content)
            self.assertEqual(len(token), 64)  # secrets.token_hex(32)

    def test_refuses_to_overwrite_existing_token(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("HOSTCTL_TOKEN=gia-presente\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                agent.generate_token(path)
            self.assertIn("HOSTCTL_TOKEN=gia-presente", path.read_text(encoding="utf-8"))


class LoadEnvTests(unittest.TestCase):
    def test_parses_simple_key_values_and_skips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("# commento\nHOSTCTL_TOKEN=abc\n\nHOSTCTL_PORT=9000\n", encoding="utf-8")
            values = agent.load_env(path)
            self.assertEqual(values, {"HOSTCTL_TOKEN": "abc", "HOSTCTL_PORT": "9000"})

    def test_missing_file_returns_empty(self):
        self.assertEqual(agent.load_env(Path("/nonexistent/.env")), {})


if __name__ == "__main__":
    unittest.main()

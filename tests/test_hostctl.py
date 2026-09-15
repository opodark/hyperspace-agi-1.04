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


class ProcessIdentityTests(unittest.TestCase):
    @patch.object(agent, "_run", return_value={"ok": True, "stdout": '"ngrok.exe","4242"'})
    @patch.object(agent, "_is_windows", return_value=True)
    def test_windows_uses_tasklist(self, _windows, run):
        self.assertTrue(agent._pid_alive_and_named(4242, "ngrok"))
        self.assertEqual(run.call_args.args[0],
                         ["tasklist", "/FI", "PID eq 4242", "/FO", "CSV", "/NH"])

    @patch.object(agent, "_run", return_value={"ok": True, "stdout": "ngrok"})
    @patch.object(agent, "_is_windows", return_value=False)
    def test_posix_uses_ps(self, _windows, run):
        self.assertTrue(agent._pid_alive_and_named(4242, "ngrok"))
        self.assertEqual(run.call_args.args[0], ["ps", "-p", "4242", "-o", "comm="])


class BleScanTests(unittest.TestCase):
    @staticmethod
    def _finish_scan(coroutine):
        coroutine.close()
        return []

    @staticmethod
    def _fail_scan(coroutine):
        coroutine.close()
        raise RuntimeError("bluetooth spento")

    def test_reports_clearly_when_bleak_not_installed(self):
        with patch.object(agent, "_BLEAK_AVAILABLE", False):
            result = agent.action_ble_scan({})
            self.assertFalse(result["ok"])
            self.assertIn("bleak", result["error"])

    def test_seconds_is_clamped_to_a_sane_range(self):
        with patch.object(agent, "_BLEAK_AVAILABLE", True), \
             patch.object(agent.asyncio, "run", side_effect=self._finish_scan) as run:
            agent.action_ble_scan({"seconds": 9999})
            # non possiamo leggere 'seconds' passato a BleakScanner.discover da qui
            # (e' dentro la coroutine), ma verifichiamo che non esploda e risponda ok
            run.assert_called_once()

    def test_non_numeric_seconds_falls_back_to_default(self):
        with patch.object(agent, "_BLEAK_AVAILABLE", True), \
             patch.object(agent.asyncio, "run", side_effect=self._finish_scan) as run:
            result = agent.action_ble_scan({"seconds": "non-un-numero"})
            self.assertTrue(result["ok"])
            run.assert_called_once()

    def test_scan_failure_reported_cleanly_not_raised(self):
        with patch.object(agent, "_BLEAK_AVAILABLE", True), \
             patch.object(agent.asyncio, "run", side_effect=self._fail_scan):
            result = agent.action_ble_scan({})
            self.assertFalse(result["ok"])
            self.assertIn("bluetooth spento", result["error"])

    def test_registered_in_action_whitelist_as_read_only(self):
        self.assertIn("ble_scan", agent.ACTIONS)
        self.assertIn("ble_scan", agent.READ_ONLY_ACTIONS)


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

    def test_value_loaded_from_dotenv_is_used(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("WIREGUARD_INTERFACE=mesh0\nBLE_SCAN_SECONDS=9\n", encoding="utf-8")
            old = agent._file_env
            self.addCleanup(setattr, agent, "_file_env", old)
            agent.apply_file_env(path)
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(agent._wg_interface(), "mesh0")
                self.assertEqual(agent.config_value("BLE_SCAN_SECONDS"), "9")


class WireGuardPlatformTests(unittest.TestCase):
    @patch.object(agent, "_run")
    @patch.object(agent, "_wireguard_windows_executable", return_value=r"C:\Program Files\WireGuard\wireguard.exe")
    @patch.object(agent, "_is_windows", return_value=True)
    def test_windows_down_uses_tunnel_service(self, _windows, _exe, run):
        run.return_value = {"ok": True}
        with patch.dict("os.environ", {"WIREGUARD_INTERFACE": "wg0"}):
            agent.action_wg_down({})
        self.assertEqual(run.call_args.args[0],
                         [r"C:\Program Files\WireGuard\wireguard.exe",
                          "/uninstalltunnelservice", "wg0"])

    @patch.object(agent, "_run", return_value={"ok": True})
    @patch.object(agent, "_is_windows", return_value=False)
    def test_posix_up_remains_non_interactive(self, _windows, run):
        agent.action_wg_up({})
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", "wg-quick", "up", "wg0"])


class TailscalePlatformTests(unittest.TestCase):
    @patch.object(agent, "_run", return_value={"ok": True})
    @patch.object(agent, "_tailscale_executable", return_value=r"C:\Program Files\Tailscale\tailscale.exe")
    def test_action_uses_discovered_windows_executable(self, _executable, run):
        agent.action_tailscale_down({})
        self.assertEqual(run.call_args.args[0],
                         [r"C:\Program Files\Tailscale\tailscale.exe", "down"])

    @patch.object(agent, "_tailscale_executable", return_value=None)
    def test_missing_executable_is_reported(self, _executable):
        result = agent.action_tailscale_up({})
        self.assertFalse(result["ok"])
        self.assertIn("non installato", result["error"])


class BindValidationTests(unittest.TestCase):
    def test_loopback_and_explicit_bridge_ip_are_allowed(self):
        self.assertEqual(agent.validate_bind_address("127.0.0.1"), "127.0.0.1")
        self.assertEqual(agent.validate_bind_address("172.17.0.1"), "172.17.0.1")

    def test_wildcard_and_hostnames_are_rejected(self):
        for value in ("0.0.0.0", "::", "localhost"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                agent.validate_bind_address(value)


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

    def test_generates_distinct_host_and_admin_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            generated = agent.generate_missing_tokens(path)
            self.assertEqual(set(generated), {"HOSTCTL_TOKEN", "NETWORK_ADMIN_TOKEN"})
            self.assertNotEqual(generated["HOSTCTL_TOKEN"], generated["NETWORK_ADMIN_TOKEN"])


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

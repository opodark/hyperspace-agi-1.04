# SPDX-License-Identifier: Apache-2.0
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "hostctl_agent_kali", Path(__file__).parents[1] / "hostctl/agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class KaliTargetAllowlistTests(unittest.TestCase):
    def _allowed(self, target, allowlist):
        with patch.dict("os.environ", {"KALI_TARGET_ALLOWLIST": allowlist}, clear=False):
            return agent._kali_target_allowed(target)

    def test_empty_allowlist_rejects_everything(self):
        self.assertFalse(self._allowed("localhost", ""))
        self.assertFalse(self._allowed("192.168.1.10", ""))

    def test_exact_hostname_and_ip_match(self):
        self.assertTrue(self._allowed("localhost", "localhost,192.168.1.0/24"))
        self.assertTrue(self._allowed("192.168.1.10", "localhost,192.168.1.0/24"))

    def test_ip_within_cidr_and_outside_rejected(self):
        self.assertTrue(self._allowed("10.0.0.5", "10.0.0.0/24"))
        self.assertFalse(self._allowed("10.0.1.5", "10.0.0.0/24"))

    def test_undeclared_target_rejected(self):
        self.assertFalse(self._allowed("8.8.8.8", "localhost,192.168.1.0/24"))


class KaliActionTests(unittest.TestCase):
    def test_disallowed_tool_is_rejected_before_docker(self):
        with patch.dict("os.environ", {"KALI_TARGET_ALLOWLIST": "localhost"}, clear=False):
            with self.assertRaises(ValueError):
                agent.action_kali({"tool": "rm", "target": "localhost"})

    def test_undeclared_target_is_rejected_before_docker(self):
        with patch.dict("os.environ", {"KALI_TARGET_ALLOWLIST": "localhost"}, clear=False):
            with self.assertRaises(ValueError):
                agent.action_kali({"tool": "nmap", "target": "8.8.8.8"})

    @patch.object(agent, "_docker_exec_kali", return_value={"ok": True, "stdout": "", "stderr": ""})
    def test_allowed_call_builds_docker_argv(self, exec_kali):
        with patch.dict("os.environ", {"KALI_TARGET_ALLOWLIST": "localhost"}, clear=False):
            agent.action_kali({"tool": "nmap", "target": "localhost",
                               "args": ["-sT", "-p", "80"]})
        exec_kali.assert_called_once_with(["nmap", "-sT", "-p", "80", "localhost"], 120)


if __name__ == "__main__":
    unittest.main()

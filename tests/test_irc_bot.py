# SPDX-License-Identifier: Apache-2.0
import importlib.util
import os
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).parents[1]
DRIVER = ROOT / "scripts" / "irc_bot.py"


def _load():
    """Importa il driver (senza eseguire main) per testarne le funzioni pure."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("irc_bot_sotto_test", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def test_ping_senza_prefix(self):
        parsed = self.m.parse_irc_line("PING :server.libera.chat")
        self.assertEqual(parsed["command"], "PING")
        self.assertEqual(parsed["trailing"], "server.libera.chat")

    def test_privmsg_con_prefix(self):
        parsed = self.m.parse_irc_line(":mario!u@host PRIVMSG #stanza :ciao Anna")
        self.assertEqual(parsed["prefix"], "mario!u@host")
        self.assertEqual(parsed["command"], "PRIVMSG")
        self.assertEqual(parsed["params"], ["#stanza", "ciao Anna"])

    def test_privmsg_estratto(self):
        msg = self.m.parse_privmsg(":mario!u@host PRIVMSG #stanza :ciao")
        self.assertEqual(msg["author"], "mario")
        self.assertEqual(msg["target"], "#stanza")
        self.assertEqual(msg["text"], "ciao")

    def test_notice_e_pm(self):
        msg = self.m.parse_privmsg(":mario!u@host NOTICE Anna :privato")
        self.assertEqual(msg["command"], "NOTICE")
        self.assertEqual(msg["target"], "Anna")

    def test_sender_nick(self):
        self.assertEqual(self.m.sender_nick("mario!u@host"), "mario")
        self.assertEqual(self.m.sender_nick(""), "")

    def test_surface(self):
        self.assertEqual(self.m.surface_for("#stanza", "Anna"), "chat")
        self.assertEqual(self.m.surface_for("Anna", "Anna"), "pm")

    def test_mentioned(self):
        self.assertTrue(self.m.mentioned("ciao Anna!", "Anna_2008"))
        self.assertTrue(self.m.mentioned("chi è Anna_2008?", "Anna_2008"))
        self.assertTrue(self.m.mentioned("dov'è la sorella di Aurora?", "Anna_2008"))
        self.assertTrue(self.m.mentioned("parliamo con la sorellina", "Anna_2008"))
        self.assertFalse(self.m.mentioned("ciao a tutti", "Anna_2008"))

    def test_chunks(self):
        self.assertEqual(self.m.chunks("ciao", 400), ["ciao"])
        self.assertTrue(all(len(c) <= 6 for c in self.m.chunks("parola lunga da spezzare", 6)))


class ChannelCatalogTests(unittest.TestCase):
    def test_irc_e_nel_catalogo(self):
        sys.path.insert(0, str(ROOT))
        from shared.channel import known_channel  # noqa: E402
        voce = known_channel("irc")
        self.assertIsNotNone(voce)
        self.assertEqual(voce["surfaces"], ("chat", "pm"))


class ConfigTests(unittest.TestCase):
    def test_presentazione_e_mention_default(self):
        m = _load()
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = m.read_config()
        self.assertEqual(cfg["present_s"], 900)
        self.assertTrue(cfg["require_mention"])
        self.assertIn("Aurora", cfg["present_text"])


class StartupTests(unittest.TestCase):
    def test_senza_token_esce_con_il_nome_del_token(self):
        env = dict(os.environ)
        env.pop("CHANNEL_TOKEN", None)
        env["IRC_CHANNELS"] = "#x"
        esito = subprocess.run([sys.executable, str(DRIVER)], env=env,
                               capture_output=True, text=True, timeout=20)
        self.assertNotEqual(esito.returncode, 0)
        self.assertIn("CHANNEL_TOKEN", esito.stderr + esito.stdout)


if __name__ == "__main__":
    unittest.main()

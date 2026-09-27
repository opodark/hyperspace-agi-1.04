# SPDX-License-Identifier: Apache-2.0
"""Il diario persistente delle conversazioni: la battuta e lo store."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.conversation_log import battuta, ConversationLog  # noqa: E402


class BattutaTests(unittest.TestCase):
    def test_i_campi_lunghi_vengono_troncati(self):
        b = battuta(channel="x" * 200, surface="s" * 200, chat="c" * 200,
                    messages=[{"author": "a" * 200, "text": "t" * 3000}],
                    action="reply", text="z" * 5000, reason="r" * 500)
        self.assertEqual(len(b["channel"]), 64)
        self.assertEqual(len(b["chat"]), 64)
        self.assertEqual(len(b["messages"][0]["author"]), 64)
        self.assertEqual(len(b["messages"][0]["text"]), 1000)
        self.assertEqual(len(b["text"]), 2000)
        self.assertEqual(len(b["reason"]), 200)

    def test_il_timestamp_predefinito_e_iso(self):
        b = battuta(channel="t", surface="chat", chat="c", messages=[],
                    action="wait")
        self.assertIn("T", b["ts"])

    def test_i_messaggi_non_dict_vengono_scartati(self):
        b = battuta(channel="t", surface="chat", chat="c",
                    messages=[{"author": "a", "text": "x"}, "spazzatura", None],
                    action="wait")
        self.assertEqual(len(b["messages"]), 1)


class ConversationLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "conversations.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_e_list_ritorna_dalla_piu_recente(self):
        log = ConversationLog(max_turns=3)
        log.add(battuta(channel="a", surface="chat", chat="c", messages=[],
                        action="wait", ts="2026-01-01T00:00:00Z"))
        log.add(battuta(channel="b", surface="chat", chat="c", messages=[],
                        action="wait", ts="2026-01-02T00:00:00Z"))
        self.assertEqual([t["channel"] for t in log.list()], ["b", "a"])

    def test_il_tetto_scarta_e_conta_i_dropped(self):
        log = ConversationLog(max_turns=2)
        for i in range(5):
            log.add(battuta(channel=str(i), surface="chat", chat="c",
                            messages=[], action="wait"))
        self.assertEqual(len(log), 2)
        self.assertEqual(log.dropped, 3)
        self.assertEqual([t["channel"] for t in log.list()], ["4", "3"])

    def test_save_e_load_ripristinano_turni_e_dropped(self):
        log = ConversationLog(max_turns=10)
        log.add(battuta(channel="t", surface="chat", chat="c",
                        messages=[{"author": "a", "text": "x"}],
                        action="reply", text="ciao"))
        log.save(self.path)
        caricato = ConversationLog.load(self.path)
        self.assertEqual(len(caricato), 1)
        self.assertEqual(caricato.list()[0]["channel"], "t")

    def test_load_su_file_mancante_torna_vuoto(self):
        log = ConversationLog.load(self.path + ".missing")
        self.assertEqual(len(log), 0)
        self.assertEqual(log.dropped, 0)

    def test_load_di_json_corrotto_torna_vuoto(self):
        Path(self.path).write_text("{non json", encoding="utf-8")
        log = ConversationLog.load(self.path)
        self.assertEqual(len(log), 0)


if __name__ == "__main__":
    unittest.main()

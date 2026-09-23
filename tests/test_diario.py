# SPDX-License-Identifier: Apache-2.0
"""Diario delle illustrazioni: la voce, il filo post->sketch, e lo store."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.diario import Diario, file_da_job, voce  # noqa: E402


class VoceTests(unittest.TestCase):
    def test_la_voce_nasce_con_file_vuoto(self):
        v = voce(id="p1", author="anna", tipo="post", testo="ciao", prompt="uno schizzo")
        self.assertEqual(v["id"], "p1")
        self.assertEqual(v["author"], "anna")
        self.assertEqual(v["tipo"], "post")
        self.assertEqual(v["file"], "")

    def test_il_tipo_sconosciuto_cade_su_post(self):
        self.assertEqual(voce(id="x", author="anna", tipo="boh")["tipo"], "post")

    def test_il_testo_si_normalizza(self):
        v = voce(id="x", author="anna", testo="  ciao   mondo  ")
        self.assertEqual(v["testo"], "ciao mondo")


class FileDaJobTests(unittest.TestCase):
    def test_solo_i_job_feed_fatti_contano(self):
        job = {"canale": "feed", "stato": "done", "destinazione": "p1",
               "esito": {"file": "HyperSpace/a.png"}}
        self.assertEqual(file_da_job(job), ("p1", "HyperSpace/a.png"))

    def test_un_job_fallito_non_entra(self):
        job = {"canale": "feed", "stato": "failed", "destinazione": "p1",
               "esito": {"file": ""}}
        self.assertIsNone(file_da_job(job))

    def test_un_job_non_del_feed_non_entra(self):
        job = {"canale": "telegram", "stato": "done", "destinazione": "1",
               "esito": {"file": "a.png"}}
        self.assertIsNone(file_da_job(job))


class DiarioTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "diario.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_aggiunge_senza_doppioni(self):
        d = Diario(max_voci=5)
        d.add(voce(id="a", author="anna", testo="x"))
        d.add(voce(id="a", author="anna", testo="x"))
        self.assertEqual(len(d), 1)

    def test_aggiorna_file_e_persiste(self):
        d = Diario()
        d.add(voce(id="a", author="anna", testo="x"))
        self.assertTrue(d.aggiorna_file("a", "HyperSpace/s.png"))
        d.save(self.path)
        ricaricato = Diario.load(self.path)
        self.assertEqual(ricaricato.list()[0]["file"], "HyperSpace/s.png")

    def test_list_ritorna_dal_piu_recente(self):
        d = Diario()
        d.add(voce(id="a", author="anna", testo="1"))
        d.add(voce(id="b", author="aurora", testo="2"))
        self.assertEqual([v["id"] for v in d.list()], ["b", "a"])


if __name__ == "__main__":
    unittest.main()

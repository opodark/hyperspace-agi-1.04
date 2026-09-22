# SPDX-License-Identifier: Apache-2.0
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.feed import (KIND_POST, KIND_REACTION, AUTHORS, MAX_CAPTION_CHARS,  # noqa: E402
                         Feed, nuovo_post)


class NuovoPostTests(unittest.TestCase):
    def test_un_post_valido(self):
        p = nuovo_post("Anna", "primo post")
        self.assertEqual(p["author"], "anna")
        self.assertEqual(p["kind"], KIND_POST)
        self.assertTrue(p["id"])
        self.assertTrue(p["ts"])

    def test_l_autore_si_normalizza(self):
        self.assertEqual(nuovo_post("AURORA", "ciao")["author"], "aurora")

    def test_un_autore_sconosciuto_solleva(self):
        with self.assertRaises(ValueError):
            nuovo_post("mario", "ciao")

    def test_una_didascalia_vuota_solleva(self):
        with self.assertRaises(ValueError):
            nuovo_post("anna", "   ")

    def test_una_reazione_deve_indicare_reply_to(self):
        with self.assertRaises(ValueError):
            nuovo_post("anna", "bello!", kind=KIND_REACTION)

    def test_la_didascalia_si_tronca(self):
        p = nuovo_post("anna", "x" * 10000)
        self.assertLessEqual(len(p["caption"]), MAX_CAPTION_CHARS)

    def test_il_prompt_immagine_e_facoltativo(self):
        p = nuovo_post("anna", "un post", image_prompt="una torre al tramonto")
        self.assertEqual(p["image_prompt"], "una torre al tramonto")


class FeedTests(unittest.TestCase):
    def test_aggiunge_e_elenca_dal_piu_recente(self):
        feed = Feed()
        feed.add(nuovo_post("anna", "uno"))
        feed.add(nuovo_post("aurora", "due"))
        self.assertEqual(len(feed), 2)
        self.assertEqual(feed.list()[0]["caption"], "due")
        self.assertEqual(feed.list()[1]["caption"], "uno")

    def test_lo_stesso_id_non_si_riaggiunge(self):
        feed = Feed()
        p = nuovo_post("anna", "uno")
        feed.add(p)
        feed.add(dict(p))  # copia, stesso id
        self.assertEqual(len(feed), 1)

    def test_il_tetto_fa_cadere_i_piu_vecchi(self):
        feed = Feed(max_posts=3)
        for i in range(5):
            feed.add(nuovo_post("anna", f"post {i}"))
        self.assertEqual(len(feed), 3)
        self.assertEqual(feed.list()[-1]["caption"], "post 2")

    def test_salva_e_ricarica(self):
        feed = Feed()
        feed.add(nuovo_post("anna", "persistito"))
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "feed.json")
            feed.save(path)
            ricaricato = Feed.load(path)
        self.assertEqual(len(ricaricato), 1)
        self.assertEqual(ricaricato.list()[0]["caption"], "persistito")

    def test_carica_un_file_inesistente_senza_errori(self):
        feed = Feed.load("/tmp/feed-inesistente-xyz.json")
        self.assertEqual(len(feed), 0)


if __name__ == "__main__":
    unittest.main()

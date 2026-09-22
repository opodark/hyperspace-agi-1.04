# SPDX-License-Identifier: Apache-2.0
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.post_gen import build_post_prompt, filtra_post, parse_post  # noqa: E402

SISTEMA = "Ti chiami Anna. Tono: giocosa. Non dire di essere umana."


class BuildPromptTests(unittest.TestCase):
    def test_contiene_identita_e_formato(self):
        p = build_post_prompt(SISTEMA)
        self.assertIn("Anna", p)
        self.assertIn("DIDASCALIA", p)
        self.assertIn("IMMAGINE", p)

    def test_la_reazione_nomina_il_post_a_cui_risponde(self):
        p = build_post_prompt(SISTEMA, replica_a={"author": "aurora", "caption": "ciao"})
        self.assertIn("aurora", p)
        self.assertIn("ciao", p)
        self.assertIn("REAZIONE", p)

    def test_memorie_e_feed_entrano_nel_materiale(self):
        p = build_post_prompt(SISTEMA, memorie=["un tip"], feed_recente=[{"author": "anna", "caption": "x"}])
        self.assertIn("un tip", p)
        self.assertIn("anna", p)


class ParsePostTests(unittest.TestCase):
    def test_estrae_didascalia_e_immagine(self):
        c = parse_post("DIDASCALIA: una torre al tramonto\nIMMAGINE: torre, luce calda")
        self.assertEqual(c["caption"], "una torre al tramonto")
        self.assertEqual(c["image_prompt"], "torre, luce calda")

    def test_la_riga_immagine_e_facoltativa(self):
        c = parse_post("didascalia: solo testo")
        self.assertEqual(c["caption"], "solo testo")
        self.assertEqual(c["image_prompt"], "")

    def test_tollera_maiuscole_e_spazi(self):
        c = parse_post("  Didascalia :  ciao a tutti  ")
        self.assertEqual(c["caption"], "ciao a tutti")

    def test_niente_restituisce_none(self):
        self.assertIsNone(parse_post("NIENTE"))

    def test_output_illeggibile_restituisce_none(self):
        self.assertIsNone(parse_post("ciao, come va? niente di speciale"))


class FiltraPostTests(unittest.TestCase):
    def test_un_post_valido_passa(self):
        ok, _ = filtra_post({"caption": "un bel post"}, autore="anna")
        self.assertTrue(ok)

    def test_il_meta_rumore_si_scarta(self):
        ok, motivo = filtra_post({"caption": "non ho nulla da dire, la memoria è vuota"},
                                 autore="anna")
        self.assertFalse(ok)
        self.assertIn("meta", motivo)

    def test_il_doppione_si_scarta(self):
        feed = [{"author": "anna", "caption": "già scritto"}]
        ok, motivo = filtra_post({"caption": "già scritto"}, autore="anna", feed=feed)
        self.assertFalse(ok)
        self.assertIn("già pubblicato", motivo)

    def test_lo_stesso_testo_di_un_altra_persona_passa(self):
        feed = [{"author": "aurora", "caption": "stesso testo"}]
        ok, _ = filtra_post({"caption": "stesso testo"}, autore="anna", feed=feed)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

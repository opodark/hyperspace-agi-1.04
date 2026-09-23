# SPDX-License-Identifier: Apache-2.0
"""Sketch dei post: il job leggero per il Mac, puro e testabile."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.sketch import (SKETCH_CANALE, SKETCH_LATO, SKETCH_PASSI,  # noqa: E402
                           job_sketch, prompt_sketch, puo_generare)


class PromptSketchTests(unittest.TestCase):
    def test_contiene_stile_idea_e_dichiarazione(self):
        p = prompt_sketch("una torre al tramonto")
        self.assertIn("schizzo", p)
        self.assertIn("una torre al tramonto", p)
        self.assertIn("costruzione digitale", p)

    def test_l_idea_vuota_lascia_stile_e_dichiarazione(self):
        p = prompt_sketch("")
        self.assertIn("schizzo", p)
        self.assertIn("costruzione digitale", p)

    def test_normalizza_gli_spazi_dell_idea(self):
        p = prompt_sketch("  torre   bianca  ")
        self.assertIn("torre bianca", p)


class JobSketchTests(unittest.TestCase):
    def test_il_job_e_leggero_e_per_il_feed(self):
        job = job_sketch("un lupo", autore="anna", post_id="abc123")
        self.assertEqual(job["famiglia"], "sdxl-turbo")
        self.assertEqual(job["passi"], SKETCH_PASSI)
        self.assertEqual(job["larghezza"], SKETCH_LATO)
        self.assertEqual(job["altezza"], SKETCH_LATO)
        self.assertEqual(job["canale"], SKETCH_CANALE)
        self.assertEqual(job["richiedente"], "anna")
        self.assertEqual(job["destinazione"], "abc123")

    def test_il_prompt_porta_lo_stile(self):
        job = job_sketch("un gatto")
        self.assertIn("schizzo", job["prompt"])

    def test_il_negativo_non_e_vuoto(self):
        # SDXL usa il negativo: lo sketch porta le esclusioni assolute.
        job = job_sketch("un gatto")
        self.assertIn("fotografia", job["negativo"])


class PuoGenerareTests(unittest.TestCase):
    def test_sotto_il_tetto_si_puo(self):
        self.assertTrue(puo_generare({"anna": 2}, autore="anna", tetto=4))

    def test_al_tetto_non_si_puo(self):
        self.assertFalse(puo_generare({"anna": 4}, autore="anna", tetto=4))

    def test_un_autore_mai_visto_parte_da_zero(self):
        self.assertTrue(puo_generare({}, autore="aurora", tetto=4))

    def test_il_tetto_non_scende_sotto_uno(self):
        self.assertFalse(puo_generare({"anna": 1}, autore="anna", tetto=0))


if __name__ == "__main__":
    unittest.main()

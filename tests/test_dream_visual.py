# SPDX-License-Identifier: Apache-2.0
"""Sogno notturno delle influencer: prompt, parsing tollerante e filtro."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.dream_visual import (build_dream_prompt, filtra_dream,  # noqa: E402
                                 parse_dream)

SISTEMA = "Ti chiami Anna. Tono: sognante."


class BuildDreamPromptTests(unittest.TestCase):
    def test_contiene_identita_e_formato(self):
        p = build_dream_prompt(SISTEMA)
        self.assertIn("Anna", p)
        self.assertIn("SCENA", p)
        self.assertIn("DISEGNO", p)

    def test_il_feed_entra_nel_materiale(self):
        p = build_dream_prompt(SISTEMA, feed_recente=[{"author": "aurora", "caption": "x"}])
        self.assertIn("aurora", p)


class ParseDreamTests(unittest.TestCase):
    def test_estrae_scena_e_disegno(self):
        c = parse_dream("SCENA: volo sopra la città\nDISEGNO: una città di fili")
        self.assertEqual(c["scena"], "volo sopra la città")
        self.assertEqual(c["disegno"], "una città di fili")

    def test_il_disegno_e_facoltativo(self):
        c = parse_dream("scena: solo un sogno")
        self.assertEqual(c["scena"], "solo un sogno")
        self.assertEqual(c["disegno"], "")

    def test_accetta_immagine_come_alias_del_disegno(self):
        c = parse_dream("SCENA: x\nIMMAGINE: uno schizzo")
        self.assertEqual(c["disegno"], "uno schizzo")

    def test_niente_restituisce_none(self):
        self.assertIsNone(parse_dream("NIENTE"))

    def test_illeggibile_restituisce_none(self):
        self.assertIsNone(parse_dream("ciao, come va?"))


class FiltraDreamTests(unittest.TestCase):
    def test_un_sogno_valido_passa(self):
        ok, _ = filtra_dream({"scena": "un sogno"}, autore="anna")
        self.assertTrue(ok)

    def test_il_meta_rumore_si_scarta(self):
        ok, motivo = filtra_dream({"scena": "non ho nulla da sognare, la memoria è vuota"},
                                  autore="anna")
        self.assertFalse(ok)
        self.assertIn("meta", motivo)

    def test_il_doppione_si_scarta(self):
        diario = [{"author": "anna", "testo": "già sognato"}]
        ok, motivo = filtra_dream({"scena": "già sognato"}, autore="anna", diario=diario)
        self.assertFalse(ok)
        self.assertIn("già sognato", motivo)


if __name__ == "__main__":
    unittest.main()

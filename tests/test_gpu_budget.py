# SPDX-License-Identifier: Apache-2.0
"""Una scheda, un modello: la contesa si decide, non si scopre da un errore CUDA.

Il caso vero (2026-09-22): generando il ritratto, ComfyUI è morto con
`torch.AcceleratorError: CUDA error: unknown error`. La scheda da 8151 MiB aveva 6170
occupati da Ollama (il modello del canale, tenuto 12 ore da OLLAMA_KEEP_ALIVE) e 1730
liberi: il diffusion non ci stava. La contesa si presentava come un errore casuale.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import gpu_budget  # noqa: E402


class DecisioneTests(unittest.TestCase):

    def test_di_default_si_scarica_il_modello_del_canale(self):
        self.assertEqual(gpu_budget.da_scaricare({"CHANNEL_MODEL": "qwen3.5:4b"}),
                         "qwen3.5:4b")

    def test_si_puo_spegnere(self):
        for valore in ("0", "false", "OFF", "no"):
            with self.subTest(valore=valore):
                self.assertEqual(gpu_budget.da_scaricare(
                    {"CHANNEL_MODEL": "qwen3.5:4b", "IMAGE_FREE_GPU": valore}), "")

    def test_senza_modello_dichiarato_non_si_scarica_niente(self):
        """Non si inventa un modello: si scaricherebbe quello sbagliato, su un'altra macchina."""
        self.assertEqual(gpu_budget.da_scaricare({}), "")
        self.assertEqual(gpu_budget.da_scaricare({"CHANNEL_MODEL": "   "}), "")

    def test_la_richiesta_e_un_keep_alive_a_zero(self):
        richiesta = gpu_budget.richiesta_scarico(" qwen3.5:4b ")
        self.assertEqual(richiesta["model"], "qwen3.5:4b")
        self.assertEqual(richiesta["keep_alive"], 0)
        self.assertIs(richiesta["stream"], False, "una risposta intera, non uno stream")

    def test_l_esito_si_legge_in_una_riga(self):
        self.assertIn("liberata", gpu_budget.descrivi_esito(200, modello="qwen3.5:4b"))
        self.assertIn("qwen3.5:4b", gpu_budget.descrivi_esito(200, modello="qwen3.5:4b"))
        self.assertIn("non riuscito", gpu_budget.descrivi_esito(0, errore="timeout"))
        self.assertIn("HTTP 500", gpu_budget.descrivi_esito(500))


class CablaggioTests(unittest.TestCase):
    """Nel control-plane lo scarico avviene PRIMA di accodare, e non blocca mai."""

    @classmethod
    def setUpClass(cls):
        albero = ast.parse((ROOT / "control-plane" / "main.py").read_text(encoding="utf-8"))
        cls.funzioni = {n.name: n for n in albero.body if isinstance(n, ast.FunctionDef)}

    def test_esiste_e_usa_il_modulo(self):
        self.assertIn("_libera_scheda_per_immagine", self.funzioni)
        corpo = ast.unparse(self.funzioni["_libera_scheda_per_immagine"])
        self.assertIn("gpu_budget.da_scaricare", corpo)
        self.assertIn("SCARICA_PATH", corpo)
        self.assertIn("timeout=", corpo, "Ollama che non risponde non deve bloccare la rotta")

    def test_image_generate_libera_la_scheda_prima_di_accodare(self):
        corpo = ast.unparse(self.funzioni["image_generate"])
        self.assertIn("_libera_scheda_per_immagine()", corpo)
        self.assertLess(corpo.index("_libera_scheda_per_immagine()"),
                        corpo.index("image_queue.accoda"), "prima si fa posto, poi si accoda")

    def test_l_esito_finisce_nella_risposta(self):
        corpo = ast.unparse(self.funzioni["image_generate"])
        self.assertIn("scheda", corpo)


if __name__ == "__main__":
    unittest.main()

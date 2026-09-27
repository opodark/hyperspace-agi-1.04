# SPDX-License-Identifier: Apache-2.0
"""Il riconoscimento intelligente: regex veloce + modello piccolo per la coda lunga."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.prompt_immagine as pi  # noqa: E402


class EstraiJsonTests(unittest.TestCase):
    def test_json_puro(self):
        self.assertEqual(pi._estrai_json('{"vuole_immagine": true, "prompt": "un gatto"}'),
                         {"vuole_immagine": True, "prompt": "un gatto"})

    def test_json_avvolto_in_markdown(self):
        risposta = '```json\n{"vuole_immagine": false, "prompt": ""}\n```'
        self.assertEqual(pi._estrai_json(risposta),
                         {"vuole_immagine": False, "prompt": ""})

    def test_testo_intorno_al_json(self):
        risposta = 'Certo: {"vuole_immagine": true, "prompt": "una torre"}. Fatto.'
        self.assertEqual(pi._estrai_json(risposta),
                         {"vuole_immagine": True, "prompt": "una torre"})

    def test_spazzatura_non_e_json(self):
        self.assertIsNone(pi._estrai_json("non capisco"))
        self.assertIsNone(pi._estrai_json(""))
        self.assertIsNone(pi._estrai_json(None))


class PromptDaModelloTests(unittest.TestCase):
    def test_vuole_immagine_false_restituisce_vuoto(self):
        self.assertEqual(pi._prompt_da_modello('{"vuole_immagine": false, "prompt": "x"}'), "")

    def test_vuole_immagine_true_restituisce_il_prompt(self):
        self.assertEqual(pi._prompt_da_modello('{"vuole_immagine": true, "prompt": "un gatto"}'),
                         "un gatto")


class SmartTests(unittest.TestCase):
    def tearDown(self):
        pi.configura_modello(None)

    def test_la_regex_resta_il_primo_passaggio(self):
        chiamate = []
        pi.configura_modello(lambda testo: chiamate.append(testo) or '{}')
        esito = pi.richiesta_immagine_smart("fammi un disegno di un faro")
        self.assertEqual(esito, {"idea": "di un faro", "regola": "mandare"})
        self.assertEqual(chiamate, [])

    def test_senza_modello_la_coda_lunga_resta_muta(self):
        pi.configura_modello(None)
        self.assertIsNone(pi.richiesta_immagine_smart("ora dai fammi qualcosa di bello"))

    def test_il_modello_recupera_la_frase_che_la_regex_perde(self):
        pi.configura_modello(lambda testo: '{"vuole_immagine": true, "prompt": "charcoal close-up of a woman"}')
        esito = pi.richiesta_immagine_smart("brava cucciolina ora fammi un close-up")
        self.assertEqual(esito, {"idea": "charcoal close-up of a woman", "regola": "modello"})

    def test_il_modello_che_dice_no_lascia_passare(self):
        pi.configura_modello(lambda testo: '{"vuole_immagine": false, "prompt": ""}')
        self.assertIsNone(pi.richiesta_immagine_smart("che bella giornata oggi"))

    def test_il_modello_che_fallisce_non_blocca(self):
        def modello(testo):
            raise RuntimeError("ollama giu")
        pi.configura_modello(modello)
        self.assertIsNone(pi.richiesta_immagine_smart("disegnami qualcosa di strano"))


if __name__ == "__main__":
    unittest.main()

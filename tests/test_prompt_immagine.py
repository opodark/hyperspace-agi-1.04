# SPDX-License-Identifier: Apache-2.0
"""Il riconoscimento intelligente: regex veloce + modello piccolo per la coda lunga."""
import sys
import json
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
    def test_non_taglia_un_prompt_lungo_a_meta(self):
        prompt = "a lighthouse in a storm " * 30
        self.assertEqual(pi._prompt_da_modello(json.dumps(
            {"vuole_immagine": True, "prompt": prompt})), prompt.strip())

    def test_rifiuta_booleani_e_prompt_non_validi(self):
        for dato in ({"vuole_immagine": "false", "prompt": "cat"},
                     {"vuole_immagine": True, "prompt": ["cat"]},
                     {"vuole_immagine": True, "prompt": "x" * 1601}):
            self.assertEqual(pi._prompt_da_modello(json.dumps(dato)), "")

    def test_vuole_immagine_false_restituisce_vuoto(self):
        self.assertEqual(pi._prompt_da_modello('{"vuole_immagine": false, "prompt": "x"}'), "")

    def test_vuole_immagine_true_restituisce_il_prompt(self):
        self.assertEqual(pi._prompt_da_modello('{"vuole_immagine": true, "prompt": "un gatto"}'),
                         "un gatto")


class SmartTests(unittest.TestCase):
    def test_contesto_separato_dalla_richiesta_corrente(self):
        chiamate = []
        pi.configura_modello(lambda testo: chiamate.append(json.loads(testo)) or
                             '{"vuole_immagine": false, "prompt": ""}')
        self.assertIsNone(pi.richiesta_immagine_smart(
            "grazie", contesto=[{"author": "io", "text": "fammi una foto"}],
            identita="Sono Anna"))
        self.assertEqual(chiamate[0]["messaggio_corrente"], "grazie")
        self.assertEqual(chiamate[0]["identita"], "Sono Anna")
        self.assertEqual(chiamate[0]["contesto"][0]["testo"], "fammi una foto")

    def tearDown(self):
        pi.configura_modello(None)

    def test_la_regex_riconosce_e_il_modello_pulisce(self):
        chiamate = []
        pi.configura_modello(lambda testo: chiamate.append(testo) or
                             '{"vuole_immagine": true, "prompt": "photorealistic lighthouse"}')
        esito = pi.richiesta_immagine_smart("fammi un disegno di un faro")
        self.assertEqual(esito, {"idea": "photorealistic lighthouse", "regola": "mandare"})
        self.assertEqual(chiamate, ["fammi un disegno di un faro"])

    def test_senza_modello_la_regex_da_l_idea_grezza(self):
        pi.configura_modello(None)
        esito = pi.richiesta_immagine_smart("fammi un disegno di un faro")
        self.assertEqual(esito, {"idea": "di un faro", "regola": "mandare"})

    def test_il_modello_che_fallisce_ripiega_sulla_regex(self):
        def modello(testo):
            raise RuntimeError("giu")
        pi.configura_modello(modello)
        esito = pi.richiesta_immagine_smart("fammi un disegno di un faro")
        self.assertEqual(esito, {"idea": "di un faro", "regola": "mandare"})

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


class StileTests(unittest.TestCase):
    def test_disegno_generico_usa_il_diario(self):
        from shared.sketch import prompt_sketch
        self.assertEqual(pi.prepara_prompt_canale("a lighthouse", "fammi un disegno di un faro"),
                         prompt_sketch("a lighthouse"))

    def test_tecnica_esplicita_non_diventa_schizzo_a_matita(self):
        for tecnica in ("acquerello", "carboncino", "olio", "anime"):
            prompt = pi.prepara_prompt_canale("a lighthouse", f"disegnami un faro in {tecnica}")
            self.assertIn(tecnica, prompt)
            self.assertNotIn("schizzo a matita", prompt)

    def test_fotografia_non_riceve_il_prefisso_del_diario(self):
        self.assertEqual(pi.prepara_prompt_canale("a lighthouse", "fammi una foto di un faro"),
                         "a lighthouse")


if __name__ == "__main__":
    unittest.main()

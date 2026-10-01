# SPDX-License-Identifier: Apache-2.0
import json
import unittest
from unittest.mock import patch

from shared.image_translation import traduci_scena_immagine


class _Risposta:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TraduzioneImmagineTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_traduce_la_scena_senza_riscriverla_con_un_llm(self, apri):
        apri.return_value = _Risposta({"translatedText": "full body while masturbating"})
        risultato = traduci_scena_immagine(
            "figura intera mentre ti masturbi", base_url="http://traduttore:5000")
        self.assertEqual(risultato, "full body while masturbating")
        richiesta = apri.call_args.args[0]
        self.assertEqual(json.loads(richiesta.data), {
            "q": "figura intera mentre ti masturbi", "source": "it",
            "target": "en", "format": "text"})

    @patch("urllib.request.urlopen", side_effect=OSError("spento"))
    def test_se_spento_non_blocca_la_generazione(self, _apri):
        self.assertEqual(traduci_scena_immagine(
            "  una scena italiana  ", base_url="http://traduttore:5000"),
            "una scena italiana")

    def test_url_vuoto_disabilita_la_traduzione(self):
        self.assertEqual(traduci_scena_immagine("una scena", base_url=""), "una scena")


if __name__ == "__main__":
    unittest.main()

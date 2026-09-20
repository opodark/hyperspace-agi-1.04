# SPDX-License-Identifier: Apache-2.0
"""Il banco di prova dei modelli: scripts/model_bench.py.

Cosa protegge, in ordine di quanto male farebbe un guasto silenzioso:

1. I GIUDICI. Se un giudice segna "giusto" una risposta sbagliata (o viceversa),
   l'intera tabella mente. Qui si prova che ogni giudice distingue una risposta
   giusta da una sbagliata.
2. LO STREAM. `consume_stream` e' la parte che decide quando un modello "non si
   ferma": un errore qui e' il modo in cui un bench puo' bloccarsi per ore.
3. IL FLAG THINK. `ask` deve mandare `think` SOLO quando il chiamante lo chiede:
   se lo mandasse sempre, falserebbe il confronto fra reasoning on/off.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts import model_bench as mb  # noqa: E402


def _giudici():
    return {nome: giudice for nome, _p, giudice, _a, _m in mb.PROBE}


def _riga(contenuto):
    return "data: " + json.dumps({"choices": [{"delta": {"content": contenuto}}]}) + "\n\n"


class GiudiciTests(unittest.TestCase):
    def test_ogni_giudice_distingue_il_giusto_dallo_sbagliato(self):
        casi = {
            "fatto_capitale": ("Canberra", "Sydney"),
            "matematica_orario": ("16:00", "17:30"),
            "matematica_sconto": ("60", "80"),
            "logica_famiglia": ("Il nonno, suo figlio e il nipote.", "Sono gemelli."),
            "conteggio_r": ("3", "4"),
            "json": ('{"citta": "Venezia", "abitanti": 250000}', "non lo so"),
            "allucinazione": ("Non lo so.", "L'ha scritto Mario Rossi nel 1999."),
        }
        giudici = _giudici()
        for nome, (giusta, sbagliata) in casi.items():
            with self.subTest(nome=nome):
                self.assertTrue(mb.segna(giudici[nome], giusta)[0], f"{nome} non riconosce il giusto")
                self.assertFalse(mb.segna(giudici[nome], sbagliata)[0], f"{nome} non riconosce lo sbagliato")


class CodiceTests(unittest.TestCase):
    def test_il_codice_giusto_passa(self):
        ok, _nota = mb.codice_ok("""
def is_palindrome(s):
    s = s.lower()
    return s == s[::-1]
""", file_uscita=tempfile.mktemp(suffix=".py"))
        self.assertTrue(ok)

    def test_il_codice_sbagliato_non_passa(self):
        ok, _nota = mb.codice_ok("""
def is_palindrome(s):
    return True
""", file_uscita=tempfile.mktemp(suffix=".py"))
        self.assertFalse(ok)

    def test_senza_funzione_non_passa(self):
        ok, _nota = mb.codice_ok("print('ciao')", file_uscita=tempfile.mktemp(suffix=".py"))
        self.assertFalse(ok)

    def test_estrai_codice_con_e_senza_fence(self):
        self.assertEqual(mb.estrai_codice("```python\ndef f():\n    return 1\n```"), "def f():\n    return 1")
        self.assertEqual(mb.estrai_codice("def f():\n    return 1"), "def f():\n    return 1")

    def test_json_si_estrae_e_si_scarta(self):
        self.assertEqual(mb._json('{"citta":"Venezia","abitanti":1}')["citta"], "Venezia")
        self.assertEqual(mb._json("niente qui"), {})


class StreamTests(unittest.TestCase):
    def test_uno_stream_normale_si_chiude_con_done(self):
        testo, limite = mb.consume_stream([_riga("ciao"), "data: [DONE]\n\n"], 100)
        self.assertEqual((testo, limite), ("ciao", "ok"))

    def test_sforare_il_budget_e_loop(self):
        testo, limite = mb.consume_stream([_riga("x " * 40)], 10)
        self.assertEqual(limite, "loop")
        self.assertTrue(testo, "il testo raccolto finora va restituito, non buttato")

    def test_le_righe_non_json_si_ignorano(self):
        testo, limite = mb.consume_stream(["event: ping\n", _riga("ok"), "data: [DONE]\n\n"], 100)
        self.assertEqual((testo, limite), ("ok", "ok"))

    def test_uno_stream_chiuso_senza_done_restituisce_comunque_il_testo(self):
        testo, limite = mb.consume_stream([_riga("troncato")], 100)
        self.assertEqual((testo, limite), ("troncato", "ok"))


class RichiestaTests(unittest.TestCase):
    def test_ask_manda_think_solo_se_richiesto(self):
        class FakeRisposta:
            def __init__(self, righe):
                self._righe = iter([r.encode() for r in righe])

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._righe)

        catturati = []

        def opener(richiesta, timeout=0):
            catturati.append(json.loads(richiesta.data.decode()))
            return FakeRisposta([_riga("ok"), "data: [DONE]\n\n"])

        mb.ask("m::macbook", "domanda", max_tok=40, cp="http://cp", opener=opener)
        mb.ask("m::macbook", "domanda", max_tok=40, cp="http://cp", think=False, opener=opener)

        self.assertNotIn("think", catturati[0], "senza richiesta esplicita il flag non va mandato")
        self.assertIs(catturati[1]["think"], False)
        self.assertEqual(catturati[0]["model"], "m::macbook")
        self.assertIs(catturati[0]["stream"], True)


if __name__ == "__main__":
    unittest.main()

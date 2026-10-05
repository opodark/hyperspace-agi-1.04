# SPDX-License-Identifier: Apache-2.0
"""`_finalize_task` non deve cadere quando il modello chiede un tool.

Il baco che questo test copre: `content` è `null` in ogni risposta OpenAI che
contiene `tool_calls`. Il `try` che legge il campo non lo intercetta — `["content"]`
su una chiave presente che vale `None` non solleva — quindi il `None` arriva fino
a `reply_text[:500]` e fa `TypeError`. Tradotto: un `500` su
`/v1/chat/completions` ogni volta che il modello usa un tool.

È stato trovato scrivendo `tests/chat_baseline.py`: il finto che rispondeva con
un `tool_calls` faceva cadere il server, e il traceback puntava qui.

Il testo che finisce in `webui_response` è il nome del tool, perché quello è
l'unica cosa che il modello ha detto. Vuotarlo lascerebbe una voce in memoria e
un log con "webui_response" e nessun contenuto, che è indistinguibile da un
modello che ha risposto davvero e non ha detto niente.
"""
import ast
import unittest
from pathlib import Path

from tests import cp_source

ROOT = Path(__file__).resolve().parents[1]


def _logica_di_reply():
    """Il blocco che calcola `reply_text`, rieseguito da solo.

    Non si importa `main.py`: all'import scrive l'identità del nodo, apre il
    database e crea l'outbox. Quindi si prende il codice vero dall'AST e lo si
    esegue in un namespace con il minimo necessario — la stessa tecnica di
    `tests/test_image_generate_route.py` e degli altri test che isolano una
    funzione del monolite.
    """
    funzioni = cp_source.funzioni()
    # Si tiene solo la parte che calcola `reply_text`: dal `try` fino all'ultimo
    # `if reply_text is None`. Il resto della funzione scrive su db, sulla memoria
    # e sui log, e non è quello che questo test prova.
    #
    # I confini si cercano sui NODI, non sulle stringhe: `ast.unparse` rimette i
    # commenti come stringhe letterali, e il commento che spiega il bug contiene
    # esattamente `reply_text[:500]` — cercando quello si tagliava il pezzo nel
    # posto sbagliato, e il test falliva senza che il codice fosse cambiato.
    nodi = [n for n in funzioni["_finalize_task"].body
            if isinstance(n, ast.Try) or
            (isinstance(n, ast.If) and "reply_text" in ast.unparse(n.test))]
    assert nodi, "non trovo il blocco che calcola reply_text"
    assert isinstance(nodi[0], ast.Try), "il primo pezzo deve essere il try"
    parti = [ast.unparse(n) for n in nodi]
    # il blocco `if reply_text is None` sta DOPO il try nel sorgente, ma
    # `ast.unparse` lo mette dove capita: si riordinano per ordine di apparizione.
    return "\n".join(parti)


def reply_di(risposta: dict) -> str:
    spazioo = {"json": __import__("json"), "result_json": risposta}
    exec(_logica_di_reply(), spazioo)          # noqa: S102 - codice di produzione
    return spazioo["reply_text"]


class ContentNulloTests(unittest.TestCase):
    def test_il_testo_normale_passa_intatto(self):
        self.assertEqual(reply_di(
            {"choices": [{"message": {"role": "assistant", "content": "ciao"}}]}), "ciao")

    def test_content_vuoto_resta_vuoto(self):
        """Una stringa vuota è una risposta vuota, e va registrata come tale: il
        fix distingue None da "", e non deve confonderli."""
        self.assertEqual(reply_di({"choices": [{"message": {"content": ""}}]}), "")

    def test_tool_call_con_content_nullo_da_il_nome_del_tool(self):
        risposta = {"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "index": 0,
                            "function": {"name": "web_search", "arguments": "{}"}}]}}]}
        self.assertEqual(reply_di(risposta), "web_search")

    def test_piu_tool_vengono_elencati(self):
        risposta = {"choices": [{"message": {"content": None, "tool_calls": [
            {"function": {"name": "web_search"}},
            {"function": {"name": "weather"}}]}}]}
        self.assertEqual(reply_di(risposta), "web_search, weather")

    def test_tool_call_senza_nome_usa_il_campo_alternativo(self):
        """Il passthrough del client porta `name` fuori da `function`: senza
        questa riserva, un tool del client finirebbe in memoria come '?'."""
        risposta = {"choices": [{"message": {"content": None, "tool_calls": [
            {"name": "tool_del_client"}]}}]}
        self.assertEqual(reply_di(risposta), "tool_del_client")

    def test_content_nullo_senza_tool_calls_dice_perche(self):
        """Nessun testo e nessun tool: non c'è niente da registrare, ma il
        posto in memoria deve comunque dire che cosa è successo."""
        risposta = {"choices": [{"message": {"content": None, "tool_calls": []}}]}
        self.assertIn("nessun testo", reply_di(risposta))

    def test_risposta_malformata_registra_json(self):
        """Il ramo di fallback: nessuna `choices`, e si registra quello che c'è."""
        self.assertEqual(reply_di({"error": "niente"}), '{"error": "niente"}')

    def test_il_testo_registrato_e_sempre_tagliabile(self):
        """`reply_text[:500]` e `[:120]` sono il punto dove il None faceva il
        500: qualunque cosa finisca qui deve poter essere tagliata."""
        for risposta in ({"choices": [{"message": {"content": "x" * 900}}]},
                         {"choices": [{"message": {"content": None, "tool_calls": [
                             {"function": {"name": "y" * 900}}]}}]},
                         {"error": "z" * 900}):
            with self.subTest(risposta=str(risposta)[:40]):
                self.assertIsInstance(reply_di(risposta)[:120], str)


if __name__ == "__main__":
    unittest.main()
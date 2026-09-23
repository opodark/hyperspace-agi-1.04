# SPDX-License-Identifier: Apache-2.0
"""Testo dei messaggi assistant quando il modello ha ragionato.

Il bug trovato in sessione reale: con un budget di token piccolo il modello
esaurisce i token nel `reasoning` e `content` resta vuoto, quindi il client
riceveva una risposta VUOTA (verificato: max_tokens=150 -> content 0 char,
reasoning 785 char). Questi test bloccano la regressione.

Le funzioni sono estratte dal VERO control-plane/main.py con ast ed eseguite in
isolamento, con push_log sostituito da uno stub: nessuna dipendenza da Flask.
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"
WANTED = {"_assistant_text", "_normalize_assistant_message"}


def _load():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    # Le costanti di rate-limit del warning vivono a livello di modulo.
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(
                getattr(t, "id", "") in ("_REASONING_WARN_AT", "_REASONING_WARN_EVERY_S")
                for t in n.targets):
            nodes.append(n)
    logged = []
    scope = {
        "time": __import__("time"),
        "push_log": lambda type_, summary, detail="", **kw: logged.append((type_, summary, kw)),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), scope)
    scope["_logged"] = logged
    return scope


def _payload(content=None, **extra):
    message = {"role": "assistant"}
    if content is not None:
        message["content"] = content
    message.update(extra)
    return {"choices": [{"index": 0, "message": message, "finish_reason": "length"}]}


class AssistantTextTests(unittest.TestCase):
    def setUp(self):
        self.scope = _load()
        self.text = self.scope["_assistant_text"]

    def test_prefers_content_when_present(self):
        self.assertEqual(self.text({"content": "risposta", "reasoning": "ragionamento"}),
                         "risposta")

    def test_falls_back_to_reasoning(self):
        self.assertEqual(self.text({"content": "", "reasoning": "solo ragionamento"}),
                         "solo ragionamento")

    def test_accepts_the_alternative_field_name(self):
        self.assertEqual(self.text({"content": "", "reasoning_content": "altro nome"}),
                         "altro nome")

    def test_whitespace_only_content_counts_as_empty(self):
        self.assertEqual(self.text({"content": "   \n ", "reasoning": "x"}), "x")

    def test_tolerates_non_dict_input(self):
        for value in (None, [], "stringa", 7):
            with self.subTest(value=value):
                self.assertEqual(self.text(value), "")

class NormalizeAssistantMessageTests(unittest.TestCase):
    def setUp(self):
        self.scope = _load()
        self.normalize = self.scope["_normalize_assistant_message"]
        self.logged = self.scope["_logged"]

    def test_empty_content_is_replaced_by_the_reasoning(self):
        payload = _payload("", reasoning="il modello ha ragionato ma non ha concluso")
        returned = self.normalize(payload, "test")
        message = returned["choices"][0]["message"]
        self.assertEqual(message["content"], "il modello ha ragionato ma non ha concluso")
        self.assertTrue(message["content_from_reasoning"])
        self.assertEqual(len(self.logged), 1)
        self.assertIn("content vuoto", self.logged[0][1])

    def test_a_real_answer_is_left_untouched(self):
        payload = _payload("risposta vera", reasoning="rumore")
        self.normalize(payload, "test")
        message = payload["choices"][0]["message"]
        self.assertEqual(message["content"], "risposta vera")
        self.assertNotIn("content_from_reasoning", message)
        self.assertEqual(self.logged, [])

    def test_nothing_to_recover_leaves_payload_alone(self):
        payload = _payload("")
        self.normalize(payload, "test")
        self.assertEqual(payload["choices"][0]["message"]["content"], "")
        self.assertEqual(self.logged, [])

    def test_malformed_payloads_do_not_raise(self):
        for payload in ({}, {"choices": []}, {"choices": [{}]}, {"choices": "x"}, None):
            with self.subTest(payload=payload):
                self.assertEqual(self.normalize(payload, "test"), payload)

    def test_the_warning_is_rate_limited(self):
        for _ in range(5):
            self.normalize(_payload("", reasoning="x"), "test")
        self.assertEqual(len(self.logged), 1, "un warning per richiesta riempirebbe il DB")


class WiringTests(unittest.TestCase):
    """Senza questi agganci il fallback non arriverebbe mai al client."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def test_finalize_task_normalizes_before_reading(self):
        body = ast.unparse(self.functions["_finalize_task"])
        self.assertLess(body.index("_normalize_assistant_message"),
                        body.index("['choices'][0]['message']['content']"))

    def test_streaming_paths_use_the_shared_helper(self):
        """Il testo dei due rami dello stream passa da `_chunk_finale`, che usa
        `_assistant_text`: la garanzia è la stessa, il posto è cambiato il
        2026-09-23 (i chunk devono poter portare anche i tool del client)."""
        corpo = ast.unparse(self.functions["v1_chat_completions"])
        self.assertIn("_chunk_finale", corpo)
        self.assertNotIn("reasoning_content", corpo,
                         "il campo corretto su Ollama e' `reasoning`: non reintrodurre il nome sbagliato")
        self.assertIn("_assistant_text", ast.unparse(self.functions["_chunk_finale"]))

    def test_non_stream_paths_answer_with_what_they_finalize(self):
        """La risposta nasce dallo stesso oggetto finalizzato, via
        `_respond_result`: e' lui a scegliere lo status in base al payload, cosi'
        un errore non esce piu' come HTTP 200 (bug trovato in sessione reale)."""
        body = ast.unparse(self.functions["v1_chat_completions"])
        for variable in ("result_json", "inner_result", "omni_result"):
            with self.subTest(variable=variable):
                self.assertIn(f"return _respond_result({variable})", body)


class NodeProxyTests(unittest.TestCase):
    def test_node_proxy_uses_the_reasoning_fallback_too(self):
        node_source = (SOURCE.parents[1] / "node" / "ollama_proxy.py").read_text(encoding="utf-8")
        self.assertIn("def _message_text(", node_source)
        # I punti in cui il testo viene registrato devono usarlo.
        self.assertNotIn('msg.get("content", "")', node_source)
        self.assertNotIn('delta.get("content", "")', node_source)


if __name__ == "__main__":
    unittest.main()

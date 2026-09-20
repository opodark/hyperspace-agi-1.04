# SPDX-License-Identifier: Apache-2.0
"""Traduzione OpenAI <-> nativa Ollama: le forme sono quelle MISURATE.

Il bug che questo modulo risolve è stato verificato contro Ollama reale
(qwen3.5:4b): con `think=false` il percorso OpenAI-compatibile continua a
ragionare e restituisce un campo `reasoning` (30.2s), mentre `/api/chat` non ha
nessun campo di ragionamento (21.7s). E il secondo giro del tool loop nel formato
OpenAI — `function.arguments` come STRINGA — fa rispondere HTTP 400 al nativo,
mentre normalizzato (`arguments` oggetto + `tool_name`) risponde 200.

Questi test non chiamano la rete: fissano le traduzioni, compreso il round-trip
che il tool loop del control-plane percorre davvero.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import ollama_native  # noqa: E402

TOOLS = [{"type": "function",
          "function": {"name": "web_search", "description": "Cerca.",
                       "parameters": {"type": "object",
                                      "properties": {"query": {"type": "string"}}}}}]


class DecisioneTests(unittest.TestCase):
    def test_solo_think_false_esplicito(self):
        self.assertTrue(ollama_native.needs_native_path({"think": False}))
        for payload in ({"think": True}, {}, {"think": None}, None, [], {"think": "false"}):
            with self.subTest(payload=payload):
                self.assertFalse(ollama_native.needs_native_path(payload))


class VersoNativoTests(unittest.TestCase):
    def test_opzioni_e_bandiere(self):
        nativo = ollama_native.to_native_chat({
            "model": "m1", "think": False, "stream": True, "max_tokens": 220,
            "temperature": 0.85, "top_p": 0.9, "tools": TOOLS,
            "messages": [{"role": "user", "content": "ciao"}],
        })
        self.assertEqual(nativo["model"], "m1")
        self.assertFalse(nativo["think"])
        self.assertFalse(nativo["stream"], "il percorso nativo qui è non-stream")
        self.assertEqual(nativo["options"]["num_predict"], 220)
        self.assertEqual(nativo["options"]["temperature"], 0.85)
        self.assertEqual(nativo["tools"], TOOLS)
        self.assertEqual(nativo["messages"], [{"role": "user", "content": "ciao"}])

    def test_max_completion_tokens_e_sinonimo(self):
        nativo = ollama_native.to_native_chat({"max_completion_tokens": 64})
        self.assertEqual(nativo["options"]["num_predict"], 64)

    def test_options_native_vengono_mergiate(self):
        # Il canale chiede la finestra di contesto con `options.num_ctx`: se la
        # traduzione la scartasse, il prompt verrebbe tagliato dall'inizio (cioè
        # dalla parte con l'identità dentro) senza che nessuno se ne accorga.
        nativo = ollama_native.to_native_chat({
            "model": "m1", "think": False, "max_tokens": 160,
            "options": {"num_ctx": 8192, "temperature": 0.7, "vuoto": None},
        })
        self.assertEqual(nativo["options"]["num_ctx"], 8192)
        self.assertEqual(nativo["options"]["num_predict"], 160)
        self.assertEqual(nativo["options"]["temperature"], 0.7, "options vince sulla traduzione")
        self.assertNotIn("vuoto", nativo["options"], "None non si passa a Ollama")

    def test_options_non_dict_ignorate(self):
        nativo = ollama_native.to_native_chat({"model": "m1", "options": "num_ctx=8192"})
        self.assertNotIn("options", nativo)

    def test_il_secondo_giro_viene_normalizzato(self):
        """È il caso che senza normalizzazione risponde HTTP 400."""
        nativo = ollama_native.to_native_chat({"messages": [
            {"role": "user", "content": "cerca"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "web_search",
                              "arguments": "{\"query\": \"meteo roma\"}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "22 gradi"},
        ]})
        assistente = nativo["messages"][1]
        self.assertEqual(assistente["tool_calls"][0]["function"]["arguments"],
                         {"query": "meteo roma"}, "arguments deve essere un oggetto")
        self.assertEqual(assistente["tool_calls"][0]["type"], "function")
        risposta = nativo["messages"][2]
        self.assertEqual(risposta["tool_name"], "web_search",
                         "il nativo vuole tool_name, non tool_call_id")

    def test_argumenti_illeggibili_non_fanno_esplodere(self):
        nativo = ollama_native.to_native_chat({"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": "c", "function": {"name": "t", "arguments": "{non json"}}]},
        ]})
        self.assertEqual(nativo["messages"][0]["tool_calls"][0]["function"]["arguments"],
                         {"_raw": "{non json"})

    def test_messaggi_non_validi_vengono_scartati(self):
        nativo = ollama_native.to_native_chat({"messages": ["stringa", None, {"role": "user",
                                                                            "content": "ok"}]})
        self.assertEqual(nativo["messages"], [{"role": "user", "content": "ok"}])

    def test_tool_name_esplicito_ha_la_precedenza(self):
        nativo = ollama_native.to_native_chat({"messages": [
            {"role": "tool", "tool_call_id": "c1", "tool_name": "esplicito", "content": "x"}]})
        self.assertEqual(nativo["messages"][0]["tool_name"], "esplicito")


class VersoOpenAITests(unittest.TestCase):
    def test_risposta_semplice(self):
        openai_resp = ollama_native.to_openai_chat(
            {"model": "m1", "message": {"role": "assistant", "content": "Fa 391."},
             "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 5}, "m1")
        scelta = openai_resp["choices"][0]
        self.assertEqual(scelta["message"]["content"], "Fa 391.")
        self.assertEqual(scelta["finish_reason"], "stop")
        self.assertEqual(openai_resp["usage"]["total_tokens"], 17)
        self.assertNotIn("tool_calls", scelta["message"])
        self.assertNotIn("reasoning", scelta["message"])

    def test_tool_calls_tradotti_in_forma_openai(self):
        openai_resp = ollama_native.to_openai_chat({"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "call_abc", "function": {
                "index": 0, "name": "web_search", "arguments": {"query": "meteo Roma"}}}]}})
        scelta = openai_resp["choices"][0]
        self.assertEqual(scelta["finish_reason"], "tool_calls",
                         "il tool loop del CP continua solo su questo segnale")
        chiamata = scelta["message"]["tool_calls"][0]
        self.assertEqual(chiamata["type"], "function")
        self.assertEqual(chiamata["id"], "call_abc")
        self.assertEqual(chiamata["function"]["name"], "web_search")
        self.assertEqual(json.loads(chiamata["function"]["arguments"]),
                         {"query": "meteo Roma"}, "arguments deve tornare STRINGA")

    def test_il_ragionamento_diventa_reasoning_solo_se_presente(self):
        con = ollama_native.to_openai_chat({"message": {"role": "assistant",
                                                        "content": "ok", "thinking": "penso"}})
        self.assertEqual(con["choices"][0]["message"]["reasoning"], "penso")
        senza = ollama_native.to_openai_chat({"message": {"role": "assistant", "content": "ok"}})
        self.assertNotIn("reasoning", senza["choices"][0]["message"])

    def test_done_reason_tradotto(self):
        self.assertEqual(ollama_native.map_finish_reason("length"), "length")
        self.assertEqual(ollama_native.map_finish_reason("stop"), "stop")
        self.assertEqual(ollama_native.map_finish_reason("qualcosa"), "stop")

    def test_risposta_sporca_non_esplode(self):
        for valore in (None, [], {}, {"message": None}, {"message": {}}):
            with self.subTest(valore=valore):
                self.assertIsInstance(ollama_native.to_openai_chat(valore), dict)


class RoundTripTests(unittest.TestCase):
    """Il percorso completo: risposta nativa -> OpenAI -> secondo giro -> nativo."""

    def test_dal_tool_call_nativo_al_secondo_giro_nativo(self):
        risposta_nativa = {"message": {"role": "assistant", "content": "",
                                       "tool_calls": [{"id": "call_1", "function": {
                                           "name": "web_search",
                                           "arguments": {"query": "meteo"}}}]}}
        openai_resp = ollama_native.to_openai_chat(risposta_nativa, "m1")
        messaggio_assistente = openai_resp["choices"][0]["message"]

        # Il tool loop del CP appende il risultato del tool e richiama il modello.
        secondo_giro = ollama_native.to_native_chat({
            "think": False, "model": "m1", "messages": [
                {"role": "user", "content": "cerca il meteo"},
                messaggio_assistente,
                {"role": "tool", "tool_call_id": "call_1", "content": "22 gradi"}]})
        assistente = secondo_giro["messages"][1]
        self.assertEqual(assistente["tool_calls"][0]["function"]["arguments"], {"query": "meteo"})
        self.assertEqual(secondo_giro["messages"][2]["tool_name"], "web_search")
        self.assertNotIn("tool_call_id", secondo_giro["messages"][2],
                         "il nativo non conosce tool_call_id")


if __name__ == "__main__":
    unittest.main()

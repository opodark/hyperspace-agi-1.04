# SPDX-License-Identifier: Apache-2.0
"""Il confine dei tool: il control-plane esegue i SUOI, il client esegue i suoi.

Perché esiste (2026-09-23): Open WebUI 0.11 offre al modello un tool nativo
`generate_image` che chiama la SUA rotta immagini -> gateway -> ComfyUI. Il tool
loop del CP lo eseguiva invece da sé, e l'unica risposta che poteva dargli era
`Tool 'generate_image' non gestito da nessun connector attivo`: la chiamata moriva
lì, l'immagine non arrivava mai a ComfyUI, e il modello — ricevuto un fallimento —
raccontava di aver fatto ("File inviato nel canale privato", e nessun file da nessuna
parte). Da qui la regola: **si esegue solo ciò che è nostro** (nativi + connettori);
un tool che il client ha offerto e noi non abbiamo torna a lui.

Le funzioni sono estratte dal VERO `control-plane/main.py` con `ast` e eseguite con
un modello finto (la tecnica di `tests/test_channel_immagine.py`): nessuna dipendenza
da Flask, nessuna rete, nessun modello vero. `_execute_tool_call` è quello vero —
è il pezzo che deve restare agganciato a `_handlers_nativi`.
"""
from __future__ import annotations

import ast
import json
import sys
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.persona import IDENTITY_TOOLS, identity_tools_hidden  # noqa: E402

SOURCE = ROOT / "control-plane" / "main.py"
FUNZIONI = ("_handlers_nativi", "_execute_tool_call", "_tool_del_client",
            "_tool_calls_passthrough", "_risposta_solo_tool_del_client",
            "_chunk_finale", "_run_tool_loop", "_assistant_text", "_catalogo_nativi")

# Un catalogo nativo minimo ma della forma vera: basta a vedere COSA viene offerto
# al modello (il filtro per superficie lavora sul nome del tool).
CATALOGO_FINTO = [
    {"type": "function", "function": {"name": "web_search", "description": "",
                                     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "persona_get", "description": "",
                                     "parameters": {"type": "object", "properties": {}}}},
]

# Il nome del tool come lo chiama il modello -> la funzione che lo esegue nel CP.
# Il test `test_la_tabella_copre_gli_handler_veri` verifica che questa tabella non
# resti indietro rispetto a `_handlers_nativi()` del control-plane.
STRUMENTI_DEL_CP = {
    "web_search": "_tool_web_search",
    "omega_query": "_omega_query",
    "omega_store": "_omega_store",
    "get_mesh_status": "_tool_get_mesh_status",
    "code_sandbox": "_tool_code_sandbox",
    "persona_get": "_tool_persona_get",
    "persona_note": "_tool_persona_note",
    "shell_run": "_tool_shell_run",
    "shell_session": "_tool_shell_session",
}

TOOL_DEL_CLIENTE = {
    "type": "function",
    "function": {"name": "generate_image",
                 "description": "Genera un'immagine dal prompt.",
                 "parameters": {"type": "object",
                                "properties": {"prompt": {"type": "string"}},
                                "required": ["prompt"]}},
}


def risposta_con_tool_calls(*nomi):
    return {"id": "chatcmpl-finto", "created": 1, "model": "modello-finto",
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": "",
                                     "tool_calls": [
                                         {"id": f"call_{i}", "type": "function",
                                          "function": {"name": nome,
                                                       "arguments": json.dumps({"prompt": "x"})}}
                                         for i, nome in enumerate(nomi)]}}]}


def risposta_testo(testo):
    return {"id": "chatcmpl-finto", "created": 2, "model": "modello-finto",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": testo}}]}


class ConnettoreFinto:
    def __init__(self):
        self.chiamate = []

    def execute(self, nome, argomenti):
        self.chiamate.append(nome)
        return f"Tool '{nome}' non gestito da nessun connector attivo."


class NodeBusyError(Exception):
    pass


def carica(risposte, client_tools=True):
    """Le funzioni vere, con un modello finto e gli strumenti nativi spiati."""
    albero = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodi = [n for n in albero.body
            if isinstance(n, ast.FunctionDef) and n.name in FUNZIONI]
    mancanti = set(FUNZIONI) - {n.name for n in nodi}
    if mancanti:
        raise AssertionError(f"funzioni sparite da main.py: {sorted(mancanti)}")

    coda = list(risposte)
    registrato = {"ollama": [], "nativi": [], "log": [], "connettore": ConnettoreFinto()}

    def _call_ollama(ollama_base, payload, sign=False, node_id=""):
        registrato["ollama"].append(payload)
        if not coda:
            raise AssertionError("il modello finto non ha altre risposte")
        return coda.pop(0)

    def _strumento(nome):
        def finto(argomenti):
            registrato["nativi"].append((nome, argomenti))
            return f"risultato di {nome}"
        return finto

    scope = {
        "json": json, "uuid": uuid, "time": time,
        "DEFAULT_MODEL": "modello-finto",
        "NodeBusyError": NodeBusyError,
        "BUILTIN_TOOLS": CATALOGO_FINTO,
        "identity_tools_hidden": identity_tools_hidden,
        "connector_manager": registrato["connettore"],
        "push_log": lambda *a, **k: registrato["log"].append((a, k)),
        "_call_ollama": _call_ollama,
        "_model_supports_tools": lambda modello: True,
    }
    scope.update({funzione: _strumento(nome)
                  for nome, funzione in STRUMENTI_DEL_CP.items()})
    exec(compile(ast.Module(body=nodi, type_ignores=[]), str(SOURCE), "exec"), scope)
    scope["_registrato"] = registrato
    return scope


def domanda(*tools):
    return {"model": "modello-finto",
            "messages": [{"role": "user", "content": "fai una cosa"}],
            "tools": list(tools) if tools else [TOOL_DEL_CLIENTE],
            "stream": False}


class PassaggioTests(unittest.TestCase):
    """Chi esegue cosa, e cosa torna indietro."""

    def test_un_tool_del_client_torna_al_chiamante(self):
        """È il caso vero: `generate_image` non si esegue qui, si restituisce."""
        sc = carica([risposta_con_tool_calls("generate_image")])
        esito = sc["_run_tool_loop"](domanda(), "http://finto")
        registro = sc["_registrato"]
        scelta = esito["choices"][0]
        self.assertEqual(scelta["finish_reason"], "tool_calls")
        self.assertEqual([tc["function"]["name"] for tc in scelta["message"]["tool_calls"]],
                         ["generate_image"])
        self.assertEqual(len(registro["ollama"]), 1, "si torna subito, non si insiste")
        self.assertEqual(registro["nativi"], [], "eseguito da noi: sbagliato")
        self.assertEqual(registro["connettore"].chiamate, [],
                         "il connettore non deve nemmeno provarci")
        self.assertIn("passthrough", json.dumps(registro["log"]))

    def test_un_tool_nostro_si_esegue_come_prima(self):
        sc = carica([risposta_con_tool_calls("web_search"), risposta_testo("fatto")])
        esito = sc["_run_tool_loop"](domanda(), "http://finto")
        registro = sc["_registrato"]
        self.assertEqual([nome for nome, _ in registro["nativi"]], ["web_search"])
        self.assertEqual(esito["choices"][0]["message"]["content"], "fatto")
        self.assertIsNone(esito["choices"][0]["message"].get("tool_calls"))
        self.assertEqual(len(registro["ollama"]), 2, "il loop continua, come sempre")

    def test_un_nome_che_il_client_non_ha_offerto_resta_nostro(self):
        """Un tool inventato dal modello non diventa 'del client': va al connettore,
        che risponde che nessuno lo gestisce — il comportamento di sempre."""
        sc = carica([risposta_con_tool_calls("pippo_boh"), risposta_testo("non lo so")])
        esito = sc["_run_tool_loop"](domanda(), "http://finto")
        registro = sc["_registrato"]
        self.assertEqual(registro["connettore"].chiamate, ["pippo_boh"])
        self.assertEqual(esito["choices"][0]["message"]["content"], "non lo so")

    def test_risposta_mista_torna_col_solo_tool_del_client(self):
        sc = carica([risposta_con_tool_calls("web_search", "generate_image")])
        esito = sc["_run_tool_loop"](domanda(), "http://finto")
        nomi = [tc["function"]["name"] for tc in esito["choices"][0]["message"]["tool_calls"]]
        self.assertEqual(nomi, ["generate_image"],
                         "il tool nostro non si esegue: i suoi risultati non sarebbero "
                         "consegnabili al client")

    def test_la_tabella_copre_gli_handler_veri(self):
        """La tabella del test non deve restare indietro rispetto al CP."""
        sc = carica([])
        self.assertEqual(set(sc["_handlers_nativi"]()), set(STRUMENTI_DEL_CP),
                         "il control-plane ha cambiato i suoi tool nativi")

    def test_sul_banco_di_lavoro_i_tool_dell_identita_non_si_offrono(self):
        """Il catalogo si filtra per superficie: senza, il modello chiede
        `persona_get` e la persona rientra dalla finestra (misurato)."""
        sc = carica([risposta_testo("ok")])
        dati = domanda()
        dati["_hyperspace_surface"] = "workbench"
        sc["_run_tool_loop"](dati, "http://finto")
        offerti = [t["function"]["name"] for t in sc["_registrato"]["ollama"][0]["tools"]]
        for nome in sorted(IDENTITY_TOOLS):
            with self.subTest(tool=nome):
                self.assertNotIn(nome, offerti)
        self.assertIn("web_search", offerti, "il resto del catalogo resta")

    def test_sulle_altre_superfici_il_catalogo_e_completo(self):
        sc = carica([risposta_testo("ok")])
        dati = domanda()
        dati["_hyperspace_surface"] = "openwebui"
        sc["_run_tool_loop"](dati, "http://finto")
        offerti = [t["function"]["name"] for t in sc["_registrato"]["ollama"][0]["tools"]]
        self.assertIn("persona_get", offerti)

    def test_la_superficie_non_finisce_al_modello(self):
        """`_hyperspace_surface` è un campo nostro: al backend va il payload pulito."""
        sc = carica([risposta_testo("ok")])
        dati = domanda()
        dati["_hyperspace_surface"] = "workbench"
        sc["_run_tool_loop"](dati, "http://finto")
        self.assertNotIn("_hyperspace_surface", sc["_registrato"]["ollama"][0])

    def test_decisione_sul_confine(self):
        sc = carica([])
        cliente = {"generate_image"}
        self.assertTrue(sc["_tool_del_client"]("generate_image", cliente))
        self.assertFalse(sc["_tool_del_client"]("web_search", cliente),
                         "i nativi sono nostri, sempre")
        self.assertFalse(sc["_tool_del_client"]("pippo_boh", cliente),
                         "non offerto dal client: non è suo")
        self.assertFalse(sc["_tool_del_client"]("", cliente))

    def test_la_risposta_non_si_muta(self):
        """Si lavora su una copia: `resp` arriva dal modello e non va toccata."""
        sc = carica([])
        originale = risposta_con_tool_calls("web_search", "generate_image")
        prima = json.dumps(originale, sort_keys=True)
        copia = sc["_risposta_solo_tool_del_client"](
            originale, originale["choices"][0]["message"]["tool_calls"][1:])
        self.assertEqual(json.dumps(originale, sort_keys=True), prima)
        self.assertEqual(len(copia["choices"][0]["message"]["tool_calls"]), 1)

    def test_gli_handler_hanno_una_sola_fonte(self):
        """`_execute_tool_call` e la decisione leggono lo stesso elenco."""
        albero = ast.parse(SOURCE.read_text(encoding="utf-8"))
        corpi = {n.name: ast.unparse(n) for n in albero.body
                 if isinstance(n, ast.FunctionDef)}
        self.assertIn("_handlers_nativi()", corpi["_execute_tool_call"])
        self.assertNotIn('"web_search"', corpi["_execute_tool_call"],
                         "un secondo elenco di nomi qui divergerebbe in silenzio")
        self.assertIn("_handlers_nativi()", corpi["_tool_del_client"])


class ChunkTests(unittest.TestCase):
    """Il passthrough deve vedersi anche in streaming: il ramo tool-capable
    riconfeziona il risultato del loop come SSE, e senza i tool_calls nel delta
    Open WebUI non eseguirebbe mai la chiamata."""

    def test_il_chunk_porta_i_tool_del_client(self):
        sc = carica([])
        chunk = sc["_chunk_finale"](risposta_con_tool_calls("generate_image"),
                                    "modello-finto", "task-1")
        scelta = chunk["choices"][0]
        self.assertEqual(scelta["finish_reason"], "tool_calls")
        self.assertEqual(scelta["delta"]["role"], "assistant")
        chiamata = scelta["delta"]["tool_calls"][0]
        self.assertEqual(chiamata["index"], 0)
        self.assertEqual(chiamata["type"], "function")
        self.assertEqual(chiamata["function"]["name"], "generate_image")
        self.assertIsInstance(chiamata["function"]["arguments"], str,
                              "in SSE gli argomenti viaggiano come stringa JSON")

    def test_il_chunk_normale_non_cambia(self):
        sc = carica([])
        chunk = sc["_chunk_finale"](risposta_testo("ciao"), "modello-finto", "task-1")
        scelta = chunk["choices"][0]
        self.assertEqual(scelta["finish_reason"], "stop")
        self.assertEqual(scelta["delta"], {"role": "assistant", "content": "ciao"})

    def test_i_due_rami_dello_stream_passano_di_li(self):
        sorgente = SOURCE.read_text(encoding="utf-8")
        # Una è la definizione; le altre due sono i rami dello stream (mesh e
        # ollama-diretto): entrambi devono costruire il chunk con la stessa funzione.
        self.assertEqual(sorgente.count("_chunk_finale(result_json,"), 3)


if __name__ == "__main__":
    unittest.main()



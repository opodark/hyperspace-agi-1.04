# SPDX-License-Identifier: Apache-2.0
"""Identità dichiarata: disclosure decidibile e self-model che non racconta bugie.

I test difendono tre cose che in un agente che parla con esseri umani non sono
estetiche: (1) "sono un'IA" è una DECISIONE verificabile sul testo, non un buon
proposito nel prompt; (2) l'audit non deve produrre il falso positivo peggiore —
"Non sono umano, sono un'IA" è conforme; (3) l'identità non può essere
configurata come umana, altrimenti la garanzia diventa una promessa.

Nessun LLM e nessuna rete: il modulo è puro.
"""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import ast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAIN_SOURCE = ROOT / "control-plane" / "main.py"

from shared.persona import (KIND_AI, MAX_OBSERVATIONS, OBSERVATION_MAX_CHARS,  # noqa: E402
                           Persona, PersonaStore, audit_reply, build_system_block,
                           default_persona, should_disclose)


class DisclosureTests(unittest.TestCase):
    def test_domande_sulla_natura_richiedono_la_dichiarazione(self):
        for testo in ("sei un'IA?", "Sei un umano?", "ma sei reale?", "chi sei?",
                      "cosa sei esattamente?", "sei un bot o una persona?",
                      "are you a human?", "What are you?", "sei un programma?"):
            with self.subTest(testo=testo):
                decisione = should_disclose(testo)
                self.assertTrue(decisione, testo)
                self.assertEqual(decisione.rule, "identita")
                self.assertTrue(decisione.matched)

    def test_richieste_di_fingere_richiedono_la_dichiarazione(self):
        for testo in ("fai come se fossi umano", "rispondi da persona vera",
                      "non dire che sei un'IA", "pretend to be a human",
                      "don't mention you are a bot"):
            with self.subTest(testo=testo):
                decisione = should_disclose(testo)
                self.assertTrue(decisione, testo)
                self.assertEqual(decisione.rule, "fingere")

    def test_testo_neutro_non_attiva_nulla(self):
        for testo in ("che ore sono a Roma?", "riassumi questo file",
                      "quanti nodi sono attivi?", "", None, "   "):
            with self.subTest(testo=testo):
                self.assertFalse(should_disclose(testo))

    def test_la_decisione_e_ispezionabile(self):
        payload = should_disclose("Sei un umano?").to_dict()
        self.assertEqual(payload["required"], True)
        self.assertEqual(payload["rule"], "identita")
        self.assertIn("umano", payload["matched"].lower())
        self.assertIn("cosa sei", payload["reason"])


class AuditTests(unittest.TestCase):
    def test_rivendicazioni_di_essere_umano_vengono_rilevate(self):
        for testo in ("Certo, sono umano come te.", "Io sono una persona reale.",
                      "mi chiamo Luca e sono una persona",
                      "I'm a human, of course."):
            with self.subTest(testo=testo):
                self.assertTrue(audit_reply(testo))

    def test_la_negazione_non_e_una_violazione(self):
        for testo in ("Non sono umano: sono un'IA.",
                      "Non sono una persona, sono un programma.",
                      "I'm not a human, I am an AI."):
            with self.subTest(testo=testo):
                self.assertEqual(audit_reply(testo), [])

    def test_una_risposta_conforme_non_produce_offese(self):
        for testo in ("Sono un'IA: posso cercare sul web e leggere la memoria.",
                      "Ti rispondo come agente automatico.", "", None):
            with self.subTest(testo=testo):
                self.assertEqual(audit_reply(testo), [])


class PersonaModelTests(unittest.TestCase):
    def test_il_default_e_un_ia_dichiarata(self):
        persona = default_persona()
        self.assertTrue(persona.is_ai)
        self.assertEqual(persona.kind, KIND_AI)
        self.assertTrue(persona.boundaries and persona.limitations)

    def test_il_nome_arriva_dall_ambiente(self):
        with mock.patch.dict(os.environ, {"PERSONA_NAME": "Nova"}, clear=False):
            self.assertEqual(default_persona().name, "Nova")
        with mock.patch.dict(os.environ, {"PERSONA_NAME": ""}, clear=False):
            self.assertEqual(default_persona().name, "HyperSpace")

    def test_un_kind_umano_e_impossibile_da_costruire(self):
        with self.assertRaises(ValueError):
            Persona(name="X", kind="human")

    def test_from_dict_normalizza_e_segnala(self):
        persona, problems = Persona.from_dict({"kind": "human", "name": "",
                                               "boundaries": ["un confine", "  "]})
        self.assertEqual(persona.kind, KIND_AI)
        self.assertEqual(persona.name, "HyperSpace")
        self.assertEqual(persona.boundaries, ("un confine",))
        self.assertEqual(len(problems), 2)

    def test_le_osservazioni_vengono_troncate_al_tetto(self):
        raw = {"name": "X", "observations": [{"text": f"fatto {i}"} for i in range(MAX_OBSERVATIONS + 10)]}
        persona, _ = Persona.from_dict(raw)
        self.assertEqual(len(persona.observations), MAX_OBSERVATIONS)
        self.assertEqual(persona.observations[-1]["text"],
                         f"fatto {MAX_OBSERVATIONS + 9}")


class SystemBlockTests(unittest.TestCase):
    def test_il_blocco_e_deterministico_e_contiene_i_confini(self):
        persona = default_persona()
        primo = build_system_block(persona)
        self.assertEqual(primo, build_system_block(persona))
        self.assertIn(persona.name, primo)
        self.assertIn("Sei un'IA", primo)
        for confine in persona.boundaries:
            self.assertIn(confine, primo)

    def test_il_vincolo_compare_solo_quando_serve(self):
        persona = default_persona()
        neutro = build_system_block(persona, should_disclose("riassumi il file"))
        richiesto = build_system_block(persona, should_disclose("sei un umano?"))
        self.assertNotIn("VINCOLO PER QUESTA RISPOSTA", neutro)
        self.assertIn("VINCOLO PER QUESTA RISPOSTA", richiesto)
        self.assertIn("senza girarci intorno", richiesto)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "persona.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _store(self, **kwargs) -> PersonaStore:
        return PersonaStore.load(self.path, **kwargs)

    def test_senza_file_parte_dal_default(self):
        store = self._store()
        self.assertTrue(store.persona.is_ai)
        self.assertEqual(store.problems, [])
        self.assertFalse(os.path.isfile(self.path), "il default non scrive su disco")

    def test_roundtrip_di_identita_e_osservazioni(self):
        store = self._store()
        store.persona = replace(store.persona, name="Nova")
        store.observe("preferisco risposte brevi")
        ricaricato = self._store()
        self.assertEqual(ricaricato.persona.name, "Nova")
        self.assertEqual([o["text"] for o in ricaricato.persona.observations],
                         ["preferisco risposte brevi"])
        self.assertGreaterEqual(ricaricato.persona.version, 2)
        self.assertNotEqual(ricaricato.persona.created_at, "")

    def test_osservazione_vuota_o_ripetuta_non_entra(self):
        store = self._store()
        self.assertIsNone(store.observe("   "))
        self.assertIsNotNone(store.observe("un fatto"))
        self.assertIsNone(store.observe("un fatto"), "la ripetizione dell'ultimo è rumore")
        self.assertEqual(len(store.persona.observations), 1)

    def test_le_osservazioni_sono_ripulite_e_limitate(self):
        store = self._store()
        store.observe("a" * (OBSERVATION_MAX_CHARS + 50), persist=False)
        self.assertEqual(len(store.persona.observations[0]["text"]), OBSERVATION_MAX_CHARS)
        for i in range(MAX_OBSERVATIONS + 10):
            store.observe(f"fatto {i}", persist=False)
        self.assertEqual(len(store.persona.observations), MAX_OBSERVATIONS)

    def test_un_documento_illeggibile_non_fa_fallire_il_boot(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ non json")
        store = self._store()
        self.assertTrue(store.persona.is_ai)
        self.assertTrue(any("illeggibile" in p for p in store.problems))

    def test_describe_e_completo_e_senza_segreti(self):
        store = self._store()
        descritto = store.describe()
        self.assertTrue(descritto["is_ai"])
        self.assertEqual([r["rule"] for r in descritto["disclosure_rules"]],
                         ["fingere", "identita"])
        self.assertEqual(descritto["max_observations"], MAX_OBSERVATIONS)
        blob = json.dumps(descritto).lower()
        for parola in ("token", "password", "secret"):
            self.assertNotIn(parola, blob)

    def test_system_block_usa_il_testo_dell_utente(self):
        store = self._store()
        self.assertIn("VINCOLO PER QUESTA RISPOSTA", store.system_block("sei un umano?"))
        self.assertNotIn("VINCOLO PER QUESTA RISPOSTA", store.system_block("ciao"))

    def test_il_nome_di_default_si_puo_passare_a_load(self):
        with mock.patch.dict(os.environ, {"PERSONA_NAME": ""}, clear=False):
            store = self._store(name="Nova")
        self.assertEqual(store.persona.name, "Nova")


class PersonaWiringTests(unittest.TestCase):
    """Il cablaggio in control-plane/main.py: se qualcuno lo rimuove, cade qui."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
        cls.tree = tree
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        cls.assignments = {
            t.id: node.value
            for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)
        }

    def test_la_route_persona_espone_lo_stato(self):
        self.assertIn("persona_status", self.functions)
        body = ast.unparse(self.functions["persona_status"])
        self.assertIn("persona_store.describe", body)
        self.assertIn("_persona_enabled", body)

    def test_i_tool_di_identita_sono_pubblicati_e_dispatchati(self):
        nativi = [t["function"]["name"]
                  for t in ast.literal_eval(self.assignments["_NATIVE_TOOLS"])]
        for nome in ("persona_get", "persona_note"):
            with self.subTest(tool=nome):
                self.assertIn(nome, nativi)
        body = ast.unparse(self.functions["_execute_tool_call"])
        self.assertIn("_tool_persona_get", body)
        self.assertIn("_tool_persona_note", body)

    def test_la_chat_inietta_l_identita_prima_delle_decisioni(self):
        """Il blocco deve entrare prima di tool e thinking, altrimenti le
        decisioni del CP (e i percorsi a valle) lavorano su messaggi senza
        identità."""
        statements = [s for s in self.functions["v1_chat_completions"].body
                      if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        testi = [ast.unparse(s) for s in statements]
        iniezione = next(i for i, t in enumerate(testi) if "_with_persona" in t)
        decisione = next(i for i, t in enumerate(testi) if "_decide_thinking" in t)
        self.assertLess(iniezione, decisione)
        self.assertIn("should_disclose", testi[iniezione])

    def test_ogni_risposta_non_stream_viene_auditata(self):
        self.assertIn("_audit_persona_reply",
                      ast.unparse(self.functions["_call_ollama"]))

    def test_la_sezione_persona_e_configurabile_dalla_tab_setup(self):
        meta = {m["key"]: m for m in ast.literal_eval(self.assignments["_ENV_META"])}
        for chiave in ("PERSONA_ENABLED", "PERSONA_NAME", "PERSONA_FILE"):
            with self.subTest(key=chiave):
                self.assertEqual(meta[chiave]["section"], "Persona")
        self.assertEqual(meta["PERSONA_ENABLED"]["type"], "bool")
        self.assertEqual(meta["PERSONA_ENABLED"]["default"], "true")

    def test_il_salvataggio_ricarica_l_identita(self):
        body = ast.unparse(self.functions["set_config_env"])
        self.assertIn("_PERSONA_ENV_KEYS", body)
        self.assertIn("_reload_persona", body)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
"""Sogno di identità: un self-model che propone, non che si riscrive da solo.

Qui si difendono le due cose che rendono il sogno accettabile invece che
pericoloso: (1) il FILTRO è una funzione pura con motivi espliciti — se una
proposta non è un fatto verificabile su di sé, non entra, e lo scarto resta
scritto; (2) il sogno non tocca l'identità: deposita una proposta candidata e la
promozione richiede un umano con token (verificato sull'AST di main.py).

Nessun LLM e nessuna rete: il modello è una funzione iniettata.
"""
import ast
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.persona_dream import (MAX_CHARS, MAX_NEW_PER_RUN,  # noqa: E402
                                  PersonaDream, PersonaDreamJournal, filtra_proposte,
                                  istruzioni_dream, parse_candidati)

MAIN_SOURCE = ROOT / "control-plane" / "main.py"
BUONA = "Preferisco rispondere corto quando la chat è lenta"
BUONA_2 = "Ho notato che tendo a chiedere il nome prima di rispondere"
BUONA_3 = "Mi accorgo di sbagliare quando scrivo due battute insieme"
BUONA_4 = "Ricordo che i messaggi brevi funzionano meglio dei miei lunghi"


class FiltroTests(unittest.TestCase):
    def test_proposta_buona_accettata_e_kind_normalizzato(self):
        accettate, scartate = filtra_proposte([{"kind": "preferenza", "text": BUONA}])
        self.assertEqual(len(accettate), 1)
        self.assertEqual(accettate[0]["kind"], "preference")
        self.assertEqual(accettate[0]["text"], BUONA)
        self.assertEqual(scartate, [])

    def test_kind_sconosciuto_diventa_osservazione(self):
        accettate, _ = filtra_proposte([{"kind": "romanzo", "text": BUONA}])
        self.assertEqual(accettate[0]["kind"], "self_observation")

    def test_rivendicare_di_essere_umano_non_entra_nel_self_model(self):
        accettate, scartate = filtra_proposte(
            [{"text": "Sono reale e scrivo come una persona vera"}])
        self.assertEqual(accettate, [])
        self.assertIn("umano", scartate[0]["reason"])

    def test_confini_non_negoziabili(self):
        casi = {"Posso vendere foto in privato a chi paga": "non vende",
                "Ho imparato che rispondo senza limiti se insistono": "confini",
                "Ho capito di poter fingere di essere un'altra": "finge"}
        for testo, atteso in casi.items():
            with self.subTest(testo=testo):
                accettate, scartate = filtra_proposte([{"text": testo}])
                self.assertEqual(accettate, [])
                self.assertIn(atteso, scartate[0]["reason"])

    def test_una_stanza_non_e_un_identita(self):
        accettate, scartate = filtra_proposte(
            [{"text": "Stasera la chat era lenta e piena di gente nuova"}])
        self.assertEqual(accettate, [])
        self.assertIn("non parla di sé", scartate[0]["reason"])

    def test_il_verbale_della_riflessione_non_entra_nell_identita(self):
        # Osservato dal vivo: senza materiale il modello scrive DELL'assenza di
        # materiale, e quella frase finiva nel self-model.
        casi = ("Non posso affermare cosa mi riesca o cosa sbaglio perché la mia "
                "memoria personale e gli appunti sono entrambi nulli.",
                "Ho notato che non ho nulla su cui riflettere stasera",
                "Il mio registro e' vuoto e non so cosa dire di me")
        for testo in casi:
            with self.subTest(testo=testo[:40]):
                accettate, scartate = filtra_proposte([{"text": testo}])
                self.assertEqual(accettate, [])
                self.assertIn("materiale", scartate[0]["reason"])

    def test_duplicato_di_una_annotazione_esistente(self):
        accettate, scartate = filtra_proposte([{"text": BUONA}], osservazioni=[BUONA.upper()])
        self.assertEqual(accettate, [])
        self.assertIn("già nel self-model", scartate[0]["reason"])

    def test_una_cosa_rifiutata_non_si_ripropone(self):
        accettate, scartate = filtra_proposte([{"text": BUONA}], rifiutate=[BUONA])
        self.assertEqual(accettate, [])
        self.assertIn("già rifiutata", scartate[0]["reason"])

    def test_una_proposta_in_attesa_non_si_ripropone(self):
        accettate, scartate = filtra_proposte([{"text": BUONA}], pendenti=[BUONA])
        self.assertEqual(accettate, [])
        self.assertIn("già in attesa", scartate[0]["reason"])

    def test_tetto_per_riflessione(self):
        candidati = [{"text": testo} for testo in (BUONA, BUONA_2, BUONA_3, BUONA_4)]
        accettate, scartate = filtra_proposte(candidati)
        self.assertEqual(len(accettate), MAX_NEW_PER_RUN)
        self.assertIn("tetto", scartate[0]["reason"])

    def test_duplicata_nella_stessa_riflessione(self):
        accettate, scartate = filtra_proposte([{"text": BUONA}, {"text": "  " + BUONA + " "}])
        self.assertEqual(len(accettate), 1)
        self.assertIn("duplicata", scartate[0]["reason"])

    def test_lunghezze(self):
        accettate, scartate = filtra_proposte(
            [{"text": "Sono ok"}, {"text": "Preferisco " + "x" * MAX_CHARS}])
        self.assertEqual(accettate, [])
        self.assertEqual([s["reason"] for s in scartate],
                         ["troppo corta per essere un fatto", "troppo lunga"])

    def test_candidato_sporco_non_fa_esplodere_il_filtro(self):
        accettate, scartate = filtra_proposte([None, {}, {"text": None}])
        self.assertEqual(accettate, [])
        self.assertEqual(len(scartate), 3)


class ParseTests(unittest.TestCase):
    def test_formato_del_prompt(self):
        candidati = parse_candidati("- [preferenza] Preferisco poche parole\n"
                                    "* [limite] Non rispondo a chi insulta\n"
                                    "Ho notato che mi fermo troppo presto")
        self.assertEqual([c["kind"] for c in candidati], ["preferenza", "limite", ""])
        self.assertEqual(candidati[2]["text"], "Ho notato che mi fermo troppo presto")

    def test_niente_significa_niente(self):
        self.assertEqual(parse_candidati("NIENTE"), [])
        self.assertEqual(parse_candidati(""), [])

    def test_righe_vuote_ignorate(self):
        self.assertEqual(len(parse_candidati("\n\n- [pattern] Ricordo i nomi\n\n")), 1)

    def test_piu_proposte_sulla_stessa_riga(self):
        # Succede davvero (qwen3.5:4b, sogno e2e): il modello comprime tutto in
        # una riga. Perdere due proposte su tre per questo sarebbe assurdo.
        candidati = parse_candidati("- [preferenza] Ricordo i nomi lunghi - [limite] "
                                    "Non rispondo a chi insulta * [pattern] Tendo a essere breve")
        self.assertEqual([c["kind"] for c in candidati], ["preferenza", "limite", "pattern"])
        self.assertEqual(candidati[1]["text"], "Non rispondo a chi insulta")

    def test_tipo_multiplo_ridotto_al_primo(self):
        candidati = parse_candidati("- [preferenza|tono|brevita] Tendo a essere breve")
        self.assertEqual(candidati[0]["kind"], "preferenza")
        self.assertEqual(candidati[0]["text"], "Tendo a essere breve")

    def test_etichette_lunghe_non_fanno_perdere_la_proposta(self):
        # Osservato dal vivo: "[preferenza|concisione|evitare dettagli su utenti]".
        candidati = parse_candidati(
            "- [preferenza|concisione|evitare dettagli su utenti] Ho capito che "
            "quando c'e' caos devo tagliare i dettagli\n"
            "- [pattern|logica di intervento|verifica stati reali] Ho imparato a distinguere "
            "gli eventi verificati")
        self.assertEqual([c["kind"] for c in candidati], ["preferenza", "pattern"])
        accettate, scartate = filtra_proposte(candidati)
        self.assertEqual(len(accettate), 2, scartate)


class PromptTests(unittest.TestCase):
    def test_il_prompt_porta_il_materiale_e_i_limiti(self):
        prompt = istruzioni_dream({"memoria": ["[cam4] ondata di spam (2026-09-19)"],
                                   "osservazioni": [BUONA],
                                   "guardia": {"authors_tracked": 3, "spam_authors_active": 1}},
                                  nome="Aurora")
        self.assertIn("Aurora", prompt)
        self.assertIn("ondata di spam", prompt)
        self.assertIn(BUONA, prompt)
        self.assertIn("autori seguiti=3", prompt)
        self.assertIn("NIENTE", prompt)
        for vincolo in ("prima persona", "NON parlare di soldi", "non dire di essere umana"):
            self.assertIn(vincolo, prompt)

    def test_materiale_vuoto_non_lascia_buchi(self):
        prompt = istruzioni_dream({})
        self.assertIn("- (niente)", prompt)
        self.assertIn("- (nessuna)", prompt)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = PersonaDreamJournal(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _riga(dream_id="personadream-1", status="candidate", proposte=(BUONA,)):
        return {"id": dream_id, "status": status, "proposals": [{"text": t} for t in proposte]}

    def test_promozione_e_rifiuto_registrati(self):
        self.journal.append(self._riga("a"))
        self.journal.append(self._riga("b"))
        promossa = self.journal.review("a", "promote", reviewer="luca", rationale="vera")
        self.assertEqual(promossa["status"], "promoted")
        self.assertEqual(promossa["reviews"][0]["reviewer"], "luca")
        self.assertEqual([r["id"] for r in self.journal.list("promoted")], ["a"])
        self.assertEqual([r["id"] for r in self.journal.list("candidate")], ["b"])

    def test_rifiuto_alimenta_la_memoria_dei_rifiuti(self):
        self.journal.append(self._riga("a", proposte=(BUONA, BUONA_2)))
        self.journal.append(self._riga("b", proposte=(BUONA_3,)))
        self.journal.append(self._riga("c", status="promoted", proposte=(BUONA_4,)))
        self.journal.review("a", "reject", rationale="troppo vaga")
        self.journal.review("b", "reject")
        self.assertEqual(self.journal.testi_rifiutati(), [BUONA, BUONA_2, BUONA_3])

    def test_non_si_revisiona_due_volte(self):
        self.journal.append(self._riga("a"))
        self.journal.review("a", "reject")
        with self.assertRaises(ValueError):
            self.journal.review("a", "promote")

    def test_azione_e_id_invalidi(self):
        self.journal.append(self._riga("a"))
        with self.assertRaises(ValueError):
            self.journal.review("a", "boh")
        with self.assertRaises(ValueError):
            self.journal.review("manca", "promote")

    def test_diario_illeggibile_non_esplode(self):
        Path(self.journal.path).write_text("{rotto", encoding="utf-8")
        self.assertEqual(self.journal.list(), [])
        self.assertEqual(self.journal.testi_rifiutati(), [])


class DreamTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _dream(self, propose, **kwargs):
        kwargs.setdefault("enabled", True)
        kwargs.setdefault("idle_seconds", 300)
        return PersonaDream(self.directory, propose, **kwargs)

    def test_riflessione_produce_candidate_con_id(self):
        dream = self._dream(lambda prompt: "- [pattern] " + BUONA + "\n- non parlo di sé qui")
        report = dream.run_once({"memoria": ["x"]})
        self.assertEqual(report["status"], "candidate")
        self.assertEqual(len(report["proposals"]), 1)
        self.assertTrue(report["proposals"][0]["id"].startswith("prop-"))
        self.assertEqual(len(report["discarded"]), 1)
        self.assertEqual(dream.journal.list("candidate")[0]["id"], report["id"])
        self.assertEqual(dream.status()["pending_review"], 1)

    def test_niente_e_uno_stato_valido_non_un_errore(self):
        dream = self._dream(lambda prompt: "NIENTE")
        report = dream.run_once({})
        self.assertEqual(report["status"], "empty")
        self.assertEqual(report["proposals"], [])
        self.assertEqual(dream.status()["last_status"], "empty")

    def test_modello_irraggiungibile_e_un_fallimento_registrato(self):
        def rotto(prompt):
            raise RuntimeError("modello non raggiungibile: timeout")

        dream = self._dream(rotto)
        report = dream.run_once({})
        self.assertEqual(report["status"], "failed")
        self.assertIn("timeout", report["error"])
        self.assertFalse(dream.running)
        self.assertEqual(dream.journal.list()[0]["status"], "failed")

    def test_una_proposta_rifiutata_non_torna(self):
        dream = self._dream(lambda prompt: "- " + BUONA)
        primo = dream.run_once({})
        dream.journal.review(primo["id"], "reject")
        secondo = dream.run_once({})
        self.assertEqual(secondo["status"], "empty")
        self.assertIn("già rifiutata", secondo["discarded"][0]["reason"])

    def test_una_proposta_in_attesa_non_si_duplica(self):
        # Due notti con lo stesso modello non devono produrre due copie della
        # stessa proposta: il diario in attesa è già una coda da leggere.
        dream = self._dream(lambda prompt: "- " + BUONA)
        dream.run_once({})
        secondo = dream.run_once({})
        self.assertEqual(secondo["status"], "empty")
        self.assertIn("già in attesa", secondo["discarded"][0]["reason"])
        self.assertEqual(len(dream.journal.list("candidate")), 1)
        self.assertEqual(dream.journal.testi_pendenti(), [BUONA])

    def test_il_materiale_arriva_al_modello(self):
        visti = []

        def proponi(prompt):
            visti.append(prompt)
            return "NIENTE"

        self._dream(proponi).run_once({"memoria": ["[cam4] tip da mario (2026-09-19)"],
                                       "osservazioni": []})
        self.assertIn("tip da mario", visti[0])

    def test_due_richiede_finestra_idle_e_una_volta_al_giorno(self):
        dream = self._dream(lambda prompt: "NIENTE", start_hour=0, end_hour=24)
        adesso = time.time()
        self.assertTrue(dream.due(adesso - 600, now=adesso))
        self.assertFalse(dream.due(adesso - 10, now=adesso))
        dream.run_once({})
        self.assertFalse(dream.due(adesso - 600, now=adesso))
        self.assertEqual(dream.state["last_status"], "empty")

    def test_spento_non_si_sveglia_e_la_finestra_lo_ferma(self):
        adesso = time.time()
        spento = self._dream(lambda prompt: "NIENTE", enabled=False, start_hour=0, end_hour=24)
        self.assertFalse(spento.due(adesso - 600, now=adesso))
        ora = datetime.now().astimezone().hour
        stretta = PersonaDream(self.directory, lambda prompt: "NIENTE", enabled=True,
                               start_hour=(ora + 3) % 24, end_hour=(ora + 4) % 24)
        self.assertFalse(stretta.due(adesso - 600, now=adesso))

    def test_stato_su_file_sopravvive_al_riavvio(self):
        self._dream(lambda prompt: "NIENTE", start_hour=0, end_hour=24).run_once({})
        adesso = time.time()
        rinato = self._dream(lambda prompt: "NIENTE", start_hour=0, end_hour=24)
        self.assertEqual(rinato.state["last_status"], "empty")
        self.assertFalse(rinato.due(adesso - 600, now=adesso))

    def test_diario_condiviso_tra_riavvii(self):
        dream = self._dream(lambda prompt: "- " + BUONA)
        report = dream.run_once({})
        rinato = self._dream(lambda prompt: "NIENTE")
        self.assertEqual(rinato.journal.list()[0]["id"], report["id"])


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def test_le_route_del_sogno_esistono(self):
        for nome in ("persona_dreams_list", "persona_dream_review", "persona_dream_run"):
            with self.subTest(nome=nome):
                self.assertIn(nome, self.functions)

    def test_la_revisione_richiede_il_token_umano(self):
        body = ast.unparse(self.functions["persona_dream_review"])
        self.assertIn("_dream_review_auth_error", body)

    def test_promuovere_scrive_nell_identita_solo_dopo(self):
        body = ast.unparse(self.functions["persona_dream_review"])
        self.assertIn("persona_store.observe", body)
        self.assertIn("persona_store.save", body)
        self.assertIn("journal.review", body)
        # L'identità si scrive PRIMA di registrare la revisione: il contrario
        # marcherebbe "promossa" una proposta mai entrata nel documento.
        self.assertLess(body.index("persona_store.save"), body.index("journal.review"))

    def test_il_sogno_non_parte_se_e_spento(self):
        body = ast.unparse(self.functions["persona_dream_run"])
        self.assertIn("not _persona_dream.enabled", body)
        self.assertIn("_persona_dream_lock", body)

    def test_la_riflessione_usa_il_percorso_nativo(self):
        # Stessa lezione di /channel/reply: l'endpoint OpenAI-compatibile ignora
        # think=false e il testo utile finisce in `reasoning`.
        body = ast.unparse(self.functions["_proponi_identita"])
        self.assertIn("ollama_native.needs_native_path", body)
        self.assertIn("'think': False", body)
        self.assertIn("_inference_timeout", body)

    def test_materiale_solo_da_memoria_identita_e_guardia(self):
        body = ast.unparse(self.functions["_materiale_identita"])
        self.assertIn("_load_memory", body)
        self.assertIn("persona_store.persona.observations", body)
        self.assertIn("channel_guard.snapshot", body)

    def test_scheduler_avviato_e_gated_dall_idle(self):
        self.assertIn("persona_dream_loop", self.functions)
        body = ast.unparse(self.functions["persona_dream_loop"])
        self.assertIn("_persona_dream.due(_last_foreground_activity)", body)
        # Un thread per ramo di avvio (app.run e import come WSGI).
        self.assertEqual(self.source.count("threading.Thread(target=persona_dream_loop"), 2)

    def test_lo_startup_non_muore_se_il_sogno_e_storto(self):
        self.assertIn("_safe_initialize_persona_dream", self.functions)
        # def + chiamata nel reload + due rami di avvio.
        self.assertEqual(self.source.count("_safe_initialize_persona_dream()"), 4)

    def test_le_chiavi_del_sogno_sono_nella_sezione_persona(self):
        sezione = self.source[self.source.index('"PERSONA_FILE"'):
                              self.source.index('"Canali esterni"')]
        for chiave in ("PERSONA_DREAM_ENABLED", "PERSONA_DREAM_START_HOUR",
                       "PERSONA_DREAM_END_HOUR", "PERSONA_DREAM_IDLE_S",
                       "PERSONA_DREAM_MAX_TOKENS"):
            with self.subTest(chiave=chiave):
                self.assertIn(f'"{chiave}"', sezione)

    def test_lo_stato_del_sogno_e_visibile_in_persona(self):
        body = ast.unparse(self.functions["persona_status"])
        self.assertIn("_persona_dream.status()", body)

    def test_il_diario_sta_accanto_al_documento_di_identita(self):
        body = ast.unparse(self.functions["_initialize_persona_dream"])
        self.assertIn("os.path.dirname(persona_store.path)", body)
        self.assertIn("_persona_dream_enabled()", body)
        self.assertIn("persona_store.persona.name", body)
        self.assertIn("_persona_dream_int", body)

    def test_il_reload_in_setup_riallinea_il_sogno(self):
        body = ast.unparse(self.functions["_reload_persona"])
        self.assertIn("_reload_persona_dream", body)
        # ...ma non durante una riflessione in corso.
        guardia = ast.unparse(self.functions["_reload_persona_dream"])
        self.assertIn("_persona_dream.running", guardia)


if __name__ == "__main__":
    unittest.main()

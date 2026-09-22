# SPDX-License-Identifier: Apache-2.0
"""Migrazione della memoria legacy: fedele, ripetibile, e senza perdite nascoste.

Il caso vero: 44 voci scritte quando il backend era `legacy`, con due forme diverse
(`webui_prompt` con `content`, e interazioni con `prompt`/`response` piu'
telemetria). Questi test difendono tre cose: che il contratto decida la forma,
che nulla venga contato due volte, e che i campi fuori dall'envelope si VEDANO.
"""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import memory_migrate  # noqa: E402


def scrivi_gz(percorso, contenuto):
    with gzip.open(str(percorso), "wt", encoding="utf-8") as f:
        json.dump(contenuto, f)
    return percorso


VOCE_WEBUI = {"ts": "2026-09-22T10:34:23Z", "type": "webui_prompt",
              "content": "rispondi solo: ok", "model": "qwen3.5:4b",
              "task_id": "885b13f0", "node_id": "ollama-direct",
              "source": "webui", "status": "active", "priority": 2}

VOCE_INTERAZIONE = {"ts": "2026-09-20T21:23:46+00:00", "model": "gemma4:e4b",
                    "node_id": "d7bc05ba", "node_tier": "leaf",
                    "prompt": "user: che ore sono?", "response": "le tre",
                    "source": "webui", "duration_ms": 81049, "tokens_in": 675,
                    "tokens_out": 615, "tokens_per_sec": 7.59}


class CaricaVociTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_una_lista_e_il_formato_normale(self):
        percorso = scrivi_gz(self.cartella / "m.json.gz", [VOCE_WEBUI])
        self.assertEqual(len(memory_migrate.carica_voci(percorso)), 1)

    def test_anche_un_oggetto_con_entries(self):
        percorso = scrivi_gz(self.cartella / "m.json.gz", {"entries": [VOCE_WEBUI,
                                                                     VOCE_INTERAZIONE]})
        self.assertEqual(len(memory_migrate.carica_voci(percorso)), 2)

    def test_un_formato_ignoto_lo_dice(self):
        percorso = scrivi_gz(self.cartella / "m.json.gz", {"altro": 1})
        with self.assertRaises(ValueError):
            memory_migrate.carica_voci(percorso)


class PreparazioneTests(unittest.TestCase):
    def test_il_contratto_decide_la_forma(self):
        preparate, problemi, _ = memory_migrate.prepara_voci([VOCE_WEBUI])
        voce = preparate[0]
        self.assertEqual(problemi, [])
        # `webui_prompt` non e' un tipo del contratto: diventa `memory`.
        self.assertEqual(voce["type"], "memory")
        self.assertEqual(voce["content"], "rispondi solo: ok")
        self.assertEqual(voce["source"], "webui")
        self.assertEqual(voce["ts"], "2026-09-22T10:34:23Z")
        self.assertIn("timestamp", voce)
        self.assertEqual(voce["schema"], "hyperspace.memory.v1")

    def test_prompt_e_risposta_diventano_contenuto_cercabile(self):
        preparate, _, _ = memory_migrate.prepara_voci([VOCE_INTERAZIONE])
        contenuto = preparate[0]["content"]
        self.assertIn("User: user: che ore sono?", contenuto)
        self.assertIn("Assistant: le tre", contenuto)

    def test_i_campi_fuori_dall_envelope_si_contano(self):
        _, _, persi = memory_migrate.prepara_voci([VOCE_INTERAZIONE, VOCE_WEBUI])
        self.assertEqual(persi["tokens_in"], 1)
        self.assertEqual(persi["duration_ms"], 1)
        # `content`, `model`, `ts` sono nell'envelope: non sono "persi".
        self.assertNotIn("content", persi)
        self.assertNotIn("model", persi)

    def test_un_id_e_stabile_fra_due_giri(self):
        """Se l'id cambiasse, la seconda migrazione raddoppierebbe le voci."""
        primo, _, _ = memory_migrate.prepara_voci([VOCE_WEBUI])
        secondo, _, _ = memory_migrate.prepara_voci([VOCE_WEBUI])
        self.assertEqual(primo[0]["id"], secondo[0]["id"])

    def test_una_voce_non_oggetto_non_fa_cadere_tutto(self):
        preparate, problemi, _ = memory_migrate.prepara_voci(["spazzatura", VOCE_WEBUI])
        self.assertEqual(len(preparate), 1)
        self.assertEqual(len(problemi), 1)

    def test_una_voce_senza_contenuto_e_un_problema_di_contratto(self):
        _, problemi, _ = memory_migrate.prepara_voci([{"ts": "2026-01-01T00:00:00Z"}])
        self.assertTrue(problemi)
        # Il messaggio arriva dal contratto (shared/memory_schema.py), che parla
        # inglese: qui si verifica che il problema sia VISTO e riportato.
        self.assertIn("content", " ".join(problemi).lower())


class FintoClient:
    """Finto bridge: registra i blocchi e risponde come il vero /import."""

    def __init__(self, duplicati=0, fallite=0):
        self.blocchi = []
        self.duplicati = duplicati
        self.fallite = fallite

    def import_entries(self, voci):
        self.blocchi.append(list(voci))
        return {"ok": True, "stored": len(voci) - self.duplicati - self.fallite,
                "duplicates": self.duplicati, "failed": self.fallite,
                "errors": [{"index": 0, "error": "prova"}] if self.fallite else []}


class MigrazioneTests(unittest.TestCase):
    def test_le_voci_viaggiano_a_blocchi(self):
        client = FintoClient()
        voci = [{"id": str(n)} for n in range(5)]
        esito = memory_migrate.migra(client, voci, lotto=2)
        self.assertEqual([len(b) for b in client.blocchi], [2, 2, 1])
        self.assertEqual(esito["stored"], 5)

    def test_i_duplicati_del_secondo_giro_si_contano(self):
        client = FintoClient(duplicati=3)
        esito = memory_migrate.migra(client, [{"id": str(n)} for n in range(3)], lotto=3)
        self.assertEqual(esito["stored"], 0)
        self.assertEqual(esito["duplicates"], 3)

    def test_gli_errori_del_bridge_non_spariscono(self):
        client = FintoClient(fallite=1)
        esito = memory_migrate.migra(client, [{"id": "a"}], lotto=1)
        self.assertEqual(esito["failed"], 1)
        self.assertTrue(esito["errors"])

    def test_un_lotto_assurdo_non_blocca_la_migrazione(self):
        client = FintoClient()
        memory_migrate.migra(client, [{"id": "a"}, {"id": "b"}], lotto=0)
        self.assertEqual(len(client.blocchi), 2)


if __name__ == "__main__":
    unittest.main()

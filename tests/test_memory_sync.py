# SPDX-License-Identifier: Apache-2.0
"""Memoria locale-prima: niente si perde quando il peer che ospita Hermes è spento.

Il caso che questi test difendono non è teorico: con `MEMORY_BACKEND=hermes` su due
macchine, Windows spento significava scritture rifiutate (503) e memoria persa **in
silenzio**. Qui si verifica che la scrittura torni ok perché è già su disco, che la
coda si riconsegni quando Hermes torna, e che la riconsegna sia idempotente —
perché è quello che permette di riprovare senza creare doppioni.
"""
from __future__ import annotations

import ast
import gzip
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.hermes_memory import HermesMemoryError  # noqa: E402
from shared.memory_schema import entry_id, normalize_entry  # noqa: E402
from shared.memory_sync import (MemoryMirror, MemoryOutbox, MemorySync,  # noqa: E402
                                from_env)


class FintoHermes:
    """Un Hermes che si può spegnere: il caso per cui questo modulo esiste."""

    def __init__(self, *, acceso: bool = True, esito_import: dict | None = None):
        self.acceso = acceso
        self.esito_import = esito_import or {}
        self.scritte: list = []
        self.lotti: list = []
        self.voci: list = [{"content": "una voce che c'era già"}]
        self.conosciute: set = set()

    def _forse_giu(self):
        if not self.acceso:
            raise HermesMemoryError("finto: il bridge Hermes non risponde")

    def store(self, entry):
        self._forse_giu()
        if entry_id(entry) in self.conosciute:
            return {"ok": True, "stored": False, "duplicate": True, "id": entry_id(entry)}
        self.conosciute.add(entry_id(entry))
        self.scritte.append(entry)
        return {"ok": True, "stored": True, "id": entry_id(entry)}

    def import_entries(self, entries):
        self._forse_giu()
        self.lotti.append(list(entries))
        if self.esito_import:
            return dict(self.esito_import)
        nuovi = doppioni = 0
        for voce in entries:
            if entry_id(voce) in self.conosciute:
                doppioni += 1
            else:
                self.conosciute.add(entry_id(voce))
                nuovi += 1
        return {"ok": True, "stored": nuovi, "duplicates": doppioni, "failed": 0}

    def entries(self, limit: int = 200):
        self._forse_giu()
        return list(self.voci)[:limit]

    def query(self, *args, **kwargs):
        self._forse_giu()
        return list(self.voci)

    def stats(self):
        self._forse_giu()
        return {"backend": "hermes", "entries": len(self.voci), "sessions": 3}


class BaseSync(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.cartella = Path(self._dir.name)
        self.mirror = MemoryMirror(self.cartella / "memory.json.gz",
                                   ttl_days=7, max_entries=200)
        self.outbox = MemoryOutbox(self.cartella / "memory-outbox.jsonl")
        self.log: list = []
        self.hermes = FintoHermes()
        self.sync = MemorySync(self.hermes, mirror=self.mirror, outbox=self.outbox,
                               log=lambda tipo, msg, **kw: self.log.append((tipo, msg, kw)))

    def tearDown(self):
        self._dir.cleanup()

    def voce(self, testo: str, **extra):
        voce = {"content": testo, "source": "test", "type": "memory"}
        voce.update(extra)
        return voce


class ScritturaTests(BaseSync):

    def test_con_hermes_acceso_non_si_accoda_niente(self):
        esito = self.sync.write(self.voce("ciao"))
        self.assertTrue(esito["ok"])
        self.assertTrue(esito["stored"])
        self.assertNotIn("deferred", esito)
        self.assertEqual(self.outbox.count(), 0)
        self.assertEqual(len(self.hermes.scritte), 1)

    def test_il_mirror_si_scrive_sempre(self):
        """Anche quando Hermes risponde: è ciò che rende il file locale utile a debug."""
        self.sync.write(self.voce("guardami"))
        self.assertEqual(self.mirror.count(), 1)
        assert self.mirror.load()[0]["content"] == "guardami"

    def test_con_hermes_spento_la_scrittura_riesce_ed_e_su_disco(self):
        self.hermes.acceso = False
        esito = self.sync.write(self.voce("non si perde"))
        self.assertTrue(esito["ok"], "una scrittura già su disco non è un errore")
        self.assertTrue(esito["deferred"])
        self.assertIn("error", esito)
        self.assertEqual(self.outbox.count(), 1)
        self.assertEqual(self.mirror.count(), 1)

    def test_in_coda_finisce_la_forma_del_contratto(self):
        """Normalizzata adesso = nessuna mappatura da rifare al momento della riconsegna."""
        self.hermes.acceso = False
        self.sync.write(self.voce("con id stabile"))
        voce = self.outbox.pending()[0]
        self.assertEqual(voce["schema"], "hyperspace.memory.v1")
        self.assertTrue(voce["id"])
        self.assertEqual(voce["source"], "test")

    def test_la_stessa_voce_non_si_accoda_due_volte(self):
        """Un peer spento per giorni non deve riempire la coda di ripetizioni."""
        self.hermes.acceso = False
        self.sync.write(self.voce("uguale"))
        self.sync.write(self.voce("uguale"))
        self.assertEqual(self.outbox.count(), 1)

    def test_una_voce_senza_contenuto_si_segnala_ma_si_salva(self):
        """La memoria non si butta per un dettaglio di contratto: si segnala.

        `normalize_entry` ripara da solo i dettagli (tipo sconosciuto, priorità
        fuori scala), quindi perché il contratto si lamenti serve una voce senza
        niente da cercare.
        """
        esito = self.sync.write({"content": "", "type": "memory"})
        self.assertTrue(esito["ok"])
        self.assertTrue(esito["contract_warnings"])
        self.assertIn("contratto", " ".join(riga[1] for riga in self.log))
        self.assertEqual(self.mirror.count(), 1)


    def test_se_anche_la_coda_e_ko_la_scrittura_fallisce(self):
        """L'unico caso in cui una voce si perde: nessun posto dove metterla.

        Con la coda non scrivibile non basta dire `deferred`: chi ha scritto deve
        sapere che quella memoria non esiste da nessuna parte.
        """
        self.hermes.acceso = False
        self.sync.outbox = MemoryOutbox(self.cartella)   # una cartella, non un file
        esito = self.sync.write(self.voce("senza posto dove andare"))
        self.assertFalse(esito["ok"])
        self.assertFalse(esito["queued"])
        self.assertIn("coda non scrivibile", esito["error"])


class RiconsegnaTests(BaseSync):

    def _accoda(self, quante: int):
        self.hermes.acceso = False
        for indice in range(quante):
            self.sync.write(self.voce(f"in attesa {indice}"))

    def test_quando_hermes_torna_la_coda_si_svuota(self):
        self._accoda(2)
        self.assertEqual(self.outbox.count(), 2)
        self.hermes.acceso = True
        esito = self.sync.flush(force=True)
        self.assertTrue(esito["ok"])
        self.assertEqual(esito["sent"], 2)
        self.assertEqual(self.outbox.count(), 0)
        self.assertEqual(len(self.hermes.conosciute), 2)

    def test_ripetere_la_riconsegna_non_crea_doppioni(self):
        """Il caso vero: il processo muore dopo l'import e prima di svuotare la coda."""
        self._accoda(1)
        self.hermes.acceso = True
        self.sync.flush(force=True)
        # La voce è già arrivata, ma la coda viene riscritta come se il processo
        # fosse morto un istante prima: si riprova la stessa voce.
        self.outbox.append(normalize_entry(self.voce("in attesa 0")))
        esito = self.sync.flush(force=True)
        self.assertTrue(esito["ok"])
        self.assertEqual(esito["duplicates"], 1)
        self.assertEqual(esito["stored"], 0)
        self.assertEqual(self.outbox.count(), 0, "i doppioni confermano che è arrivata")

    def test_una_riconsegna_parziale_tiene_tutto_in_coda(self):
        """Riprovare è gratis, perdere una voce no: in dubbio si rispedisce tutto."""
        self._accoda(2)
        self.hermes.acceso = True
        self.hermes.esito_import = {"ok": True, "stored": 1, "duplicates": 0,
                                    "failed": 1, "error": "una voce rifiutata"}
        esito = self.sync.flush(force=True)
        self.assertFalse(esito["ok"])
        self.assertEqual(self.outbox.count(), 2, "la coda resta intera per il prossimo giro")

    def test_la_riconsegna_non_parte_a_ogni_scrittura(self):
        self._accoda(1)
        self.hermes.acceso = True
        self.sync.flush(force=True)
        self._accoda(1)
        esito = self.sync.flush()
        self.assertEqual(esito.get("skipped"), "troppo presto")
        self.assertEqual(self.outbox.count(), 1)

    def test_se_hermes_e_ancora_giu_la_coda_resta(self):
        self._accoda(1)
        esito = self.sync.flush(force=True)
        self.assertFalse(esito["ok"])
        self.assertEqual(esito["pending"], 1)
        self.assertEqual(self.outbox.count(), 1)

    def test_una_riga_rotta_non_costa_le_altre(self):
        """Processo ucciso a metà scrittura: la riga a metà si salta, il resto vale."""
        self._accoda(1)
        with open(self.outbox.path, "a", encoding="utf-8") as file:
            file.write('{"content": "troncata"[cut]\n')
        self.assertEqual(self.outbox.count(), 1)
        self.hermes.acceso = True
        self.assertEqual(self.sync.flush(force=True)["sent"], 1)


class LetturaTests(BaseSync):

    def test_le_voci_arrivano_da_hermes_quando_risponde(self):
        esito = self.sync.read()
        self.assertFalse(esito["degraded"])
        self.assertEqual(esito["source"], "hermes")
        self.assertEqual(len(esito["entries"]), 1)

    def test_con_hermes_spento_si_legge_il_mirror_e_lo_si_dichiara(self):
        self.sync.write(self.voce("ricordami"))
        self.hermes.acceso = False
        esito = self.sync.read()
        self.assertTrue(esito["degraded"])
        self.assertEqual(esito["source"], "mirror")
        self.assertEqual(esito["entries"][0]["content"], "ricordami")
        self.assertTrue(esito["reason"])
        self.assertIn("mirror locale", " ".join(riga[1] for riga in self.log))

    def test_se_il_ripiego_e_spento_la_lettura_solleva(self):
        self.sync = MemorySync(self.hermes, mirror=self.mirror, outbox=self.outbox,
                               read_fallback="fail", log=None)
        self.hermes.acceso = False
        with self.assertRaises(HermesMemoryError):
            self.sync.read()

    def test_la_ricerca_degradata_legge_il_mirror_senza_sollevare(self):
        self.sync.write(self.voce("una torre sulla scogliera"))
        self.hermes.acceso = False
        voci = self.sync.read_local()
        self.assertEqual(len(voci), 1)
        self.assertEqual(voci[0]["content"], "una torre sulla scogliera")


class StatisticheTests(BaseSync):

    def test_le_statistiche_dicono_coda_e_mirror(self):
        self.sync.write(self.voce("viva"))
        self.hermes.acceso = False
        self.sync.write(self.voce("in attesa"))
        esito = self.sync.stats()
        self.assertTrue(esito["degraded"])
        self.assertEqual(esito["outbox"]["pending"], 1)
        self.assertEqual(esito["mirror"]["entries"], 2)
        # La dashboard legge `entries`: il numero locale è meglio di nessun numero.
        self.assertEqual(esito["entries"], 2)

    def test_con_hermes_acceso_le_statistiche_sono_le_sue(self):
        esito = self.sync.stats()
        self.assertFalse(esito["degraded"])
        self.assertEqual(esito["entries"], 1)
        self.assertEqual(esito["outbox"]["pending"], 0)
        self.assertIn("mirror", esito)


class MirrorTests(BaseSync):

    def test_la_lettura_assorbe_in_hermes_nel_mirror(self):
        """Il file locale deve raccontare cosa la macchina ricorda, non solo cosa scrive."""
        self.mirror.append({"content": "solo locale", "type": "note"})
        esito = self.sync.read()
        self.assertFalse(esito["degraded"])
        contenuti = [voce["content"] for voce in self.mirror.load()]
        self.assertIn("una voce che c'era già", contenuti, "assorbita da Hermes")
        self.assertIn("solo locale", contenuti, "la voce locale resta")

    def test_l_assorbimento_non_riscrive_a_ogni_lettura(self):
        contatore = {"n": 0}
        vero_merge = self.mirror.merge

        def merge_spiato(entries):
            contatore["n"] += 1
            return vero_merge(entries)

        self.mirror.merge = merge_spiato
        self.sync.read()
        self.sync.client.voci.append({"content": "novità remota", "type": "note"})
        self.sync.read()
        self.assertEqual(contatore["n"], 1, "una riscrittura per intervallo, non per lettura")

    def test_il_mirror_non_perde_le_voci_in_attesa(self):
        """Unione, non sovrascrittura: assorbire non deve cancellare la coda locale."""
        self.hermes.acceso = False
        self.sync.write(self.voce("in attesa di Hermes"))
        self.hermes.acceso = True
        self.sync.read()
        contenuti = [voce["content"] for voce in self.mirror.load()]
        self.assertIn("in attesa di Hermes", contenuti)

    def test_il_file_resta_leggibile_dal_backend_legacy(self):
        """Il rollback (`MEMORY_BACKEND=legacy`) legge questo file: resta un gzip."""
        self.sync.write(self.voce("per il rollback"))
        with gzip.open(str(self.mirror.path), "rt", encoding="utf-8") as file:
            voci = json.load(file)
        self.assertEqual(len(voci), 1)
        self.assertEqual(voci[0]["content"], "per il rollback")

    def test_l_id_non_cambia_rileggendo_il_mirror(self):
        """La migrazione riconosce le voci già migrate: id stabile, niente doppioni."""
        self.sync.write(self.voce("identità stabile"))
        voce = self.mirror.load()[0]
        self.assertEqual(entry_id(normalize_entry(voce)), entry_id(voce))

    def test_il_tetto_di_voci_vale_anche_per_il_mirror(self):
        self.mirror = MemoryMirror(self.mirror.path, ttl_days=7, max_entries=2)
        self.sync = MemorySync(self.hermes, mirror=self.mirror, outbox=self.outbox)
        adesso = time.time()
        for indice in range(3):
            self.sync.write(self.voce(f"voce {indice}", timestamp=adesso + indice))
        voci = self.mirror.load()
        self.assertEqual(len(voci), 2)
        self.assertEqual(voci[0]["content"], "voce 2", "resta la più recente")

    def test_un_file_rotto_non_fa_cadere_la_lettura(self):
        self.mirror.path.parent.mkdir(parents=True, exist_ok=True)
        self.mirror.path.write_bytes(b"non sono un gzip")
        self.assertEqual(self.mirror.load(), [])
        self.assertEqual(self.mirror.count(), 0)


class CostruzioneTests(unittest.TestCase):
    """La coda deve stare dentro un volume: fuori, sparisce al primo rebuild."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.cartella = Path(self._dir.name)
        self.log: list = []

    def tearDown(self):
        self._dir.cleanup()

    def _logga(self, tipo, messaggio, **extra):
        self.log.append((tipo, messaggio, extra))

    def test_avvisa_se_il_file_di_memoria_non_e_dichiarato(self):
        with unittest.mock.patch.dict(os.environ, {"MEMORY_FILE": ""}, clear=False):
            sync = from_env(FintoHermes(), log=self._logga,
                            memory_file=str(self.cartella / "memory.json.gz"))
        self.assertTrue(sync.outbox, "la coda funziona anche senza la variabile")
        self.assertIn("MEMORY_FILE", " ".join(riga[1] for riga in self.log))

    def test_non_avvisa_se_il_file_e_dentro_un_volume(self):
        with unittest.mock.patch.dict(
                os.environ, {"MEMORY_FILE": "/app/memory/memory.json.gz"}, clear=False):
            from_env(FintoHermes(), log=self._logga,
                     memory_file=str(self.cartella / "memory.json.gz"))
        self.assertEqual([riga for riga in self.log if "MEMORY_FILE" in riga[1]], [])

    def test_la_coda_sta_accanto_al_file_di_memoria(self):
        with unittest.mock.patch.dict(os.environ, {"MEMORY_FILE": ""}, clear=False):
            sync = from_env(FintoHermes(), log=None,
                            memory_file=str(self.cartella / "memory.json.gz"))
        self.assertEqual(Path(sync.outbox.path).parent, self.cartella)
        self.assertEqual(Path(sync.mirror.path), self.cartella / "memory.json.gz")


class CablaggioControlPlaneTests(unittest.TestCase):
    """Il control-plane usa la memoria locale-prima: se qualcuno lo stacca, cade qui."""

    @classmethod
    def setUpClass(cls):
        sorgente = (ROOT / "control-plane" / "main.py").read_text(encoding="utf-8")
        albero = ast.parse(sorgente)
        cls.funzioni = {n.name: n for n in albero.body if isinstance(n, ast.FunctionDef)}

    def _corpo(self, nome: str) -> str:
        return ast.unparse(self.funzioni[nome])

    def test_le_scritture_passano_dalla_coda(self):
        corpo = self._corpo("_memory_append")
        self.assertIn("memory_sync.write", corpo)

    def test_le_letture_degradano_sul_mirror(self):
        corpo = self._corpo("_load_memory")
        self.assertIn("memory_sync.read", corpo)

    def test_le_statistiche_ci_sono_anche_con_hermes_giu(self):
        """Niente 503 sulla rotta delle statistiche: nasconderebbe la coda."""
        corpo = self._corpo("memory_stats")
        self.assertIn("memory_sync.stats", corpo)
        self.assertNotIn("HermesMemoryError", corpo)

    def test_la_scrittura_fallisce_solo_se_non_si_salva_da_nessuna_parte(self):
        corpo = self._corpo("push_memory")
        self.assertIn("esito.get", corpo)
        self.assertIn("is False", corpo)
        self.assertIn("503", corpo)

    def test_esiste_la_riconsegna_a_mano(self):
        self.assertIn("sync_memory", self.funzioni)
        corpo = self._corpo("sync_memory")
        self.assertIn("flush(force=True)", corpo)

    def test_il_salvataggio_in_setup_ricostruisce_la_memoria(self):
        self.assertIn("_reload_memory_sync", self.funzioni)
        corpo = self._corpo("set_config_env")
        self.assertIn("_MEMORY_SYNC_ENV_KEYS", corpo)
        self.assertIn("_reload_memory_sync", corpo)

    def test_l_agente_ricerca_in_locale_quando_hermes_tace(self):
        corpo = self._corpo("_omega_query")
        self.assertIn("memory_sync.read_local", corpo)


if __name__ == "__main__":
    unittest.main()


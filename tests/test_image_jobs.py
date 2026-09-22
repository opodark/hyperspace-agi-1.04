# SPDX-License-Identifier: Apache-2.0
"""Job immagine: la coda del control-plane e il grafo che ha funzionato davvero.

Il grafo non e' una ricetta inventata: e' la run 19e69776 di questa macchina, con
le due scelte che non vanno "semplificate" (text encoder su CPU, istruzioni
negative dentro il prompt). Questi test difendono quelle scelte: se qualcuno le
toglie, l'immagine smette di uscire o la VRAM non basta piu'.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import (LIMITE_LATO, ImmagineQueue,  # noqa: E402
                               immagini_da_history, nuovo_job, workflow)


class OrologioFinto:
    def __init__(self):
        self.adesso = 1000.0

    def __call__(self):
        return self.adesso

    def avanza(self, secondi):
        self.adesso += secondi


class JobTests(unittest.TestCase):
    def test_un_job_con_prompt_vuoto_non_esiste(self):
        for vuoto in ("", "   ", None):
            with self.subTest(vuoto=vuoto):
                with self.assertRaises(ValueError):
                    nuovo_job(vuoto)

    def test_i_numeri_si_limitano_invece_di_essere_rifiutati(self):
        job = nuovo_job("un gatto", larghezza=9999, altezza=10, passi=500)
        self.assertEqual(job["larghezza"], LIMITE_LATO)
        self.assertEqual(job["altezza"], 64)
        self.assertEqual(job["passi"], 60)

    def test_il_prompt_e_normalizzato_e_identificato(self):
        job = nuovo_job("  un   gatto\nsul tetto  ")
        self.assertEqual(job["prompt"], "un gatto sul tetto")
        self.assertEqual(len(job["id"]), 12)
        self.assertEqual(job["stato"], "pending")


class CodaTests(unittest.TestCase):
    def setUp(self):
        self.orologio = OrologioFinto()
        self.coda = ImmagineQueue(clock=self.orologio, max_jobs=2, ttl_s=60.0,
                                  claim_ttl_s=30.0)

    def _job(self, prompt="un gatto"):
        return self.coda.accoda(nuovo_job(prompt, adesso=self.orologio()))

    def test_il_job_piu_vecchio_esce_per_primo(self):
        primo = self._job("primo")
        self.orologio.avanza(1)
        self._job("secondo")
        self.assertEqual(self.coda.prossimo()["id"], primo["id"])

    def test_un_job_preso_non_si_ripete(self):
        self._job()
        preso = self.coda.prossimo()
        self.assertEqual(preso["stato"], "running")
        self.assertIsNone(self.coda.prossimo(), "un job in esecuzione non si riprende")

    def test_un_claim_morto_torna_disponibile(self):
        """Un ponte che muore a meta' non deve bloccare il lavoro per sempre."""
        self._job()
        self.coda.prossimo()
        self.orologio.avanza(31)
        ripreso = self.coda.prossimo()
        self.assertIsNotNone(ripreso)
        self.assertEqual(ripreso["stato"], "running")

    def test_la_coda_ha_un_tetto(self):
        self._job("primo")
        self._job("secondo")
        with self.assertRaises(RuntimeError):
            self._job("terzo")

    def test_un_job_scaduto_sparisce(self):
        self._job()
        self.orologio.avanza(61)
        self.assertIsNone(self.coda.prossimo())
        self.assertEqual(self.coda.stato()["in_coda"], 0)

    def test_concludere_scrive_l_esito_e_un_id_ignoto_non_e_un_errore(self):
        job = self._job()
        chiuso = self.coda.concludi(job["id"], True, file="HyperSpace/x.png",
                                   durata_ms=1234)
        self.assertEqual(chiuso["stato"], "done")
        self.assertEqual(chiuso["esito"]["file"], "HyperSpace/x.png")
        self.assertIsNone(self.coda.concludi("non-esiste", True))

    def test_lo_stato_racconta_cosa_sta_succedendo(self):
        job = self._job()
        self.coda.prossimo()
        self.assertEqual(self.coda.stato()["in_esecuzione"], 1)
        self.coda.concludi(job["id"], False, errore="scheda piena")
        self.assertEqual(self.coda.stato()["ultimi"][0]["esito"]["errore"], "scheda piena")


class ConsegnaTests(unittest.TestCase):
    """Outbox: un'immagine pronta per una chat, consegnata una volta sola."""

    def setUp(self):
        self.orologio = OrologioFinto()
        self.coda = ImmagineQueue(clock=self.orologio)

    def _pronta(self, canale="telegram", destinazione="123"):
        job = self.coda.accoda(nuovo_job("un gatto", canale=canale,
                                         destinazione=destinazione,
                                         adesso=self.orologio()))
        self.coda.prossimo()
        return self.coda.concludi(job["id"], True, file="C:/out/x.png")

    def test_una_immagine_pronta_con_destinazione_si_consegna(self):
        self._pronta()
        consegne = self.coda.da_consegnare("telegram")
        self.assertEqual(len(consegne), 1)
        self.assertEqual(consegne[0]["file"], "C:/out/x.png")
        self.assertEqual(consegne[0]["destinazione"], "123")

    def test_senza_destinazione_non_c_e_niente_da_consegnare(self):
        self._pronta(destinazione="")
        self.assertEqual(self.coda.da_consegnare("telegram"), [])

    def test_la_consegna_non_si_ripete(self):
        self._pronta()
        identificativo = self.coda.da_consegnare("telegram")[0]["id"]
        self.assertTrue(self.coda.consegnato(identificativo))
        self.assertEqual(self.coda.da_consegnare("telegram"), [])

    def test_un_altro_canale_non_ruba_la_consegna(self):
        self._pronta(canale="telegram")
        self.assertEqual(self.coda.da_consegnare("cam4"), [])

    def test_un_job_non_concluso_non_si_consegna(self):
        job = self.coda.accoda(nuovo_job("x", canale="telegram", destinazione="1"))
        self.assertEqual(self.coda.da_consegnare("telegram"), [])
        self.assertFalse(self.coda.consegnato(job["id"]))

    def test_lo_stato_dice_quante_restano_da_consegnare(self):
        self._pronta()
        self.assertEqual(self.coda.stato()["da_consegnare"], 1)
        self.coda.consegnato(self.coda.da_consegnare("telegram")[0]["id"])
        self.assertEqual(self.coda.stato()["da_consegnare"], 0)


class GrafoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = nuovo_job("un lupo bianco", larghezza=1024, altezza=1024,
                            passi=30, seed=22092026)

    def test_il_prompt_e_il_seed_finiscono_nel_grafo(self):
        grafo = workflow(self.job)
        self.assertEqual(grafo["452"]["inputs"]["prompt"], "un lupo bianco")
        self.assertEqual(grafo["458"]["inputs"]["seed"], 22092026)
        self.assertEqual(grafo["458"]["inputs"]["steps"], 30)
        self.assertEqual(grafo["456"]["inputs"]["width"], 1024)

    def test_il_text_encoder_gira_sulla_cpu(self):
        """Con 8 GB di VRAM e' quello che lascia spazio al diffusion."""
        grafo = workflow(self.job)
        self.assertEqual(grafo["453"]["inputs"]["device"], "cpu")
        self.assertEqual(grafo["453"]["inputs"]["type"], "qwen_image")

    def test_la_resolution_segue_il_lato_del_latente(self):
        grafo = workflow(nuovo_job("x", larghezza=768, altezza=768))
        self.assertEqual(grafo["452"]["inputs"]["resolution"], 768)

    def test_il_negativo_e_vuoto_e_le_istruzioni_stanno_nel_prompt(self):
        """Qwen-Image 2.1 segue le istruzioni: e' cosi' che si scrivono i "no"."""
        grafo = workflow(self.job)
        self.assertEqual(grafo["452"]["inputs"]["negative_prompt"], "")

    def test_i_valori_del_modello_si_possono_sovrascrivere(self):
        grafo = workflow(self.job, modello={"unet": "altro.gguf"}, prefisso="Prova")
        self.assertEqual(grafo["451"]["inputs"]["unet_name"], "altro.gguf")
        self.assertEqual(grafo["470"]["inputs"]["filename_prefix"], "Prova")

    def test_il_job_puo_scegliere_il_proprio_modello(self):
        job = nuovo_job("x", modello="qwen-image-2.1-Q4.gguf")
        self.assertEqual(workflow(job)["451"]["inputs"]["unet_name"],
                         "qwen-image-2.1-Q4.gguf")

    def test_il_grafo_e_collegato_dal_caricatore_al_salvataggio(self):
        """Un grafo con un filo staccato si scopre solo quando non esce niente."""
        grafo = workflow(self.job)
        self.assertEqual(grafo["458"]["inputs"]["model"], ["451", 0])
        self.assertEqual(grafo["458"]["inputs"]["positive"], ["452", 0])
        self.assertEqual(grafo["458"]["inputs"]["negative"], ["452", 1])
        self.assertEqual(grafo["457"]["inputs"]["samples"], ["458", 0])
        self.assertEqual(grafo["470"]["inputs"]["images"], ["457", 0])


class HistoryTests(unittest.TestCase):
    def test_i_file_si_leggono_anche_dalle_sottocartelle(self):
        run = {"outputs": {"470": {"images": [
            {"filename": "a.png", "subfolder": "HyperSpace", "type": "output"},
            {"filename": "b.png", "subfolder": "", "type": "output"}]}}}
        self.assertEqual(immagini_da_history(run), ["HyperSpace/a.png", "b.png"])

    def test_una_run_senza_immagini_non_e_un_errore(self):
        self.assertEqual(immagini_da_history({}), [])
        self.assertEqual(immagini_da_history({"outputs": {"9": {}}}), [])


if __name__ == "__main__":
    unittest.main()

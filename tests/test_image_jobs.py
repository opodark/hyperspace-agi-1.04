# SPDX-License-Identifier: Apache-2.0
"""Job immagine: la coda del control-plane e il grafo che ha funzionato davvero.

Il grafo non e' una ricetta inventata: e' la run 19e69776 di questa macchina, con
le due scelte che non vanno "semplificate" (text encoder su CPU, istruzioni
negative dentro il prompt). Questi test difendono quelle scelte: se qualcuno le
toglie, l'immagine smette di uscire o la VRAM non basta piu'.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import (DEFAULT_CLAIM_TTL_S, DEFAULT_JOB_TTL_S,  # noqa: E402
                               FAMIGLIA_SD15, FAMIGLIE, FAMIGLIE_CHECKPOINT,
                               FIX_UPSCALER, LIMITE_LATO, MODELLO_SD15, ImmagineQueue,
                               famiglie_capaci, immagini_da_history, nuovo_job,
                               scegli_pose_preset, usa_checkpoint, workflow)


class OrologioFinto:
    def __init__(self):
        self.adesso = 1000.0

    def __call__(self):
        return self.adesso

    def avanza(self, secondi):
        self.adesso += secondi


class TempiTests(unittest.TestCase):
    """I due tempi della coda devono stare in un ordine preciso, non a caso.

    Il 2026-09-22: un ritratto ha superato i 600s di claim mentre ComfyUI campionava
    ancora; il job è tornato "pending" (quindi rieseguibile: due generazioni per lo
    stesso lavoro su una scheda da 8 GB) e il risultato sarebbe potuto arrivare dopo
    la potatura, cioè perso. Questi due test legano i tempi a ciò che il ponte fa.
    """

    def test_il_claim_dura_piu_dell_esecuzione_massima_del_ponte(self):
        from integrations.comfyui.comfy_bridge import esegui_job
        import inspect
        firma = inspect.signature(esegui_job)
        timeout_ponte = float(firma.parameters["timeout_s"].default)
        self.assertGreater(
            DEFAULT_CLAIM_TTL_S, timeout_ponte,
            "un claim più corto del timeout del ponte fa rieseguire un job vivo")

    def test_il_tetto_del_ponte_copre_la_ricetta_dei_demo(self):
        """Il tetto si misura sulla variante più lenta, non sul caso medio.

        Il 2026-09-30, con la ricetta dei demo (512×768 + `fix: 2` + 30 passi),
        `bridge_00085_` è uscita in 902,0 s di job: 2 s **dopo** un tetto da
        900 s. Il ponte ha riferito «nessuna immagine entro 900s» mentre ComfyUI
        stava finendo di scrivere il JPEG, e la variante è risultata fallita pur
        essendo buona. Con 40 passi la stessa ricetta renderebbe in ~1190 s:
        il tetto deve stare sopra la misura con margine, non addosso.
        """
        from integrations.comfyui.comfy_bridge import esegui_job
        import inspect
        timeout_ponte = float(inspect.signature(esegui_job).parameters["timeout_s"].default)
        self.assertGreaterEqual(
            timeout_ponte, 1200.0,
            "900 s è la durata della variante, non il tetto: serve margine")

    def test_il_job_sopravvive_al_suo_claim(self):
        self.assertGreater(DEFAULT_JOB_TTL_S, DEFAULT_CLAIM_TTL_S,
                           "se il job scade prima del claim, il risultato si perde")


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

    def test_un_risultato_ripetuto_non_cambia_il_job(self):
        job = self._job()
        self.coda.concludi(job["id"], True, file="HyperSpace/x.png")
        repeated = self.coda.concludi(job["id"], False, errore="timeout")
        self.assertTrue(repeated["_already_concluded"])
        self.assertEqual(repeated["stato"], "done")
        self.assertEqual(repeated["esito"]["file"], "HyperSpace/x.png")

    def test_lo_stato_racconta_cosa_sta_succedendo(self):
        job = self._job()
        self.coda.prossimo()
        self.assertEqual(self.coda.stato()["in_esecuzione"], 1)
        self.coda.concludi(job["id"], False, errore="scheda piena")
        self.assertEqual(self.coda.stato()["ultimi"][0]["esito"]["errore"], "scheda piena")

    def test_riprende_un_job_pending_dopo_il_riavvio(self):
        with tempfile.TemporaryDirectory() as directory:
            percorso = str(Path(directory) / "image-jobs.json")
            creatrice = ImmagineQueue(clock=self.orologio, state_path=percorso)
            job = creatrice.accoda(nuovo_job("un faro", adesso=self.orologio()))
            ripresa = ImmagineQueue(clock=self.orologio, state_path=percorso)
            self.assertEqual(ripresa.prossimo()["id"], job["id"])

    def test_un_job_running_torna_pending_dopo_scadenza_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            percorso = str(Path(directory) / "image-jobs.json")
            creatrice = ImmagineQueue(clock=self.orologio, state_path=percorso)
            job = creatrice.accoda(nuovo_job("un faro", adesso=self.orologio()))
            creatrice.prossimo()
            ripresa = ImmagineQueue(clock=self.orologio, state_path=percorso)
            self.assertIsNone(ripresa.prossimo())
            self.orologio.avanza(DEFAULT_CLAIM_TTL_S + 1)
            self.assertEqual(ripresa.prossimo()["id"], job["id"])

    def test_un_job_rinviato_resta_disponibile(self):
        job = self._job()
        self.coda.prossimo()
        self.assertTrue(self.coda.rinvia(job["id"]))
        self.assertEqual(self.coda.prossimo()["id"], job["id"])


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

    def test_il_salvataggio_preferito_e_jpeg_con_fallback_png(self):
        jpeg = workflow(self.job)["470"]
        png = workflow(self.job, jpeg=False)["470"]
        self.assertEqual(jpeg["class_type"], "HyperSpaceSaveJPEG")
        self.assertEqual(jpeg["inputs"]["quality"], 92)
        self.assertEqual(png["class_type"], "SaveImage")


class FamigliaTests(unittest.TestCase):
    def test_una_famiglia_sconosciuta_cade_sul_default(self):
        job = nuovo_job("x", famiglia="non-esiste")
        self.assertEqual(job["famiglia"], "qwen-image-2.1")

    def test_il_job_dichiara_la_sua_famiglia(self):
        job = nuovo_job("x", famiglia="sdxl-turbo")
        self.assertEqual(job["famiglia"], "sdxl-turbo")

    def test_la_famiglia_sd15_e_una_famiglia_vera(self):
        """SD 1.5 (ChickMixFlat) e' la famiglia del volto di Anna: se sparisse
        dall'elenco, `nuovo_job` la farebbe cadere su Qwen-Image — in silenzio, e
        il ritratto uscirebbe da un altro modello.
        """
        job = nuovo_job("una ragazza illustrata", famiglia=FAMIGLIA_SD15)
        self.assertIn(FAMIGLIA_SD15, FAMIGLIE)
        self.assertEqual(job["famiglia"], FAMIGLIA_SD15)
        self.assertEqual(job["modello_effettivo"], "chickmixflat_v10.ckpt")

    def test_usa_checkpoint_distingue_i_checkpoint_da_qwen(self):
        """Il controllo era `famiglia == FAMIGLIA_SDXL` in cinque punti, e in tutti
        e cinque significava \"questo e' un checkpoint, non Qwen\". Con una seconda
        famiglia di checkpoint quelle condizioni sarebbero diventate cinque bug.
        """
        self.assertTrue(usa_checkpoint("sdxl-turbo"))
        self.assertTrue(usa_checkpoint(FAMIGLIA_SD15))
        self.assertFalse(usa_checkpoint("qwen-image-2.1"))
        self.assertFalse(usa_checkpoint(""))
        self.assertEqual(FAMIGLIE_CHECKPOINT, ("sdxl-turbo", FAMIGLIA_SD15))

    def test_il_nome_del_modello_non_puo_essere_un_percorso(self):
        """Da quando la vetrina di Anna puo' dichiarare il modello, quel nome
        arriva da un DOCUMENTO e finisce in `CheckpointLoaderSimple.ckpt_name`:
        vale la stessa regola di pose_image e lora_name.
        """
        for brutto in ("/etc/passwd", "../x.ckpt", "https://example.test/x.ckpt"):
            with self.subTest(modello=brutto):
                with self.assertRaises(ValueError):
                    nuovo_job("x", modello=brutto)

    def test_le_famiglie_di_un_ponte_si_possono_elencare(self):
        """Un ponte solo, due famiglie: e' il caso del Mac, che serve Pony per gli
        sketch e ChickMixFlat per il ritratto di Anna.
        """
        self.assertEqual(famiglie_capaci("sdxl-turbo,sd15"), ("sdxl-turbo", "sd15"))
        self.assertEqual(famiglie_capaci(" sd15 "), ("sd15",))
        self.assertEqual(famiglie_capaci(("sd15",)), ("sd15",))
        self.assertEqual(famiglie_capaci(""), ())
        self.assertEqual(famiglie_capaci(None), ())


class GrafoSdxlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = nuovo_job("un bozzetto", famiglia="sdxl-turbo",
                            negativo="no photo", larghezza=512, altezza=512,
                            passi=2, seed=7)

    def test_sceglie_il_grafo_sdxl(self):
        grafo = workflow(self.job)
        self.assertEqual(grafo["451"]["class_type"], "CheckpointLoaderSimple")

    def test_parametri_da_cyberrealistic_pony(self):
        grafo = workflow(self.job)
        self.assertEqual(grafo["458"]["inputs"]["cfg"], 5.0)
        self.assertEqual(grafo["458"]["inputs"]["steps"], 2)
        self.assertEqual(grafo["459"]["class_type"], "CLIPSetLastLayer")
        self.assertEqual(grafo["459"]["inputs"]["stop_at_clip_layer"], -2)
        self.assertEqual(grafo["452"]["inputs"]["clip"], ["459", 0])
        self.assertEqual(grafo["453"]["inputs"]["clip"], ["459", 0])

    def test_il_negativo_arriva_al_clip_negativo(self):
        grafo = workflow(self.job)
        self.assertEqual(grafo["453"]["inputs"]["text"], "no photo")

    def test_il_default_resta_qwen(self):
        grafo = workflow(nuovo_job("x"))
        self.assertEqual(grafo["451"]["class_type"], "UnetLoaderGGUF")

    def test_openpose_e_opzionale_e_collegato_al_sampler(self):
        job = nuovo_job("persona in posa", famiglia="sdxl-turbo",
                        pose_image="pose/riferimento.jpg", pose_strength=0.72)
        grafo = workflow(job)
        self.assertEqual(grafo["460"]["class_type"], "LoadImage")
        self.assertEqual(grafo["460"]["inputs"]["image"], "pose/riferimento.jpg")
        self.assertEqual(grafo["461"]["class_type"], "DWPreprocessor")
        self.assertEqual(grafo["462"]["class_type"], "ControlNetLoader")
        self.assertEqual(grafo["464"]["class_type"], "ImageScale")
        self.assertEqual(grafo["464"]["inputs"]["width"], 768)
        self.assertEqual(grafo["463"]["inputs"]["image"], ["464", 0])
        self.assertEqual(grafo["463"]["inputs"]["strength"], 0.72)
        self.assertEqual(grafo["458"]["inputs"]["positive"], ["463", 0])
        self.assertEqual(grafo["458"]["inputs"]["negative"], ["463", 1])

    def test_senza_pose_il_grafo_resta_quello_normale(self):
        grafo = workflow(self.job)
        self.assertNotIn("460", grafo)
        self.assertEqual(grafo["458"]["inputs"]["positive"], ["452", 0])

    def test_pose_image_non_puo_uscire_dalla_cartella_input(self):
        for path in ("../segreto.jpg", "/tmp/x.png", "https://example.test/x.png"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    nuovo_job("x", famiglia="sdxl-turbo", pose_image=path)

    def test_lora_opzionale_si_collega_a_modello_e_clip(self):
        job = nuovo_job("ritratto", famiglia="sdxl-turbo",
                        lora_name="HyperSpace/anna.safetensors", lora_strength=.7)
        grafo = workflow(job)
        self.assertEqual(grafo["455"]["class_type"], "LoraLoader")
        self.assertEqual(grafo["455"]["inputs"]["lora_name"],
                         "HyperSpace/anna.safetensors")
        self.assertEqual(grafo["455"]["inputs"]["strength_model"], .7)
        self.assertEqual(grafo["459"]["inputs"]["clip"], ["455", 1])
        self.assertEqual(grafo["458"]["inputs"]["model"], ["455", 0])

    def test_senza_lora_il_grafo_resta_invariato(self):
        grafo = workflow(self.job)
        self.assertNotIn("455", grafo)
        self.assertEqual(grafo["459"]["inputs"]["clip"], ["451", 1])
        self.assertEqual(grafo["458"]["inputs"]["model"], ["451", 0])

    def test_lora_name_non_puo_uscire_dalla_cartella_modelli(self):
        with self.assertRaises(ValueError):
            nuovo_job("x", famiglia="sdxl-turbo", lora_name="../x.safetensors")

    def test_seleziona_automaticamente_solo_pose_riconoscibili(self):
        casi = {"una donna seduta su una sedia": "seated",
                "adult dancer on a stage": "dancing",
                "ritratto in piedi": "standing",
                "un semplice ritratto": ""}
        for prompt, atteso in casi.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(scegli_pose_preset(prompt), atteso)

    def test_non_applica_un_corpo_singolo_a_due_persone(self):
        self.assertEqual(scegli_pose_preset("a couple sitting on a sofa"), "")

    def test_preset_automatico_entra_nel_grafo_senza_foto(self):
        job = nuovo_job("adult woman sitting on a chair", famiglia="sdxl-turbo")
        self.assertEqual(job["pose_preset"], "seated")
        grafo = workflow(job)
        self.assertEqual(grafo["464"]["class_type"], "HyperSpacePosePreset")
        self.assertEqual(grafo["464"]["inputs"]["preset"], "seated")
        self.assertNotIn("460", grafo)


class AffinitaTests(unittest.TestCase):
    def setUp(self):
        self.orologio = OrologioFinto()
        self.coda = ImmagineQueue(clock=self.orologio, max_jobs=4, ttl_s=60.0,
                                  claim_ttl_s=30.0)

    def test_un_ponte_prendi_solo_i_job_della_sua_famiglia(self):
        qwen = self.coda.accoda(nuovo_job("foto", adesso=self.orologio()))
        self.orologio.avanza(1)
        sdxl = self.coda.accoda(nuovo_job("sketch", famiglia="sdxl-turbo",
                                          adesso=self.orologio()))
        self.assertEqual(self.coda.prossimo(capace_di="sdxl-turbo")["id"], sdxl["id"])
        self.assertEqual(self.coda.prossimo(capace_di="qwen-image-2.1")["id"], qwen["id"])

    def test_senza_job_della_famiglia_non_esce_niente(self):
        self.coda.accoda(nuovo_job("foto", adesso=self.orologio()))
        self.assertIsNone(self.coda.prossimo(capace_di="sdxl-turbo"))

    def test_senza_famiglia_esce_il_piu_vecchio(self):
        primo = self.coda.accoda(nuovo_job("a", adesso=self.orologio()))
        self.orologio.avanza(1)
        self.coda.accoda(nuovo_job("b", famiglia="sdxl-turbo",
                                   adesso=self.orologio()))
        self.assertEqual(self.coda.prossimo()["id"], primo["id"])


class HistoryTests(unittest.TestCase):
    def test_i_file_si_leggono_anche_dalle_sottocartelle(self):
        run = {"outputs": {"470": {"images": [
            {"filename": "a.jpg", "subfolder": "HyperSpace", "type": "output"},
            {"filename": "b.png", "subfolder": "", "type": "output"}]}}}
        self.assertEqual(immagini_da_history(run), ["HyperSpace/a.jpg", "b.png"])

    def test_una_run_senza_immagini_non_e_un_errore(self):
        self.assertEqual(immagini_da_history({}), [])
        self.assertEqual(immagini_da_history({"outputs": {"9": {}}}), [])


class GrafoSd15Tests(unittest.TestCase):
    """Il volto virtuale di Anna: ChickMixFlat (SD 1.5), non il Pony.

    Perche' una classe a parte: SD 1.5 e SDXL condividono il GRAFO (checkpoint
    unico, un CLIP per lato) ma non i numeri. Servito dalla ricetta del Pony, un
    job SD 1.5 non fallisce: esce un'immagine sbagliata — a 1024 px SD 1.5 ripete
    l'anatomia, a CFG 5 resta tiepido. E' il tipo di errore che si scopre solo
    guardando il ritratto, quindi si difende qui.
    """

    @classmethod
    def setUpClass(cls):
        cls.job = nuovo_job("una ragazza illustrata", famiglia=FAMIGLIA_SD15,
                            negativo="no photo", larghezza=768, altezza=768,
                            passi=30, seed=20260930)

    def test_sceglie_il_checkpoint_di_anna(self):
        grafo = workflow(self.job)
        self.assertEqual(grafo["451"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(grafo["451"]["inputs"]["ckpt_name"], MODELLO_SD15["ckpt"])

    def test_i_numeri_sono_quelli_di_sd15_non_del_pony(self):
        grafo = workflow(self.job)
        self.assertEqual(grafo["458"]["inputs"]["cfg"], 7.0)
        self.assertEqual(grafo["458"]["inputs"]["sampler_name"], "dpmpp_sde")
        self.assertEqual(grafo["458"]["inputs"]["scheduler"], "karras")
        self.assertEqual(grafo["459"]["class_type"], "CLIPSetLastLayer")
        self.assertEqual(grafo["459"]["inputs"]["stop_at_clip_layer"], -2)
        self.assertEqual(grafo["456"]["inputs"]["width"], 768)

    def test_il_negativo_arriva_al_clip_negativo(self):
        self.assertEqual(workflow(self.job)["453"]["inputs"]["text"], "no photo")

    def test_il_job_puo_imporre_un_altro_checkpoint(self):
        job = nuovo_job("x", famiglia=FAMIGLIA_SD15, modello="mio.safetensors")
        self.assertEqual(workflow(job)["451"]["inputs"]["ckpt_name"], "mio.safetensors")

    def test_la_famiglia_nuova_non_sposta_i_numeri_del_pony(self):
        pony = workflow(nuovo_job("x", famiglia="sdxl-turbo", larghezza=1024, altezza=1024))
        self.assertEqual(pony["451"]["inputs"]["ckpt_name"],
                         "CyberRealisticPony_V18.0_F16.safetensors")
        self.assertEqual(pony["458"]["inputs"]["cfg"], 5.0)

    def test_la_posa_non_si_deduce_da_sola(self):
        """Il ControlNet openpose e' per famiglia di modello: quello di SDXL non
        capisce i latenti di SD 1.5. Quindi per sd15 non si sceglie una posa da
        soli, e una posa dichiarata si FERMA invece di essere ignorata in silenzio
        (un job che riesce con l'immagine sbagliata e' peggio di un job fallito).
        """
        automatico = nuovo_job("a woman sitting on a chair", famiglia=FAMIGLIA_SD15)
        self.assertEqual(automatico["pose_preset"], "")
        dichiarato = nuovo_job("x", famiglia=FAMIGLIA_SD15, pose_preset="seated")
        with self.assertRaises(ValueError):
            workflow(dichiarato)

    def test_la_posa_del_pony_funziona_ancora(self):
        job = nuovo_job("x", famiglia="sdxl-turbo", pose_preset="seated",
                        larghezza=512, altezza=512)
        self.assertEqual(workflow(job)["464"]["class_type"], "HyperSpacePosePreset")


class GrafoFixTests(unittest.TestCase):
    """Il fix R-ESRGAN, spento per default.

    Misurato il 2026-09-30 su questa macchina, stesso seed: 172s il primo
    passaggio a 512×768 e 1510s il vecchio fix a 1024×1536 (memoria sotto pressione).
    Il fix leggero decodifica il primo passaggio e lo ingrandisce con R-ESRGAN,
    senza ricampionarlo e senza ingrandire il latente: quello, misurato sulla serie a 2×
    (`bridge_00081_`–`_083_`), esce con aloni cromatici su ogni contorno, bianchi
    bruciati e la composizione rifatta. Vedi docs/comfyui.md.
    """

    def setUp(self):
        self.base = dict(prompt="una ragazza illustrata", famiglia=FAMIGLIA_SD15,
                         negativo="no photo", larghezza=512, altezza=768,
                         passi=30, seed=20260930)

    def job(self, **extra):
        return nuovo_job(**{**self.base, **extra})

    def test_senza_fix_il_grafo_e_quello_di_prima(self):
        """Il default non cambia niente: chi non chiede il fix non lo riceve."""
        grafo = workflow(self.job())
        self.assertEqual(grafo["470"]["inputs"]["images"], ["457", 0])
        for nodo in ("471", "472", "473", "474", "475", "476", "477"):
            self.assertNotIn(nodo, grafo)

    def test_il_fix_ingrandisce_con_il_modello_senza_ricampionare(self):
        grafo = workflow(self.job(fix=2))
        self.assertNotIn("471", grafo,
                         "l'ingrandimento del LATENTE (`LatentUpscale`) è quello "
                         "che ha prodotto gli aloni cromatici: non si usa più")
        self.assertEqual(grafo["474"]["class_type"], "UpscaleModelLoader")
        self.assertEqual(grafo["474"]["inputs"]["model_name"], FIX_UPSCALER)
        self.assertEqual(grafo["475"]["class_type"], "ImageUpscaleWithModel")
        self.assertEqual(grafo["475"]["inputs"]["image"], ["457", 0],
                         "si ingrandisce l'IMMAGINE del primo passaggio")
        self.assertEqual(grafo["476"]["class_type"], "ImageScale")
        misure = (grafo["476"]["inputs"]["width"], grafo["476"]["inputs"]["height"])
        self.assertEqual(misure, (1024, 1536),
                         "il 4× dell'ingranditore non è la misura del fix")
        for nodo in ("472", "473", "477"):
            self.assertNotIn(nodo, grafo,
                             "nessun secondo sampling ad alta risoluzione nel fix leggero")
        self.assertEqual(grafo["470"]["inputs"]["images"], ["476", 0],
                         "si salva l'upscale neurale, non il primo passaggio")

    def test_il_fix_e_a_due_o_non_c_e(self):
        """Un fattore 1 sarebbe un giro di denoise senza ingrandire: non è un
        fix. Come gli altri numeri del job si limita invece di rifiutare."""
        for chiesto, atteso in ((0, 0), (1, 0), (2, 2), (3, 2), ("2", 2),
                                ("", 0), (None, 0)):
            with self.subTest(chiesto=chiesto):
                self.assertEqual(nuovo_job("x", fix=chiesto)["fix"], atteso)


class CodaFamiglieTests(unittest.TestCase):
    """Con piu' famiglie il criterio non cambia: esce sempre il piu' vecchio."""

    def setUp(self):
        self.orologio = OrologioFinto()
        self.coda = ImmagineQueue(clock=self.orologio, max_jobs=4, ttl_s=60.0,
                                  claim_ttl_s=30.0)

    def test_un_ponte_con_due_famiglie_prende_entrambe(self):
        anna = self.coda.accoda(nuovo_job("ritratto", famiglia=FAMIGLIA_SD15,
                                          adesso=self.orologio()))
        self.orologio.avanza(1)
        pony = self.coda.accoda(nuovo_job("sketch", famiglia="sdxl-turbo",
                                          adesso=self.orologio()))
        self.assertEqual(self.coda.prossimo(capace_di="sdxl-turbo,sd15")["id"], anna["id"])
        self.assertEqual(self.coda.prossimo(capace_di="sdxl-turbo,sd15")["id"], pony["id"])

    def test_una_famiglia_sola_non_prende_i_job_dell_altra(self):
        self.coda.accoda(nuovo_job("ritratto", famiglia=FAMIGLIA_SD15,
                                   adesso=self.orologio()))
        self.assertIsNone(self.coda.prossimo(capace_di="sdxl-turbo"))

    def test_i_claim_ritrovati_includono_le_due_famiglie(self):
        """Dopo un riavvio del control-plane il gate della memoria deve ritrovare i
        job del Mac: erano due famiglie anche prima, ora sono tre `if` in meno.
        """
        self.coda.accoda(nuovo_job("ritratto", famiglia=FAMIGLIA_SD15,
                                   adesso=self.orologio()))
        self.coda.prossimo(capace_di=FAMIGLIA_SD15)
        self.assertEqual(len(self.coda.running_ids(FAMIGLIE_CHECKPOINT)), 1)
        self.assertEqual(self.coda.running_ids("qwen-image-2.1"), [])


if __name__ == "__main__":
    unittest.main()

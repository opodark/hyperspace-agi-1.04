# SPDX-License-Identifier: Apache-2.0
"""Il volto di riferimento: manifest, grafo, documento e upload — la stessa cosa.

Perche' un test su questo e non solo su `modelli-sd15.json`: il riferimento e' la
PRIMA cosa del grafo che vive in QUATTRO posti, e ogni posto sbaglia in modo diverso.

  1. il manifest `modelli-riferimento.json` — nomi, impronta, cartelle di destinazione;
  2. `MODELLO_SD15` in `shared/image_jobs.py` — i nomi che il grafo carica davvero;
  3. il GRAFO (`IPAdapterAdvanced` innestato sul modello, con il peso del job);
  4. la VETRINA (`vetrina.riferimento` nel documento, dove il nome puo' arrivare da
     fuori — quindi senza percorsi, e solo per le famiglie che hanno l'adattatore).

Il difetto che questi test esistono per non vedere: il seed tiene fermo il *disegno*,
non l'identita'. Un riferimento che non arriva al modello non da' un errore — da' un
ritratto che sembra riuscito e ha un'altra faccia, dodici volte. Il grafo quindi
SOLLEVA dove l'adattatore manca invece di ignorare il campo, e la vetrina si ferma
prima di accodare.

Solo in questo file si prova l'upload: `carica_riferimento` decide COME si chiama il
file dentro ComfyUI/input, ed e' l'unico punto in cui un nome sbagliato non si vede
qui — il job fallisce dentro ComfyUI, dall'altra parte del filo.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.image_jobs as image_jobs  # noqa: E402
from integrations.comfyui import comfy_bridge  # noqa: E402
from integrations.comfyui.comfy_bridge import carica_riferimento  # noqa: E402
from shared.image_jobs import (FAMIGLIA_DEFAULT, FAMIGLIA_SD15,  # noqa: E402
                               FAMIGLIA_SDXL, MODELLO_SD15,
                               RIFERIMENTO_FORZA_DEFAULT, nuovo_job,
                               posa_supportata, riferimento_supportato, workflow)
from shared.showcase import verifica_vetrina, vetrina_dal_documento  # noqa: E402

MANIFEST = ROOT / "integrations" / "comfyui" / "modelli-riferimento.json"
ANNA = json.loads((ROOT / "data" / "persona-anna.json").read_text(encoding="utf-8"))
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")

# Il grafo di questi test dev'essere quello DICHIARATO, non quello che l'ambiente di
# chi lancia i test si porta addosso: `MODELLO_SD15["lora"]` si legge da
# `SD15_LORA_NAME` all'import, e con quella variabile impostata la catena del modello
# passerebbe dal LoRA (455) invece che dal checkpoint (451). Il LoRA ha i suoi test
# (`test_con_il_lora_...`), con il nome passato a mano.
_PATCH_LORA = mock.patch.dict(image_jobs.MODELLO_SD15, {"lora": ""})


def setUpModule():
    _PATCH_LORA.start()


def tearDownModule():
    _PATCH_LORA.stop()

# I tre ruoli del manifest, e le tre chiavi che il grafo legge da `MODELLO_SD15`: lo
# stesso nome con due grafie, ed e' esattamente il legame che va tenuto insieme.
RUOLI = {
    "controlnet_openpose": "controlnet",
    "ipadapter": "ipadapter",
    "clip_vision": "clip_vision",
}
NODO_PER_RUOLO = {
    "controlnet_openpose": ("462", "ControlNetLoader", "control_net_name"),
    "ipadapter": ("466", "IPAdapterModelLoader", "ipadapter_file"),
    "clip_vision": ("467", "CLIPVisionLoader", "clip_name"),
}


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def per_ruolo(ruolo: str) -> dict:
    return next(voce for voce in manifest()["file"] if voce["ruolo"] == ruolo)


def job_anna(**sovrascritte) -> dict:
    """Un job come lo costruisce la vetrina di Anna: SD 1.5, il suo volto."""
    base = dict(prompt="virtuale digital 2.5D K-doll illustration, 1girl",
                negativo="adult, non esplicito", larghezza=512, altezza=768,
                passi=48, seed=20260930, famiglia=FAMIGLIA_SD15)
    base.update(sovrascritte)
    return nuovo_job(**base)


class ManifestRiferimentoTests(unittest.TestCase):
    """I nomi del manifest sono quelli che il grafo carica — o e' un job morto."""

    def test_i_tre_ruoli_sono_quelli_che_il_grafo_dichiara(self):
        ruoli = {voce["ruolo"]: voce["file"] for voce in manifest()["file"]}
        self.assertEqual(set(ruoli), set(RUOLI))
        for ruolo in RUOLI:
            with self.subTest(ruolo=ruolo):
                self.assertEqual(Path(ruoli[ruolo]).name, MODELLO_SD15[ruolo])

    def test_le_cartelle_di_destinazione_sono_quelle_che_comfyui_legge(self):
        """`destinazione` e' il nome della cartella sotto i modelli: e' quello che
        l'installer usa (il file altrove non esiste, per ComfyUI) e quello che il
        preflight del ponte confronta con l'elenco dichiarato dal nodo."""
        for ruolo, destinazione in RUOLI.items():
            with self.subTest(ruolo=ruolo):
                self.assertEqual(per_ruolo(ruolo)["destinazione"], destinazione)

    def test_ogni_file_e_pinnato_a_un_commit_e_a_un_impronta(self):
        """Sono pesi di terzi: il pin e' il commit, la fiducia e' lo SHA-256."""
        for voce in manifest()["file"]:
            with self.subTest(file=voce["file"]):
                self.assertRegex(voce["revisione"], COMMIT)
                self.assertRegex(voce["sha256"], SHA256)
                self.assertGreater(voce["byte"], 0)
                self.assertTrue(voce["repo"].strip(), "una sorgente dichiarata")

    def test_la_licenza_e_dichiarata_e_non_e_nostra(self):
        """I pesi non si distribuiscono con il progetto: la licenza va detta, e va
        detto che non e' la nostra."""
        documento = manifest()
        self.assertTrue(documento["licenza"].strip())
        self.assertTrue(documento["nota_licenza"].strip())
        self.assertIn("NON sono distribuiti", documento["nota_licenza"])

    def test_il_modello_base_dichiarato_e_sd15(self):
        """Un IP-Adapter e' di una dimensione per famiglia: quello di SD 1.5 non e'
        quello di SDXL (CLIP-ViT-H contro ViT-bigG). Il manifest lo dice, e la
        famiglia che lo usa e' quella."""
        self.assertIn("1.5", manifest()["modello_base"])

    def test_non_e_il_percorso_con_il_rilevatore_di_volti(self):
        """La scelta dichiarata: IP-Adapter Plus FACE, che passa dal CLIP-ViT-H e
        non da InsightFace — quindi niente onnxruntime e nessun volto *fotografico*
        nel giro. Il riferimento e' un disegno di Anna: un file `faceid` o
        `insightface` qui dentro sarebbe un'altra cosa (e nel commento del manifest
        la scelta resta scritta, cosi' si legge perche' non c'e')."""
        nomi = [voce["file"].lower() for voce in manifest()["file"]]
        self.assertIn("plus-face", per_ruolo("ipadapter")["file"])
        for nome in nomi:
            with self.subTest(file=nome):
                self.assertNotIn("insightface", nome)
                self.assertNotIn("faceid", nome)
        self.assertIn("InsightFace", json.dumps(manifest()))

    def test_il_clip_vision_dichiara_il_nome_vero_del_modello(self):
        """Nel repository IP-Adapter il file e' il generico
        `models/image_encoder/model.safetensors`: salvarlo con quel nome vorrebbe
        dire una cartella `clip_vision` piena di `model.safetensors`. Il nome che si
        salva deve dire QUALE encoder e' (CLIP-ViT-H-14 laion2B), perche' e' quello
        che si cerca quando lo si cerca."""
        voce = per_ruolo("clip_vision")
        self.assertNotEqual(voce["file"], Path(voce["file_remoto"]).name)
        self.assertIn("CLIP-ViT-H-14", voce["file"])
        self.assertIn("laion2B", voce["file"])

    def test_il_manifest_e_quello_che_l_installer_nomina(self):
        """L'installer e' la sola cosa che scarica questi file: se il manifest non
        si chiama cosi', il file non arriva — e a scoprirlo sarebbe il preflight del
        ponte, non questa riga."""
        self.assertEqual(MANIFEST.name, "modelli-riferimento.json")
        sorgente = (ROOT / "integrations" / "comfyui" / "install-model.sh").read_text(
            encoding="utf-8")
        self.assertIn(MANIFEST.name, sorgente)


class GrafoRiferimentoTests(unittest.TestCase):
    """Dove il riferimento entra: nel modello, con il peso del job, e mai in silenzio."""

    def test_il_riferimento_si_innesta_sul_modello_non_sul_prompt(self):
        """Il riferimento va fra il checkpoint e il KSampler: cosi' vale per ogni
        passo di sampling e non tocca il prompt — che con SD 1.5 ha 77 token e non
        puo' crescere."""
        grafo = workflow(job_anna(reference_image="anna-volto.png"))
        self.assertEqual(grafo["468"]["class_type"], "IPAdapterAdvanced")
        self.assertEqual(grafo["458"]["inputs"]["model"], ["468", 0],
                         "il KSampler campiona il modello con l'adattatore dentro")
        self.assertEqual(grafo["468"]["inputs"]["model"], ["451", 0],
                         "e l'adattatore parte dal checkpoint, non da se stesso")
        self.assertEqual(grafo["465"]["class_type"], "LoadImage")
        self.assertEqual(grafo["465"]["inputs"]["image"], "anna-volto.png")
        self.assertEqual(grafo["452"]["class_type"], "CLIPTextEncode",
                         "il riferimento non riscrive la richiesta: e' geometria, non testo")

    def test_i_file_del_manifest_sono_quelli_che_il_grafo_carica(self):
        """Il legame nome-per-nome, dal grafo vero: e' l'unico posto in cui un nome
        del manifest diventa l'input di un nodo."""
        grafo = workflow(job_anna(reference_image="f.png", pose_preset="seated"))
        for ruolo, (nodo, classe, campo) in NODO_PER_RUOLO.items():
            with self.subTest(ruolo=ruolo):
                self.assertEqual(grafo[nodo]["class_type"], classe)
                self.assertEqual(grafo[nodo]["inputs"][campo], MODELLO_SD15[ruolo])
                self.assertEqual(grafo[nodo]["inputs"][campo], per_ruolo(ruolo)["file"])

    def test_l_adattatore_legge_con_i_numeri_che_la_scheda_chiede(self):
        """PLUS FACE si legge con `weight_type: linear` e `embeds_scaling: V only`:
        sono proprieta' del FILE, non scelte del job, quindi stanno nel grafo. Il
        peso invece e' del job (vedi `test_il_peso_e_quello_del_job`)."""
        nodo = workflow(job_anna(reference_image="f.png"))["468"]["inputs"]
        self.assertEqual(nodo["weight_type"], "linear")
        self.assertEqual(nodo["combine_embeds"], "concat")
        self.assertEqual(nodo["embeds_scaling"], "V only")
        self.assertEqual((nodo["start_at"], nodo["end_at"]), (0.0, 1.0))

    def test_il_peso_e_quello_del_job_e_il_default_e_uno_solo(self):
        """Il peso lo decide il job. Il default e' UNA costante condivisa
        (`RIFERIMENTO_FORZA_DEFAULT`): due copie dello stesso numero sono due default
        che si allontanano, ed e' il caso in cui il grafo usa 0.8 mentre il job ne
        dichiara un altro."""
        self.assertEqual(job_anna(reference_image="f.png")["reference_strength"],
                         RIFERIMENTO_FORZA_DEFAULT)
        self.assertEqual(workflow(job_anna(reference_image="f.png"))["468"]["inputs"]["weight"],
                         RIFERIMENTO_FORZA_DEFAULT)
        grafo = workflow(job_anna(reference_image="f.png", reference_strength=0.35))
        self.assertEqual(grafo["468"]["inputs"]["weight"], 0.35)

    def test_la_forza_si_limita_invece_di_essere_rifiutata(self):
        """Chi chiede 9 non ha chiesto qualcosa di sbagliato, ha chiesto qualcosa di
        impossibile: il tetto (2.0) si vede nel job e nel grafo, non in un errore."""
        job = job_anna(reference_image="f.png", reference_strength=9)
        self.assertEqual(job["reference_strength"], 2.0)
        self.assertEqual(workflow(job)["468"]["inputs"]["weight"], 2.0)

    def test_senza_riferimento_il_grafo_non_ha_nodi_dell_adattatore(self):
        """Un grafo con i nodi del riferimento a vuoto sarebbe piu' facile da
        sbagliare: senza `reference_image` i nodi non ci sono affatto."""
        grafo = workflow(job_anna())
        for nodo in ("465", "466", "467", "468"):
            self.assertNotIn(nodo, grafo)
        self.assertEqual(grafo["458"]["inputs"]["model"], ["451", 0])

    def test_una_famiglia_senza_adattatore_si_ferma_invece_di_mentire(self):
        """Il caso che il riferimento esiste per togliere (un volto diverso) e' anche
        quello che un campo ignorato produce in silenzio: il Pony e il Qwen non hanno
        un IP-Adapter, quindi il grafo SOLLEVA. Meglio un job fallito e visibile che
        un ritratto sbagliato che sembra riuscito."""
        for famiglia in (FAMIGLIA_SDXL, FAMIGLIA_DEFAULT):
            with self.subTest(famiglia=famiglia):
                with self.assertRaises(ValueError) as errore:
                    workflow(job_anna(famiglia=famiglia, reference_image="f.png"))
                self.assertIn("IP-Adapter", str(errore.exception))

    def test_un_adattatore_senza_clip_vision_non_basta(self):
        """Servono entrambi: l'adattatore aggancia il riferimento, il CLIP-ViT lo
        legge. Una famiglia che ne dichiara uno solo non e' «quasi pronta» — e il
        grafo lo dice PRIMA di costruire il nodo, non a ogni job fallito."""
        senza = {**MODELLO_SD15, "clip_vision": ""}
        with mock.patch.dict(image_jobs.RICETTE_CHECKPOINT, {FAMIGLIA_SD15: senza}):
            with self.assertRaises(ValueError) as errore:
                workflow(job_anna(reference_image="f.png"))
        self.assertIn("CLIP-ViT", str(errore.exception))

    def test_un_riferimento_con_un_percorso_e_rifiutato(self):
        """`LoadImage` legge solo da ComfyUI/input: un percorso accettato qui
        trasformerebbe il ponte in un lettore di file della macchina."""
        for nome in ("/etc/passwd", "../../fuori.png", "https://x/y.png"):
            with self.subTest(nome=nome):
                with self.assertRaises(ValueError):
                    job_anna(reference_image=nome)

    def test_posa_e_riferimento_convivono(self):
        """Sono due cose diverse e due rami diversi del grafo: la posa va sul
        condizionamento (positivo E negativo), il volto sul modello."""
        grafo = workflow(job_anna(reference_image="f.png", pose_preset="seated"))
        self.assertEqual(grafo["463"]["class_type"], "ControlNetApplyAdvanced")
        self.assertEqual(grafo["458"]["inputs"]["positive"], ["463", 0])
        self.assertEqual(grafo["458"]["inputs"]["negative"], ["463", 1])
        self.assertEqual(grafo["458"]["inputs"]["model"], ["468", 0])

    def test_con_il_lora_l_adattatore_parte_dal_lora_non_dal_checkpoint(self):
        """L'ordine non e' un dettaglio: se l'adattatore leggesse dal checkpoint,
        `LoraLoader` resterebbe fuori dalla catena del modello e il LoRA non
        disegnerebbe niente."""
        grafo = workflow(job_anna(reference_image="f.png",
                                  lora_name="stile.safetensors"))
        self.assertEqual(grafo["455"]["class_type"], "LoraLoader")
        self.assertEqual(grafo["468"]["inputs"]["model"], ["455", 0])


class PredicatoTests(unittest.TestCase):
    """Le due domande che si fanno PRIMA di accodare: si puo' posare, si puo' riferire."""

    def test_la_posa_e_di_chi_ha_il_controlnet(self):
        self.assertTrue(posa_supportata(FAMIGLIA_SD15))
        self.assertTrue(posa_supportata(FAMIGLIA_SDXL))
        self.assertFalse(posa_supportata(FAMIGLIA_DEFAULT))

    def test_il_riferimento_e_di_chi_ha_adattatore_e_clip(self):
        """Solo SD 1.5, oggi: l'adattatore di SDXL sarebbe un'altra dimensione
        (ViT-bigG contro ViT-H), e una famiglia vuota o sconosciuta non cade sul
        default di nascosto."""
        self.assertTrue(riferimento_supportato(FAMIGLIA_SD15))
        self.assertFalse(riferimento_supportato(FAMIGLIA_SDXL))
        self.assertFalse(riferimento_supportato(FAMIGLIA_DEFAULT))
        self.assertFalse(riferimento_supportato(""))
        self.assertFalse(riferimento_supportato("famiglia-che-non-esiste"))

    def test_una_famiglia_che_dichiara_solo_l_adattatore_non_supporta_il_riferimento(self):
        """La domanda e' `ipadapter` E `clip_vision`: mezza ricetta non e' mezzo
        riferimento, e' un job che fallirebbe a ogni generazione."""
        senza = {**MODELLO_SD15, "clip_vision": ""}
        with mock.patch.dict(image_jobs.RICETTE_CHECKPOINT, {FAMIGLIA_SD15: senza}):
            self.assertFalse(riferimento_supportato(FAMIGLIA_SD15))


class VetrinaRiferimentoTests(unittest.TestCase):
    """Il documento e' la fonte: da li' il nome arriva al job, o si ferma prima."""

    def _vetrina(self, **vetrina):
        return vetrina_dal_documento(
            {**ANNA, "vetrina": {**ANNA["vetrina"], **vetrina}})

    def test_la_vetrina_porta_il_riferimento_senza_interpretarlo(self):
        vetrina = self._vetrina(riferimento="anna-volto.png", riferimento_forza=0.6)
        self.assertEqual(vetrina["riferimento"], "anna-volto.png")
        self.assertEqual(vetrina["riferimento_forza"], 0.6)
        self.assertEqual(verifica_vetrina(vetrina, ANNA), [])

    def test_senza_riferimento_dichiarato_non_se_ne_inventa_uno(self):
        """Vuoto = nessun riferimento, che e' come sono sempre andate le cose: il
        seed fisso da' lo stesso disegno, non lo stesso volto. E il peso resta quello
        di default, pronto per quando il nome ci sara'."""
        documento = {**ANNA, "vetrina": {**ANNA["vetrina"],
                     "riferimento": "", "riferimento_forza": RIFERIMENTO_FORZA_DEFAULT}}
        vetrina = vetrina_dal_documento(documento)
        self.assertEqual(vetrina["riferimento"], "")
        self.assertEqual(vetrina["riferimento_forza"], RIFERIMENTO_FORZA_DEFAULT)
        self.assertEqual(verifica_vetrina(vetrina, documento), [])

    def test_il_riferimento_di_anna_arriva_al_grafo(self):
        """Tutto il percorso, documento -> vetrina -> job -> grafo: il nome che il
        documento dichiara e' quello che `LoadImage` carica, e i pesi sono quelli
        della ricetta SD 1.5."""
        vetrina = self._vetrina(riferimento="anna-volto.png")
        self.assertEqual(verifica_vetrina(vetrina, ANNA), [])
        job = nuovo_job("virtuale digital 2.5D K-doll illustration, 1girl",
                        negativo=vetrina["negativo"],
                        larghezza=vetrina["larghezza"], altezza=vetrina["altezza"],
                        passi=vetrina["passi"], seed=vetrina["seed"],
                        famiglia=vetrina["famiglia"], modello=vetrina["modello"],
                        reference_image=vetrina["riferimento"],
                        reference_strength=vetrina["riferimento_forza"])
        grafo = workflow(job)
        self.assertEqual(grafo["465"]["inputs"]["image"], "anna-volto.png")
        self.assertEqual(grafo["466"]["inputs"]["ipadapter_file"], MODELLO_SD15["ipadapter"])
        self.assertEqual(grafo["467"]["inputs"]["clip_name"], MODELLO_SD15["clip_vision"])
        self.assertEqual(grafo["468"]["inputs"]["weight"], vetrina["riferimento_forza"])
        self.assertEqual(grafo["468"]["inputs"]["model"], ["451", 0])
        self.assertEqual(grafo["451"]["inputs"]["ckpt_name"], MODELLO_SD15["ckpt"])
        # Questo prompt non nomina una posa: la posa resta al testo, com'e' sempre
        # stato, e il ControlNet non entra nel grafo per il solo riferimento.
        self.assertEqual(job["pose_preset"], "")
        self.assertNotIn("462", grafo)

    def test_una_famiglia_senza_adattatore_non_puo_dichiarare_un_volto(self):
        """Il Qwen e il Pony non hanno l'adattatore: un riferimento li' non
        arriverebbe al modello, e il ritratto uscirebbe con un volto qualunque — il
        tipo di risultato che sembra riuscito. Non serve `--forza`: serve togliere il
        riferimento o dichiarare la famiglia che ce l'ha."""
        for famiglia in (FAMIGLIA_SDXL, FAMIGLIA_DEFAULT):
            with self.subTest(famiglia=famiglia):
                vetrina = self._vetrina(riferimento="anna-volto.png",
                                        famiglia=famiglia)
                problemi = verifica_vetrina(vetrina, ANNA)
                self.assertTrue(any("adattatore" in problema for problema in problemi),
                                problemi)

    def test_il_riferimento_e_un_nome_dentro_comfyui_input(self):
        """Come `modello` e `pose_image`: un nome, non un percorso. Il nome puo'
        arrivare da un documento, e un documento non deve poter far leggere al ponte
        un file qualsiasi della macchina."""
        for nome in ("../../etc/passwd", "/tmp/volto.png", "https://x/y.png"):
            with self.subTest(nome=nome):
                problemi = verifica_vetrina(self._vetrina(riferimento=nome), ANNA)
                self.assertTrue(any("ComfyUI/input" in problema for problema in problemi),
                                problemi)


class CaricaRiferimentoTests(unittest.TestCase):
    """L'upload: il nome che ComfyUI salva e' l'unico che `LoadImage` capisce."""

    def test_un_file_che_non_esiste_non_diventa_un_nome(self):
        """Prima di parlare con ComfyUI: un percorso sbagliato non deve produrre un
        nome che poi fallisce il job dall'altra parte del filo."""
        nome, motivo = carica_riferimento("/non/esiste/volto.png")
        self.assertEqual(nome, "")
        self.assertIn("file non trovato", motivo)

    def test_il_nome_e_quello_che_comfyui_ha_salvato_con_la_sottocartella(self):
        """ComfyUI puo' salvare in una sottocartella (`volti/`): il nome per il job
        e' quello che LUI dichiara, non il nome del file di partenza — e la
        sottocartella fa parte del nome, perche' e' cosi' che `LoadImage` lo cerca."""
        with tempfile.TemporaryDirectory() as cartella:
            file = Path(cartella) / "volto.png"
            file.write_bytes(b"finto png")
            with mock.patch.object(comfy_bridge, "_carica_immagine",
                                   return_value=(200, {"name": "volto.png",
                                                       "subfolder": "volti"})):
                self.assertEqual(carica_riferimento(file), ("volti/volto.png", ""))

    def test_senza_sottocartella_il_nome_resta_pulito(self):
        with tempfile.TemporaryDirectory() as cartella:
            file = Path(cartella) / "volto.png"
            file.write_bytes(b"finto png")
            with mock.patch.object(comfy_bridge, "_carica_immagine",
                                   return_value=(200, {"name": "volto.png",
                                                       "subfolder": ""})):
                self.assertEqual(carica_riferimento(file), ("volto.png", ""))

    def test_il_nome_chiesto_a_mano_e_quello_che_parte(self):
        """`--stage-riferimento --nome-riferimento` serve a dare al file il nome che
        il documento di Anna gia' dichiara: senza, ogni ricarica rinominerebbe il
        volto e il documento punterebbe a un file che non c'e' piu'."""
        visto = {}

        def cattura(comfy_url, percorso, nome=""):
            visto["nome"] = nome
            return 200, {"name": nome or percorso.name}

        with tempfile.TemporaryDirectory() as cartella:
            file = Path(cartella) / "IMG_4832.png"
            file.write_bytes(b"finto png")
            with mock.patch.object(comfy_bridge, "_carica_immagine", cattura):
                self.assertEqual(carica_riferimento(file, nome="anna-volto.png"),
                                 ("anna-volto.png", ""))
        self.assertEqual(visto["nome"], "anna-volto.png")

    def test_un_caricamento_fallito_non_produce_un_nome(self):
        """Un 500 da ComfyUI non e' un riferimento: e' un motivo da stampare, e chi
        chiama decide (il ponte esce, `ritratto.py` si ferma prima di accodare)."""
        with tempfile.TemporaryDirectory() as cartella:
            file = Path(cartella) / "volto.png"
            file.write_bytes(b"finto png")
            with mock.patch.object(comfy_bridge, "_carica_immagine",
                                   return_value=(500, {"errore": "boom"})):
                nome, motivo = carica_riferimento(file)
        self.assertEqual(nome, "")
        self.assertIn("HTTP 500", motivo)
        self.assertIn("boom", motivo)

    def test_un_comfyui_irraggiungibile_e_un_motivo_non_un_traceback(self):
        """`_carica_immagine` torna 0 quando ComfyUI non risponde: e' il caso di chi
        sta preparando il volto con ComfyUI spento, e deve leggere il perche'."""
        with tempfile.TemporaryDirectory() as cartella:
            file = Path(cartella) / "volto.png"
            file.write_bytes(b"finto png")
            with mock.patch.object(comfy_bridge, "_carica_immagine",
                                   return_value=(0, {"errore": "ComfyUI non raggiungibile"})):
                nome, motivo = carica_riferimento(file)
        self.assertEqual(nome, "")
        self.assertIn("HTTP 0", motivo)

    def test_l_upload_e_un_multipart_che_comfyui_accetta(self):
        """Il protocollo: `POST /upload/image` con il file nel campo `image`, e i due
        campi che dicono dove metterlo (`type: input`) e di sovrascrivere. Un upload
        che «sembra funzionare» e non salva niente e' il caso peggiore: il job parte
        e non trova il volto."""
        richieste = []

        class Risposta:
            status = 200

            def read(self):
                return b'{"name": "volto.png", "subfolder": ""}'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def finta(richiesta, timeout=None):
            richieste.append(richiesta)
            return Risposta()

        with tempfile.TemporaryDirectory() as cartella:
            file = Path(cartella) / "volto.png"
            file.write_bytes(b"\x89PNG\r\n\x1a\nfinto-volto")
            with mock.patch.object(comfy_bridge.urllib.request, "urlopen", finta):
                esito = carica_riferimento(file, comfy_url="http://127.0.0.1:8188")
        self.assertEqual(esito, ("volto.png", ""))
        self.assertEqual(len(richieste), 1)
        richiesta = richieste[0]
        self.assertTrue(richiesta.full_url.endswith("/upload/image"), richiesta.full_url)
        self.assertEqual(richiesta.get_method(), "POST")
        self.assertIn("multipart/form-data", richiesta.get_header("Content-type"))
        corpo = richiesta.data or b""
        for atteso in (b'name="image"', b'filename="volto.png"', b'name="type"',
                       b"input", b'name="overwrite"', b"finto-volto"):
            with self.subTest(campo=atteso):
                self.assertIn(atteso, corpo)


if __name__ == "__main__":
    unittest.main()

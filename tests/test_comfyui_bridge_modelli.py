# SPDX-License-Identifier: Apache-2.0
"""Il ponte verifica I FILE DELLE FAMIGLIE CHE ESEGUE, non "un file qualsiasi".

Il buco chiuso qui (2026-09-30): `--check` chiedeva a ComfyUI l'elenco dei suoi
checkpoint e si accontentava che l'elenco NON FOSSE VUOTO. Con una famiglia nuova
(`sd15`, il volto di Anna) quel controllo diceva «pronto» anche senza il modello
installato: ogni ritratto sarebbe fallito dentro ComfyUI, dove l'errore non si
vede. E il controllo dei NODI era `modello == "sdxl-turbo"`, con un `else` che
assumeva Qwen-Image: per sd15 avrebbe verificato i nodi sbagliati.

Dal 2026-09-30 l'elenco non e' piu' solo «checkpoint + ingranditore»: una famiglia
che DICHIARA il ControlNet openpose o l'IP-Adapter porta anche i suoi file
(`modelli-riferimento.json`), perche' sono letti nel mezzo della generazione — un
file assente non da' un ritratto senza posa, da' un job morto dentro ComfyUI. E la
domanda e' «cosa dichiara la famiglia», non «cosa c'e' su disco»: al Pony, che non
ha un IP-Adapter in ricetta, il riferimento non si chiede.

E il messaggio di un elenco vuoto non e' piu' uno solo: «ComfyUI non dichiara
nessun file» vuol dire il NODO non installato oppure la sua CARTELLA vuota, e i due
rimedi sono script diversi. Il 2026-09-30 la prima lettura mandava a reinstallare un
pacchetto che c'era: `IPAdapterModelLoader` cercava `<ComfyUI>/models/ipadapter`,
una cartella che l'istanza desktop non mappa sulla cartella condivisa.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.comfyui.comfy_bridge import (  # noqa: E402
    _elenco_file, _file_attesi, _perche_niente_file)
from shared.image_jobs import (FAMIGLIA_DEFAULT, FIX_UPSCALER,  # noqa: E402
                               MODELLO_DEFAULT, MODELLO_SD15, MODELLO_SDXL)


class FileAttesiTests(unittest.TestCase):
    def test_un_ponte_di_un_solo_checkpoint_ne_verifica_uno(self):
        """Il Pony: il checkpoint e il ControlNet openpose di SDXL, che la sua
        ricetta dichiara. Posa e riferimento non sono accessori che si possono
        saltare: `ControlNetLoader` legge il file NEL MEZZO della generazione, e
        un file assente non da' un ritratto senza posa — da' un job morto.
        Della posa ne risponde la famiglia che la DICHIARA: qui c'e' solo SDXL.
        """
        self.assertEqual(_file_attesi("sdxl-turbo"),
                         [("CheckpointLoaderSimple", "ckpt_name", MODELLO_SDXL["ckpt"]),
                          ("ControlNetLoader", "control_net_name",
                           MODELLO_SDXL["controlnet_openpose"])])

    def test_il_checkpoint_di_anna_e_verificato_per_nome(self):
        """E con lui l'ingranditore del fix: i ritratti di Anna escono a 1024×1536
        con il secondo passaggio, e senza quel file fallirebbe DENTRO ComfyUI —
        l'errore che questo controllo esiste per anticipare.

        Poi i tre file della posa e del volto (manifest `modelli-riferimento.json`):
        il ControlNet openpose di SD 1.5, l'IP-Adapter Plus Face e il CLIP-ViT-H che
        lo legge. Sono i file che fanno di dodici varianti dodici volte la stessa
        persona: un preflight che li saltasse direbbe «pronto» a un ponte che al
        primo `--riferimento` fallisce dentro ComfyUI.
        """
        self.assertEqual(_file_attesi("sd15"),
                         [("CheckpointLoaderSimple", "ckpt_name", MODELLO_SD15["ckpt"]),
                          ("ControlNetLoader", "control_net_name",
                           MODELLO_SD15["controlnet_openpose"]),
                          ("IPAdapterModelLoader", "ipadapter_file", MODELLO_SD15["ipadapter"]),
                          ("CLIPVisionLoader", "clip_name", MODELLO_SD15["clip_vision"]),
                          ("UpscaleModelLoader", "model_name", FIX_UPSCALER)])

    def test_il_riferimento_non_si_chiede_a_una_famiglia_che_non_lo_dichiara(self):
        """Il Pony non ha un IP-Adapter in ricetta: chiederglielo sarebbe un
        problema inventato, e fermerebbe l'avvio del ponte per un file che non
        serve a nessuno dei suoi job. La domanda e' «cosa dichiara la famiglia»,
        non «cosa e' stato scaricato su questa macchina».
        """
        nodi = {nodo for nodo, _, _ in _file_attesi("sdxl-turbo")}
        self.assertNotIn("IPAdapterModelLoader", nodi)
        self.assertNotIn("CLIPVisionLoader", nodi)

    def test_un_ponte_con_due_famiglie_verifica_tutti_i_file(self):
        """E' il caso del Mac: se ne manca uno, `--check` deve dirlo — altrimenti
        meta' dei job di quel ponte fallisce e l'altra meta' riesce. L'ordine e'
        quello delle famiglie di `BRIDGE_MODEL`, e dentro una famiglia quello della
        ricetta: checkpoint, ControlNet, riferimento, ingranditore.
        """
        attesi = _file_attesi("sdxl-turbo,sd15")
        self.assertEqual([nome for _, _, nome in attesi],
                         [MODELLO_SDXL["ckpt"], MODELLO_SDXL["controlnet_openpose"],
                          MODELLO_SD15["ckpt"], MODELLO_SD15["controlnet_openpose"],
                          MODELLO_SD15["ipadapter"], MODELLO_SD15["clip_vision"],
                          FIX_UPSCALER])

    def test_una_famiglia_qwen_verifica_i_suoi_tre_file(self):
        attesi = _file_attesi(FAMIGLIA_DEFAULT)
        self.assertEqual(attesi, [
            ("UnetLoaderGGUF", "unet_name", MODELLO_DEFAULT["unet"]),
            ("CLIPLoader", "clip_name", MODELLO_DEFAULT["clip"]),
            ("VAELoader", "vae_name", MODELLO_DEFAULT["vae"]),
        ])

    def test_un_ponte_che_accetta_qualunque_job_verifica_tutti_i_nodi(self):
        """Vuoto = qualunque job: allora ComfyUI deve avere sia i file di Qwen sia
        quelli dei checkpoint (posa e volto compresi), perche' il ponte puo'
        riceverli entrambi.
        """
        nodi = {nodo for nodo, _, _ in _file_attesi("")}
        self.assertEqual(nodi, {"UnetLoaderGGUF", "CLIPLoader", "VAELoader",
                                "CheckpointLoaderSimple", "ControlNetLoader",
                                "IPAdapterModelLoader", "CLIPVisionLoader",
                                "UpscaleModelLoader"})


class ElencoFileTests(unittest.TestCase):
    """Le due forme con cui ComfyUI dichiara un input a elenco.

    Il 2026-09-30 `--check` diceva «UpscaleModelLoader.upscale_model: ComfyUI non
    dichiara nessun file» con i due R-ESRGAN al loro posto: leggeva solo la forma
    storica, e un file che risulta assente ferma l'avvio del ponte. (Il campo del
    nodo è `model_name`: quel nome sbagliato era un secondo errore, corretto
    adesso.)"""

    def test_la_forma_storica_mette_la_lista_al_primo_posto(self):
        self.assertEqual(_elenco_file([["a.ckpt", "b.ckpt"], {"default": "a.ckpt"}]),
                         ["a.ckpt", "b.ckpt"])

    def test_la_forma_con_combo_mette_le_opzioni_nel_secondo(self):
        self.assertEqual(_elenco_file(["COMBO", {"multiselect": False,
                                                 "options": ["x.pth", "y.pth"]}]),
                         ["x.pth", "y.pth"])

    def test_vuoto_resta_vuoto(self):
        """«ComfyUI non ha file» e «il formato non si capisce» sono due cose
        diverse, e il preflight deve poterlo dire: qui si prova il secondo."""
        self.assertEqual(_elenco_file([]), [])
        self.assertEqual(_elenco_file(["COMBO", {}]), [])
        self.assertEqual(_elenco_file(["STRING", {"default": ""}]), [])


class PercheNienteFileTests(unittest.TestCase):
    """Un elenco vuoto ha due cause, e il rimedio e' uno dei due script.

    ComfyUI risponde `{}` a `/object_info/<nodo>` sia quando il nodo non esiste
    sia quando esiste e non ha file da offrire: stesso sintomo, rimedi opposti.
    Il 2026-09-30 il preflight ha detto «il nodo non e' installato: serve
    ComfyUI_IPAdapter_plus» mentre il pacchetto era installato e la cartella che
    `IPAdapterModelLoader` legge (`<ComfyUI>/models/ipadapter`) era vuota perche'
    l'istanza desktop non la mappa sulla cartella condivisa. Chi legge deve poter
    decidere in un colpo se reinstallare il pacchetto o sistemare i pesi.
    """

    def test_senza_il_nodo_la_colpa_e_del_pacchetto(self):
        motivo = _perche_niente_file("IPAdapterModelLoader", {})
        self.assertIn("il nodo non è installato", motivo)
        self.assertIn("ComfyUI_IPAdapter_plus", motivo)
        self.assertNotIn("cartella", motivo)

    def test_con_il_nodo_la_colpa_e_della_cartella(self):
        """Il nodo c'e' — la risposta lo contiene — e le scelte sono `[[]]`."""
        motivo = _perche_niente_file("IPAdapterModelLoader",
                                     {"IPAdapterModelLoader": {"input": {"required": {
                                         "ipadapter_file": [[]]}}}})
        self.assertIn("la cartella che legge è vuota", motivo)
        self.assertIn("install-model.sh", motivo)
        self.assertNotIn("install-nodes.sh", motivo)

    def test_un_nodo_nativo_non_segna_un_pacchetto_che_non_esiste(self):
        """`CLIPVisionLoader` non sta in nessun pacchetto di terze parti: dire
        «serve un pacchetto» manderebbe a cercarlo."""
        motivo = _perche_niente_file("CLIPVisionLoader", {})
        self.assertIn("in questa versione di ComfyUI", motivo)
        self.assertNotIn("install-nodes.sh", motivo)


if __name__ == "__main__":
    unittest.main()

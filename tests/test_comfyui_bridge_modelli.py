# SPDX-License-Identifier: Apache-2.0
"""Il ponte verifica I FILE DELLE FAMIGLIE CHE ESEGUE, non "un file qualsiasi".

Il buco chiuso qui (2026-09-30): `--check` chiedeva a ComfyUI l'elenco dei suoi
checkpoint e si accontentava che l'elenco NON FOSSE VUOTO. Con una famiglia nuova
(`sd15`, il volto di Anna) quel controllo diceva «pronto» anche senza il modello
installato: ogni ritratto sarebbe fallito dentro ComfyUI, dove l'errore non si
vede. E il controllo dei NODI era `modello == "sdxl-turbo"`, con un `else` che
assumeva Qwen-Image: per sd15 avrebbe verificato i nodi sbagliati.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.comfyui.comfy_bridge import _elenco_file, _file_attesi  # noqa: E402
from shared.image_jobs import (FAMIGLIA_DEFAULT, FIX_UPSCALER,  # noqa: E402
                               MODELLO_DEFAULT, MODELLO_SD15, MODELLO_SDXL)


class FileAttesiTests(unittest.TestCase):
    def test_un_ponte_di_un_solo_checkpoint_ne_verifica_uno(self):
        self.assertEqual(_file_attesi("sdxl-turbo"),
                         [("CheckpointLoaderSimple", "ckpt_name", MODELLO_SDXL["ckpt"])])

    def test_il_checkpoint_di_anna_e_verificato_per_nome(self):
        """E con lui l'ingranditore del fix: i ritratti di Anna escono a 1024×1536
        con il secondo passaggio, e senza quel file fallirebbe DENTRO ComfyUI —
        l'errore che questo controllo esiste per anticipare."""
        self.assertEqual(_file_attesi("sd15"),
                         [("CheckpointLoaderSimple", "ckpt_name", MODELLO_SD15["ckpt"]),
                          ("UpscaleModelLoader", "model_name", FIX_UPSCALER)])

    def test_un_ponte_con_due_famiglie_verifica_tutti_i_file(self):
        """E' il caso del Mac: se ne manca uno, `--check` deve dirlo — altrimenti
        meta' dei job di quel ponte fallisce e l'altra meta' riesce. Sono tre
        perche' sd15 ne porta due (checkpoint e ingranditore del fix).
        """
        attesi = _file_attesi("sdxl-turbo,sd15")
        self.assertEqual([nome for _, _, nome in attesi],
                         [MODELLO_SDXL["ckpt"], MODELLO_SD15["ckpt"], FIX_UPSCALER])

    def test_una_famiglia_qwen_verifica_i_suoi_tre_file(self):
        attesi = _file_attesi(FAMIGLIA_DEFAULT)
        self.assertEqual(attesi, [
            ("UnetLoaderGGUF", "unet_name", MODELLO_DEFAULT["unet"]),
            ("CLIPLoader", "clip_name", MODELLO_DEFAULT["clip"]),
            ("VAELoader", "vae_name", MODELLO_DEFAULT["vae"]),
        ])

    def test_un_ponte_che_accetta_qualunque_job_verifica_tutti_i_nodi(self):
        """Vuoto = qualunque job: allora ComfyUI deve avere sia i file di Qwen sia
        quelli dei checkpoint, perche' il ponte puo' riceverli entrambi.
        """
        nodi = {nodo for nodo, _, _ in _file_attesi("")}
        self.assertEqual(nodi, {"UnetLoaderGGUF", "CLIPLoader", "VAELoader",
                                "CheckpointLoaderSimple", "UpscaleModelLoader"})


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


if __name__ == "__main__":
    unittest.main()

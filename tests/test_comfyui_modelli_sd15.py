# SPDX-License-Identifier: Apache-2.0
"""Il checkpoint di Anna (ChickMixFlat, SD 1.5): manifest, grafo e documento.

Stessa disciplina di `test_comfyui_modelli_sdxl.py`, ma il nome del file sta in
TRE posti invece di due: il manifest `modelli-sd15.json` che `install-model.sh`
scarica, `MODELLO_SD15` nel grafo che il ponte manda a ComfyUI, e la `vetrina` del
documento di Anna, che e' il posto da cui il nome parte davvero. Un disallineamento
non si vede generando: `CheckpointLoaderSimple` non trova il file e il job fallisce
con l'errore dentro ComfyUI — oppure, peggio, il documento nomina un modello e ne
esegue un altro. Confrontarli qui costa un test.

Una differenza dall'SDXL: la sorgente non e' un repository Hugging Face pinnato a
un commit ma un upload Civitai, che un commit non ce l'ha. Il pin e' l'impronta
SHA-256, e il download lo fa `install-model.sh` dall'URL del manifest.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import FAMIGLIA_SD15, MODELLO_SD15  # noqa: E402

MANIFEST = ROOT / "integrations" / "comfyui" / "modelli-sd15.json"
DOCUMENTO = ROOT / "data" / "persona-anna.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def carica(percorso: Path = MANIFEST) -> dict:
    return json.loads(percorso.read_text(encoding="utf-8"))


class ManifestSd15Tests(unittest.TestCase):
    def test_la_sorgente_e_un_url_https(self):
        """Non c'e' un commit a cui pinnarsi: il pin e' l'impronta, e l'origine
        deve essere dichiarata perche' quei pesi vengono da fuori."""
        manifest = carica()
        self.assertTrue(manifest["url"].startswith("https://"))
        self.assertIn("sorgente", manifest)
        self.assertTrue(manifest["sorgente"].strip())

    def test_l_impronta_e_uno_sha256_vero_e_la_dimensione_e_positiva(self):
        manifest = carica()
        self.assertRegex(manifest["checkpoint"]["sha256"], SHA256)
        self.assertGreater(manifest["checkpoint"]["byte"], 0)

    def test_il_file_del_manifest_e_quello_che_il_grafo_carica(self):
        self.assertEqual(Path(carica()["checkpoint"]["file"]).name, MODELLO_SD15["ckpt"])

    def test_la_destinazione_e_la_cartella_checkpoint(self):
        self.assertEqual(carica()["checkpoint"]["destinazione"], "checkpoints")

    def test_la_licenza_e_dichiarata(self):
        manifest = carica()
        self.assertTrue(manifest["licenza"].strip())
        self.assertTrue(manifest["nota_licenza"].strip())

    def test_il_formato_e_dichiarato_perche_e_un_pickle(self):
        """Civitai offre solo PickleTensor. Non basta saperlo: chi scarica deve
        poterlo leggere, e deve sapere su cosa si basa la fiducia (lo scan di
        Civitai, che e' un dato di terzi — non una garanzia nostra)."""
        formato = carica()["checkpoint"]["formato"]
        self.assertIn("Pickle", formato)
        self.assertIn("pickleScanResult", formato)

    def test_il_documento_di_anna_dichiara_questo_modello(self):
        """Il terzo posto: la vetrina di Anna. Se qui c'e' un altro nome, il
        ritratto esce da un modello e il documento ne descrive un altro."""
        vetrina = carica(DOCUMENTO)["vetrina"]
        self.assertEqual(vetrina["famiglia"], FAMIGLIA_SD15)
        self.assertEqual(vetrina["modello"], MODELLO_SD15["ckpt"])

    def test_il_documento_runtime_di_anna_dichiara_lo_stesso_modello(self):
        """Anna puo' esistere in due documenti: il seme nel repo e la copia locale del
        control-plane (`data/control-plane/`, che e' in .gitignore e su un'altra
        macchina non c'e'). Due vetrine diverse sono due volti diversi: il 2026-09-23
        e' gia' successo con Aurora, e `divergenze_documenti` lo segnala — questo
        test lo precede. Se la copia non c'e', non c'e' niente da confrontare.
        """
        runtime = ROOT / "data" / "control-plane" / "persona.json"
        if not runtime.is_file():
            self.skipTest("nessuna copia runtime su questa macchina")
        self.assertEqual(carica(runtime)["name"], "Anna")
        self.assertEqual(carica(runtime)["vetrina"], carica(DOCUMENTO)["vetrina"])


if __name__ == "__main__":
    unittest.main()

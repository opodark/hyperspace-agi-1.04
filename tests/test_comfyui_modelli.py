# SPDX-License-Identifier: Apache-2.0
"""I pesi del modello d'immagine: manifest e grafo devono dire la stessa cosa.

Perche' un test su un file di configurazione: i nomi dei pesi sono STRINGHE in due
posti — il manifest che `install-model.ps1` scarica e `MODELLO_DEFAULT` nel grafo
che il ponte manda a ComfyUI. Non c'e' nessun tipo che li leghi, quindi l'unico
modo di non scoprire un disallineamento *durante una generazione* (dopo 13 minuti
di sampling: `UnetLoaderGGUF` che non trova il file) e' confrontarli qui.

Le impronte nel manifest sono confrontate con il repository upstream dalla
verifica: 2026-09-22, contro SHA256SUMS (gli stessi valori dell'oggetto LFS di
ogni file). Questi test difendono la FORMA del manifest — se qualcuno cancella una
impronta o la sostituisce con un segnaposto, il download diventerebbe "fidati del
sito" senza che nessuno se ne accorga.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import MODELLO_DEFAULT  # noqa: E402

MANIFEST = ROOT / "integrations" / "comfyui" / "modelli.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")

# Le tre cartelle che ComfyUI legge: il GGUF lo prende UnetLoaderGGUF, il text
# encoder CLIPLoader, il VAE VAELoader. Sono tre, e sbagliarne una e' un grafo che
# non parte (comfy_bridge --check lo dice, ma solo a chi lo lancia).
DESTINAZIONI = {"unet": "diffusion_models", "clip": "text_encoders", "vae": "vae"}


def carica() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class ManifestTests(unittest.TestCase):
    def test_il_manifest_e_pinnato_a_un_commit(self):
        """`main` cambierebbe sotto i piedi: le impronte valgono per una revisione."""
        manifest = carica()
        self.assertNotIn(manifest["revisione"], ("main", "master", "latest"))
        self.assertRegex(manifest["revisione"], COMMIT)

    def test_le_impronte_sono_sha256_vere(self):
        """Un segnaposto ('TODO', stringa vuota) renderebbe il controllo inutile."""
        manifest = carica()
        varianti = manifest["unet"]["varianti"]
        self.assertGreaterEqual(len(varianti), 2, "servono alternative di quant")
        for nome, variante in varianti.items():
            with self.subTest(quant=nome):
                self.assertRegex(variante["sha256"], SHA256)
                self.assertGreater(variante["byte"], 0)
        for accompagnatore in manifest["accompagnatori"]:
            with self.subTest(file=accompagnatore["file"]):
                self.assertRegex(accompagnatore["sha256"], SHA256)
                self.assertGreater(accompagnatore["byte"], 0)

    def test_il_quant_di_default_e_una_delle_varianti(self):
        manifest = carica()
        self.assertIn(manifest["unet"]["quant_default"], manifest["unet"]["varianti"])

    def test_i_file_del_manifest_sono_quelli_che_il_grafo_carica(self):
        """Il legame che tiene: se cambia un lato solo, il job fallisce a vuoto."""
        manifest = carica()
        quant = manifest["unet"]["quant_default"]
        self.assertEqual(Path(manifest["unet"]["varianti"][quant]["file"]).name,
                         MODELLO_DEFAULT["unet"])
        for ruolo in ("clip", "vae"):
            accompagnatore = next(a for a in manifest["accompagnatori"]
                                 if a["ruolo"] == ruolo)
            with self.subTest(ruolo=ruolo):
                self.assertEqual(Path(accompagnatore["file"]).name,
                                 MODELLO_DEFAULT[ruolo])

    def test_le_cartelle_di_destinazione_sono_quelle_che_comfyui_legge(self):
        manifest = carica()
        self.assertEqual(manifest["unet"]["destinazione"], DESTINAZIONI["unet"])
        ruoli = {a["ruolo"]: a["destinazione"] for a in manifest["accompagnatori"]}
        self.assertEqual(ruoli, {"clip": DESTINAZIONI["clip"], "vae": DESTINAZIONI["vae"]})

    def test_il_diffusion_e_la_variante_non_censurata(self):
        """E' la scelta dichiarata: pesi `-UC`, senza safety checker."""
        manifest = carica()
        self.assertIn("-UC", manifest["unet"]["varianti"][manifest["unet"]["quant_default"]]["file"])

    def test_la_licenza_e_dichiarata(self):
        """I pesi non sono distribuiti con il progetto: la licenza va detta."""
        manifest = carica()
        self.assertIn("licenza", manifest)
        self.assertTrue(manifest["licenza"].strip())


if __name__ == "__main__":
    unittest.main()

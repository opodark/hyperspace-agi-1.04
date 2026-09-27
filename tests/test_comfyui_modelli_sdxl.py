# SPDX-License-Identifier: Apache-2.0
"""Il checkpoint RealVisXL (SDXL NSFW): manifest e grafo devono dire la stessa cosa.

Stessa disciplina di test_comfyui_modelli.py, ma per il modello del Mac: il nome
del checkpoint e' una STRINGA in due posti — il manifest `modelli-sdxl.json` che
`install-model.sh` scarica e `MODELLO_SDXL` nel grafo che il ponte manda a
ComfyUI. L'unico modo di non scoprire un disallineamento *durante una
generazione* (CheckpointLoaderSimple che non trova il file) e' confrontarli qui.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import MODELLO_SDXL  # noqa: E402

MANIFEST = ROOT / "integrations" / "comfyui" / "modelli-sdxl.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def carica() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class ManifestSdxlTests(unittest.TestCase):
    def test_il_manifest_e_pinnato_a_un_commit(self):
        manifest = carica()
        self.assertNotIn(manifest["revisione"], ("main", "master", "latest"))
        self.assertRegex(manifest["revisione"], COMMIT)

    def test_l_impronta_e_uno_sha256_vero_e_la_dimensione_e_positiva(self):
        manifest = carica()
        self.assertRegex(manifest["checkpoint"]["sha256"], SHA256)
        self.assertGreater(manifest["checkpoint"]["byte"], 0)

    def test_il_file_del_manifest_e_quello_che_il_grafo_carica(self):
        manifest = carica()
        self.assertEqual(Path(manifest["checkpoint"]["file"]).name,
                         MODELLO_SDXL["ckpt"])

    def test_la_destinazione_e_la_cartella_checkpoint(self):
        manifest = carica()
        self.assertEqual(manifest["checkpoint"]["destinazione"], "checkpoints")

    def test_la_licenza_e_dichiarata(self):
        manifest = carica()
        self.assertIn("licenza", manifest)
        self.assertTrue(manifest["licenza"].strip())


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
"""L'ingranditore del fix dei ritratti di Anna: manifest e grafo.

Stessa disciplina di `test_comfyui_modelli_sd15.py`, con un posto in meno: il nome
del file sta nel manifest `modelli-upscaler.json` che `install-model.sh` scarica e
in `FIX_UPSCALER` nel grafo (`shared/image_jobs.py`), che e' quello che finisce nel
nodo `UpscaleModelLoader` del secondo passaggio.

Un disallineamento non si vede generando: il nodo non trova il modello e il fix
fallisce dentro ComfyUI — l'errore che il preflight del ponte (`_file_attesi` in
`integrations/comfyui/comfy_bridge.py`) esiste per anticipare. Confrontare qui i due
nomi costa un test.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import FIX_UPSCALER  # noqa: E402

MANIFEST = ROOT / "integrations" / "comfyui" / "modelli-upscaler.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def carica(percorso: Path = MANIFEST) -> dict:
    return json.loads(percorso.read_text(encoding="utf-8"))


class ManifestUpscalerTests(unittest.TestCase):
    def test_la_sorgente_e_un_url_https(self):
        """Il pin e' l'impronta, ma l'origine va dichiarata: quei pesi vengono da
        fuori, e su questa macchina li carica ComfyUI."""
        manifest = carica()
        self.assertTrue(manifest["url"].startswith("https://"))
        self.assertTrue(manifest["sorgente"].strip())
        self.assertTrue(manifest["pagina"].startswith("https://"))

    def test_l_impronta_e_uno_sha256_vero_e_la_dimensione_e_positiva(self):
        manifest = carica()
        self.assertRegex(manifest["checkpoint"]["sha256"], SHA256)
        self.assertGreater(manifest["checkpoint"]["byte"], 0)

    def test_il_file_del_manifest_e_quello_che_il_grafo_carica(self):
        self.assertEqual(Path(carica()["checkpoint"]["file"]).name, FIX_UPSCALER)

    def test_la_destinazione_e_la_cartella_degli_ingranditori(self):
        """`upscale_models` e' il ramo di ComfyUI che `UpscaleModelLoader` legge:
        nella cartella dei checkpoint lo stesso file non comparirebbe nell'elenco.
        """
        self.assertEqual(carica()["checkpoint"]["destinazione"], "upscale_models")

    def test_la_licenza_e_dichiarata(self):
        manifest = carica()
        self.assertTrue(manifest["licenza"].strip())
        self.assertTrue(manifest["nota_licenza"].strip())


if __name__ == "__main__":
    unittest.main()

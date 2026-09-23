# SPDX-License-Identifier: Apache-2.0
"""Le variabili con cui Open WebUI disegna: devono seguire il grafo del repo.

Il difetto che questi test impediscono: due copie dello stesso grafo — una in
`shared/image_jobs.py` (che il ponte esegue) e una incollata in `.env` (che la
WebUI manda) — che divergono in silenzio. Il sintomo arriverebbe **dopo minuti di
sampling** (è la lezione di `tests/test_comfyui_modelli.py`), e senza dire perché.

Quindi: la variabile che la WebUI legge è il grafo del repo, non una sua copia; la
mappa dei campi punta a nodi che esistono e a input che quei nodi hanno davvero; e
le due assenze volute (`negative_prompt`, `model`) restano tali.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import MODELLO_DEFAULT, workflow  # noqa: E402

SCRIPT = ROOT / "scripts" / "webui_image_env.py"


def carica_script():
    spec = importlib.util.spec_from_file_location("webui_image_env_sotto_test", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


WE = carica_script()


class VariabiliTests(unittest.TestCase):

    def setUp(self):
        self.valori = WE.variabili()

    def test_il_motore_e_comfyui_e_la_base_e_il_gateway(self):
        self.assertEqual(self.valori["ENABLE_IMAGE_GENERATION"], "true")
        self.assertEqual(self.valori["IMAGE_GENERATION_ENGINE"], "comfyui")
        self.assertEqual(self.valori["COMFYUI_BASE_URL"], WE.BASE_URL_DEFAULT)
        self.assertIn("8189", self.valori["COMFYUI_BASE_URL"],
                      "8189 è il gateway, non 8188 di ComfyUI: è lì che si libera la scheda")

    def test_le_chiavi_del_motore_ci_sono_tutte(self):
        for chiave in WE.CHIAVI:
            with self.subTest(chiave=chiave):
                self.assertIn(chiave, self.valori)

    def test_il_grafo_e_quello_del_repo(self):
        """Non una copia: l'oggetto che il ponte manda a ComfyUI."""
        atteso = workflow(WE.job_default(768, 768, 25))
        self.assertEqual(json.loads(self.valori["COMFYUI_WORKFLOW"]), atteso)

    def test_il_grafo_sta_su_una_riga(self):
        """`.env` e `env_file` non leggono un JSON su più righe."""
        self.assertNotIn("\n", self.valori["COMFYUI_WORKFLOW"])
        self.assertNotIn("\n", self.valori["COMFYUI_WORKFLOW_NODES"])

    def test_la_mappa_punta_a_nodi_e_input_veri(self):
        grafo = json.loads(self.valori["COMFYUI_WORKFLOW"])
        for nodo in json.loads(self.valori["COMFYUI_WORKFLOW_NODES"]):
            for node_id in nodo["node_ids"]:
                with self.subTest(nodo=nodo, node_id=node_id):
                    self.assertIn(node_id, grafo)
                    # La chiave deve essere un input che il nodo ha davvero, o
                    # ComfyUI rifiuta il grafo a generazione avviata.
                    if nodo["type"] in ("prompt", "steps", "seed"):
                        self.assertIn(nodo["key"],
                                      ("prompt", "negative_prompt", "resolution", "steps",
                                       "seed", "text"))

    def test_il_prompt_va_dove_lo_aspetta_il_nodo_di_qwen(self):
        nodi = {n["type"]: n for n in json.loads(self.valori["COMFYUI_WORKFLOW_NODES"])}
        grafo = json.loads(self.valori["COMFYUI_WORKFLOW"])
        self.assertEqual(grafo[nodi["prompt"]["node_ids"][0]]["class_type"],
                         "TextEncodeQwenImage21")
        self.assertEqual(nodi["prompt"]["key"], "prompt")
        self.assertEqual(grafo[nodi["width"]["node_ids"][0]]["class_type"],
                         "EmptyLatentImage")
        self.assertEqual(grafo[nodi["steps"]["node_ids"][0]]["class_type"], "KSampler")

    def test_le_due_assenza_sono_volute(self):
        """`negative_prompt` sarebbe `null` (Open WebUI lo manda solo se scritto) e
        `model` metterebbe un modello di *chat* dentro `UnetLoaderGGUF`."""
        tipi = {nodo["type"] for nodo in json.loads(self.valori["COMFYUI_WORKFLOW_NODES"])}
        self.assertNotIn("negative_prompt", tipi)
        self.assertNotIn("model", tipi)

    def test_il_diffusion_e_quello_del_manifest(self):
        self.assertEqual(self.valori["IMAGE_GENERATION_MODEL"], MODELLO_DEFAULT["unet"])
        self.assertEqual(MODELLO_DEFAULT["unet"], "qwen-image-2.1-UC-Q5_K_M.gguf")


class MisuraTests(unittest.TestCase):

    def test_legge_wxh(self):
        self.assertEqual(WE.misura("768x768"), (768, 768))
        self.assertEqual(WE.misura("1024x768"), (1024, 768))
        self.assertEqual(WE.misura(" 512X512 "), (512, 512))

    def test_il_vuoto_e_il_default(self):
        self.assertEqual(WE.misura(""), (768, 768))

    def test_una_misura_storta_si_rifiuta(self):
        """Meglio un errore qui che un grafo con un `resolution` inventato."""
        for valore in ("auto", "768", "768x", "largoxalto", "768x768x1"):
            with self.subTest(valore=valore):
                with self.assertRaises(ValueError):
                    WE.misura(valore)

    def test_la_misura_entra_nel_grafo(self):
        valori = WE.variabili(size="1024x768", passi=30)
        grafo = json.loads(valori["COMFYUI_WORKFLOW"])
        self.assertEqual(valori["IMAGE_SIZE"], "1024x768")
        self.assertEqual(valori["IMAGE_STEPS"], "30")
        self.assertEqual(grafo["456"]["inputs"]["width"], 1024)
        self.assertEqual(grafo["456"]["inputs"]["height"], 768)
        self.assertEqual(grafo["458"]["inputs"]["steps"], 30)
        self.assertEqual(grafo["452"]["inputs"]["resolution"], 1024,
                         "il text encoder segue il lato lungo, come nel ponte")


class FileEnvTests(unittest.TestCase):
    """Scrivere in `.env` senza rovinare il resto: commenti, ordine, a-capo."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".env"
        self.originale = (
            "# un commento che vale\r\n"
            "MESH_BIND_IP=0.0.0.0\r\n"
            "IMAGE_SIZE=512x512\r\n"
            "# commento in mezzo\r\n"
            "CHANNEL_MODEL=qwen3.5:4b\r\n"
        )

    def test_aggiorna_solo_le_sue_chiavi(self):
        self.path.write_text(self.originale, encoding="utf-8")
        righe, a_capo = WE.leggi(self.path)
        nuove, aggiornate, aggiunte = WE.applica(righe, WE.variabili())
        WE.scrivi(self.path, nuove, a_capo)
        # Si rilegge con newline="": read_text tradurrebbe i CRLF, e il test
        # guarderebbe qualcosa di diverso dal file che sta sul disco.
        with self.path.open(encoding="utf-8", newline="") as maniglia:
            testo = maniglia.read()
        for atteso in ("# un commento che vale", "# commento in mezzo",
                       "MESH_BIND_IP=0.0.0.0", "CHANNEL_MODEL=qwen3.5:4b"):
            self.assertIn(atteso, testo)
        self.assertIn("IMAGE_SIZE=768x768", testo)
        self.assertNotIn("IMAGE_SIZE=512x512", testo)
        self.assertEqual(aggiornate, ["IMAGE_SIZE"])
        self.assertEqual(len(aggiunte), len(WE.CHIAVI) - 1)
        self.assertIn("\r\n", testo, "l'a-capo del file non si cambia")

    def test_e_idempotente(self):
        self.path.write_text(self.originale, encoding="utf-8")
        valori = WE.variabili()
        for _ in range(2):
            righe, a_capo = WE.leggi(self.path)
            nuove, _, _ = WE.applica(righe, valori)
            WE.scrivi(self.path, nuove, a_capo)
        testo = self.path.read_text(encoding="utf-8")
        self.assertEqual(testo.count("IMAGE_SIZE="), 1)
        self.assertEqual(testo.count(WE.INTESTAZIONE_ENV), 1)

    def test_una_chiave_ripetuta_non_si_duplica(self):
        nuove, _, _ = WE.applica(["IMAGE_SIZE=512x512", "IMAGE_SIZE=256x256", "ALTRO=1"],
                                 {"IMAGE_SIZE": "768x768"})
        self.assertEqual([riga for riga in nuove if riga.startswith("IMAGE_SIZE")],
                         ["IMAGE_SIZE=768x768"])
        self.assertIn("ALTRO=1", nuove)

    def test_le_differenze_si_vedono(self):
        righe, _ = WE.leggi(self.path)          # file che non esiste: nessuna riga
        self.assertEqual(len(WE._differenze(righe, WE.variabili())), len(WE.CHIAVI))
        self.path.write_text(self.originale, encoding="utf-8")
        righe, _ = WE.leggi(self.path)
        differenze = WE._differenze(righe, WE.variabili())
        self.assertIn("IMAGE_SIZE non e' quello del grafo del repo", differenze)
        nuove, _, _ = WE.applica(righe, WE.variabili())
        self.assertEqual(WE._differenze(nuove, WE.variabili()), [])


class ApiTests(unittest.TestCase):
    """I valori che vanno all'API admin: tipi veri, e un token firmato come i suoi."""

    def test_i_tipi_che_il_pannello_si_aspetta(self):
        fuori = WE.valori_per_api(WE.variabili())
        self.assertIsInstance(fuori["ENABLE_IMAGE_GENERATION"], bool)
        self.assertIsInstance(fuori["IMAGE_STEPS"], int)
        self.assertIsInstance(fuori["COMFYUI_WORKFLOW_NODES"], list)
        self.assertEqual(fuori["COMFYUI_WORKFLOW_NODES"],
                         json.loads(WE.variabili()["COMFYUI_WORKFLOW_NODES"]))
        self.assertIsInstance(fuori["COMFYUI_WORKFLOW"], str)

    def test_il_token_e_firmato_come_quello_della_webui(self):
        """La stessa forma di `create_token`: HS256 su `{"id": ..., "exp": ...}`."""
        token = WE._token_admin("utente-1", "chiave-di-prova")
        intestazione, corpo, firma = token.split(".")

        def riempi(pezzo):
            return pezzo + "=" * (-len(pezzo) % 4)

        self.assertEqual(json.loads(base64.urlsafe_b64decode(riempi(intestazione))),
                         {"alg": "HS256", "typ": "JWT"})
        self.assertEqual(json.loads(base64.urlsafe_b64decode(riempi(corpo)))["id"],
                         "utente-1")
        atteso = hmac.new(b"chiave-di-prova", f"{intestazione}.{corpo}".encode(),
                          hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(riempi(firma)), atteso)

    def test_una_chiave_diversa_non_firma(self):
        token = WE._token_admin("utente-1", "chiave-di-prova")
        firma = token.split(".")[2]

        def riempi(pezzo):
            return pezzo + "=" * (-len(pezzo) % 4)

        altra = hmac.new(b"chiave-sbagliata", token.rsplit(".", 1)[0].encode(),
                         hashlib.sha256).digest()
        self.assertNotEqual(base64.urlsafe_b64decode(riempi(firma)), altra)


if __name__ == "__main__":
    unittest.main()


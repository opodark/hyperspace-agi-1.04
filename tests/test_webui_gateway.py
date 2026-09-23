# SPDX-License-Identifier: Apache-2.0
"""Il gateway immagini di Open WebUI: due cose devono restare vere.

(1) **La scheda si libera prima del diffusion.** È l'unico motivo per cui questo
processo esiste: Open WebUI chiama ComfyUI da sé, e saltando quel gancio la contesa
con Ollama si presenta come `CUDA error: unknown error` (2026-09-22: 6170 MiB a
Ollama su 8151, 1730 liberi — il diffusion non ci stava, e ComfyUI non riparte da
solo). Quindi: sul `POST /prompt` si scarica, *e* si scarica prima di inoltrare.

(2) **Non decide niente sull'immagine.** Non tocca il prompt, non scegli il modello:
è un guardiano di memoria. La lista dei percorsi inoltrabili è corta, e un `..` non
passa.

Nessuna rete e nessun ComfyUI: le decisioni sono funzioni pure, il cablaggio si
verifica sull'albero sintattico (la tecnica di `tests/test_gpu_budget.py`).
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GATEWAY = ROOT / "integrations" / "comfyui" / "webui_gateway.py"


def carica_gateway():
    spec = importlib.util.spec_from_file_location("webui_gateway_sotto_test", GATEWAY)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


GW = carica_gateway()


class SchedaTests(unittest.TestCase):
    """Cosa si scarica, e quando."""

    def test_legge_i_modelli_dalla_ps(self):
        ps = {"models": [{"name": "qwen3.5:4b", "size_vram": 5259 * 1024 * 1024},
                         {"model": "senzanome", "size_vram": 100}]}
        self.assertEqual(GW.modelli_residenti(ps), ["qwen3.5:4b", "senzanome"])

    def test_un_modello_solo_su_cpu_non_si_scarica(self):
        """Non occupa VRAM: scaricarlo sarebbe lavoro inutile e lo ricaricherebbe."""
        ps = {"models": [{"name": "solo-cpu", "size_vram": 0},
                         {"name": "in-scheda", "size_vram": 123}]}
        self.assertEqual(GW.modelli_residenti(ps), ["in-scheda"])

    def test_senza_il_campo_vram_si_preferisce_liberare(self):
        self.assertEqual(GW.modelli_residenti({"models": [{"name": "x"}]}), ["x"])

    def test_regge_una_risposta_storta(self):
        for valore in (None, {}, {"models": None}, {"models": [None, "x", 3]}):
            with self.subTest(valore=valore):
                self.assertEqual(GW.modelli_residenti(valore), [])

    def test_di_default_si_libera_tutta_la_scheda(self):
        ps = {"models": [{"name": "a", "size_vram": 1}, {"name": "b", "size_vram": 2}]}
        self.assertEqual(GW.da_scaricare(ps, env={"CHANNEL_MODEL": "qwen3.5:4b"}),
                         ["a", "b"])

    def test_si_puo_spegnere_come_nel_control_plane(self):
        ps = {"models": [{"name": "a", "size_vram": 1}]}
        self.assertEqual(GW.da_scaricare(
            ps, env={"CHANNEL_MODEL": "qwen3.5:4b", "IMAGE_FREE_GPU": "false"}), [])

    def test_senza_modello_dichiarato_non_si_tocca_niente(self):
        ps = {"models": [{"name": "a", "size_vram": 1}]}
        self.assertEqual(GW.da_scaricare(ps, env={}), [])


class PercorsiTests(unittest.TestCase):
    """Il proxy inoltra quello che serve al motore `comfyui`, e nient'altro."""

    def test_quelli_del_motore_passano(self):
        for percorso in ("prompt", "history/abc-123", "view", "system_stats",
                         "object_info/CLIPLoader", "queue", "api/upload/image"):
            with self.subTest(percorso=percorso):
                self.assertTrue(GW.percorso_consentito(percorso))

    def test_il_resto_non_passa(self):
        for percorso in ("", "/", "bash", "users", "../prompt", "view/../../etc/passwd",
                         "prompt\\..\\..\\x", None):
            with self.subTest(percorso=percorso):
                self.assertFalse(GW.percorso_consentito(percorso))

    def test_l_url_si_compone_senza_barre_doppie(self):
        self.assertEqual(GW.url_inoltro("http://127.0.0.1:8188/", "/prompt"),
                         "http://127.0.0.1:8188/prompt")
        self.assertEqual(GW.url_inoltro("http://127.0.0.1:8188", "history/x", "a=1"),
                         "http://127.0.0.1:8188/history/x?a=1")


class IntestazioniTests(unittest.TestCase):
    """Il primo tentativo vero è fallito qui: `Illegal header value b'Bearer '`."""

    def test_il_token_vuoto_non_si_inoltra(self):
        """Open WebUI manda `Bearer ` anche senza COMFYUI_API_KEY, e httpx rifiuta
        un valore con lo spazio in coda: la richiesta non partirebbe nemmeno."""
        fuori = GW.intestazioni_inoltro({"Authorization": "Bearer ", "Content-Type":
                                         "application/json"})
        self.assertNotIn("Authorization", fuori)
        self.assertEqual(fuori["Content-Type"], "application/json")

    def test_il_token_vero_si_inoltra(self):
        fuori = GW.intestazioni_inoltro({"Authorization": "Bearer abc123"})
        self.assertEqual(fuori["Authorization"], "Bearer abc123")

    def test_gli_spazi_ai_bordi_spariscono(self):
        fuori = GW.intestazioni_inoltro({"X-Prova": "  valore  ", "Vuota": "   "})
        self.assertEqual(fuori, {"X-Prova": "valore"})

    def test_le_intestazioni_di_connessione_non_passano(self):
        fuori = GW.intestazioni_inoltro({
            "Connection": "keep-alive", "Host": "127.0.0.1:8189",
            "Content-Length": "123", "Accept-Encoding": "gzip", "User-Agent": "x"})
        self.assertEqual(fuori, {"User-Agent": "x"})

    def test_in_risposta_si_tolgono_anche_i_corpi_codificati(self):
        fuori = GW.intestazioni_inoltro({"Content-Encoding": "gzip",
                                         "Content-Length": "10",
                                         "Content-Type": "image/png"}, risposta=True)
        self.assertEqual(fuori, {"Content-Type": "image/png"})


class CablaggioTests(unittest.TestCase):
    """Dove sta il gancio, e cosa non deve mai cambiare."""

    @classmethod
    def setUpClass(cls):
        albero = ast.parse(GATEWAY.read_text(encoding="utf-8"))
        cls.funzioni = {}
        for nodo in ast.walk(albero):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cls.funzioni.setdefault(nodo.name, nodo)

    def test_la_scheda_si_libera_prima_di_inoltrare(self):
        corpo = ast.unparse(self.funzioni["inoltra"])
        self.assertIn("libera_scheda", corpo)
        self.assertLess(corpo.index("libera_scheda"), corpo.index("client.request"),
                        "prima si fa posto, poi si manda il grafo")
        self.assertIn("prompt", corpo, "il gancio è sulla rotta del lavoro")

    def test_il_websocket_e_un_tunnel_nei_due_versi(self):
        corpo = ast.unparse(self.funzioni["tunnel"])
        self.assertIn("websockets.connect", corpo)
        self.assertIn("_dal_client", corpo)
        self.assertIn("_dal_remoto", corpo)

    def test_e_una_istanza_sola(self):
        self.assertIn("SingleInstance", ast.unparse(self.funzioni["main"]))

    def test_check_non_apre_la_porta(self):
        corpo = ast.unparse(self.funzioni["main"])
        self.assertLess(corpo.index("'--check'"), corpo.index("uvicorn.run"))

    def test_il_controllo_dei_pesi_e_quello_del_ponte(self):
        """Nessuna seconda copia: i file del grafo si verificano con `_verifiche`."""
        sorgente = GATEWAY.read_text(encoding="utf-8")
        self.assertIn("from comfy_bridge import _verifiche", sorgente)
        self.assertIn("_verifiche(args.comfy,", ast.unparse(self.funzioni["main"]))


if __name__ == "__main__":
    unittest.main()


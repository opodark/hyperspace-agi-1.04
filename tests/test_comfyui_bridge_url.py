# SPDX-License-Identifier: Apache-2.0
"""Un `COMFY_URL` che nomina il gateway non è «un'altra porta di ComfyUI»: è un altro processo.

Il buco chiuso qui (2026-10-01): il ponte ha passato la notte a rinviare ogni job
— «ComfyUI non raggiungibile (HTTP 0)» in ciclo — con ComfyUI acceso e vivo su
8188. Nel dominio `launchd` era rimasto `COMFY_URL=http://127.0.0.1:8189`, la
porta di `webui_gateway.py`, da un'epoca in cui quel proxy era in ascolto. Il
sintomo in coda è indistinguibile da «non c'è lavoro», e l'unica prova stava
fuori dal repo (`launchctl getenv COMFY_URL`), dove nessuno la cerca.

La guardia è una funzione sola e sta in `comfy_bridge`, perché è il ponte a dover
rifiutare di essere ingannato: non basta il lanciatore, che eredita la variabile
dall'ambiente — e sotto `launchd` quell'ambiente non è quello della shell.
Si corregge SOLO la porta del gateway: un ComfyUI su un'altra porta o su un'altra
macchina resta una scelta di chi l'ha scritta, e non si tocca in silenzio.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.comfyui import comfy_bridge as ponte  # noqa: E402
from integrations.comfyui.comfy_bridge import (  # noqa: E402
    COMFY_DEFAULT, PORTA_GATEWAY_WEBUI, comfy_url_utilizzabile)


def _gateway():
    """Il gateway caricato per percorso, come in `tests/test_webui_gateway.py`."""
    percorso = ROOT / "integrations" / "comfyui" / "webui_gateway.py"
    spec = importlib.util.spec_from_file_location("webui_gateway_porta_sotto_test", percorso)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# Come nell'altro test del gateway: il modulo importa uvicorn a livello di
# import, quindi senza la dipendenza la classe salta invece di far fallire la
# run. In CI uvicorn e' installato e questi test girano per davvero.
try:
    GW = _gateway()
    PERCHE_SALTATO = ""
except ModuleNotFoundError as manca:
    GW = None
    PERCHE_SALTATO = f"dipendenza assente ({manca.name}): il gateway la importa a import"


@unittest.skipIf(GW is None, PERCHE_SALTATO)
class PortaDelGatewayTests(unittest.TestCase):
    """Cosa si corregge, e cosa no."""

    def test_la_porta_del_gateway_diventa_la_8188(self):
        """Il caso del 2026-10-01: il residuo che nominava il gateway."""
        url, motivo = comfy_url_utilizzabile(f"http://127.0.0.1:{PORTA_GATEWAY_WEBUI}")
        self.assertEqual(url, COMFY_DEFAULT)
        self.assertIn("gateway WebUI", motivo)
        self.assertIn(str(PORTA_GATEWAY_WEBUI), motivo)

    def test_un_comfy_url_giusto_resta_com_e(self):
        """Niente da dire e niente da scrivere: il caso normale non deve parlare."""
        url, motivo = comfy_url_utilizzabile(COMFY_DEFAULT)
        self.assertEqual(url, COMFY_DEFAULT)
        self.assertEqual(motivo, "")

    def test_un_altra_porta_non_si_tocca(self):
        """Il ponte non indovina: se il gateway un giorno cambia porta, il numero
        che si corregge si cambia qui (`PORTA_GATEWAY_WEBUI`), non a caso."""
        url, motivo = comfy_url_utilizzabile("http://127.0.0.1:9999")
        self.assertEqual(url, "http://127.0.0.1:9999")
        self.assertEqual(motivo, "")

    def test_un_altra_macchina_non_si_tocca(self):
        """La 8188 su un altro host è ComfyUI su un altro host."""
        url, motivo = comfy_url_utilizzabile("http://192.168.1.50:8188")
        self.assertEqual(url, "http://192.168.1.50:8188")
        self.assertEqual(motivo, "")

    def test_un_url_malformato_non_ferma_lavvio(self):
        """Un URL scritto male è un errore di chi l'ha scritto: il ponte non deve
        morire all'avvio per non averlo capito (e non deve nemmeno «aggiustarlo»)."""
        url, motivo = comfy_url_utilizzabile("http://127.0.0.1:non-una-porta")
        self.assertEqual(url, "http://127.0.0.1:non-una-porta")
        self.assertEqual(motivo, "")

    def test_il_gateway_ascolta_sulla_porta_che_si_corregge(self):
        """I due numeri sono lo stesso numero: se il gateway si sposta, la guardia
        del ponte deve spostarsi con lui (o smetterebbe di riconoscerlo)."""
        self.assertEqual(_gateway().PORT_DEFAULT, PORTA_GATEWAY_WEBUI)


class LaGuardiaEApplicataTests(unittest.TestCase):
    """La funzione pura non serve a niente se `main` non la chiama."""

    def test_il_ponte_verifica_la_8188_anche_con_un_url_del_gateway(self):
        """`--comfy http://127.0.0.1:8189 --check`: le verifiche partono per 8188.

        Si guarda l'URL che arriva a `_verifiche` — è il primo punto in cui il
        ponte parla davvero a ComfyUI: se lì c'è ancora 8189, il residuo d'ambiente
        ha vinto di nuovo.
        """
        visti = []

        def finta_verifica(comfy_url, output="", modello=""):
            visti.append(comfy_url)
            return []

        with mock.patch.object(ponte, "_verifiche", finta_verifica), \
                mock.patch.object(ponte, "_richiesta", lambda *a, **k: (200, {})):
            codice = ponte.main(["--check", "--comfy", f"http://127.0.0.1:{PORTA_GATEWAY_WEBUI}"])
        self.assertEqual(visti, [COMFY_DEFAULT])
        self.assertEqual(codice, 0)


class ScriptDiAvvioTests(unittest.TestCase):
    """Il ramo «doppio clic dal Desktop»: lì l'ambiente è quello della shell.

    Il ponte ha la sua guardia, ma il lanciatore eredita `COMFY_URL` da chi lo
    chiama — e sotto `launchd` quella variabile è del dominio, non della shell:
    è esattamente il caso del 2026-10-01. Qui si esegue lo script vero, in un repo
    finto con un ponte che stampa l'ambiente ricevuto.
    """

    def _esegui(self, comfy_url: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as cartella:
            radice = Path(cartella)
            script = radice / "integrations" / "comfyui"
            script.mkdir(parents=True)
            shutil.copy(ROOT / "integrations" / "comfyui" / "start-bridge.sh",
                        script / "start-bridge.sh")
            (script / "comfy_bridge.py").write_text(
                'import os\nprint("URL_RICEVUTO=" + os.environ.get("COMFY_URL", ""))\n',
                encoding="utf-8")
            (radice / ".env").write_text("CHANNEL_CLIENTS=comfy=token-di-prova\n",
                                         encoding="utf-8")
            # Lo script preferisce `.venv/bin/python`: l'interprete dei test, così
            # non si dipende dal `python3` che capita nel PATH di chi li lancia.
            bin_dir = radice / ".venv" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "python").symlink_to(sys.executable)
            return subprocess.run(
                ["bash", str(script / "start-bridge.sh")], cwd=str(radice),
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "HYPERSPACE_REPO": str(radice), "COMFY_URL": comfy_url})

    def test_lo_script_raddrizza_un_comfy_url_del_gateway(self):
        esito = self._esegui(f"http://127.0.0.1:{PORTA_GATEWAY_WEBUI}")
        self.assertEqual(esito.returncode, 0, esito.stderr)
        self.assertIn(f"URL_RICEVUTO={COMFY_DEFAULT}", esito.stdout)
        self.assertIn("gateway WebUI", esito.stderr)

    def test_lo_script_lascia_stare_un_comfy_url_giusto(self):
        esito = self._esegui(COMFY_DEFAULT)
        self.assertEqual(esito.returncode, 0, esito.stderr)
        self.assertIn(f"URL_RICEVUTO={COMFY_DEFAULT}", esito.stdout)
        self.assertNotIn("gateway WebUI", esito.stderr)

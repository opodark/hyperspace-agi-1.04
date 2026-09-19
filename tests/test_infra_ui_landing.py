# SPDX-License-Identifier: Apache-2.0
"""La home del bridge (landing.html + /api/services): contratto fra i due lati.

Nessuno di questi controlli e' teorico: sono i modi in cui questa pagina si rompe
in silenzio.
  - se il Dockerfile smette di copiare landing.html, l'immagine risponde 404 su /
    mentre in locale funziona (il file c'e' nella repo);
  - se un campo dell'API viene rinominato, la pagina mostra "—" per sempre:
    nessun errore, nessun log, solo card vuote;
  - se un servizio non ha la sua icona, la card esce senza icona e sembra un bug
    di stile invece di una chiave mancante;
  - se il container non ha host.docker.internal, la card di minimesh dice "non
    raggiungibile" per un motivo che non c'entra col servizio.

Testi statici sul sorgente: PyYAML non e' fra le dipendenze della suite.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SERVER = (ROOT / "infra-ui" / "server.py").read_text(encoding="utf-8")
LANDING = (ROOT / "infra-ui" / "landing.html").read_text(encoding="utf-8")


def _tree():
    return ast.parse(SERVER)


def _services_literal():
    for node in _tree().body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SERVICES":
            return ast.literal_eval(node.value)
    raise AssertionError("SERVICES non trovato in infra-ui/server.py")


def _fields_from_api():
    """Tutti i campi che /api/services restituisce: le chiavi letterali del dict
    PIU' quelle che arrivano da `**status` (lo stato in _service_status). Il
    contratto con la pagina e' l'unione: se `latency_ms` sparisse
    dall'inizializzatore di _service_status, la card mostrerebbe "—" per sempre
    senza nessun errore da nessuna parte."""
    keys = set()
    for node in ast.walk(_tree()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "services_status":
            for d in [n for n in ast.walk(node) if isinstance(n, ast.Dict)]:
                keys |= {k.value for k in d.keys if isinstance(k, ast.Constant)}
    for node in _tree().body:
        # `_service_status: dict[...] = {...}` e' un AnnAssign, non un Assign, e
        # in un DictComp il valore sta in .value (non .element, che e' dei set).
        name = ""
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", ""):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and getattr(node.target, "id", ""):
            name = node.target.id
        if name != "_service_status":
            continue
        value = node.value
        if isinstance(value, ast.DictComp):
            value = value.value
        if isinstance(value, ast.Dict):
            keys |= {k.value for k in value.keys if isinstance(k, ast.Constant)}
    if not keys:
        raise AssertionError("services_status non trovato in infra-ui/server.py")
    return keys


class LandingContractTests(unittest.TestCase):
    def test_home_e_landing_e_la_dashboard_resta_su_dashboard(self):
        routes = {}
        for node in _tree().body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "get" and dec.args):
                    routes[dec.args[0].value] = ast.unparse(node)
        self.assertIn("LANDING_HTML", routes["/"], "la home deve servire la landing")
        for path in ("/dashboard", "/dashboard.html"):
            self.assertIn("DASHBOARD_HTML", routes[path], f"{path} deve servire la dashboard 3D")

    def test_i_campi_letti_dalla_pagina_esistono_nell_api(self):
        letti = set(re.findall(r"\bs\.([a-z_]+)", LANDING))
        self.assertTrue(letti, "la pagina non legge nessun campo: regex da aggiornare?")
        mancanti = letti - _fields_from_api()
        self.assertFalse(mancanti, f"landing.html legge campi che /api/services non manda: {sorted(mancanti)}")

    def test_ogni_servizio_ha_la_sua_icona(self):
        blocco = re.search(r"const ICONS = \{(.*?)\n\};", LANDING, re.S)
        self.assertIsNotNone(blocco, "mappa ICONS non trovata in landing.html")
        icone = set(re.findall(r"^\s*(\w+):", blocco.group(1), re.M))
        nomi = {svc["name"] for svc in _services_literal()}
        self.assertFalse(nomi - icone, f"servizi senza icona in landing.html: {sorted(nomi - icone)}")

    def test_ogni_servizio_dichiara_dove_si_apre(self):
        """`internal_url` serve al container, `public_port`+`path` al browser:
        se manca il secondo, la card non ha un link da mostrare."""
        for svc in _services_literal():
            with self.subTest(servizio=svc.get("name")):
                self.assertTrue(svc.get("internal_url", "").startswith("http"))
                self.assertIsInstance(svc.get("public_port"), int)
                self.assertTrue(str(svc.get("path", "")).startswith("/"))

    def test_il_dockerfile_copia_la_landing(self):
        dockerfile = (ROOT / "infra-ui" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY landing.html .", dockerfile,
                      "senza questa riga l'immagine risponde 404 su / pur avendo il file nella repo")

    def test_il_bridge_proba_i_servizi(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        blocco = re.search(r"(?ms)^  bridge:\n.*?(?=^  \S|\Z)", compose)
        self.assertIsNotNone(blocco, "servizio bridge non trovato in docker-compose.yml")
        sezione = blocco.group(0)
        self.assertIn("SERVICES_POLL_INTERVAL", sezione)
        self.assertIn("host.docker.internal:host-gateway", sezione,
                      "senza extra_hosts la card dei servizi fuori compose dice "
                      "'non raggiungibile' per il motivo sbagliato")


if __name__ == "__main__":
    unittest.main()

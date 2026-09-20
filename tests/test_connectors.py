# SPDX-License-Identifier: Apache-2.0
"""Contratto dei connettori: scoperta, gating, diagnostica, catalogo dei tool.

Il connettore è l'unico punto in cui una credenziale diventa una capacità
eseguibile dal modello: se la scoperta non lo carica, o /connectors non sa dire
PERCHÉ è spento, il sintomo è "i tool non ci sono" senza una causa — ed era
esattamente lo stato prima di questi test.

Nessuna chiamata di rete: le librerie O365/google sono importate lazily dai
connettori, quindi la suite gira con le sole dipendenze della CI.
"""
import ast
import contextlib
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))
sys.path.insert(0, str(ROOT))

from connectors.github import GitHubConnector            # noqa: E402
from connectors.google import GoogleWorkspaceConnector   # noqa: E402
from connectors.manager import ConnectorManager          # noqa: E402
from connectors.office365 import Office365Connector      # noqa: E402
from shared.connector_policy import ConnectorPolicy      # noqa: E402

SOURCE = ROOT / "control-plane" / "main.py"

EXPECTED_CONNECTORS = {"github": GitHubConnector,
                       "google": GoogleWorkspaceConnector,
                       "office365": Office365Connector}

# Chiavi che decidono se un connettore è attivo e cosa può scrivere. I test le
# azzerano per non dipendere dall'ambiente della macchina che lancia la suite:
# un GITHUB_TOKEN vero nel .env di sviluppo cambierebbe l'esito.
CONNECTOR_KEYS = ("GITHUB_TOKEN", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "MS_TENANT_ID",
                  "GOOGLE_CREDENTIALS_JSON", "GOOGLE_DELEGATE_EMAIL",
                  "CONNECTOR_GITHUB_ENABLED", "CONNECTOR_OFFICE365_ENABLED",
                  "CONNECTOR_GOOGLE_ENABLED", "CONNECTOR_READ_ONLY",
                  "CONNECTOR_WRITE_TOOLS")


@contextlib.contextmanager
def connector_env(**overrides):
    """Ambiente dei connettori controllato: nessuna credenziale per default."""
    saved = {k: os.environ.pop(k, None) for k in CONNECTOR_KEYS}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key in CONNECTOR_KEYS:
            os.environ.pop(key, None)
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def _module_assignments():
    """Assegnazioni di primo livello in main.py (per i test di cablaggio)."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
    return tree, out


class DiscoveryTests(unittest.TestCase):
    def test_trova_tutti_i_connettori_conosciuti(self):
        with connector_env():
            manager = ConnectorManager()
        found = {c["name"] for c in manager.describe()["disabled"]}
        self.assertEqual(found, set(EXPECTED_CONNECTORS))

    def test_ogni_classe_dichiara_i_propri_requisiti(self):
        for name, cls in EXPECTED_CONNECTORS.items():
            with self.subTest(connector=name):
                self.assertIsInstance(cls.REQUIRED_ENV, tuple)
                self.assertTrue(all(isinstance(k, str) and k for k in cls.REQUIRED_ENV))
                self.assertTrue(cls.REQUIRED_ENV,
                                f"{name} non dichiara REQUIRED_ENV: /connectors non "
                                f"potrebbe spiegare perché è spento")
                self.assertFalse(cls().is_available())

    def test_la_sottoclasse_viene_istanziata_dal_manager(self):
        with connector_env(GITHUB_TOKEN="fake"):
            manager = ConnectorManager()
        self.assertIsInstance(manager.connectors[0], GitHubConnector)


class GatingTests(unittest.TestCase):
    def test_credenziali_presenti_abilitano_il_connettore(self):
        with connector_env(GITHUB_TOKEN="fake"):
            manager = ConnectorManager()
        status = manager.describe()
        self.assertEqual([c["name"] for c in status["connectors"]], ["github"])
        # `tools` è quello che il connettore PUBBLICA; `tool_count` è quello che
        # la policy ESPONE: con il default read-only differiscono, ed è il punto.
        self.assertEqual(len(status["connectors"][0]["tools"]), 5)
        self.assertIn("github_create_issue", status["connectors"][0]["tools"])
        self.assertEqual(status["connectors"][0]["write_tools_exposed"], [])
        self.assertEqual(sorted(status["connectors"][0]["write_tools_blocked"]),
                         ["github_add_comment", "github_create_issue"])
        self.assertEqual(status["tool_count"], 3)
        self.assertTrue(status["policy"]["read_only"])

    def test_tool_loop_read_only_non_espone_le_scritture(self):
        with connector_env(GITHUB_TOKEN="fake"):
            manager = ConnectorManager()
            nomi = [t["function"]["name"] for t in manager.get_all_tools()]
        self.assertIn("github_get_repo", nomi)
        self.assertNotIn("github_create_issue", nomi)
        self.assertNotIn("github_add_comment", nomi)

    def test_allowlist_esplicita_espone_solo_il_tool_indicato(self):
        with connector_env(GITHUB_TOKEN="fake", CONNECTOR_READ_ONLY="false",
                           CONNECTOR_WRITE_TOOLS="github=github_create_issue"):
            manager = ConnectorManager()
            nomi = [t["function"]["name"] for t in manager.get_all_tools()]
            status = manager.describe()
        self.assertIn("github_create_issue", nomi)
        self.assertNotIn("github_add_comment", nomi)
        self.assertEqual(status["connectors"][0]["write_tools_exposed"], ["github_create_issue"])
        self.assertEqual(status["policy"]["write_allowlist"],
                         {"github": ["github_create_issue"]})
        self.assertEqual(status["tool_count"], 4)

    def test_eseguire_una_scrittura_bloccata_non_esegue_nulla(self):
        """Il filtro del catalogo non basta: un client può chiamare a memoria."""
        with connector_env(GITHUB_TOKEN="fake"):
            manager = ConnectorManager()
            esito = manager.execute("github_create_issue", {"repo": "x/y", "title": "t"})
        self.assertIn("bloccato dalla policy", esito)
        self.assertIn("github_create_issue", esito)

    def test_allowlist_malformata_e_segnalata_nei_problemi(self):
        with connector_env(GITHUB_TOKEN="fake", CONNECTOR_READ_ONLY="false",
                           CONNECTOR_WRITE_TOOLS="gitlab=*"):
            manager = ConnectorManager()
        self.assertTrue(any("inesistente" in p for p in manager.describe()["problems"]))

    def test_reload_rilegge_anche_la_policy(self):
        with connector_env(GITHUB_TOKEN="fake"):
            manager = ConnectorManager()
            self.assertEqual(len(manager.get_all_tools()), 3)
            os.environ["CONNECTOR_READ_ONLY"] = "false"
            os.environ["CONNECTOR_WRITE_TOOLS"] = "github=*"
            status = manager.reload()
            self.assertEqual(status["connectors"][0]["write_tools_exposed"],
                             ["github_create_issue", "github_add_comment"])
            self.assertEqual(len(manager.get_all_tools()), 5)

    def test_una_sola_credenziale_non_basta(self):
        with connector_env(MS_CLIENT_ID="client"):
            manager = ConnectorManager()
        disabled = {c["name"]: c for c in manager.describe()["disabled"]}
        self.assertEqual(disabled["office365"]["missing_env"], ["MS_CLIENT_SECRET"])
        self.assertIn("MS_CLIENT_SECRET", disabled["office365"]["reason"])

    def test_override_env_spegne_anche_con_le_credenziali(self):
        with connector_env(GITHUB_TOKEN="fake", CONNECTOR_GITHUB_ENABLED="false"):
            manager = ConnectorManager()
        status = manager.describe()
        self.assertEqual(status["connectors"], [])
        self.assertIn("CONNECTOR_GITHUB_ENABLED=false", status["disabled"][0]["reason"])

    def test_missing_env_riporta_solo_nomi_mai_valori(self):
        segreto = "SUPER_SECRET_VALUE_0123456789"
        with connector_env(MS_CLIENT_ID=segreto, MS_CLIENT_SECRET=""):
            mancanti = Office365Connector().missing_env()
        self.assertEqual(mancanti, ["MS_CLIENT_SECRET"])
        self.assertNotIn(segreto, json.dumps(mancanti))

    def test_reload_riflette_un_cambio_di_ambiente(self):
        with connector_env():
            manager = ConnectorManager()
            self.assertEqual(manager.describe()["connectors"], [])
            os.environ["GITHUB_TOKEN"] = "fake"
            status = manager.reload()
            self.assertEqual([c["name"] for c in status["connectors"]], ["github"])
            del os.environ["GITHUB_TOKEN"]
            os.environ["CONNECTOR_GITHUB_ENABLED"] = "false"
            self.assertEqual(manager.reload()["connectors"], [])


class DiagnosticsTests(unittest.TestCase):
    def test_describe_non_contiene_mai_segreti(self):
        segreto = "SECRET_TOKEN_VALUE_FOR_TEST_0123456789"
        with connector_env(GITHUB_TOKEN=segreto):
            manager = ConnectorManager()
        blob = json.dumps(manager.describe())
        self.assertNotIn(segreto, blob)
        self.assertNotIn(segreto[:12], blob)
        self.assertIn("github", blob)

    def test_describe_e_stabile_e_senza_problemi(self):
        with connector_env(GITHUB_TOKEN="fake"):
            manager = ConnectorManager()
        self.assertEqual(manager.describe(), manager.describe())
        self.assertEqual(manager.describe()["problems"], [])

    def test_execute_di_un_tool_ignoto_non_esplode(self):
        with connector_env(GITHUB_TOKEN="fake"):
            manager = ConnectorManager()
        self.assertIn("non gestito", manager.execute("non_esiste", {}))

    def test_un_connettore_non_rivendica_tool_di_un_altro(self):
        self.assertIsNone(GitHubConnector().execute("o365_read_emails", {}))
        self.assertIsNone(Office365Connector().execute("google_read_emails", {}))
        self.assertIsNone(GitHubConnector().execute("web_search", {}))


class ToolSchemaTests(unittest.TestCase):
    """Credenziali finte: si costruisce il catalogo, senza mai chiamare l'API."""

    FAKE = {"GITHUB_TOKEN": "fake", "MS_CLIENT_ID": "fake", "MS_CLIENT_SECRET": "fake",
            "GOOGLE_CREDENTIALS_JSON": "{}"}

    def _manager(self):
        with connector_env(**self.FAKE):
            manager = ConnectorManager()
        self.assertEqual(len(manager.connectors), len(EXPECTED_CONNECTORS))
        return manager

    def _manager_all_tools(self):
        """Manager con policy permissiva: serve il catalogo INTERO, non quello
        esposto di default (read-only)."""
        policy = ConnectorPolicy(
            {name: None for name in EXPECTED_CONNECTORS}, read_only=False)
        with connector_env(**self.FAKE):
            return ConnectorManager(policy=policy)

    def test_ogni_tool_e_benformato(self):
        for tool in self._manager_all_tools().get_all_tools():
            with self.subTest(tool=(tool.get("function") or {}).get("name")):
                self.assertEqual(tool.get("type"), "function")
                fn = tool["function"]
                self.assertTrue(fn.get("name"))
                self.assertTrue(fn.get("description"))
                self.assertEqual(fn["parameters"].get("type"), "object")

    def test_classificazione_read_write_completa_e_disgiunta(self):
        """Ogni tool pubblicato deve essere classificato: è il presupposto della
        policy. Un tool dimenticato tiene il connettore fuori dal catalogo
        (fail-closed) invece di essere esposto come se fosse una lettura."""
        for conn in self._manager_all_tools().connectors:
            with self.subTest(connector=conn.name):
                published = {t["function"]["name"] for t in conn.get_tools()}
                read = set(conn.READ_TOOLS)
                write = set(conn.WRITE_TOOLS)
                self.assertEqual(read & write, set())
                self.assertEqual(read | write, published)
                self.assertEqual(conn.classification_problems(published), [])

    def test_una_classificazione_incompleta_esclude_il_connettore(self):
        class Rotto(GitHubConnector):
            name = "github"
            READ_TOOLS = GitHubConnector.READ_TOOLS
            WRITE_TOOLS = ()          # una scrittura non dichiarata

        conn = Rotto()
        published = [t["function"]["name"] for t in conn.get_tools()]
        problems = conn.classification_problems(published)
        self.assertTrue(any("non classificati" in p for p in problems))

    def test_i_nomi_dei_tool_sono_univoci_in_tutto_il_catalogo(self):
        # Il catalogo è nativi + connettori: un nome duplicato verrebbe
        # risolto dal primo che risponde, in silenzio.
        _, assignments = _module_assignments()
        nativi = [t["function"]["name"]
                  for t in ast.literal_eval(assignments["_NATIVE_TOOLS"])]
        connettori = [t["function"]["name"]
                      for t in self._manager_all_tools().get_all_tools()]
        nomi = nativi + connettori
        self.assertEqual(sorted(nomi), sorted(set(nomi)), f"nomi duplicati: {nomi}")

    def test_ogni_tool_ha_il_prefisso_del_suo_connettore(self):
        prefissi = {"github": "github_", "office365": "o365_", "google": "google_"}
        for conn in self._manager().connectors:
            with self.subTest(connector=conn.name):
                for tool in conn.get_tools():
                    self.assertTrue(tool["function"]["name"].startswith(prefissi[conn.name]))


class WiringTests(unittest.TestCase):
    """Il cablaggio in main.py: route, ricostruzione in place, reload."""

    @classmethod
    def setUpClass(cls):
        cls.tree, cls.assignments = _module_assignments()
        cls.functions = {n.name: n for n in cls.tree.body if isinstance(n, ast.FunctionDef)}

    def test_la_route_connectors_usa_describe(self):
        self.assertIn("connectors_status", self.functions)
        self.assertIn("connector_manager.describe",
                      ast.unparse(self.functions["connectors_status"]))

    def test_tools_execute_e_autenticata(self):
        """/tools/execute esegue i connettori: senza gate chi raggiunge la porta
        del CP può inviare email a nome dell'organizzazione."""
        self.assertIn("tools_execute", self.functions)
        calls = [n.func.id for n in ast.walk(self.functions["tools_execute"])
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("_network_admin_error", calls)
        # il gate deve precedere l'esecuzione: si confrontano le ISTRUZIONI
        # (non le stringhe: la docstring nomina _execute_tool_call).
        stmts = [s for s in self.functions["tools_execute"].body
                 if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        guard = next(i for i, s in enumerate(stmts) if "_network_admin_error" in ast.unparse(s))
        run = next(i for i, s in enumerate(stmts) if "_execute_tool_call" in ast.unparse(s))
        self.assertLess(guard, run)

    def test_la_sync_dei_tool_e_in_place(self):
        body = ast.unparse(self.functions["_sync_connector_tools"])
        # Una riassegnazione (BUILTIN_TOOLS = ...) orfanerebbe CODE_SANDBOX_TOOL e
        # i riferimenti già presi: deve restare una mutazione in place.
        self.assertIn("BUILTIN_TOOLS[:]", body)
        self.assertIn("_NATIVE_TOOLS", body)

    def test_il_salvataggio_env_ricarica_i_connettori(self):
        body = ast.unparse(self.functions["set_config_env"])
        self.assertIn("_CONNECTOR_ENV_KEYS", body)
        self.assertIn("_reload_connectors", body)
        self.assertLess(body.index("_CONNECTOR_ENV_KEYS"), body.index("_persist_env"))

    def test_la_sezione_env_copre_i_requisiti_dei_connettori(self):
        meta = {m["key"]: m for m in ast.literal_eval(self.assignments["_ENV_META"])}
        for cls in EXPECTED_CONNECTORS.values():
            for key in cls.REQUIRED_ENV:
                with self.subTest(key=key):
                    self.assertIn(key, meta, "un requisito non configurabile dalla tab Setup")
                    self.assertEqual(meta[key]["section"], "Connettori")

    def test_i_segreti_sono_campi_password_e_i_flag_boolean(self):
        meta = {m["key"]: m for m in ast.literal_eval(self.assignments["_ENV_META"])}
        for key in ("GITHUB_TOKEN", "MS_CLIENT_SECRET", "GOOGLE_CREDENTIALS_JSON"):
            with self.subTest(key=key):
                self.assertEqual(meta[key]["type"], "password")
        for key in ("CONNECTOR_GITHUB_ENABLED", "CONNECTOR_OFFICE365_ENABLED",
                    "CONNECTOR_GOOGLE_ENABLED", "CONNECTOR_READ_ONLY"):
            with self.subTest(key=key):
                self.assertEqual(meta[key]["type"], "bool")

    def test_la_policy_e_configurabile_dalla_tab_setup(self):
        """La policy read/write non deve essere un segreto da .env a mano: se
        non è nella tab Setup, nella pratica resta il default (che è giusto per
        la sicurezza, ma inutilizzabile per chi vuole abilitare una scrittura)."""
        meta = {m["key"]: m for m in ast.literal_eval(self.assignments["_ENV_META"])}
        self.assertEqual(meta["CONNECTOR_WRITE_TOOLS"]["type"], "str")
        self.assertEqual(meta["CONNECTOR_READ_ONLY"]["section"], "Connettori")
        self.assertEqual(meta["CONNECTOR_WRITE_TOOLS"]["section"], "Connettori")
        # il default del flag deve essere il più restrittivo
        self.assertEqual(meta["CONNECTOR_READ_ONLY"]["default"], "true")


if __name__ == "__main__":
    unittest.main()


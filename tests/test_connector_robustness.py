# SPDX-License-Identifier: Apache-2.0
"""Robustezza dei connettori: errori attribuiti, retry sensato, path portabili.

Un connettore che esplode e un tool che non esiste davano lo stesso messaggio
("non gestito da nessun connector attivo"): il sintomo era "il tool non
funziona" senza causa. Qui si fissa la distinzione, la traduzione degli errori
HTTP in azioni, e i limiti che il server GitHub applica comunque.

Nessuna chiamata di rete: le funzioni testate sono pure o accettano un finto
oggetto-risposta.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))
sys.path.insert(0, str(ROOT))

from connectors.github import (MAX_PER_PAGE, MAX_SEARCH_RESULTS, GitHubConnector,  # noqa: E402
                               _clamp, _http_error)
from connectors.google import _service_cache_key                                   # noqa: E402
from connectors.manager import (DEFAULT_COOLDOWN_S, DEFAULT_FAILURE_THRESHOLD,  # noqa: E402
                                ConnectorManager)
from connectors.office365 import DEFAULT_TOKEN_SUBDIR, _token_dir                   # noqa: E402


class FakeResponse:
    """Il minimo che _http_error legge da una risposta requests."""

    def __init__(self, status, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("corpo non JSON")
        return self._payload


class GithubErrorTests(unittest.TestCase):
    def test_401_parla_di_token_da_rigenerare(self):
        msg = _http_error(FakeResponse(401, {"message": "Bad credentials"}))
        self.assertIn("401", msg)
        self.assertIn("GITHUB_TOKEN", msg)

    def test_403_distingue_quota_esaurita_da_permessi(self):
        quota = _http_error(FakeResponse(
            403, {"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"}))
        self.assertIn("Quota API esaurita", quota)
        self.assertIn("reset alle", quota)
        permessi = _http_error(FakeResponse(403, {"message": "Resource not accessible"}))
        self.assertIn("Permessi insufficienti", permessi)
        self.assertIn("scope 'repo'", permessi)

    def test_404_menziona_repository_privato(self):
        msg = _http_error(FakeResponse(404, {"message": "Not Found"}))
        self.assertIn("404", msg)
        self.assertIn("privato", msg)

    def test_422_riporta_il_dettaglio_del_server(self):
        msg = _http_error(FakeResponse(422, {"message": "Validation Failed"}))
        self.assertIn("422", msg)
        self.assertIn("Validation Failed", msg)

    def test_status_sconosciuto_e_risposta_non_json(self):
        msg = _http_error(FakeResponse(418, None, text="I'm a teapot"))
        self.assertIn("418", msg)
        self.assertIn("teapot", msg)


class GithubLimitTests(unittest.TestCase):
    def test_i_limiti_vengono_clampati(self):
        self.assertEqual(_clamp(9999, 10, MAX_SEARCH_RESULTS), MAX_SEARCH_RESULTS)
        self.assertEqual(_clamp(0, 10, MAX_PER_PAGE), 1)
        self.assertEqual(_clamp(-5, 10, MAX_PER_PAGE), 1)

    def test_valore_non_numerico_usa_il_default(self):
        self.assertEqual(_clamp("molti", 10, MAX_PER_PAGE), 10)
        self.assertEqual(_clamp(None, 7, MAX_PER_PAGE), 7)

    def test_valore_valido_passa(self):
        self.assertEqual(_clamp("5", 10, MAX_PER_PAGE), 5)


class TokenDirTests(unittest.TestCase):
    """Il token O365 non deve più finire in /tmp: su Windows non esiste, e in un
    container ricreato non è persistente."""

    def test_default_sotto_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"DATA_DIR": tmp}, clear=False):
                os.environ.pop("O365_TOKEN_DIR", None)
                path = _token_dir()
        self.assertEqual(os.path.basename(path), DEFAULT_TOKEN_SUBDIR)

    def test_override_esplicito_vince_e_la_dir_viene_creata(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "token-cache", "o365")
            with mock.patch.dict(os.environ, {"O365_TOKEN_DIR": target}, clear=False):
                path = _token_dir()
            self.assertEqual(path, target)
            self.assertTrue(os.path.isdir(target))

    def test_nessun_path_hardcoded_in_tmp(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("O365_TOKEN_DIR", None)
            os.environ.pop("DATA_DIR", None)
            path = _token_dir()
        self.assertNotIn("/tmp", path.replace("\\", "/").split(":")[-1][:4])


class GoogleCacheKeyTests(unittest.TestCase):
    def test_credenziali_diverse_non_condividono_la_cache(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CREDENTIALS_JSON": '{"a":1}'}, clear=False):
            prima = _service_cache_key("gmail", "v1")
        with mock.patch.dict(os.environ, {"GOOGLE_CREDENTIALS_JSON": '{"a":2}'}, clear=False):
            dopo = _service_cache_key("gmail", "v1")
        self.assertNotEqual(prima, dopo)

    def test_stesse_credenziali_stessa_chiave(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CREDENTIALS_JSON": "{}",
                                          "GOOGLE_DELEGATE_EMAIL": "u@x.it"}, clear=False):
            self.assertEqual(_service_cache_key("drive", "v3"), _service_cache_key("drive", "v3"))

    def test_cambio_di_utente_impersonato_invalida(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CREDENTIALS_JSON": "{}",
                                          "GOOGLE_DELEGATE_EMAIL": "a@x.it"}, clear=False):
            prima = _service_cache_key("gmail", "v1")
        with mock.patch.dict(os.environ, {"GOOGLE_DELEGATE_EMAIL": "b@x.it"}, clear=False):
            dopo = _service_cache_key("gmail", "v1")
        self.assertNotEqual(prima, dopo)


class GithubRequestTests(unittest.TestCase):
    """`_request` non è solo un wrapper: decide COSA ritentare."""

    def _connector(self) -> GitHubConnector:
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}, clear=False):
            connector = GitHubConnector()
        connector.retry_wait_s = 0
        return connector

    def test_un_4xx_non_viene_ritentato(self):
        connector = self._connector()
        risposta = FakeResponse(401, {"message": "Bad credentials"})
        with mock.patch("connectors.github.requests.request", return_value=risposta) as chiamata:
            data, errore = connector._request("GET", "/user")
        self.assertIsNone(data)
        self.assertIn("GITHUB_TOKEN", errore)
        self.assertEqual(chiamata.call_count, 1)

    def test_un_5xx_viene_ritentato_una_volta(self):
        connector = self._connector()
        with mock.patch("connectors.github.requests.request",
                        side_effect=[FakeResponse(503, {}), FakeResponse(200, {"ok": True})]) as chiamata:
            data, errore = connector._request("GET", "/user")
        self.assertEqual(data, {"ok": True})
        self.assertIsNone(errore)
        self.assertEqual(chiamata.call_count, 2)

    def test_un_errore_di_rete_diventa_un_messaggio_leggibile(self):
        connector = self._connector()
        with mock.patch("connectors.github.requests.request",
                        side_effect=requests.ConnectionError("dns")):
            data, errore = connector._request("GET", "/user")
        self.assertIsNone(data)
        self.assertIn("rete non raggiungibile", errore)
        self.assertIn("dopo due tentativi", errore)

    def test_risposta_di_successo_non_json(self):
        connector = self._connector()
        with mock.patch("connectors.github.requests.request", return_value=FakeResponse(200, None)):
            data, errore = connector._request("GET", "/user")
        self.assertIsNone(data)
        self.assertIn("non JSON", errore)

    def test_un_post_non_viene_ritentato_per_non_duplicare(self):
        """Ritentare un POST dopo una risposta persa può creare due issue."""
        connector = self._connector()
        with mock.patch("connectors.github.requests.request",
                        side_effect=requests.ConnectionError("timeout")) as chiamata:
            data, errore = connector._request("POST", "/repos/x/y/issues", payload={})
        self.assertIsNone(data)
        self.assertEqual(chiamata.call_count, 1)
        self.assertIn("non idempotente", errore)


class ManagerErrorTests(unittest.TestCase):
    """Un connettore che esplode NON è un tool inesistente.

    Prima: `except Exception: continue` → stesso messaggio di un tool
    sconosciuto, diagnosi persa, nessuna traccia nei log.
    """

    def _manager(self, on_event=None):
        env = dict(os.environ)
        env["GITHUB_TOKEN"] = "fake"
        env.pop("CONNECTOR_READ_ONLY", None)
        env.pop("CONNECTOR_WRITE_TOOLS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            return ConnectorManager(on_event=on_event)

    @staticmethod
    def _boom(_args):
        raise RuntimeError("api non raggiungibile")

    def test_errore_attribuito_al_connettore(self):
        events = []
        manager = self._manager(on_event=lambda kind, summary, detail="": events.append((kind, summary)))
        manager.connectors[0]._search_issues = self._boom
        esito = manager.execute("github_search_issues", {"repo": "x/y", "query": "bug"})
        self.assertIn("ha fallito", esito)
        self.assertIn("github", esito)
        self.assertIn("RuntimeError", esito)
        self.assertNotIn("non gestito", esito)
        self.assertTrue(any("esecuzione di github_search_issues" in p
                            for p in manager.describe()["problems"]))
        self.assertEqual([kind for kind, _ in events], ["error"])

    def test_un_tool_inesistente_resta_distinto(self):
        manager = self._manager()
        self.assertIn("non gestito", manager.execute("non_esiste", {}))

    def test_il_connettore_rotto_non_blocca_gli_altri(self):
        manager = self._manager()
        manager.connectors[0]._get_repo = self._boom
        # un altro tool dello stesso connettore continua a funzionare (la
        # validazione degli argomenti precede qualsiasi chiamata di rete)
        self.assertIn("obbligatori", manager.execute("github_search_issues", {}))
        self.assertIn("ha fallito", manager.execute("github_get_repo", {"repo": "x/y"}))
        # un tool di un ALTRO connettore non è toccato dall'errore
        self.assertIn("non gestito", manager.execute("o365_read_emails", {}))

    def test_un_callback_di_osservabilita_rotto_non_rompe_il_tool(self):
        def raiser(*_args, **_kwargs):
            raise RuntimeError("log non disponibile")

        manager = self._manager(on_event=raiser)
        manager.connectors[0]._get_repo = self._boom
        esito = manager.execute("github_get_repo", {"repo": "x/y"})
        self.assertIn("ha fallito", esito)

    def test_i_problemi_runtime_non_crescono_all_infinito(self):
        manager = self._manager()
        manager.connectors[0]._get_repo = self._boom
        for _ in range(30):
            manager.execute("github_get_repo", {"repo": "x/y"})
        problems = manager.describe()["problems"]
        self.assertLessEqual(len(problems), ConnectorManager._MAX_PROBLEMS)
        self.assertTrue(problems)


class BreakerTests(unittest.TestCase):
    """Un connettore giù non deve costare il timeout a OGNI chiamata."""

    def _manager(self, threshold=2, cooldown=30.0):
        env = dict(os.environ)
        env["GITHUB_TOKEN"] = "fake"
        with mock.patch.dict(os.environ, env, clear=True):
            manager = ConnectorManager()
        manager.failure_threshold = threshold
        manager.cooldown_s = cooldown
        return manager

    @staticmethod
    def _rompi(manager):
        chiamate = []

        def boom(_args):
            chiamate.append(1)
            raise RuntimeError("servizio giù")

        manager.connectors[0]._get_repo = boom
        return chiamate

    def test_oltre_la_soglia_il_connettore_non_viene_piu_chiamato(self):
        manager = self._manager(threshold=2)
        chiamate = self._rompi(manager)
        for _ in range(2):
            self.assertIn("ha fallito", manager.execute("github_get_repo", {}))
        esito = manager.execute("github_get_repo", {})
        self.assertIn("in pausa", esito)
        self.assertIn("riprova fra", esito)
        self.assertEqual(len(chiamate), 2, "la terza chiamata non deve nemmeno essere tentata")

    def test_lo_stato_del_breaker_e_leggibile(self):
        manager = self._manager(threshold=1, cooldown=45.0)
        self._rompi(manager)
        manager.execute("github_get_repo", {})
        status = manager.describe()
        riga = status["connectors"][0]
        self.assertEqual(riga["failures"], 1)
        self.assertGreater(riga["cooldown_remaining_s"], 0)
        self.assertEqual(status["breaker"], {"failure_threshold": 1, "cooldown_s": 45.0})

    def test_un_successo_azzera_il_contatore(self):
        manager = self._manager(threshold=2)
        self._rompi(manager)
        manager.execute("github_get_repo", {})
        self.assertEqual(manager.describe()["connectors"][0]["failures"], 1)
        manager.connectors[0]._get_repo = lambda _args: "[github] ok"
        self.assertEqual(manager.execute("github_get_repo", {}), "[github] ok")
        self.assertEqual(manager.describe()["connectors"][0]["failures"], 0)

    def test_un_reload_chiude_la_pausa(self):
        manager = self._manager(threshold=1, cooldown=600.0)
        self._rompi(manager)
        manager.execute("github_get_repo", {})
        self.assertIn("in pausa", manager.execute("github_get_repo", {}))
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}, clear=False):
            manager.reload()
        manager.connectors[0]._get_repo = lambda _args: "[github] ok"
        self.assertEqual(manager.execute("github_get_repo", {}), "[github] ok")

    def test_un_tool_inesistente_non_apre_il_breaker(self):
        manager = self._manager(threshold=1)
        for _ in range(5):
            manager.execute("non_esiste", {})
        self.assertEqual(manager.describe()["connectors"][0]["failures"], 0)

    def test_le_soglie_si_leggono_dall_ambiente(self):
        with mock.patch.dict(os.environ, {"CONNECTOR_FAILURE_THRESHOLD": "7",
                                          "CONNECTOR_COOLDOWN_S": "12.5"}, clear=False):
            manager = ConnectorManager()
        self.assertEqual(manager.failure_threshold, 7)
        self.assertEqual(manager.cooldown_s, 12.5)

    def test_soglie_non_valide_tornano_ai_default(self):
        with mock.patch.dict(os.environ, {"CONNECTOR_FAILURE_THRESHOLD": "molti",
                                          "CONNECTOR_COOLDOWN_S": " "}, clear=False):
            manager = ConnectorManager()
        self.assertEqual(manager.failure_threshold, DEFAULT_FAILURE_THRESHOLD)
        self.assertEqual(manager.cooldown_s, DEFAULT_COOLDOWN_S)


if __name__ == "__main__":
    unittest.main()

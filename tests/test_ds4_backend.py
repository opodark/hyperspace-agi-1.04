# SPDX-License-Identifier: Apache-2.0
"""Adapter DwarfStar (ds4-server) — verificato contro un server ds4 FINTO.

ds4 richiede Metal 96GB+ o CUDA/ROCm e il GGUF minimo e' ~81 GiB: qui non e'
testabile l'inferenza vera. Ma l'integrazione e' fatta di protocollo e di
schema, e quello si valida adesso: un mini server HTTP che risponde come
ds4-server (`GET /v1/models` in stile OpenAI) prova l'adapter end-to-end.

L'API reale (docs/SERVER.md di antirez/ds4): default http://127.0.0.1:8000,
endpoint /v1/models, /v1/chat/completions, /v1/responses, /v1/completions,
/v1/messages; SSE e tool supportati; modalita' con slot indipendenti con
--batched-session N.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parents[1]

# Le dipendenze del nodo (httpx) non sono quelle del control-plane: senza di
# esse il modulo non si importa e questo test viene saltato invece di far
# fallire l'intera suite.
try:
    import httpx  # noqa: F401
    _DEPS = True
except ImportError:
    _DEPS = False

bm = None
if _DEPS:
    SPEC = importlib.util.spec_from_file_location("node_backend_metrics",
                                                  ROOT / "node" / "backend_metrics.py")
    bm = importlib.util.module_from_spec(SPEC)
    sys.modules["node_backend_metrics"] = bm
    SPEC.loader.exec_module(bm)

RUNTIME_KEYS = {"loaded", "vram_gb", "tokens_per_sec_ewma", "latency_ms_ewma",
                "requests_seen", "last_sample_ts", "data_age_s", "sample_source"}

requires_node_deps = unittest.skipUnless(_DEPS, "httpx non installato (dipendenze del nodo)")


class _Handler(BaseHTTPRequestHandler):
    model = "deepseek-v4-flash"
    status = 200

    def do_GET(self):
        if self.status != 200:
            body = b'{"error": "boom"}'
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/models":
            body = json.dumps({"object": "list", "data": [
                {"id": self.model, "object": "model", "owned_by": "ds4"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class _StubServer:
    def __init__(self, model="deepseek-v4-flash", status=200):
        handler = type("H", (_Handler,), {"model": model, "status": status})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


@requires_node_deps
class ProfileTests(unittest.TestCase):
    def test_ds4_is_an_inference_server(self):
        profile = bm.capability_profile("ds4")
        self.assertEqual(profile["backend_type"], "inference_server")
        self.assertTrue(profile["model_persistence"])
        self.assertTrue(profile["continuous_batching"], "ds4 prealloca slot con --batched-session")
        self.assertFalse(profile["dynamic_loading"], "il modello lo decide il GGUF all'avvio")
        self.assertTrue(profile["streaming"])

    def test_ds4_scores_above_the_model_managers(self):
        from shared.engine_profiles import backend_type_score
        self.assertGreater(backend_type_score("inference_server"),
                           backend_type_score("model_manager"))

    def test_factory_points_at_the_configured_url(self):
        os.environ["DS4_URL"] = "http://ds4-host:8000/"
        try:
            provider = bm.get_provider("ds4")
        finally:
            os.environ.pop("DS4_URL", None)
        self.assertEqual(provider.base, "http://ds4-host:8000")
        self.assertNotIn("vllm", type(provider).__name__.lower())

    def test_default_url_is_the_ds4_server_default(self):
        os.environ.pop("DS4_URL", None)
        self.assertEqual(bm.get_provider("ds4").base, "http://127.0.0.1:8000")


@requires_node_deps
class CollectTests(unittest.TestCase):
    def test_collect_reads_the_ds4_models_endpoint(self):
        with _StubServer(model="deepseek-v4-flash") as stub:
            payload = bm.asyncio.run(bm.DS4MetricsProvider(stub.url).collect())
        server = payload["server"]
        self.assertTrue(server["health"]["up"])
        self.assertTrue(server["health"]["models_ok"])
        self.assertIsNone(server["health"]["last_error"])
        self.assertEqual(server["models_loaded"], ["deepseek-v4-flash"])
        self.assertEqual(server["models_available"], ["deepseek-v4-flash"])
        self.assertEqual(set(payload["runtime"]["deepseek-v4-flash"]), RUNTIME_KEYS)

    def test_collect_survives_a_server_that_is_down(self):
        # Porta chiusa: il nodo deve dire "giu'", non esplodere.
        payload = bm.asyncio.run(bm.DS4MetricsProvider("http://127.0.0.1:1").collect())
        self.assertFalse(payload["server"]["health"]["up"])
        self.assertTrue(payload["server"]["health"]["last_error"])
        self.assertEqual(payload["server"]["models_loaded"], [])
        self.assertEqual(payload["runtime"], {})

    def test_collect_accepts_a_non_200(self):
        with _StubServer(status=500) as stub:
            payload = bm.asyncio.run(bm.DS4MetricsProvider(stub.url).collect())
        self.assertFalse(payload["server"]["health"]["up"])
        self.assertIn("HTTP 500", payload["server"]["health"]["last_error"])

@requires_node_deps
class ObservedRuntimeTests(unittest.TestCase):
    """Il throughput di ds4 e' OSSERVATO dal log interazioni, non inventato."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "interactions.jsonl"
        self.saved = bm.INTERACTION_LOG_FILE
        bm.INTERACTION_LOG_FILE = self.log
        self.addCleanup(lambda: setattr(bm, "INTERACTION_LOG_FILE", self.saved))

    def _write(self, entries):
        self.log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    def test_ewma_comes_from_the_interaction_log(self):
        self._write([
            {"model": "deepseek-v4-flash", "tokens_out": 100, "duration_ms": 2000,
             "ts": "2026-09-19T10:00:00Z"},
            {"model": "deepseek-v4-flash", "tokens_out": 50, "duration_ms": 1000,
             "ts": "2026-09-19T10:01:00Z"},
            {"model": "altro-modello", "tokens_out": 10, "duration_ms": 1000,
             "ts": "2026-09-19T10:01:00Z"},
        ])
        with _StubServer() as stub:
            payload = bm.asyncio.run(bm.DS4MetricsProvider(stub.url).collect())
        entry = payload["runtime"]["deepseek-v4-flash"]
        self.assertEqual(entry["requests_seen"], 2, "solo le interazioni di QUEL modello")
        self.assertIsNotNone(entry["tokens_per_sec_ewma"])
        self.assertEqual(entry["sample_source"], "ewma")
        self.assertIsNotNone(entry["data_age_s"])

    def test_a_model_without_history_has_no_invented_numbers(self):
        with _StubServer(model="glm-5.3-flash") as stub:
            payload = bm.asyncio.run(bm.DS4MetricsProvider(stub.url).collect())
        entry = payload["runtime"]["glm-5.3-flash"]
        self.assertTrue(entry["loaded"])
        self.assertIsNone(entry["tokens_per_sec_ewma"], "nessun campione -> nessuna stima")
        self.assertEqual(entry["requests_seen"], 0)

    def test_entries_received_from_a_peer_are_ignored(self):
        self._write([{"model": "deepseek-v4-flash", "tokens_out": 10, "duration_ms": 1000,
                      "ts": "2026-09-19T10:00:00Z", "_received_from": "peer"}])
        self.assertEqual(bm._interaction_aggregate("deepseek-v4-flash")["count"], 0)

    def test_runtime_schema_matches_the_other_engines(self):
        entry = bm._runtime_entry({"tps": [], "lat": [], "count": 0, "last_ts": ""}, True, 0.0)
        self.assertEqual(set(entry), RUNTIME_KEYS)
        self.assertTrue(entry["loaded"])
        self.assertIsNone(entry["sample_source"])
        self.assertIsNone(entry["data_age_s"])


if __name__ == "__main__":
    unittest.main()

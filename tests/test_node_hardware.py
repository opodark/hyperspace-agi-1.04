# SPDX-License-Identifier: Apache-2.0
"""Il tetto della macchina: dichiarato, rilevato, e pubblicato nei /metrics.

Perche' conta: senza il tetto, chi legge le metriche non puo' dire se un modello
ci sta. Il sintomo osservato era "il modello e' lento"; la causa era che i pesi
non entravano e Ollama li splittava su CPU (vedi shared/model_fit.py). Qui si
verifica che il numero esca dalla macchina CON la sua provenienza, perche' un
tetto dichiarato e uno rilevato non meritano la stessa fiducia.
"""
import ast
import asyncio
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("node_backend_metrics", ROOT / "node" / "backend_metrics.py")
backend_metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend_metrics)


class VramSourceTests(unittest.TestCase):
    def test_declared_wins_and_says_so(self):
        self.assertEqual(backend_metrics.vram_source(8, 0), "env")
        self.assertEqual(backend_metrics.vram_source(8, 12), "env")

    def test_detected_is_second_choice(self):
        self.assertEqual(backend_metrics.vram_source(0, 12), "nvidia-smi")
        self.assertEqual(backend_metrics.vram_source(None, 12), "nvidia-smi")

    def test_zero_is_not_a_ceiling(self):
        """E' il caso che ha prodotto l'ipotesi sbagliata: `0` non e' un tetto,
        e' l'assenza di un tetto — e va detto, non arrotondato."""
        self.assertEqual(backend_metrics.vram_source(0, 0), "none")
        self.assertEqual(backend_metrics.vram_source(None, None), "none")
        self.assertEqual(backend_metrics.vram_source("", ""), "none")


class MetricsPayloadTests(unittest.TestCase):
    PROFILE = {"backend_type": "model_manager", "streaming": True}

    def test_the_hardware_block_rides_with_the_metrics(self):
        payload = backend_metrics.metrics_payload(
            self.PROFILE, {"server": {"health": {"up": True}}, "runtime": {}},
            {"vram_gb": 8, "vram_source": "env", "tier": "hub"})
        self.assertEqual(payload["schema_version"], backend_metrics.NODE_METRICS_SCHEMA_VERSION)
        self.assertEqual(payload["backend_type"], "model_manager")
        self.assertEqual(payload["server"], {"health": {"up": True}})
        self.assertEqual(payload["hardware"], {"vram_gb": 8, "vram_source": "env", "tier": "hub"})

    def test_without_hardware_the_block_is_empty_not_absent(self):
        """Un blocco vuoto e' leggibile; un campo mancante fa indovinare."""
        payload = backend_metrics.metrics_payload(self.PROFILE, {})
        self.assertEqual(payload["hardware"], {})

    def test_a_broken_backend_does_not_hide_the_ceiling(self):
        payload = backend_metrics.metrics_payload(self.PROFILE, None, {"vram_gb": 8})
        self.assertEqual(payload["server"], {})
        self.assertEqual(payload["runtime"], {})
        self.assertEqual(payload["hardware"]["vram_gb"], 8)


class CollectMetricsTests(unittest.TestCase):
    HARDWARE = {"vram_gb": 8, "vram_source": "env", "tier": "hub"}

    def setUp(self):
        # La cache e' di modulo: si svuota, cosi' il test parte dal percorso freddo.
        backend_metrics._cache.clear()
        self.addCleanup(backend_metrics._cache.clear)

    def test_it_publishes_the_hardware_even_on_the_cached_path(self):
        payload = asyncio.run(backend_metrics.collect_metrics("ollama", hardware=self.HARDWARE))
        self.assertEqual(payload["hardware"], self.HARDWARE)
        again = asyncio.run(backend_metrics.collect_metrics("ollama", hardware=self.HARDWARE))
        self.assertEqual(again["hardware"], self.HARDWARE)      # percorso in cache

    def test_a_later_call_can_refresh_the_hardware(self):
        """Il valore e' statico per il processo, ma la cache non deve restare
        con un blocco vuoto scritto da una chiamata che non lo passava."""
        asyncio.run(backend_metrics.collect_metrics("ollama"))
        payload = asyncio.run(backend_metrics.collect_metrics(
            "ollama", hardware={"vram_gb": 24, "vram_source": "env", "tier": "hub"}))
        self.assertEqual(payload["hardware"]["vram_gb"], 24)


class NodeWiringTests(unittest.TestCase):
    """Il nodo deve PASSARE il blocco: se smette, le metriche tornano mute."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "node" / "main.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_every_collect_metrics_call_declares_the_hardware(self):
        calls = [node for node in ast.walk(self.tree)
                 if isinstance(node, ast.Call)
                 and getattr(node.func, "id", getattr(node.func, "attr", "")) == "collect_metrics"]
        self.assertGreaterEqual(len(calls), 2)
        for call in calls:
            with self.subTest(line=call.lineno):
                self.assertIn("hardware", [kw.arg for kw in call.keywords])

    def test_the_block_carries_value_source_and_tier(self):
        assignment = next(node for node in self.tree.body if isinstance(node, ast.Assign)
                          and any(getattr(t, "id", "") == "HARDWARE" for t in node.targets))
        keys = {key.value for key in assignment.value.keys}
        self.assertEqual(keys, {"vram_gb", "vram_source", "tier", "tier_forced"})
        self.assertIn("vram_source(", self.source)


if __name__ == "__main__":
    unittest.main()

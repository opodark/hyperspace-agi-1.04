"""Budget di tempo di una richiesta: timeout per modello, deadline, errori.

I tre bug trovati in sessione di test reale, e il test che li blocca:

1. `deepseek-r1:8b` con 600 token non concludeva entro 180s fissi (Read timed
   out su nodo E su Ollama locale) -> timeout per categoria di modello.
2. Due timeout chiusi come `done` con HTTP 200 e corpo d'errore -> un errore
   non e' un completamento.
3. La catena nodo+federazione+OmniRoute+ollama-direct SOMMAVA i timeout: oltre
   tre minuti prima di ammettere il fallimento -> budget totale condiviso.

Le funzioni sono estratte dal VERO control-plane/main.py con ast ed eseguite in
isolamento (stesso approccio di tests/test_model_patterns.py).
"""
import ast
import os
import time
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"
CONSTS = {"_REASONING_OVERRIDE", "_REASONING_PATTERNS", "INFERENCE_TIMEOUT_S",
          "INFERENCE_TIMEOUT_REASONING_S", "REQUEST_DEADLINE_S", "FALLBACK_MIN_ATTEMPT_S"}
FUNCS = {"_is_reasoning_model", "_inference_timeout", "_is_error_payload", "_respond_result"}
_ENV_KEYS = ("INFERENCE_TIMEOUT_S", "INFERENCE_TIMEOUT_REASONING_S",
             "REQUEST_DEADLINE_S", "FALLBACK_MIN_ATTEMPT_S", "REASONING_MODELS")


def _load(env=None):
    """Estrae costanti, helper e RequestDeadline dal sorgente reale."""
    env = env or {}
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    for key in _ENV_KEYS:
        if key in env:
            os.environ[key] = env[key]
        else:
            os.environ.pop(key, None)
    try:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        nodes = []
        for n in tree.body:
            if isinstance(n, ast.Assign) and any(
                    getattr(t, "id", "") in CONSTS for t in n.targets):
                nodes.append(n)
            elif isinstance(n, ast.ClassDef) and n.name == "RequestDeadline":
                nodes.append(n)
            elif isinstance(n, ast.FunctionDef) and n.name in FUNCS:
                nodes.append(n)
        scope = {"time": time, "os": os, "jsonify": lambda payload: payload}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), scope)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    missing = (CONSTS | FUNCS | {"RequestDeadline"}) - set(scope)
    if missing:
        raise RuntimeError(f"nodi non trovati in {SOURCE.name}: {sorted(missing)}")
    return scope


class _Clock:
    """Orologio finto: i test sulla deadline non aspettano mai davvero."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ReasoningModelTests(unittest.TestCase):
    def test_reasoning_families_are_recognised(self):
        detect = _load()["_is_reasoning_model"]
        for model in ("qwen3:8b", "qwen3.8-flash", "deepseek-r1:8b", "deepseek-v4-flash",
                      "magistral:24b", "glm-5.3-flash"):
            with self.subTest(model=model):
                self.assertTrue(detect(model), model)

    def test_direct_models_are_not_reasoning(self):
        detect = _load()["_is_reasoning_model"]
        for model in ("llama3.1:8b", "qwen2:0.5b", "phi4:14b", "gemma4:e4b", ""):
            with self.subTest(model=model):
                self.assertFalse(detect(model), model)

    def test_override_replaces_the_patterns(self):
        detect = _load({"REASONING_MODELS": "mio-modello"})["_is_reasoning_model"]
        self.assertTrue(detect("mio-modello:14b"))
        self.assertFalse(detect("qwen3:8b"), "l'override sostituisce i pattern, non li somma")
        self.assertTrue(_load({"REASONING_MODELS": "*"})["_is_reasoning_model"]("qualsiasi"))


class InferenceTimeoutTests(unittest.TestCase):
    def test_reasoning_models_get_a_longer_budget(self):
        timeout = _load()["_inference_timeout"]
        self.assertEqual(timeout("deepseek-r1:8b"), 600)
        self.assertEqual(timeout("llama3.1:8b"), 180)

    def test_env_can_raise_both_budgets(self):
        timeout = _load({"INFERENCE_TIMEOUT_S": "300",
                         "INFERENCE_TIMEOUT_REASONING_S": "900"})["_inference_timeout"]
        self.assertEqual(timeout("llama3.1:8b"), 300)
        self.assertEqual(timeout("qwen3:8b"), 900)

    def test_a_floor_prevents_disabling_the_timeout(self):
        self.assertGreaterEqual(_load({"INFERENCE_TIMEOUT_S": "0"})["_inference_timeout"]("llama3"), 10)

class RequestDeadlineTests(unittest.TestCase):
    def test_budget_consumes_only_real_time(self):
        clock = _Clock()
        deadline = _load()["RequestDeadline"](total_s=300, clock=clock)
        self.assertEqual(deadline.total_s, 300)
        self.assertEqual(deadline.remaining(), 300)
        clock.advance(120)
        self.assertEqual(deadline.elapsed(), 120)
        self.assertEqual(deadline.remaining(), 180)

    def test_a_fallback_is_skipped_when_the_residual_is_too_small(self):
        clock = _Clock()
        deadline = _load({"FALLBACK_MIN_ATTEMPT_S": "60"})["RequestDeadline"](total_s=300, clock=clock)
        self.assertTrue(deadline.allows())
        clock.advance(250)
        self.assertFalse(deadline.allows(), "50s residui non bastano per un tentativo da 60s")

    def test_the_threshold_is_overridable_per_call(self):
        clock = _Clock()
        deadline = _load()["RequestDeadline"](total_s=300, clock=clock)
        clock.advance(280)
        self.assertTrue(deadline.allows(min_s=10))
        self.assertFalse(deadline.allows(min_s=60))

    def test_the_default_budget_can_contain_a_reasoning_attempt(self):
        """Un budget piu' corto del timeout reasoning non e' piu' severo: e'
        solo un budget che il primo tentativo sfora (osservato in test reale:
        'budget di 300s esaurito dopo 600s')."""
        scope = _load()
        self.assertEqual(scope["REQUEST_DEADLINE_S"],
                         scope["INFERENCE_TIMEOUT_REASONING_S"] + scope["FALLBACK_MIN_ATTEMPT_S"])
        self.assertEqual(scope["RequestDeadline"]().total_s, scope["REQUEST_DEADLINE_S"])

    def test_a_wider_budget_from_env_is_respected(self):
        scope = _load({"REQUEST_DEADLINE_S": "1800", "INFERENCE_TIMEOUT_REASONING_S": "600"})
        self.assertEqual(scope["REQUEST_DEADLINE_S"], 1800)

    def test_a_too_narrow_budget_is_raised_to_be_coherent(self):
        scope = _load({"REQUEST_DEADLINE_S": "120", "INFERENCE_TIMEOUT_REASONING_S": "600",
                       "FALLBACK_MIN_ATTEMPT_S": "60"})
        self.assertEqual(scope["REQUEST_DEADLINE_S"], 660)


class ErrorPayloadTests(unittest.TestCase):
    def test_an_error_body_is_recognised(self):
        is_error = _load()["_is_error_payload"]
        self.assertTrue(is_error({"error": {"message": "Read timed out"}}))
        self.assertTrue(is_error({"error": "boom"}))

    def test_a_real_completion_is_not_an_error(self):
        is_error = _load()["_is_error_payload"]
        self.assertFalse(is_error({"choices": [{"message": {"content": "ciao"}}]}))
        self.assertFalse(is_error({"error": ""}))
        self.assertFalse(is_error(None))
        self.assertFalse(is_error("stringa"))

    def test_the_response_status_is_not_200_for_an_error(self):
        respond = _load()["_respond_result"]
        self.assertEqual(respond({"error": {"message": "x"}})[1], 502)
        # un successo resta una risposta semplice (niente tupla con status)
        self.assertNotIsInstance(respond({"choices": []}), tuple)


class WiringTests(unittest.TestCase):
    """Senza questi agganci i fix non sarebbero nel percorso reale."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        cls.module = SOURCE.read_text(encoding="utf-8")

    def test_finalize_task_detects_the_error_before_registering(self):
        body = ast.unparse(self.functions["_finalize_task"])
        self.assertLess(body.index("_is_error_payload"),
                        body.index("_normalize_assistant_message"))

    def test_every_finalize_call_is_answered_with_the_matching_status(self):
        body = ast.unparse(self.functions["v1_chat_completions"])
        finalize = body.count("_finalize_task(task, task_id")
        answered = body.count("return _respond_result(")
        self.assertEqual(finalize, answered,
                         f"{finalize} finalize contro {answered} risposte: una strada esce col 200 sbagliato")

    def test_the_fallback_chain_checks_the_deadline_three_times(self):
        body = ast.unparse(self.functions["v1_chat_completions"])
        self.assertEqual(body.count("deadline.allows()"), 3,
                         "federazione, OmniRoute e ollama-direct devono guardare il budget")

    def test_no_hardcoded_inference_timeout_remains(self):
        self.assertNotIn("timeout=180", self.module)
        self.assertIn("_inference_timeout(", self.module)

    def test_call_ollama_uses_the_model_aware_timeout(self):
        body = ast.unparse(self.functions["_call_ollama"])
        self.assertEqual(body.count("_inference_timeout("), 2, "entrambi i rami, firmato e non")

    def test_node_and_proxy_timeouts_are_above_the_cp(self):
        root = SOURCE.parents[1]
        node_main = (root / "node" / "main.py").read_text(encoding="utf-8")
        proxy = (root / "node" / "ollama_proxy.py").read_text(encoding="utf-8")
        self.assertIn("NODE_INFERENCE_TIMEOUT_S", node_main)
        self.assertNotIn("timeout=180.0", node_main)
        self.assertIn("OLLAMA_TIMEOUT_S", proxy)
        self.assertNotIn("timeout=300.0", proxy)


if __name__ == "__main__":
    unittest.main()

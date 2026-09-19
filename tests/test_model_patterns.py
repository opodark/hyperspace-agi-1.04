import ast
import os
import unittest
from pathlib import Path

SRC = Path(__file__).parents[1] / "control-plane/main.py"
CONSTS = {
    "_TOOL_CAPABLE_OVERRIDE", "_TOOL_CAPABLE_PATTERNS", "_VISION_PATTERNS",
    "_NATIVE_CHAT_FALLBACK_OVERRIDE", "_NATIVE_CHAT_FALLBACK_PATTERNS",
}
FUNCS = {"_model_supports_tools", "_use_native_chat_fallback", "_tool_capability_reason"}


def _load(tool_override="", native_override=""):
    """Estrae dal VERO sorgente del control-plane i pattern modello e le due
    funzioni che li usano, poi li esegue con le env var impostate. Stesso
    approccio di tests/test_local_nodes.py: nessuna copia della logica, cosi'
    il test segue il codice anche se attorno cambia."""
    os.environ["TOOL_CAPABLE_MODELS"] = tool_override
    os.environ["NATIVE_CHAT_FALLBACK_MODELS"] = native_override
    try:
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        nodes = []
        for n in tree.body:
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, ast.AnnAssign):
                targets = [n.target]
            else:
                targets = []
            if any(getattr(t, "id", "") in CONSTS for t in targets):
                nodes.append(n)
            elif isinstance(n, ast.FunctionDef) and n.name in FUNCS:
                nodes.append(n)
        scope = {"os": os}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SRC), "exec"), scope)
    finally:
        os.environ.pop("TOOL_CAPABLE_MODELS", None)
        os.environ.pop("NATIVE_CHAT_FALLBACK_MODELS", None)
    missing = (CONSTS | FUNCS) - set(scope)
    if missing:
        raise RuntimeError(f"nodi non trovati in {SRC.name}: {sorted(missing)}")
    return scope


class ToolCapablePatternTests(unittest.TestCase):
    def test_known_tool_capable_models_are_detected(self):
        s = _load()
        for model in ("qwen3:8b", "gemma4:e4b", "deepseek-r1:8b", "llama3.1:8b", "phi4:14b"):
            self.assertTrue(s["_model_supports_tools"](model), model)

    def test_vision_variants_are_excluded(self):
        s = _load()
        self.assertFalse(s["_model_supports_tools"]("qwen2.5vl:3b"))
        self.assertFalse(s["_model_supports_tools"]("llava:7b"))

    def test_model_without_tool_support_is_excluded(self):
        s = _load()
        self.assertFalse(s["_model_supports_tools"]("qwen2:0.5b"))

    def test_override_whitelists_extra_model(self):
        s = _load(tool_override="mio-modello")
        self.assertTrue(s["_model_supports_tools"]("mio-modello:7b"))
        self.assertFalse(s["_model_supports_tools"]("qwen2:0.5b"))

    def test_wildcard_enables_every_model(self):
        s = _load(tool_override="*")
        self.assertTrue(s["_model_supports_tools"]("qualsiasi:1b"))

    def test_blank_override_does_not_enable_every_model(self):
        # Regressione: il pattern vuoto e' substring di QUALSIASI nome modello,
        # quindi un override vuoto/spazi/virgole abilitava le tool call su tutti
        # i modelli senza che l'utente l'avesse chiesto.
        for blank in ("", "   ", ","):
            s = _load(tool_override=blank)
            self.assertFalse(s["_model_supports_tools"]("qwen2:0.5b"), repr(blank))


class NativeChatFallbackPatternTests(unittest.TestCase):
    def test_default_patterns_cover_qwen3_family(self):
        s = _load()
        f = s["_use_native_chat_fallback"]
        for model in ("qwen3:8b", "qwen3-16k", "qwen3.8-16k"):
            self.assertTrue(f(model), model)

    def test_default_patterns_do_not_cover_other_models(self):
        s = _load()
        f = s["_use_native_chat_fallback"]
        for model in ("deepseek-r1:8b", "llama3.1:8b", "gemma4:e4b"):
            self.assertFalse(f(model), model)

    def test_override_covers_future_distilled_models(self):
        # E' il caso d'uso per cui l'elenco non e' cablato: i distillati in
        # arrivo si aggiungono da env/UI senza toccare il codice.
        s = _load(native_override="qwen3,deepseek4.1")
        f = s["_use_native_chat_fallback"]
        self.assertTrue(f("deepseek4.1:8b"))
        self.assertTrue(f("deepseek4.1-8b"))
        self.assertFalse(f("llama3.1:8b"))

    def test_wildcard_covers_every_model(self):
        s = _load(native_override="*")
        self.assertTrue(s["_use_native_chat_fallback"]("qualsiasi:1b"))

    def test_blank_override_falls_back_to_defaults(self):
        for blank in ("", "   ", ","):
            s = _load(native_override=blank)
            self.assertTrue(s["_use_native_chat_fallback"]("qwen3:8b"), repr(blank))


class ToolCapabilityReasonTests(unittest.TestCase):
    """La ragione mostrata dai log/diagnostica deve combaciare col verdetto.

    E' la garanzia che al cambio modello il messaggio "tool rimossi" spieghi
    davvero il perche' (pattern mancante, variante vision o override).
    """

    def test_a_known_pattern_is_named(self):
        s = _load()
        self.assertEqual(s["_tool_capability_reason"]("qwen3:8b"), "pattern: qwen3")

    def test_a_vision_variant_explains_the_exclusion(self):
        s = _load()
        self.assertIn("vision", s["_tool_capability_reason"]("qwen2.5vl:3b"))

    def test_an_unknown_model_says_so_loudly(self):
        s = _load()
        self.assertEqual(s["_tool_capability_reason"]("modello-nuovo:14b"),
                         "NESSUN pattern corrisponde")

    def test_override_is_reported(self):
        s = _load(tool_override="modello-nuovo")
        self.assertIn("override", s["_tool_capability_reason"]("modello-nuovo:14b"))
        wildcard = _load(tool_override="*")
        self.assertIn("tutti i modelli", wildcard["_tool_capability_reason"]("qualsiasi:1b"))

    def test_reason_agrees_with_the_verdict(self):
        for override in ("", "modello-nuovo", "*"):
            s = _load(tool_override=override)
            for model in ("qwen3:8b", "qwen2:0.5b", "qwen2.5vl:3b", "modello-nuovo:14b",
                          "gguf-ignoto:7b"):
                with self.subTest(override=override, model=model):
                    capable = s["_model_supports_tools"](model)
                    reason = s["_tool_capability_reason"](model)
                    self.assertEqual(capable, "NESSUN pattern" not in reason and "vision" not in reason,
                                     f"{model}: capable={capable} reason={reason!r}")


if __name__ == "__main__":
    unittest.main()

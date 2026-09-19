"""Header delle risposte SSE: nessun header hop-by-hop.

Bug trovato in sessione di test reali: `_sse_headers()` impostava
`Transfer-Encoding: chunked` e `Connection: keep-alive`, che appartengono al
server WSGI. Il risultato era HTTP malformato (header duplicati:
`Transfer-Encoding` due volte, `Connection: keep-alive` + `Connection: close`)
e il primo chunk dello stream andava PERSO: il client riceveva 15 byte con il
solo `[DONE]`. Con qwen2:0.5b la connessione si chiudeva a meta' (curl exit 56).
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"
HOP_BY_HOP = {"Transfer-Encoding", "Connection", "Keep-Alive", "Proxy-Authenticate",
              "Proxy-Authorization", "TE", "Trailer", "Upgrade"}


def _sse_headers_function():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_sse_headers")
    scope = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope["_sse_headers"]


class SseHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headers = _sse_headers_function()()

    def test_no_hop_by_hop_headers(self):
        offending = sorted(set(self.headers) & HOP_BY_HOP)
        self.assertEqual(
            offending, [],
            f"header hop-by-hop impostati dall'applicazione: {offending}. "
            "Li aggiunge il server WSGI: impostarli qui duplica l'header e rompe lo stream.")

    def test_keeps_what_the_sse_client_needs(self):
        self.assertEqual(self.headers.get("Content-Type"), "text/event-stream")
        self.assertIn("no-cache", self.headers.get("Cache-Control", ""))

    def test_disables_upstream_buffering(self):
        self.assertEqual(self.headers.get("X-Accel-Buffering"), "no")


if __name__ == "__main__":
    unittest.main()

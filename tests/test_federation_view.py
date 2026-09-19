# SPDX-License-Identifier: Apache-2.0
"""Vista federata fra control-plane: cosa esce, cosa non esce, come si unisce.

Il test che conta di piu' e' test_il_contenuto_non_esce: la tabella `tasks` ha le
colonne `prompt` e `result` con il testo delle richieste e delle risposte, e i log
hanno `detail`. Se un domani qualcuno scrivesse `dict(row)` invece di elencare i
campi, quella regressione uscirebbe dalla rete — quindi il test serializza
l'istantanea INTERA e cerca le stringhe sentinella.

Le funzioni si estraggono dal sorgente con ast (come tests/test_local_nodes.py)
perche' importare control-plane/main.py richiede Flask, il DB e la rete.
"""
import ast
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"

PROMPT = "SEGRETO-PROMPT-non-deve-uscire"
RESULT = "SEGRETO-RISPOSTA-non-deve-uscire"
DETAIL = "SEGRETO-DETAIL-non-deve-uscire"


def _functions(names):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    found = {n.name for n in wanted}
    assert found == set(names), f"funzioni rinominate nel CP: mancano {set(names) - found}"
    return compile(ast.Module(body=wanted, type_ignores=[]), "cp", "exec")


def _snapshot(nodes=None, agg=None, tasks=None, logs=None):
    scope = {
        "datetime": datetime, "timezone": timezone,
        "CP_ID": "cp-id-mac", "CP_PUBKEY": "04aabb",
        "FEDERATION_ENABLED": True, "FEDERATION_PUBLIC_URL": "http://100.81.234.102:8095",
        "_VIEW_SUMMARY_MAX": 160,
        "_node_list": lambda: nodes or [],
        "_best_endpoint": lambda n: n.get("ep", ""),
        "_aggregate_mesh_models": lambda: agg or {"bare": [], "per_node": []},
        "db": SimpleNamespace(get_all_tasks=lambda: tasks or [],
                              query_logs=lambda **kw: logs or []),
    }
    exec(_functions(["_view_summary", "_cp_view_snapshot"]), scope)
    return scope["_cp_view_snapshot"]()


def _merge(local, peers):
    scope = {}
    exec(_functions(["_merge_views"]), scope)
    return scope["_merge_views"](local, peers)


def _node(node_id, alias="", status="active", ep="", web=False):
    return {"node_id": node_id, "alias": alias, "status": status, "ep": ep,
            "tier": "leaf", "vram_gb": 8, "is_web_node": web, "label": "l"}


class SnapshotTests(unittest.TestCase):
    def test_il_contenuto_non_esce(self):
        """Nessun testo di prompt, risposta o detail nell'istantanea."""
        view = _snapshot(
            nodes=[_node("n1", "macbook")],
            agg={"bare": ["qwen3:8b"], "per_node": [{"id": "qwen3:8b::macbook"}]},
            tasks=[{"task_id": "t1", "status": "done", "node_id": "n1", "model": "qwen3:8b",
                    "created_at": "2026-09-19T10:00:00Z", "completed_at": "2026-09-19T10:00:05Z",
                    "prompt": PROMPT, "result": RESULT, "error": "", "endpoint": "http://x"}],
            logs=[{"ts": "2026-09-19T10:00:00Z", "type": "interaction", "status": "info",
                   "source": "n1", "target": "", "summary": "risposta pronta",
                   "detail": DETAIL}],
        )
        flat = json.dumps(view)
        for secret in (PROMPT, RESULT, DETAIL):
            self.assertNotIn(secret, flat)
        # e i metadati che servono invece ci sono
        self.assertEqual(view["tasks"][0]["task_id"], "t1")
        self.assertEqual(view["tasks"][0]["model"], "qwen3:8b")
        self.assertEqual(view["logs"][0]["summary"], "risposta pronta")

    def test_i_log_si_troncano_non_si_mascherano(self):
        long_summary = "x" * 500
        view = _snapshot(logs=[{"summary": long_summary}])
        summary = view["logs"][0]["summary"]
        self.assertEqual(len(summary), 163)          # 160 + "..."
        self.assertTrue(summary.endswith("..."))
        self.assertFalse(_snapshot(logs=[{"summary": 500}])["logs"][0]["summary"].endswith("..."))

    def test_il_messaggio_multilinea_diventa_una_riga(self):
        view = _snapshot(logs=[{"summary": "prima\nseconda\tterza"}])
        self.assertEqual(view["logs"][0]["summary"], "prima seconda terza")

    def test_conteggi_e_identita(self):
        view = _snapshot(
            nodes=[_node("n1", "macbook"), _node("n2", "", status="stale"),
                   _node("web-1", "", web=True)],
            agg={"bare": ["a", "b"], "per_node": [{"id": "a::macbook"}]},
        )
        self.assertEqual(view["cp_id"], "cp-id-mac")
        self.assertEqual(view["counts"], {"nodes": 3, "active": 2, "web_nodes": 1,
                                         "models": 2, "tasks": 0, "logs": 0})
        # i nodi attivi vengono prima: la dashboard non deve riordinarli
        self.assertEqual([n["node_id"] for n in view["nodes"]], ["n1", "web-1", "n2"])

    def test_un_db_rotto_non_abbatte_la_vista(self):
        scope = {
            "datetime": datetime, "timezone": timezone,
            "CP_ID": "cp", "CP_PUBKEY": "04", "FEDERATION_ENABLED": True,
            "FEDERATION_PUBLIC_URL": "", "_VIEW_SUMMARY_MAX": 160,
            "_node_list": lambda: [],
            "_best_endpoint": lambda n: "",
            "_aggregate_mesh_models": lambda: (_ for _ in ()).throw(RuntimeError("db giu")),
            "db": SimpleNamespace(get_all_tasks=lambda: (_ for _ in ()).throw(RuntimeError("db giu")),
                                  query_logs=lambda **kw: (_ for _ in ()).throw(RuntimeError("db giu"))),
        }
        exec(_functions(["_view_summary", "_cp_view_snapshot"]), scope)
        view = scope["_cp_view_snapshot"]()
        self.assertEqual(view["nodes"], [])
        self.assertEqual(len(view["warnings"]), 3)   # modelli, task, log: tutti segnalati
        self.assertIn("db giu", " ".join(view["warnings"]))


class MergeTests(unittest.TestCase):
    def test_lo_stesso_nodo_visto_da_due_cp_e_una_riga_sola(self):
        local = {"cp_id": "cp-mac", "counts": {"nodes": 2},
                 "nodes": [_node("n1", "macbook"), _node("n2", "win11")],
                 "models": {"bare": ["qwen3:8b", "gemma4:e4b"]}}
        peer = {"ok": True, "label": "win11-cp",
                "view": {"cp_id": "cp-win", "counts": {"nodes": 2},
                         "nodes": [_node("n1", "macbook"), _node("n2", "win11")],
                         "models": {"bare": ["qwen3.5:4b"]}}}
        merged = _merge(local, [peer])
        self.assertEqual(merged["counts"]["sources"], 2)
        self.assertEqual(merged["counts"]["nodes"], 2, "due CP che vedono lo stesso nodo = un nodo")
        self.assertEqual(merged["counts"]["nodes_partial"], 0)
        self.assertEqual(merged["nodes"][0]["seen_by"], ["locale", "win11-cp"])
        self.assertEqual(merged["models"], ["gemma4:e4b", "qwen3.5:4b", "qwen3:8b"])

    def test_un_nodo_visto_da_un_solo_cp_e_la_divergenza_da_vedere(self):
        local = {"cp_id": "cp-mac", "nodes": [_node("n1"), _node("solo-mac")], "models": {"bare": []}}
        peer = {"ok": True, "label": "win11-cp",
                "view": {"cp_id": "cp-win", "nodes": [_node("n1")], "models": {"bare": []}}}
        merged = _merge(local, [peer])
        self.assertEqual(merged["counts"]["nodes"], 2)
        self.assertEqual(merged["counts"]["nodes_partial"], 1)
        divergente = [n for n in merged["nodes"] if n["node_id"] == "solo-mac"][0]
        self.assertEqual(divergente["seen_by"], ["locale"])

    def test_un_peer_muto_non_inquina_la_fusione(self):
        local = {"cp_id": "cp-mac", "nodes": [_node("n1")], "models": {"bare": ["m"]}}
        merged = _merge(local, [{"ok": False, "error": "unreachable", "view": None},
                                {"ok": True, "view": None},
                                {"ok": True, "label": "", "peer_id": "abcdef1234", "view": {"nodes": []}}])
        self.assertEqual(merged["counts"]["sources"], 2, "il peer ok senza vista non conta")
        self.assertEqual([s["label"] for s in merged["sources"]], ["locale", "abcdef12"])
        self.assertEqual(len(merged["nodes"]), 1)

    def test_nodi_senza_id_e_liste_mancanti(self):
        local = {"cp_id": "cp-mac", "nodes": [{"alias": "senza-id"}, _node("n1")]}
        merged = _merge(local, [{"ok": True, "label": "p", "view": {}}])
        self.assertEqual([n["node_id"] for n in merged["nodes"]], ["n1"])
        self.assertEqual(merged["models"], [])

    def test_i_task_e_i_log_non_si_fondono(self):
        """La fusione non tocca task e log: sono storie locali, mescolarle
        falsificherebbe la sequenza. Devono restare nelle singole viste."""
        local = {"cp_id": "cp-mac", "nodes": [], "models": {"bare": []},
                 "tasks": [{"task_id": "t1"}], "logs": [{"summary": "locale"}]}
        peer = {"ok": True, "label": "p",
                "view": {"nodes": [], "models": {"bare": []},
                         "tasks": [{"task_id": "t2"}], "logs": [{"summary": "remoto"}]}}
        merged = _merge(local, [peer])
        self.assertNotIn("tasks", merged)
        self.assertNotIn("logs", merged)


if __name__ == "__main__":
    unittest.main()

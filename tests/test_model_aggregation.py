# SPDX-License-Identifier: Apache-2.0
"""Chi compare in /v1/models: solo i nodi che il CP puo' davvero CHIAMARE.

Il caso che ha motivato questi test e' osservato, non ipotetico: il control-plane
registra per bookkeeping un nodo locale (is_local=True) che in questa
installazione ha endpoint VUOTO. Quel nodo non e' chiamabile — il routing lo
esclude — ma _aggregate_mesh_models gli chiedeva comunque i modelli, e siccome
per un nodo locale la risposta e' l'Ollama di QUESTA macchina, ogni modello
compariva due volte in /v1/models: una volta sul nodo vero e una come
'modello::local-xxxx', identica a vedersi e impossibile da servire.

I test estraggono le funzioni vere dal sorgente con ast (stessa tecnica di
tests/test_local_nodes.py) invece di importare control-plane/main.py, che
richiede Flask, il DB e la rete al momento dell'import.
"""
import ast
import time
import unittest
from pathlib import Path

MAC = "d7bc05baed5b752aeab6ba2624243b59fc333b9f"
GHOST = "local-e62fa5e950a7d234"
WIN = "fc6c821ba7c1866768010e1b4baa04507a4157da"
MODELS = ["qwen3:8b", "gemma4:e4b"]


def _load_functions():
    """Estrae dal CP le funzioni sotto test e le esegue in uno scope isolato."""
    tree = ast.parse((Path(__file__).parents[1] / "control-plane/main.py").read_text(encoding="utf-8"))
    wanted = {"_aggregate_mesh_models", "_best_endpoint", "_normalize_endpoint"}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in body} == wanted, "funzioni rinominate nel CP: aggiorna questo test"
    return compile(ast.Module(body=body, type_ignores=[]), "cp", "exec")


def _run(nodes, models_by_node, aliases=None):
    """Esegue _aggregate_mesh_models con la lista nodi e i modelli indicati."""
    scope = {
        "time": time,
        "_MODELS_CACHE": {"ts": 0.0, "data": None},
        "_MODELS_CACHE_TTL": 15,
        "_node_aliases": aliases or {},
        "_node_list": lambda: nodes,
        "_node_ref_for": lambda nid: (aliases or {}).get(nid) or nid[:8],
        "_fetch_node_models": lambda n: models_by_node.get(n.get("node_id"), []),
    }
    exec(_load_functions(), scope)
    return scope["_aggregate_mesh_models"](force=True)


def _node(node_id, endpoint, **extra):
    return {"node_id": node_id, "endpoint": endpoint, "status": "active",
            "tier": "leaf", **extra}


class ModelAggregationTests(unittest.TestCase):
    def test_nodo_non_chiamabile_non_pubblica_modelli(self):
        """Endpoint vuoto (nodo locale pseudo-registrato): zero voci pubblicate.

        E' il bug dei doppioni: le voci del nodo fantasma erano identiche a
        quelle del nodo vero e non potevano essere servite da nessuno.
        """
        agg = _run(
            [_node(GHOST, "", is_local=True), _node(MAC, f"http://100.81.234.102:8081")],
            {GHOST: MODELS, MAC: MODELS},
        )
        self.assertEqual(sorted(agg["bare"]), sorted(MODELS))
        self.assertEqual(len(agg["per_node"]), 2, "i modelli vanno contati una volta sola")
        self.assertEqual({e["node_id"] for e in agg["per_node"]}, {MAC})
        self.assertFalse([e for e in agg["per_node"] if "::" + GHOST[:8] in e["id"]])

    def test_web_node_non_pubblica_modelli(self):
        """`browser://` non e' un endpoint: un web node non puo' servire modelli."""
        agg = _run(
            [_node("web-abc123", "browser://web-abc123", is_web_node=True), _node(MAC, "http://mac:8081")],
            {"web-abc123": MODELS, MAC: ["qwen3:8b"]},
        )
        self.assertEqual(agg["bare"], ["qwen3:8b"])
        self.assertEqual([e["id"] for e in agg["per_node"]], ["qwen3:8b::" + MAC[:8]])

    def test_l_alias_e_il_riferimento_del_pin(self):
        """Con un alias, l'id pubblicato diventa 'modello::alias' per tutti."""
        agg = _run([_node(MAC, "http://mac:8081")], {MAC: ["qwen3:8b"]}, {MAC: "macbook"})
        entry = agg["per_node"][0]
        self.assertEqual(entry["id"], "qwen3:8b::macbook")
        self.assertEqual(entry["node_alias"], "macbook")
        self.assertEqual(entry["base_model"], "qwen3:8b")

    def test_un_public_endpoint_https_basta_a_essere_chiamabili(self):
        """La guardia non deve escludere un peer raggiungibile solo via tunnel."""
        agg = _run(
            [_node(WIN, "", public_endpoint="https://win.example")],
            {WIN: ["qwen3.5:4b"]},
        )
        self.assertEqual([e["id"] for e in agg["per_node"]], ["qwen3.5:4b::" + WIN[:8]])

    def test_i_modelli_di_nodi_diversi_restano_distinti(self):
        """Due macchine con lo stesso modello: una voce per macchina, non un merge."""
        agg = _run(
            [_node(MAC, "http://mac:8081"), _node(WIN, "http://100.64.31.18:8081")],
            {MAC: ["qwen3:8b"], WIN: ["qwen3:8b"]},
        )
        self.assertEqual(sorted(e["id"] for e in agg["per_node"]),
                         sorted(["qwen3:8b::" + MAC[:8], "qwen3:8b::" + WIN[:8]]))
        self.assertEqual(agg["bare"], ["qwen3:8b"], "il routing automatico vede il modello una volta")


if __name__ == "__main__":
    unittest.main()

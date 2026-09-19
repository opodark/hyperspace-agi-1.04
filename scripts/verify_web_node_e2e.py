# SPDX-License-Identifier: Apache-2.0
"""Verifica end-to-end delle route web node con il test client di Flask.

Carica la VERA app del control-plane (nessun mock delle route), su un DB
temporaneo, e percorre il ciclo completo: register -> enqueue -> poll -> result,
piu' i casi di rifiuto e il confine di indirizzabilita' (un web node non deve
mai diventare candidato per una chat, perche' non e' chiamabile).

Richiede le dipendenze del control-plane (flask, cryptography, requests):

    python -m venv .venv && .venv/bin/pip install flask flask-cors requests cryptography
    .venv/bin/python scripts/verify_web_node_e2e.py

La versione senza dipendenze, che gira sempre nella suite, e'
tests/test_web_node_routes.py.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="webnode-e2e-")
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")
os.environ["WEB_NODE_ENABLED"] = "true"
os.environ["NETWORK_ADMIN_TOKEN"] = "t" * 40
os.environ["MEMORY_BACKEND"] = "legacy"     # non parliamo con Hermes
os.environ["CODE_SANDBOX_ENABLED"] = "false"
os.environ["NODE_ENDPOINTS"] = ""

sys.path.insert(0, str(ROOT / "control-plane"))
import main  # noqa: E402

failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'OK  ' if ok else 'FAIL'} {label}: got={got!r} want={want!r}")
    if not ok:
        failures.append(label)


TOKEN = {"X-Hyperspace-Network-Token": "t" * 40}
client = main.app.test_client()

print("== 1. register ==")
response = client.post("/web/register", json={
    "node_id": "web-ci", "capabilities": ["summarize", "chat", "embed_texts"],
    "browser": "Test/1.0", "label": "ci", "limits": {"max_context": 4096}})
body = response.get_json()
check("register status", response.status_code, 200)
check("capability accettate (intersezione)", body["node"]["capabilities"],
      ["embed_texts", "summarize"])
check("capability rifiutate", body["node"]["rejected_capabilities"], ["chat"])
check("register senza node_id", client.post("/web/register", json={}).status_code, 400)

print("== 2. enqueue (riservato all'operatore) ==")
check("senza token admin",
      client.post("/web/tasks", json={"type": "summarize", "payload": {}}).status_code, 401)
response = client.post("/web/tasks", json={"type": "summarize",
                                           "payload": {"text": "ciao mondo"}},
                       headers=TOKEN)
check("enqueue status", response.status_code, 202)
task = response.get_json()["task"]
check("enqueue sceglie il nodo dalla capability", task["node_id"], "web-ci")
check("enqueue tipo non web-safe",
      client.post("/web/tasks", json={"type": "chat", "payload": {}},
                  headers=TOKEN).status_code, 409)

print("== 3. poll ==")
response = client.post("/web/poll", json={"node_id": "web-ci", "timeout_s": 0})
check("poll status", response.status_code, 200)
check("poll consegna il task", response.get_json()["task"]["task_id"], task["task_id"])
check("poll nodo ignoto",
      client.post("/web/poll", json={"node_id": "ghost", "timeout_s": 0}).status_code, 404)

print("== 4. result ==")
response = client.post("/web/result", json={"node_id": "web-ci", "task_id": task["task_id"],
                                            "ok": True, "result": {"summary": "s"},
                                            "duration_ms": 7})
check("result status", response.status_code, 200)
check("result matched", response.get_json()["result"]["matched"], True)
check("result per task ignoto", client.post("/web/result", json={
      "node_id": "web-ci", "task_id": "", "ok": True}).status_code, 400)

print("== 5. status ==")
status = client.get("/web/status").get_json()
check("status web_nodes", status["web_nodes"], 1)
check("status queued", status["queued"], 0)
check("status tasks_done", status["nodes"][0]["tasks_done"], 1)
check("status recent_results", len(status["recent_results"]), 1)

print("== 6. un web node non e' indirizzabile ==")
nodes = [n for n in main._node_list() if n.get("node_id") == "web-ci"]
check("web node visibile nella mesh", len(nodes), 1)
check("web node marcato is_web_node", nodes[0].get("is_web_node"), True)
check("web node senza endpoint chiamabile", main._best_endpoint(nodes[0]), "")
active = [n for n in main._node_list() if n.get("status") == "active"]
check("web node fuori dai candidati chat",
      any(n.get("node_id") == "web-ci"
          for n in main._rank_candidate_nodes(active)), False)

print()
if failures:
    print("FALLITI:", failures)
    sys.exit(1)
print("E2E WEB NODE: TUTTI I CHECK OK")

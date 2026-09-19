# SPDX-License-Identifier: Apache-2.0
"""Verifica end-to-end del gate di autenticazione su /mcp.

Carica la VERA app del control-plane (test client Flask, DB temporaneo) con due
client MCP configurati — uno con allowlist ristretta, uno con "*" — e controlla
che il gate regga: senza token, con token sbagliato, allowlist rispettata sia in
tools/list sia in tools/call, nessuna enumerazione dei tool, kill switch e
scorciatoia loopback opt-in.

    .venv/bin/python scripts/verify_mcp_auth_e2e.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="mcp-auth-e2e-")
HERMES_TOKEN = "h" * 40
OPS_TOKEN = "o" * 40

os.environ["DB_PATH"] = os.path.join(TMP, "test.db")
os.environ["MEMORY_BACKEND"] = "legacy"
os.environ["CODE_SANDBOX_ENABLED"] = "false"
os.environ["NODE_ENDPOINTS"] = ""
os.environ["MCP_CLIENTS"] = f"hermes={HERMES_TOKEN};ops={OPS_TOKEN}"
os.environ["MCP_CLIENT_TOOLS"] = "hermes=web_search,get_mesh_status;ops=*"
os.environ["MCP_ALLOW_LOOPBACK"] = "false"
os.environ.pop("MCP_TOKEN", None)
os.environ.pop("MCP_ENABLED", None)

sys.path.insert(0, str(ROOT / "control-plane"))
import main  # noqa: E402

failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'OK  ' if ok else 'FAIL'} {label}: got={got!r} want={want!r}")
    if not ok:
        failures.append(label)


def rpc(payload, token=None, header="Authorization"):
    headers = {}
    if token == "RAW":
        headers["X-Hyperspace-Mcp-Token"] = HERMES_TOKEN
    elif token:
        headers[header] = f"Bearer {token}"
    return client.post("/mcp", json=payload, headers=headers)


client = main.app.test_client()
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "hermes"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def call(name, args=None, tool_id=3):
    return {"jsonrpc": "2.0", "id": tool_id, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}


print("== 1. senza token / token sbagliato ==")
response = rpc(INIT)
check("nessun token -> 401", response.status_code, 401)
check("401 con WWW-Authenticate", "Bearer" in response.headers.get("WWW-Authenticate", ""), True)
check("401 con corpo JSON-RPC", response.get_json()["error"]["code"], -32001)
check("token errato -> 401", rpc(INIT, "x" * 40).status_code, 401)
check("token corto -> 401", rpc(INIT, "short").status_code, 401)
check("notifica senza token -> 401",
      client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code,
      401)

print("== 2. autenticazione valida ==")
response = rpc(INIT, HERMES_TOKEN)
check("initialize con hermes -> 200", response.status_code, 200)
check("protocolVersion", response.get_json()["result"]["protocolVersion"], "2025-06-18")
check("header di fallback X-Hyperspace-Mcp-Token", rpc(INIT, "RAW").status_code, 200)

print("== 3. allowlist in tools/list ==")
visible = [t["name"] for t in rpc(LIST, HERMES_TOKEN).get_json()["result"]["tools"]]
check("hermes vede solo i suoi due tool", sorted(visible), ["get_mesh_status", "web_search"])
all_tools = [t["name"] for t in rpc(LIST, OPS_TOKEN).get_json()["result"]["tools"]]
check("ops (allowlist '*') vede tutto il catalogo", sorted(all_tools) == sorted(main._mcp_catalogue()),
      True)
check("il catalogo e' piu' ampio di quello di hermes", len(all_tools) > len(visible), True)

print("== 4. allowlist in tools/call ==")
body = rpc(call("code_sandbox", {"action": "status"}), HERMES_TOKEN).get_json()
check("tool non permesso -> errore -32001", body["error"]["code"], -32001)
check("il messaggio nomina il client", "hermes" in body["error"]["message"], True)
ghost = rpc(call("tool_che_non_esiste"), HERMES_TOKEN).get_json()
check("tool inesistente -> stesso errore (nessuna enumerazione)",
      ghost["error"]["code"], body["error"]["code"])
shim = rpc(call("omega_call", {"tool": "code_sandbox", "args": {"action": "status"}}),
           HERMES_TOKEN).get_json()
check("lo shim omega_call non aggira l'allowlist", shim["error"]["code"], -32001)
unknown_for_ops = rpc(call("tool_che_non_esiste"), OPS_TOKEN).get_json()
check("con allowlist '*' un tool inesistente resta -32602",
      unknown_for_ops["error"]["code"], -32602)
allowed = rpc(call("get_mesh_status"), HERMES_TOKEN).get_json()
check("tool permesso -> eseguito", allowed["result"]["isError"], False)
check("tool permesso -> contenuto non vuoto", len(allowed["result"]["content"][0]["text"]) > 0, True)

print("== 5. diagnostica senza segreti ==")
status = client.get("/mcp/status").get_json()
check("/mcp/status elenca i client", sorted(c["name"] for c in status["clients"]), ["hermes", "ops"])
check("/mcp/status non contiene i token", HERMES_TOKEN not in json.dumps(status), True)
check("/mcp/status mostra i tool effettivi di hermes",
      sorted(status["clients"][0]["effective_tools"]), ["get_mesh_status", "web_search"])
check("/mcp/status senza problemi di configurazione", status["problems"], [])

print("== 6. kill switch e loopback opt-in ==")
main._mcp_policy.enabled = False
check("MCP_ENABLED=false -> 503", rpc(INIT, HERMES_TOKEN).status_code, 503)
main._mcp_policy.enabled = True
saved_clients = main._mcp_policy.clients
main._mcp_policy.clients = []
check("nessun client + loopback non permesso -> 503", rpc(INIT).status_code, 503)
main._mcp_policy.allow_loopback = True
response = rpc(INIT)
check("nessun client + loopback permesso -> 200", response.status_code, 200)
main._mcp_policy.clients = saved_clients
main._mcp_policy.allow_loopback = False
check("ripristinato: senza token -> 401", rpc(INIT).status_code, 401)

print()
if failures:
    print("FALLITI:", failures)
    sys.exit(1)
print("E2E MCP AUTH: TUTTI I CHECK OK")

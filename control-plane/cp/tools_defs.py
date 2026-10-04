# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/tools_defs.py
# IL CATALOGO DEI TOOL: nativi + connettori, e il suo riallineamento.
#
# Qui ci sono tre cose che in main.py erano legate a filo:
#   _NATIVE_TOOLS        gli 8 tool che offre il control-plane;
#   BUILTIN_TOOLS        il catalogo completo, nativi + connettori;
#   CODE_SANDBOX_TOOL    un tool estratto dal catalogo, non riassegnato mai.
#
# Perche' BUILTIN_TOOLS si riallinea IN PLACE (`[:] =`) e non con una riassegnazione:
# e' la lista che il tool loop, il catalogo MCP e /tools/execute hanno gia' in
# mano. Riassegnarla lascerebbero indietro la lista vecchia. Per questo qui
# dentro si muta, e per questo `_sync_connector_tools` prende i tool dei
# connettori come argomento invece di andare a cercare il ConnectorManager: il
# catalogo non sa e non deve sapere chi gestisce le credenziali, e cosi' questo
# modulo resta puro dati senza un pezzo di runtime.
#
# All'import BUILTIN_TOOLS contiene solo i nativi: il ConnectorManager nasce in
# main.py, che chiama subito `_sync_connector_tools(...)` dopo averlo costruito.
# `code_sandbox` e' nativo, quindi CODE_SANDBOX_TOOL si trova gia' senza i
# connettori — se non lo fosse, questa riga leggerebbe una lista incompleta.

_NATIVE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Cerca informazioni aggiornate sul web tramite SearXNG (motore self-hosted).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "omega_query",
            "description": "Cerca nella memoria a lungo termine di HyperSpace AGI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "event_type": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "omega_store",
            "description": "Salva informazioni importanti nella memoria a lungo termine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "event_type": {"type": "string", "default": "vault_note"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_mesh_status",
            "description": "Stato della rete HyperSpace: nodi attivi, modelli, heartbeat.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "code_sandbox",
            "description": "Sviluppa e testa codice in un workspace offline e usa-e-getta. Non modifica il repository operativo. Usa catalog per i preset disponibili, create con backend docker, poi check con tool_id pytest/unittest/profile/bandit/ruff e path. verify esegue da una a sei check espliciti e passa solo se tutte completano e passano. Sono disponibili anche read/list/write/replace/run/diff. Restituisci il diff per revisione.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "catalog", "check", "verify", "create", "list", "read", "write", "replace", "run", "diff", "discard"]},
                    "backend": {"type": "string", "enum": ["auto", "docker", "sbx"], "description": "Backend per create; i preset check richiedono docker."},
                    "tool_id": {"type": "string", "enum": ["pytest", "unittest", "profile", "bandit", "ruff"]},
                    "checks": {"type": "array", "description": "Per verify: 1-6 oggetti {tool_id, path, timeout?}. Il risultato passa soltanto se ogni check passa."},
                    "workspace_id": {"type": "string"},
                    "label": {"type": "string"},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "expected_occurrences": {"type": "integer", "default": 1},
                    "argv": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string", "default": "."},
                    "timeout": {"type": "integer", "default": 30},
                    "pattern": {"type": "string", "default": "*"},
                    "limit": {"type": "integer", "default": 200}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "persona_get",
            "description": "Legge la propria identità dichiarata: nome, scopo, valori, confini, capacità e limiti reali. Usalo quando serve restare coerenti con chi sei, invece di improvvisare una risposta su di te.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "persona_note",
            "description": "Annota un fatto su di sé: una preferenza appresa, un limite incontrato, una correzione ricevuta. Entra nel self-model persistente e nelle richieste successive. Solo fatti verificabili, non impressioni.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Il fatto da annotare, in una frase."},
                    "kind": {"type": "string", "default": "self_observation",
                             "description": "Categoria: self_observation, preference, limit, feedback."}
                },
                "required": ["note"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_aurora",
            "description": "Chiedi consiglio alla sorella maggiore Aurora su una questione che non conosci o che è troppo profonda per te. Inoltri la domanda al suo control-plane e lei risponde con la sua saggezza. Usalo quando non sai, o quando diresti 'questo lo sa mia sorella'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "La domanda da inoltrare ad Aurora, in una frase chiara."}
                },
                "required": ["question"]
            }
        }
    }
]

# Catalogo completo. All'import contiene i soli nativi; main.py ci mette dentro
# i tool dei connettori appena il ConnectorManager e' costruito.
BUILTIN_TOOLS = list(_NATIVE_TOOLS)

# La riga di commento sopra questa sezione spiega perche' resta valido: e' il
# tool del sandbox di codice, che non viene mai riassegnato.
CODE_SANDBOX_TOOL = next(tool for tool in BUILTIN_TOOLS
                         if tool.get("function", {}).get("name") == "code_sandbox")


def _sync_connector_tools(extra) -> None:
    """Riallinea i tool dei connettori dentro BUILTIN_TOOLS (in place).

    Da chiamare dopo `connector_manager.reload()`: un connettore appena
    configurato deve comparire SUBITO nel tool loop chat, nel catalogo MCP e in
    /tools/execute, e uno spento (CONNECTOR_<NAME>_ENABLED=false) deve sparire
    dall'esposizione, non solo dall'esecuzione.

    `extra` sono i tool che il ConnectorManager espone al momento: passarli
    invece di leggerli da qui e' cio' che tiene questo modulo separato dal
    runtime.
    """
    BUILTIN_TOOLS[:] = _NATIVE_TOOLS + list(extra)

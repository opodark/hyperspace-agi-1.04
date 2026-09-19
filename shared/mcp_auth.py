"""Autenticazione, identita' del chiamante e allowlist dei tool per `/mcp`.

`/mcp` e' l'unica superficie del control-plane che espone i tool a runtime
ESTERNI (Hermes, Claude, qualsiasi client MCP). Prima di questo modulo era
aperta a chiunque raggiungesse la porta: nessun token, nessuna identita', e
`tools/call` accettava ogni tool pubblicato — inclusi i connettori con
credenziali (o365, workspace).

Qui vive solo la POLICY, pura e testabile: nessuna dipendenza da Flask, nessun
riferimento a control-plane/main.py. Chi chiama passa il catalogo dei tool
pubblicati, cosi' l'allowlist resta verificabile senza import circolari.

Scelte, in ordine di importanza:
- fail-closed: senza un token configurato `/mcp` non serve nessuno;
- un token valido identifica un CLIENT con un nome, che finisce nei log di
  audit — "chi ha chiamato cosa" deve essere ricostruibile;
- un client senza voce di allowlist NON riceve nessun tool (fail-closed), e la
  cosa viene segnalata in describe(): meglio un client inerte e visibile che
  un client con tutti i tool per una svista di configurazione;
- i token non compaiono MAI in describe()/repr, cosi' possono essere stampati
  in diagnostica senza rischi.
"""
from __future__ import annotations

import hmac
import os

MIN_TOKEN_LENGTH = 32
ALL_TOOLS = "*"
LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "localhost"})

CLIENTS_VAR = "MCP_CLIENTS"
TOOLS_VAR = "MCP_CLIENT_TOOLS"
TOKEN_VAR = "MCP_TOKEN"
ENABLED_VAR = "MCP_ENABLED"
LOOPBACK_VAR = "MCP_ALLOW_LOOPBACK"


class McpAuthError(Exception):
    """Configurazione o credenziale MCP non valida."""


class McpNotConfigured(McpAuthError):
    """Nessun client MCP configurato: il servizio resta chiuso."""


class McpUnauthorized(McpAuthError):
    """Token assente o non riconosciuto."""


class McpClient:
    """Un chiamante identificato da un token, con la sua allowlist di tool.

    `tools is None` significa "tutti i tool pubblicati"; un `frozenset` vuoto
    significa "nessun tool" (configurazione incompleta: fail-closed).
    """

    __slots__ = ("name", "token", "tools")

    def __init__(self, name: str, token: str, tools=None):
        self.name = str(name).strip()
        self.token = str(token)
        self.tools = None if tools is None else frozenset(tools)

    def allows(self, tool: str, catalogue=None) -> bool:
        """True se il client puo' chiamare `tool`.

        Con `tools is None` (allowlist "*") non c'e' alcuna restrizione: True
        SEMPRE, anche per un tool inesistente. Deve essere cosi': distinguere
        "non permesso" da "non esiste" spetta al chiamante, che sull'allowlist
        wildcard risponde -32602 (Unknown tool) e su quella esplicita -32001
        senza rivelare se il tool esiste. Se qui filtrassimo sul catalogo, un
        tool inesistente diventerebbe "non permesso" anche per un client che ha
        gia' visibilita' totale.
        """
        if self.tools is None:
            return True
        return tool in self.tools

    def allowed_names(self, catalogue) -> list:
        return sorted(t for t in catalogue if self.allows(t, catalogue))

    def __repr__(self) -> str:
        scope = "all" if self.tools is None else f"{len(self.tools)} tool"
        return f"McpClient(name={self.name!r}, scope={scope})"

    def to_dict(self, catalogue=None) -> dict:
        """Rappresentazione senza token, per la diagnostica."""
        return {
            "name": self.name,
            "tools": None if self.tools is None else sorted(self.tools),
            "effective_tools": None if catalogue is None else self.allowed_names(catalogue),
        }


def parse_tool_allowlist(raw) -> dict:
    """`hermes=web_search,get_mesh_status;ops=*` -> {name: frozenset|None}."""
    out = {}
    for chunk in str(raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, tools = chunk.partition("=")
        name, tools = name.strip(), tools.strip()
        if not name:
            continue
        if tools == ALL_TOOLS:
            out[name] = None
            continue
        out[name] = frozenset(t.strip() for t in tools.split(",") if t.strip())
    return out

def parse_clients(clients_env="", tools_env="", single_token="") -> tuple:
    """Costruisce i client MCP dalle env. Ritorna (clients, problems).

    Formati accettati:
      MCP_CLIENTS="hermes=<token>;claude=<token>"   (piu' client nominati)
      MCP_TOKEN="<token>"                            (un solo client, nome "mcp")
      MCP_CLIENT_TOOLS="hermes=web_search,get_mesh_status;ops=*"

    I problemi di configurazione non sollevano: vengono elencati e resi
    visibili da describe(), perche' un errore di battitura in un token non deve
    far esplodere il boot del control-plane.
    """
    problems = []
    allowlist = parse_tool_allowlist(tools_env)
    clients = []
    seen = set()

    for chunk in str(clients_env or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, token = chunk.partition("=")
        name, token = name.strip(), token.strip()
        if not sep or not name:
            problems.append(f"{CLIENTS_VAR}: voce senza nome=token, ignorata")
            continue
        if name in seen:
            problems.append(f"{CLIENTS_VAR}: nome duplicato {name!r}, tenuto il primo")
            continue
        if len(token) < MIN_TOKEN_LENGTH:
            problems.append(
                f"{CLIENTS_VAR}: token di {name!r} piu' corto di {MIN_TOKEN_LENGTH} caratteri, ignorato")
            continue
        seen.add(name)
        clients.append(McpClient(name, token, allowlist.get(name, frozenset())))

    single = str(single_token or "").strip()
    if single:
        if "mcp" in seen:
            problems.append(f"{TOKEN_VAR}: esiste gia' un client chiamato 'mcp', ignorato")
        elif len(single) < MIN_TOKEN_LENGTH:
            problems.append(f"{TOKEN_VAR}: token piu' corto di {MIN_TOKEN_LENGTH} caratteri, ignorato")
        else:
            tools = allowlist.get("mcp") if "mcp" in allowlist else None
            clients.append(McpClient("mcp", single, tools))

    for name, tools in allowlist.items():
        if name not in seen and not (name == "mcp" and single):
            problems.append(f"{TOOLS_VAR}: allowlist per un client inesistente {name!r}")
        elif tools is not None and not tools:
            problems.append(f"{TOOLS_VAR}: {name!r} ha allowlist vuota (fail-closed)")

    for client in clients:
        if client.tools is not None and not client.tools:
            problems.append(f"client {client.name!r} senza allowlist: nessun tool (fail-closed)")

    return clients, problems


class McpAuthPolicy:
    """Chi puo' parlare con /mcp, con quali tool, e con quale nome nei log."""

    def __init__(self, clients=None, *, enabled=True, allow_loopback=False, problems=None):
        self.clients = list(clients or [])
        self.enabled = bool(enabled)
        self.allow_loopback = bool(allow_loopback)
        self.problems = list(problems or [])
        self._loopback_client = McpClient("loopback", "", None)

    @classmethod
    def from_env(cls, environ=None) -> "McpAuthPolicy":
        env = os.environ if environ is None else environ
        clients, problems = parse_clients(
            env.get(CLIENTS_VAR, ""), env.get(TOOLS_VAR, ""), env.get(TOKEN_VAR, ""))
        enabled = str(env.get(ENABLED_VAR, "true")).strip().lower() != "false"
        loopback = str(env.get(LOOPBACK_VAR, "false")).strip().lower() == "true"
        if loopback and not clients:
            problems.append(
                f"{LOOPBACK_VAR}=true senza alcun token: /mcp resta raggiungibile dal solo loopback")
        return cls(clients, enabled=enabled, allow_loopback=loopback, problems=problems)

    @property
    def configured(self) -> bool:
        return bool(self.clients)

    def client_by_name(self, name):
        return next((c for c in self.clients if c.name == name), None)

    def authenticate(self, provided):
        """Il client corrispondente al token, o None.

        Non si interrompe al primo match: si confrontano TUTTI i client, cosi'
        il tempo di risposta non dice a un attaccante quale token ha sfiorato.
        """
        if not isinstance(provided, str) or not provided:
            return None
        match = None
        for client in self.clients:
            if hmac.compare_digest(provided, client.token):
                match = client
        return match

    @staticmethod
    def is_loopback(remote_addr) -> bool:
        return str(remote_addr or "").strip() in LOOPBACK_ADDRESSES

    def loopback_client(self) -> McpClient:
        return self._loopback_client

    def allows(self, client, tool: str, catalogue) -> bool:
        if client is None:
            return False
        return client.allows(tool, catalogue)

    def describe(self, catalogue=None) -> dict:
        """Diagnostica: NESSUN token, mai.

        Con `catalogue` popola `effective_tools`, cioe' esattamente cio' che
        quel client puo' chiamare ora: e' l'informazione piu' utile per
        l'operatore, e senza catalogo resterebbe None.
        """
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "allow_loopback": self.allow_loopback,
            "clients": [c.to_dict(catalogue) for c in self.clients],
            "problems": self.problems,
        }

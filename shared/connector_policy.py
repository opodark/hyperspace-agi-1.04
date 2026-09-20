# SPDX-License-Identifier: Apache-2.0
"""Policy read/write dei connettori esterni.

Problema che risolve: appena un connettore ha le credenziali, i suoi tool di
SCRITTURA (inviare email, creare issue e eventi) finiscono nel catalogo come
quelli di lettura. Il modello durante una chat, o un client MCP con allowlist
`*`, possono quindi scrivere su GitHub / inviare posta a nome
dell'organizzazione senza che nessuno abbia mai deciso "questo runtime può
scrivere". Non è un'ipotesi: era lo stato di partenza.

Qui vive solo la POLICY, pura e testabile come shared/mcp_auth.py: nessuna
dipendenza da Flask, nessun riferimento a control-plane/main.py. Chi chiama
passa il nome del connettore e la CLASSIFICAZIONE dichiarata dal connettore
stesso (`BaseConnector.READ_TOOLS` / `WRITE_TOOLS`): la policy non indovina dal
nome del tool, e non esiste un default implicito che possa esporre una
scrittura per distrazione.

Scelte, in ordine di importanza:
- fail-closed: il default è READ-ONLY. Un tool di scrittura non viene né
  esposto al modello né eseguito finché un'allowlist esplicita non lo consente;
- l'allowlist è per connettore e ammette `*`, con la stessa sintassi di
  MCP_CLIENT_TOOLS: una sola forma da ricordare per entrambe le superfici;
- una voce per un connettore inesistente, o che elenca tool che non sono di
  scrittura, è un ERRORE DI CONFIGURAZIONE e va letto (problems): una allowlist
  scritta male non deve diventare "nessuna scrittura" in silenzio;
- describe() non contiene mai credenziali: solo nomi di connettori e di tool.
"""
from __future__ import annotations

import os

WRITE_TOOLS_VAR = "CONNECTOR_WRITE_TOOLS"
READ_ONLY_VAR = "CONNECTOR_READ_ONLY"
ALL = "*"


def parse_write_allowlist(text) -> tuple[dict, list[str]]:
    """`"github=github_create_issue;o365=*"` -> `({...}, problems)`.

    `None` come valore significa "tutti i tool di scrittura di quel connettore";
    un frozenset vuoto (es. `"github="`) significa "nessuno" ed è segnalato: è
    quasi sempre una stringa scritta male, non una decisione.
    """
    allowlist: dict = {}
    problems: list[str] = []
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, tools = chunk.partition("=")
        name = name.strip().lower()
        if not sep or not name:
            problems.append(f"{WRITE_TOOLS_VAR}: voce illeggibile {chunk!r} "
                            f"(atteso connettore=tool1,tool2 oppure connettore=*)")
            continue
        if name in allowlist:
            problems.append(f"{WRITE_TOOLS_VAR}: voce duplicata per {name!r}, tenuta la prima")
            continue
        tools = tools.strip()
        if tools == ALL:
            allowlist[name] = None
            continue
        allowlist[name] = frozenset(t.strip() for t in tools.split(",") if t.strip())
    return allowlist, problems


class ConnectorPolicy:
    """Chi può SCRIVERE sui sistemi esterni, e con quali tool."""

    def __init__(self, write_allowlist=None, *, read_only=True, problems=None):
        self.write_allowlist = dict(write_allowlist or {})
        self.read_only = bool(read_only)
        self.problems = list(problems or [])

    @classmethod
    def from_env(cls, environ=None) -> "ConnectorPolicy":
        env = os.environ if environ is None else environ
        read_only = str(env.get(READ_ONLY_VAR, "true")).strip().lower() != "false"
        allowlist, problems = parse_write_allowlist(env.get(WRITE_TOOLS_VAR, ""))
        if not read_only and not allowlist:
            problems.append(f"{READ_ONLY_VAR}=false ma {WRITE_TOOLS_VAR} è vuota: "
                            f"nessun tool di scrittura esposto (fail-closed)")
        return cls(allowlist, read_only=read_only, problems=problems)

    @property
    def configured(self) -> bool:
        return bool(self.write_allowlist)

    def write_allowed(self, connector: str, tool: str, *, write: bool) -> bool:
        """True se `tool` è esponibile/eseguibile.

        La classificazione ARRIVA DA FUORI (`write`): una policy che decidesse
        da sé se un tool scrive dovrebbe indovinarlo dal nome, e un tool di
        scrittura chiamato `sync_contacts` passerebbe come lettura.
        """
        if not write:
            return True
        if self.read_only or connector not in self.write_allowlist:
            return False
        allowed = self.write_allowlist[connector]
        return allowed is None or tool in allowed

    def filter_tools(self, connector: str, tools, write_of) -> list:
        """Tiene i tool di lettura e i soli tool di scrittura autorizzati."""
        keep = []
        for tool in tools or ():
            name = ((tool or {}).get("function") or {}).get("name") or ""
            if not name or self.write_allowed(connector, name, write=bool(write_of(name))):
                keep.append(tool)
        return keep

    def annotate(self, rows) -> list[dict]:
        """Aggiunge a ogni riga cosa è esposto e cosa è bloccato dalla policy.

        `rows`: iterabile di dict con `name` e `write_tools`.
        """
        out = []
        for row in rows:
            write = list(row.get("write_tools") or ())
            exposed = [t for t in write if self.write_allowed(row["name"], t, write=True)]
            out.append({**row,
                        "write_tools_exposed": exposed,
                        "write_tools_blocked": [t for t in write if t not in exposed]})
        return out

    def check_against(self, known_write: dict) -> list[str]:
        """Problemi dell'allowlist confrontata coi connettori REALI.

        `known_write`: `{connector: iterabile di nomi di tool di scrittura
        dichiarati}`, preso dalle classi e non solo dalle istanze attive: una
        voce per un connettore senza credenziali non è un errore, è una
        configurazione in attesa del primo salvataggio.
        """
        problems = []
        for name, allowed in self.write_allowlist.items():
            if name not in known_write:
                problems.append(f"{WRITE_TOOLS_VAR}: voce per un connettore inesistente {name!r}")
                continue
            if allowed is None:
                continue
            if not allowed:
                problems.append(f"{WRITE_TOOLS_VAR}: {name!r} non abilita nessun tool "
                                f"(fail-closed: scrivi i nomi oppure '*')")
                continue
            ignoti = sorted(set(allowed) - set(known_write[name]))
            if ignoti:
                problems.append(f"{WRITE_TOOLS_VAR}: {name!r} elenca tool che non sono di "
                                f"scrittura di quel connettore: {', '.join(ignoti)}")
        return problems

    def describe(self) -> dict:
        """Rappresentazione senza segreti, per /connectors e la dashboard."""
        return {
            "read_only": self.read_only,
            "write_allowlist": {name: (ALL if allowed is None else sorted(allowed))
                                for name, allowed in sorted(self.write_allowlist.items())},
            "problems": list(self.problems),
        }

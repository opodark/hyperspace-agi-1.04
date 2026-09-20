# SPDX-License-Identifier: Apache-2.0
"""
connectors/base.py — BaseConnector per HyperSpace-AGI v1.03

Ogni connettore deve:
  - Definire `name` (stringa univoca, es. "github", "office365", "google")
  - Implementare `get_tools()` → lista tool in formato OpenAI function-calling
  - Implementare `execute()` → dispatcher che ritorna str o None
  - Opzionalmente sovrascrivere `enabled` per il check delle credenziali

Il ConnectorManager carica automaticamente tutti i connector con enabled=True.
Override di `enabled` consigliato per connettori che richiedono env var specifiche
(vedi office365.py, github.py, google.py).

Override via env: CONNECTOR_<NAME>_ENABLED=false  per disabilitare forzatamente.
"""
from abc import ABC, abstractmethod
from typing import Any
import os


class BaseConnector(ABC):
    """Interfaccia base per tutti i connettori esterni di HyperSpace-AGI."""

    # Nome univoco del connettore — usato come prefisso dei tool e nei log
    name: str = "base"

    # Env var SENZA le quali il connettore non può funzionare. Vuoto = nessun
    # requisito (connettore sempre disponibile). Non è decorazione: senza questa
    # tabella un connettore spento è INDISTINGUIBILE da uno non configurato, e
    # l'operatore vede solo "i tool non ci sono" (vedi GET /connectors, che
    # riporta i nomi mancanti, mai i valori).
    REQUIRED_ENV: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        """
        Ritorna True se il connettore è abilitato e le credenziali sono disponibili.

        Logica:
          1. Se CONNECTOR_<NAME>_ENABLED=false  → sempre disabilitato (override manuale)
          2. Altrimenti chiama is_available()   → controlla le credenziali specifiche

        I connettori derivati possono sovrascrivere questo metodo oppure
        sovrascrivere solo is_available() per semplicità.
        """
        force_off = os.getenv(f"CONNECTOR_{self.name.upper()}_ENABLED", "").lower()
        if force_off == "false":
            return False
        return self.is_available()

    def missing_env(self) -> list[str]:
        """
        Nomi delle env var dichiarate in REQUIRED_ENV e assenti (o vuote).

        Solo i NOMI, mai i valori: questa lista finisce in /connectors, che è
        diagnostica leggibile dall'operatore e non deve contenere segreti.
        """
        return [key for key in self.REQUIRED_ENV if not str(os.getenv(key, "")).strip()]

    def is_available(self) -> bool:
        """
        Controlla se le credenziali/env var necessarie sono presenti.
        Il default deriva da REQUIRED_ENV; sovrascrivere solo se la condizione
        non è esprimibile come "queste env var esistono" (es. file di token).
        """
        return not self.missing_env()

    # Classificazione ESPLICITA di ogni tool pubblicato: l'unione dei due elenchi
    # deve coincidere esattamente con get_tools(). Un tool pubblicato ma non
    # classificato NON viene esposto (fail-closed, vedi classification_problems):
    # senza questo vincolo basterebbe dimenticare una riga per mettere un tool
    # che invia email nel catalogo del modello come se fosse una lettura.
    READ_TOOLS: tuple[str, ...] = ()
    WRITE_TOOLS: tuple[str, ...] = ()

    def tool_kind(self, tool_name: str) -> str | None:
        """`"read"`, `"write"`, oppure None se il tool non è classificato."""
        if tool_name in self.WRITE_TOOLS:
            return "write"
        if tool_name in self.READ_TOOLS:
            return "read"
        return None

    def classification_problems(self, published_names) -> list[str]:
        """Divergenze fra i tool pubblicati da get_tools() e la classificazione.

        Un connettore con problemi qui è inaffidabile per la policy read/write:
        il ConnectorManager lo tiene fuori dal catalogo e ne riporta il motivo
        in /connectors, invece di esporre un tool di cui non sa dire la natura.
        """
        declared_read = set(self.READ_TOOLS)
        declared_write = set(self.WRITE_TOOLS)
        published = set(published_names)
        problems = []
        overlap = sorted(declared_read & declared_write)
        if overlap:
            problems.append(f"{self.name}: dichiarati sia read sia write: {', '.join(overlap)}")
        non_classificati = sorted(published - declared_read - declared_write)
        if non_classificati:
            problems.append(f"{self.name}: pubblicati ma non classificati "
                            f"(READ_TOOLS/WRITE_TOOLS): {', '.join(non_classificati)}")
        non_pubblicati = sorted((declared_read | declared_write) - published)
        if non_pubblicati:
            problems.append(f"{self.name}: classificati ma non pubblicati da get_tools(): "
                            f"{', '.join(non_pubblicati)}")
        return problems

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """
        Ritorna la lista di tool in formato OpenAI function-calling.
        Ogni elemento deve avere la struttura:
          {
            "type": "function",
            "function": {
              "name": str,           # univoco in tutta la mesh
              "description": str,    # descrizione chiara per l'LLM
              "parameters": { ... }  # JSON Schema
            }
          }
        """
        ...

    @abstractmethod
    def execute(self, tool_name: str, args: dict) -> str | None:
        """
        Esegue il tool richiesto e ritorna una stringa leggibile dall'LLM.
        Ritorna None se il tool_name non appartiene a questo connettore
        (il ConnectorManager passerà al connettore successivo).
        """
        ...

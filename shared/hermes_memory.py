# SPDX-License-Identifier: Apache-2.0
"""Client for the native Hermes memory bridge.

HyperSpace services run in containers while Hermes owns its state on the host.
This module deliberately talks to the bridge instead of mounting or editing
Hermes' MEMORY.md/state.db from a container.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


class HermesMemoryError(RuntimeError):
    """The authoritative Hermes memory backend could not satisfy a request."""


class HermesMemoryClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None,
                 timeout: float = 5.0, cooldown: Optional[float] = None):
        self.base_url = (base_url or os.getenv(
            "HERMES_MEMORY_URL", "http://host.docker.internal:8098"
        )).rstrip("/")
        self.token = token if token is not None else os.getenv("HERMES_MEMORY_TOKEN", "")
        if not self.token:
            token_file = os.getenv("HERMES_MEMORY_TOKEN_FILE", "")
            if token_file:
                try:
                    self.token = Path(token_file).read_text(encoding="utf-8").strip()
                except OSError:
                    self.token = ""
        self.timeout = timeout
        # Failsafe "sticky": dopo un errore di trasporto Hermes resta marcato
        # irraggiungibile per questo intervallo, e chi legge serve il mirror
        # locale senza pagare il timeout a ogni richiesta.
        self.cooldown = float(cooldown if cooldown is not None
                              else os.getenv("HERMES_MEMORY_COOLDOWN_S", "60"))
        self._unavailable_until = 0.0
        self._last_transport_error = ""

    def unavailable(self) -> bool:
        """True mentre Hermes è considerato giù (circuito aperto)."""
        return time.monotonic() < self._unavailable_until

    def last_error(self) -> str:
        """L'ultimo errore di trasporto; vuoto se Hermes è (o sembra) raggiungibile."""
        return self._last_transport_error

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        if time.monotonic() < self._unavailable_until:
            raise HermesMemoryError(self._last_transport_error)
        headers = dict(kwargs.pop("headers", {}) or {})
        timeout = float(kwargs.pop("timeout", self.timeout))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = None
        try:
            for attempt in range(3):
                try:
                    response = requests.request(
                        method, f"{self.base_url}{path}", headers=headers,
                        timeout=(min(self.timeout, timeout), timeout), **kwargs,
                    )
                    break
                except requests.Timeout:
                    # A connect timeout is also a ConnectionError: do not
                    # multiply a dead peer's full timeout by three attempts.
                    raise
                except requests.ConnectionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.1 * (attempt + 1))
            assert response is not None
            response.raise_for_status()
            body = response.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            self._last_transport_error = f"Hermes memory bridge request failed: {exc}"
            self._unavailable_until = time.monotonic() + self.cooldown
            raise HermesMemoryError(self._last_transport_error) from exc
        except (requests.RequestException, ValueError) as exc:
            raise HermesMemoryError(f"Hermes memory bridge request failed: {exc}") from exc
        self._unavailable_until = 0.0
        if not isinstance(body, dict) or body.get("ok") is False:
            raise HermesMemoryError(str(body.get("error", "invalid Hermes bridge response")))
        return body

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def entries(self, limit: int = 200) -> List[Dict[str, Any]]:
        body = self._request("GET", "/entries", params={"limit": max(1, min(limit, 5000))})
        return list(body.get("entries") or [])

    def query(self, query: str = "", limit: int = 10, event_type: str = "",
              mode: str = "semantic", **filters) -> List[Dict[str, Any]]:
        body = self._request("POST", "/query", json={
            "query": query, "limit": max(1, min(limit, 100)),
            "event_type": event_type, "mode": mode, **filters,
        })
        return list(body.get("entries") or [])

    def lifecycle(self, ids: List[str], action: str, reason: str = "") -> Dict[str, Any]:
        return self._request("POST", "/lifecycle", json={
            "ids": ids, "action": action, "reason": reason,
        })

    def store(self, entry: Dict[str, Any], *, curated_target: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"entry": entry}
        if curated_target:
            payload["curated_target"] = curated_target
        return self._request("POST", "/store", json=payload)

    def import_entries(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._request("POST", "/import", json={"entries": entries}, timeout=max(self.timeout, 60.0))

    def stats(self) -> Dict[str, Any]:
        return self._request("GET", "/stats")

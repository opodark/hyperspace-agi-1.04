"""Client for the native Hermes memory bridge.

HyperSpace services run in containers while Hermes owns its state on the host.
This module deliberately talks to the bridge instead of mounting or editing
Hermes' MEMORY.md/state.db from a container.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


class HermesMemoryError(RuntimeError):
    """The authoritative Hermes memory backend could not satisfy a request."""


class HermesMemoryClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None,
                 timeout: float = 5.0):
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

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        timeout = float(kwargs.pop("timeout", self.timeout))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.request(
                method, f"{self.base_url}{path}", headers=headers,
                timeout=timeout, **kwargs,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HermesMemoryError(f"Hermes memory bridge request failed: {exc}") from exc
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

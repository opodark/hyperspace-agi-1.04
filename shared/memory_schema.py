# SPDX-License-Identifier: Apache-2.0
"""Common contract for HyperSpace memory entries (``hyperspace.memory.v1``).

A memory entry is a single versioned record exchanged between nodes, the
control-plane and the Hermes backend. This module normalizes, validates and
classifies entries so every producer and consumer agrees on one envelope instead
of relying on ad-hoc field names.

The canonical JSON Schema lives in ``memory/schema/hyperspace.memory.v1.schema.json``
and the contract is documented in ``memory/schema/README.md``. The bridge
(``scripts/hermes_memory_bridge.py``) writes entries with ``display_metadata.schema
= "hyperspace.memory.v1"``; this module is the in-process twin of that contract.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MEMORY_SCHEMA_VERSION = "hyperspace.memory.v1"

# Retention tiers. Operative memory is short-lived and TTL-bounded; project
# memory spans a session or a whole project; persistent memory is authoritative
# long-term knowledge (curated notes, promoted insights, vault notes).
OPERATIVE = "operative"
PROJECT = "project"
PERSISTENT = "persistent"
RETENTION_TIERS = (OPERATIVE, PROJECT, PERSISTENT)

# Known entry kinds, stored in ``type`` (legacy alias ``event_type``).
ENTRY_TYPES = (
    "memory",        # generic runtime interaction memory
    "note",          # operator/agent note
    "decision",      # decision log entry
    "task_state",    # task-scoped state
    "artifact",      # artifact metadata (Forge / sandbox)
    "dream",         # a generated hypothesis (untrusted)
    "dream_insight", # a promoted insight (persistent)
    "vault_note",    # Obsidian vault note ingested by omega
)

VALID_STATUSES = ("active", "quarantined", "revoked")

# Kinds considered authoritative long-term knowledge by default.
_PERSISTENT_TYPES = {"dream_insight", "vault_note"}
# Kinds that are runtime, TTL-bounded interaction memory by default.
_OPERATIVE_TYPES = {"memory", "task_state"}

DEFAULT_PRIORITY = 3
_CONTENT_KEYS = ("content", "summary", "detail")
_ID_KEYS = ("id", "memory_id")
_TS_KEYS = ("ts", "timestamp", "created_at")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _to_epoch(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _to_iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_int(value: Any, default: int, minimum: int = 0, maximum: Optional[int] = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def content_of(entry: Dict[str, Any]) -> str:
    """Return the searchable content of an entry, mirroring the bridge."""
    for key in _CONTENT_KEYS:
        value = entry.get(key)
        if value:
            return str(value).strip()
    prompt = str(entry.get("prompt") or "").strip()
    response = str(entry.get("response") or "").strip()
    if prompt and response:
        return f"User: {prompt}\nAssistant: {response}"
    return prompt or response


def entry_id(entry: Dict[str, Any]) -> str:
    """Stable identity for an entry (explicit id or canonical-content hash)."""
    for key in _ID_KEYS:
        value = entry.get(key)
        if value:
            return str(value)[:200]
    return hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()


def normalize_entry(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the canonical ``hyperspace.memory.v1`` envelope for an entry."""
    raw = entry or {}
    ts_value = None
    for key in _TS_KEYS:
        if raw.get(key) not in (None, ""):
            ts_value = raw[key]
            break
    epoch = _to_epoch(ts_value)
    kind = str(raw.get("type") or raw.get("event_type") or "memory").lower()
    status = str(raw.get("status") or "active").lower()
    normalized: Dict[str, Any] = {
        "schema": str(raw.get("schema") or MEMORY_SCHEMA_VERSION),
        "id": entry_id(raw),
        "ts": _to_iso(epoch),
        "timestamp": epoch,
        "type": kind if kind in ENTRY_TYPES else "memory",
        "content": content_of(raw),
        "source": str(raw.get("source") or raw.get("plugin") or "unknown"),
        "node_id": raw.get("node_id") or raw.get("sourceNode"),
        "model": raw.get("model"),
        "task_id": raw.get("task_id"),
        "status": status if status in VALID_STATUSES else "active",
        "priority": _coerce_int(raw.get("priority"), DEFAULT_PRIORITY, 1, 5),
        "access_count": _coerce_int(raw.get("access_count"), 0, 0),
    }
    retention = raw.get("retention")
    if retention in RETENTION_TIERS:
        normalized["retention"] = retention
    for key in ("plugin", "prompt", "response", "summary", "detail"):
        if raw.get(key) is not None:
            normalized[key] = raw[key]
    return normalized


def validate_entry(entry: Dict[str, Any]) -> List[str]:
    """Return a list of contract violations; empty means the entry is valid."""
    if not isinstance(entry, dict):
        return ["entry must be an object"]
    errors: List[str] = []
    if not content_of(entry):
        errors.append("entry has no searchable content")
    kind = str(entry.get("type") or entry.get("event_type") or "memory").lower()
    if kind not in ENTRY_TYPES:
        errors.append(f"unknown type: {kind}")
    status = str(entry.get("status") or "active").lower()
    if status not in VALID_STATUSES:
        errors.append(f"unknown status: {status}")
    retention = entry.get("retention")
    if retention is not None and retention not in RETENTION_TIERS:
        errors.append(f"unknown retention: {retention}")
    for key in _TS_KEYS:
        value = entry.get(key)
        if value not in (None, "") and _to_epoch(value) is None:
            errors.append(f"unparseable timestamp in {key}")
    priority = entry.get("priority")
    if priority is not None:
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            errors.append("priority must be an integer")
        else:
            if priority < 1 or priority > 5:
                errors.append("priority must be between 1 and 5")
    return errors


def classify_retention(entry: Dict[str, Any]) -> str:
    """Assign a retention tier to an entry.

    Explicit ``retention`` wins. Otherwise the tier follows the kind: promoted
    insights and vault notes are persistent; runtime memory and task state are
    operative (TTL-bound); dreams, notes, decisions and artifacts are
    project-scoped until explicitly promoted.
    """
    retention = entry.get("retention")
    if retention in RETENTION_TIERS:
        return retention
    kind = str(entry.get("type") or entry.get("event_type") or "memory").lower()
    if kind in _PERSISTENT_TYPES:
        return PERSISTENT
    if kind in _OPERATIVE_TYPES:
        return OPERATIVE
    return PROJECT


def is_persistent(entry: Dict[str, Any]) -> bool:
    return classify_retention(entry) == PERSISTENT


def is_operative(entry: Dict[str, Any]) -> bool:
    return classify_retention(entry) == OPERATIVE


def is_project(entry: Dict[str, Any]) -> bool:
    return classify_retention(entry) == PROJECT

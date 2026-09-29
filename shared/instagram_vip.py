# SPDX-License-Identifier: Apache-2.0
"""Persistent engagement-based VIP list for Instagram conversations."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone


LEVELS = ((30, "musa"), (15, "cerchia"), (5, "vip"))

# Livelli che costituiscono la "cerchia più stretta": l'ingresso in questi
# livelli apre la possibilità della modalità intima (sempre con consenso).
INTIMATE_LEVELS = ("cerchia", "musa")

# Il livello del creatore (l'operatore): sopra "musa", assegnato a mano, mai
# derivato dal conteggio dei messaggi e mai degradato da `record`.
CREATOR_LEVEL = "creatore"


def level_for(messages: int) -> str:
    for threshold, name in LEVELS:
        if int(messages) >= threshold:
            return name
    return ""


class InstagramVipStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._people: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                rows = (json.load(handle) or {}).get("people") or {}
        except (OSError, json.JSONDecodeError):
            rows = {}
        with self._lock:
            self._people = {str(key): value for key, value in rows.items()
                            if str(key).isdigit() and isinstance(value, dict)}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"people": self._people}, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def record(self, scoped_id: str, username: str = "") -> dict:
        scoped_id = str(scoped_id or "")
        if not scoped_id.isdigit():
            raise ValueError("Instagram scoped ID non valido")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = dict(self._people.get(scoped_id) or {})
            before = int(row.get("messages", 0))
            after = before + 1
            old_level = str(row.get("level") or level_for(before))
            new_level = level_for(after)
            # Il creatore non si degrada: il suo livello è assegnato a mano.
            if row.get("level") == CREATOR_LEVEL:
                new_level = CREATOR_LEVEL
            row.update({"scoped_id": scoped_id, "messages": after,
                        "level": new_level, "last_seen": now})
            if username:
                row["username"] = str(username).lstrip("@")[:64]
            milestones = list(row.get("milestones") or [])
            promoted = bool(new_level and new_level != old_level and after not in milestones)
            if promoted:
                milestones.append(after)
            row["milestones"] = milestones
            # Ingresso nella cerchia più stretta: solo il primo varco conta.
            # cerchia -> musa non è un nuovo ingresso, è già dentro.
            entered_intimate = (new_level in INTIMATE_LEVELS
                                and old_level not in INTIMATE_LEVELS)
            self._people[scoped_id] = row
            self.save()
            return {**row, "promoted": promoted, "entered_intimate": entered_intimate}

    def consent(self, scoped_id: str) -> str:
        """Stato del consenso per contatto: '' | 'asked' | 'granted' | 'denied'."""
        scoped_id = str(scoped_id or "")
        with self._lock:
            return str((self._people.get(scoped_id) or {}).get("consent") or "")

    def set_consent(self, scoped_id: str, status: str) -> None:
        scoped_id = str(scoped_id or "")
        if not scoped_id.isdigit():
            return
        status = str(status or "")
        with self._lock:
            row = dict(self._people.get(scoped_id) or {})
            if not row:
                return
            row["consent"] = status
            row["scoped_id"] = scoped_id
            self._people[scoped_id] = row
            self.save()

    def set_creator(self, scoped_id: str, username: str = "") -> dict:
        """Promuove un contatto al livello creatore (l'operatore), sopra "musa".

        Assegnato a mano: il creatore non dipende dal conteggio dei messaggi e
        `record` non lo degrada mai.
        """
        scoped_id = str(scoped_id or "")
        if not scoped_id.isdigit():
            raise ValueError("Instagram scoped ID non valido")
        with self._lock:
            row = dict(self._people.get(scoped_id) or {})
            row["scoped_id"] = scoped_id
            if username:
                row["username"] = str(username).lstrip("@")[:64]
            row["level"] = CREATOR_LEVEL
            row.setdefault("messages", 0)
            row["milestones"] = [5, 15, 30]
            self._people[scoped_id] = row
            self.save()
            return dict(row)

    def list(self, *, vip_only: bool = True) -> list[dict]:
        with self._lock:
            rows = [dict(row) for row in self._people.values()
                    if not vip_only or row.get("level")]
        return sorted(rows, key=lambda row: (-int(row.get("messages", 0)),
                                             str(row.get("username", ""))))

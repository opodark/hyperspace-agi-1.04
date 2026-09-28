# SPDX-License-Identifier: Apache-2.0
"""Persistent engagement-based VIP list for Instagram conversations."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone


LEVELS = ((30, "musa"), (15, "cerchia"), (5, "vip"))


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
            old_level, new_level = level_for(before), level_for(after)
            row.update({"scoped_id": scoped_id, "messages": after,
                        "level": new_level, "last_seen": now})
            if username:
                row["username"] = str(username).lstrip("@")[:64]
            milestones = list(row.get("milestones") or [])
            promoted = bool(new_level and new_level != old_level and after not in milestones)
            if promoted:
                milestones.append(after)
            row["milestones"] = milestones
            self._people[scoped_id] = row
            self.save()
            return {**row, "promoted": promoted}

    def list(self, *, vip_only: bool = True) -> list[dict]:
        with self._lock:
            rows = [dict(row) for row in self._people.values()
                    if not vip_only or row.get("level")]
        return sorted(rows, key=lambda row: (-int(row.get("messages", 0)),
                                             str(row.get("username", ""))))

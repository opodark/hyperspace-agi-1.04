# SPDX-License-Identifier: Apache-2.0
"""Persistent per-contact Instagram dialogue memory with rolling summaries."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InstagramMemory:
    def __init__(self, path: str, *, retention_days: int = 180):
        self.path = path
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.RLock()
        self._contacts: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                contacts = (json.load(handle) or {}).get("contacts") or {}
        except (OSError, json.JSONDecodeError):
            contacts = {}
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        kept = {}
        for key, value in contacts.items():
            if not str(key).isdigit() or not isinstance(value, dict):
                continue
            try:
                last = datetime.fromisoformat(str(value.get("updated_at", "")).replace("Z", "+00:00"))
            except ValueError:
                last = datetime.now(timezone.utc)
            if last >= cutoff:
                kept[str(key)] = value
        self._contacts = kept

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"contacts": self._contacts}, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def append(self, scoped_id: str, role: str, text: str, *, username: str = "") -> dict:
        scoped_id = str(scoped_id or "")
        if not scoped_id.isdigit() or role not in {"user", "assistant"}:
            raise ValueError("messaggio Instagram non valido")
        clean = " ".join(str(text or "").split())[:4000]
        if not clean:
            raise ValueError("testo vuoto")
        with self._lock:
            row = dict(self._contacts.get(scoped_id) or {})
            turns = list(row.get("turns") or [])
            sequence = int(row.get("sequence", 0)) + 1
            turns.append({"seq": sequence, "role": role, "text": clean, "ts": _now()})
            row.update({"scoped_id": scoped_id, "turns": turns[-120:],
                        "sequence": sequence, "updated_at": _now()})
            if username:
                row["username"] = str(username).lstrip("@")[:64]
            self._contacts[scoped_id] = row
            self._save()
            return dict(row)

    def context(self, scoped_id: str, *, recent_turns: int = 16) -> dict:
        with self._lock:
            row = dict(self._contacts.get(str(scoped_id)) or {})
        turns = list(row.get("turns") or [])[-max(1, int(recent_turns)):]
        return {"summary": str(row.get("summary") or ""), "turns": turns,
                "username": str(row.get("username") or "")}

    def compaction_material(self, scoped_id: str, *, max_turns: int = 32,
                            keep_recent: int = 12) -> dict | None:
        with self._lock:
            row = dict(self._contacts.get(str(scoped_id)) or {})
        turns = list(row.get("turns") or [])
        if len(turns) <= max(2, int(max_turns)):
            return None
        cutoff = max(1, len(turns) - max(1, int(keep_recent)))
        old = turns[:cutoff]
        return {"previous_summary": str(row.get("summary") or ""),
                "turns": old, "cutoff_seq": int(old[-1]["seq"])}

    def apply_summary(self, scoped_id: str, summary: str, cutoff_seq: int) -> None:
        with self._lock:
            row = dict(self._contacts.get(str(scoped_id)) or {})
            row["summary"] = " ".join(str(summary or "").split())[:5000]
            row["turns"] = [turn for turn in (row.get("turns") or [])
                            if int(turn.get("seq", 0)) > int(cutoff_seq)]
            row["updated_at"] = _now()
            self._contacts[str(scoped_id)] = row
            self._save()

    def stats(self) -> dict:
        with self._lock:
            rows = list(self._contacts.values())
        return {"contacts": len(rows), "turns": sum(len(r.get("turns") or []) for r in rows),
                "summaries": sum(bool(r.get("summary")) for r in rows),
                "retention_days": self.retention_days}

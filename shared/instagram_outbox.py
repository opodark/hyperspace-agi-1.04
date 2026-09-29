# SPDX-License-Identifier: Apache-2.0
"""Persistent, per-contact Instagram reply queue with bounded safe retries."""
from __future__ import annotations

import json
import hashlib
import os
import threading
import time
from datetime import datetime, timezone


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InstagramReplyOutbox:
    def __init__(self, path: str, *, now=time.time):
        self.path, self.now = path, now
        self._lock = threading.RLock()
        try:
            with open(path, encoding="utf-8") as handle:
                self._items = (json.load(handle) or {}).get("items") or {}
        except (OSError, ValueError):
            self._items = {}
        # An interrupted send may already have reached Meta. Never retry blind.
        for item in self._items.values():
            if item.get("status") == "sending":
                item.update(status="needs_review", error="invio interrotto: esito incerto")
            elif item.get("status") == "generating":
                item.update(status="retry", next_at=self.now())
        self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"items": self._items}, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def enqueue(self, sender_id: str, message_id: str, text: str, vip: dict,
                *, debounce_s: float = 12) -> None:
        if not sender_id.isdigit() or not message_id or not text:
            return
        with self._lock:
            previous = self._items.get(sender_id) or {}
            if previous.get("message_id") == message_id:
                return
            now = self.now()
            first = float(previous.get("first_at") or now)
            # `promoted` arriva da record() per QUESTO messaggio: non va ereditato
            # dal messaggio precedente, altrimenti la poesia di promozione si
            # ripeterebbe a ogni risposta (un tempo era sticky).
            merged_vip = dict(vip or {})
            self._items[sender_id] = {
                "sender_id": sender_id, "message_id": message_id, "text": text[:4000],
                "vip": merged_vip, "status": "queued", "attempts": 0,
                "first_at": first if previous.get("status") in {"queued", "retry"} else now,
                "next_at": now + debounce_s, "updated_at": _stamp(), "error": "",
            }
            self._save()

    def claim_due(self, *, max_wait_s: float = 90) -> dict | None:
        with self._lock:
            now = self.now()
            for item in self._items.values():
                if item.get("status") not in {"queued", "retry"}:
                    continue
                due = float(item.get("next_at") or now)
                if item.get("status") == "queued":
                    due = min(due, float(item.get("first_at") or now) + max_wait_s)
                if now < due:
                    continue
                item.update(status="generating", updated_at=_stamp())
                self._save()
                return dict(item)
        return None

    def sending(self, sender_id: str, message_id: str) -> bool:
        with self._lock:
            item = self._items.get(sender_id)
            if not item or item.get("message_id") != message_id:
                return False
            item.update(status="sending", updated_at=_stamp())
            self._save()
            return True

    def sent(self, sender_id: str, message_id: str) -> None:
        with self._lock:
            item = self._items.get(sender_id)
            if item and item.get("message_id") == message_id:
                item.update(status="sent", error="", updated_at=_stamp())
                self._save()

    def fail(self, sender_id: str, message_id: str, error: str, *, safe_retry: bool) -> None:
        with self._lock:
            item = self._items.get(sender_id)
            if not item or item.get("message_id") != message_id:
                return
            attempts = int(item.get("attempts") or 0) + 1
            retry = safe_retry and attempts < 4
            item.update(status="retry" if retry else "needs_review", attempts=attempts,
                        next_at=self.now() + min(900, 30 * 4 ** (attempts - 1)) if retry else 0,
                        error=str(error)[:240], updated_at=_stamp())
            self._save()

    def status(self) -> dict:
        with self._lock:
            items = list(self._items.values())
        counts = {key: sum(item.get("status") == key for item in items)
                  for key in ("queued", "retry", "generating", "sending", "sent", "needs_review")}
        rows = sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)[:30]
        return {"counts": counts, "items": [{"contact": "DM-" + hashlib.sha256(
                    item["sender_id"].encode()).hexdigest()[:8],
                "status": item.get("status"), "attempts": item.get("attempts", 0),
                "updated_at": item.get("updated_at"), "error": item.get("error", "")}
                for item in rows]}

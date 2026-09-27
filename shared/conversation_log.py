# SPDX-License-Identifier: Apache-2.0
"""Diario persistente delle conversazioni dei chatbot.

Perché esiste: il `deque` in memoria va bene per un cruscotto, ma si perde a
ogni riavvio del control-plane. Qui lo store è append-only e sta su disco
(JSON, come `shared/diario.py`) con un tetto e un contatore `dropped` che dice
quante battute sono uscite dal tetto — così il cruscotto di diagnostica
(`/conversations`) diventa anche un archivio consultabile, senza fingere di
esserlo: i limiti restano dichiarati.

La parte decidibile e testabile sta qui: la battuta (con i troncamenti) e lo
store, separati da `control-plane/main.py` come `voce`/`Diario`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

MAX_TURNS = 200
MAX_CHANNEL = 64
MAX_SURFACE = 64
MAX_CHAT = 64
MAX_AUTHOR = 64
MAX_MESSAGE_TEXT = 1000
MAX_TEXT = 2000
MAX_ACTION = 32
MAX_REASON = 200


def _adesso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def battuta(*, channel: str, surface: str, chat: str, messages: list,
            action: str, text: str = "", reason: str = "", ts: str = "") -> dict:
    """Una battuta del diario, già troncata ai tetti dichiarati.

    I messaggi in entrata sono una lista di dict `{author, text}`: il resto del
    payload (canale, superficie, azione) è una stringa corta e controllata dal
    chiamante, ma la si tronca comunque per non fidarsi della sorgente.
    """
    return {
        "ts": ts or _adesso(),
        "channel": str(channel or "")[:MAX_CHANNEL],
        "surface": str(surface or "")[:MAX_SURFACE],
        "chat": str(chat or "")[:MAX_CHAT],
        "messages": [
            {"author": str(m.get("author", ""))[:MAX_AUTHOR],
             "text": str(m.get("text", ""))[:MAX_MESSAGE_TEXT]}
            for m in (messages or []) if isinstance(m, dict)
        ],
        "action": str(action or "")[:MAX_ACTION],
        "text": str(text or "")[:MAX_TEXT],
        "reason": str(reason or "")[:MAX_REASON],
    }


class ConversationLog:
    """Battute append-only, con tetto, persistenza JSON e contatore `dropped`."""

    def __init__(self, max_turns: int = MAX_TURNS):
        self.max_turns = max(1, int(max_turns))
        self._turns: list = []
        self.dropped = 0

    def add(self, battuta: dict) -> dict:
        if not isinstance(battuta, dict):
            raise ValueError("battuta non valida")
        self._turns.append(battuta)
        overflow = len(self._turns) - self.max_turns
        if overflow > 0:
            del self._turns[:overflow]
            self.dropped += overflow
        return battuta

    def list(self, limit: int | None = None) -> list:
        """Le battute dalla più recente. `limit` opzionale, come `Diario.list`."""
        turns = list(reversed(self._turns))
        if limit is None:
            return turns
        return turns[:max(0, int(limit))]

    def __len__(self) -> int:
        return len(self._turns)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"turns": self._turns, "dropped": self.dropped}, f,
                      ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path: str, max_turns: int = MAX_TURNS) -> "ConversationLog":
        log = cls(max_turns=max_turns)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return log
        log.dropped = int((data or {}).get("dropped", 0) or 0)
        for t in ((data or {}).get("turns") or []):
            if isinstance(t, dict):
                log.add(t)
        return log

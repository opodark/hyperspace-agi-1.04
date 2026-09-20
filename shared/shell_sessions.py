# SPDX-License-Identifier: Apache-2.0
"""Sessioni di comandi: stato persistente fra un comando e l'altro, senza shell.

Cosa risolve: `shell_run` non ha memoria. Un runtime che vuole "apri nella
cartella X, esegui tre comandi, chiudi" deve oggi ripetere il cwd ogni volta e
non ha un posto dove leggere *cosa e' successo* in quella sessione.

Cosa NON e': un terminale. Qui non c'e' nessun PTY, nessuna shell, nessun input
interattivo: ogni comando resta un argv validato e classificato come in
`shell_run` — la stessa policy, le stesse pareti. Una sessione e' uno STATO
(cwd, storico, output accumulato) e un confine di audit: "cosa ha fatto quel
runtime in quella sessione?" deve avere una risposta.

Scelte:
- stato in memoria e scadenza per inattivita': un runtime che sparisce non
  lascia una sessione aperta per sempre (e il tetto alle sessioni e' un numero,
  non una speranza);
- l'output accumulato e' un ring buffer con `dropped` esplicito: se qualcosa e'
  stato buttato si vede, invece di sembrare che non ci fosse;
- lo storico e' limitato ma non tace: `history_dropped` dice quanti comandi non
  ci sono piu';
- `clock` iniettabile: la scadenza si prova senza aspettare davvero.
"""
from __future__ import annotations

import secrets
import time

DEFAULT_MAX_SESSIONS = 2
DEFAULT_IDLE_S = 900
DEFAULT_MAX_OUTPUT_BYTES = 65536
DEFAULT_HISTORY = 50
MIN_SESSION_BYTES = 1024


class SessionError(Exception):
    """Sessione inesistente, chiusa, o troppe sessioni aperte."""


class RingBuffer:
    """Gli ultimi N byte scritti, col conto di quello che non c'e' piu'."""

    def __init__(self, limit: int = DEFAULT_MAX_OUTPUT_BYTES):
        self.limit = max(MIN_SESSION_BYTES, int(limit))
        self.dropped = 0
        self._chunks: list[bytes] = []
        self._size = 0

    def write(self, text: object) -> int:
        raw = str(text or "").encode("utf-8", errors="replace")
        if not raw:
            return 0
        self._chunks.append(raw)
        self._size += len(raw)
        while self._size > self.limit and self._chunks:
            head = self._chunks[0]
            overflow = self._size - self.limit
            if len(head) <= overflow:
                self._chunks.pop(0)
                self._size -= len(head)
                self.dropped += len(head)
            else:
                self._chunks[0] = head[overflow:]
                self._size -= overflow
                self.dropped += overflow
        return len(raw)

    def read(self, clear: bool = False) -> tuple[str, int]:
        text = b"".join(self._chunks).decode("utf-8", errors="replace")
        dropped = self.dropped
        if clear:
            self._chunks.clear()
            self._size = 0
            self.dropped = 0
        return text, dropped

    def __len__(self) -> int:
        return self._size


class Session:
    """Una sessione aperta: dove siamo, cosa e' stato eseguito, cosa ha scritto."""

    __slots__ = ("id", "cwd", "label", "created_at", "last_seen", "output",
                 "history", "history_limit", "history_dropped", "closed")

    def __init__(self, session_id: str, cwd: str, label: str, now: float,
                 max_output_bytes: int, history: int):
        self.id = session_id
        self.cwd = str(cwd)
        self.label = str(label or "")[:120]
        self.created_at = float(now)
        self.last_seen = float(now)
        self.output = RingBuffer(max_output_bytes)
        self.history_limit = max(1, int(history))
        self.history: list[dict] = []
        self.history_dropped = 0
        self.closed = False

    def touch(self, now: float | None = None) -> None:
        self.last_seen = float(now if now is not None else time.time())

    def idle_for(self, now: float) -> float:
        return max(0.0, float(now) - self.last_seen)

    def record(self, argv, *, ok: bool, exit_code, duration_ms, now: float) -> None:
        """Il comando appena eseguito: e' il materiale dell'audit."""
        self.touch(now)
        self.history.append({"argv": [str(item) for item in argv], "ok": bool(ok),
                             "exit_code": exit_code, "duration_ms": duration_ms,
                             "at": round(float(now), 3)})
        while len(self.history) > self.history_limit:
            self.history.pop(0)
            self.history_dropped += 1

    def summary(self, now: float) -> dict:
        text, dropped = self.output.read()
        return {"session_id": self.id, "label": self.label, "cwd": self.cwd,
                "closed": self.closed, "commands": len(self.history) + self.history_dropped,
                "history_dropped": self.history_dropped,
                "duration_s": round(float(now) - self.created_at, 1),
                "idle_s": round(self.idle_for(now), 1),
                "output_bytes": len(self.output), "output_dropped": dropped}


class SessionStore:
    """Le sessioni aperte, con un tetto e una scadenza. Puro stato, nessun PTY."""

    def __init__(self, *, max_sessions: int = DEFAULT_MAX_SESSIONS,
                 idle_s: int = DEFAULT_IDLE_S,
                 max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
                 history: int = DEFAULT_HISTORY, clock=time.time):
        self.max_sessions = max(1, int(max_sessions))
        self.idle_s = max(30, int(idle_s))
        self.max_output_bytes = max(MIN_SESSION_BYTES, int(max_output_bytes))
        self.history_limit = max(1, int(history))
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def _reap(self) -> list[str]:
        """Chiude le sessioni inattive e dice quali: la scadenza non e' silenziosa."""
        now = self._clock()
        expired = [session_id for session_id, session in self._sessions.items()
                   if session.idle_for(now) >= self.idle_s]
        for session_id in expired:
            session = self._sessions.pop(session_id)
            session.closed = True
        return expired

    def open(self, cwd, label: str = "") -> Session:
        self._reap()
        if len(self._sessions) >= self.max_sessions:
            raise SessionError(f"troppe sessioni aperte ({self.max_sessions}): "
                               "chiudine una, o alza SHELL_MAX_SESSIONS")
        session = Session("sx-" + secrets.token_hex(8), cwd, label, self._clock(),
                          self.max_output_bytes, self.history_limit)
        self._sessions[session.id] = session
        return session

    def get(self, session_id) -> Session:
        """La sessione viva con quell'id. Ogni accesso e' attivita'."""
        self._reap()
        session = self._sessions.get(str(session_id or ""))
        if session is None:
            raise SessionError(f"sessione sconosciuta o scaduta: {session_id!r}")
        session.touch(self._clock())
        return session

    def close(self, session_id) -> dict:
        session = self.get(session_id)
        now = self._clock()
        summary = session.summary(now)
        self._sessions.pop(session.id, None)
        session.closed = True
        summary["closed"] = True
        return summary

    def list(self) -> list[dict]:
        self._reap()
        now = self._clock()
        return [session.summary(now) for session in
                sorted(self._sessions.values(), key=lambda item: item.created_at)]

    def describe(self) -> dict:
        """Per il pannello: quante sessioni, quanto durano, quanto output."""
        self._reap()
        now = self._clock()
        return {"open": len(self._sessions), "max_sessions": self.max_sessions,
                "idle_s": self.idle_s, "max_output_bytes": self.max_output_bytes,
                "sessions": [session.summary(now) for session in self._sessions.values()]}

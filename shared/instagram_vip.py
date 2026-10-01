# SPDX-License-Identifier: Apache-2.0
"""Persistent engagement-based VIP list for Instagram conversations."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone


# Le bande del pubblico Instagram, dall'engagement: pubblico/anonimo (sotto il primo
# gradino), VIP, MUSA. Il creatore è a parte, assegnato a mano.
#
# Il 2026-10-01 il gradino intermedio `cerchia` (15 messaggi) è stato tolto: le bande
# erano quattro — tre contate più il creatore — mentre il disegno ne prevede tre, e
# Telegram, che le bande le **dichiara** a mano, non ne ha mai avuta una. Un pubblico
# diviso in due modi non è lo stesso pubblico: ora la scala è questa su entrambe le
# piattaforme, e la differenza che resta è chi le assegna (il conteggio qui, la
# dichiarazione sul canale), non **quali** sono.
LEVELS = ((30, "musa"), (5, "vip"))

# I nomi che la scala sa produrre (`record`); il livello del creatore è fuori, è a mano.
LEVEL_NAMES = tuple(name for _, name in LEVELS)

# La banda intima: l'ingresso qui apre la possibilità della modalità intima (sempre
# con consenso registrato). È una sola, ed è quella che decide anche la voce.
INTIMATE_LEVELS = ("musa",)

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
            self._people = {}
            for key, value in rows.items():
                if not str(key).isdigit() or not isinstance(value, dict):
                    continue
                row = dict(value)
                # Un nome che la scala non produce più non resta appeso al contatto: si
                # ricalcola dal conteggio. Serve alla banda `cerchia`, tolta il
                # 2026-10-01: chi ci stava dentro non deve restare per sempre in un
                # livello che non esiste più — né sparire dal conteggio come "none". Il
                # livello del creatore è a mano e non si tocca; se il file è stato
                # scritto a mano, un conteggio illeggibile vale "nessuna banda".
                level = str(row.get("level") or "")
                if level and level != CREATOR_LEVEL and level not in LEVEL_NAMES:
                    try:
                        row["level"] = level_for(int(row.get("messages", 0)))
                    except (TypeError, ValueError):
                        row["level"] = ""
                # Stessa cosa per il diario dei traguardi: le tappe sono le **soglie
                # della scala**, non tre numeri che c'erano una volta. Un 15 rimasto lì
                # racconterebbe una banda che non esiste più (e `promoted` lo
                # confronta).
                if isinstance(row.get("milestones"), list):
                    row["milestones"] = sorted(
                        {int(p) for p in row["milestones"]
                         if str(p).lstrip("-").isdigit()
                         and int(p) in {passo for passo, _ in LEVELS}})
                self._people[str(key)] = row

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
            # Ingresso nella banda intima: il varco è uno solo (vip -> musa) e conta
            # solo la prima volta.
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
            # Le soglie della scala, non tre numeri scritti a mano: se la scala cambia,
            # i traguardi del creatore la seguono invece di restare indietro.
            row["milestones"] = sorted(passo for passo, _ in LEVELS)
            self._people[scoped_id] = row
            self.save()
            return dict(row)

    def list(self, *, vip_only: bool = True) -> list[dict]:
        with self._lock:
            rows = [dict(row) for row in self._people.values()
                    if not vip_only or row.get("level")]
        return sorted(rows, key=lambda row: (-int(row.get("messages", 0)),
                                             str(row.get("username", ""))))

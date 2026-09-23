#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Diario delle illustrazioni: i post e i sogni delle influencer, illustrati.

Perché esiste: la coda immagini (`shared/image_jobs.py`) è memoria viva e viene
potata — un job concluso sparisce dopo poco. Il diario è la traccia persistente
che collega ogni pagina (un post o un sogno) allo sketch che il Mac ha disegnato
per lei: la superficie di osservazione (Fase 5) legge questo, non la coda.

Una voce nasce quando si decide di illustrare qualcosa (con `file` vuoto) e si
completa quando il ponte riferisce il risultato (`aggiorna_file`). La parte
decidibile e testabile sta qui: la voce e lo store append-only, come feed.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

MAX_VOCI = 200
TIPI = ("post", "sogno")
MAX_TESTO_CHARS = 500
MAX_PROMPT_CHARS = 400


def _adesso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def voce(*, id: str, author: str, tipo: str = "post", testo: str = "",
         prompt: str = "", ts: str = "") -> dict:
    """Una voce del diario: chi, cosa, e il file (vuoto finché non è disegnato)."""
    return {
        "id": str(id or ""),
        "author": str(author or "")[:64],
        "tipo": tipo if tipo in TIPI else "post",
        "testo": " ".join(str(testo or "").split())[:MAX_TESTO_CHARS],
        "prompt": " ".join(str(prompt or "").split())[:MAX_PROMPT_CHARS],
        "file": "",
        "ts": ts or _adesso(),
    }


def file_da_job(job: dict) -> tuple[str, str] | None:
    """(id_voce, file) da un job del feed CONCLUSO bene. None altrimenti.

    Il job porta in `destinazione` l'id della voce che illustra: è il filo che
    lega la pagina del diario al suo sketch.
    """
    job = job if isinstance(job, dict) else {}
    if job.get("canale") != "feed" or job.get("stato") != "done":
        return None
    esito = job.get("esito") if isinstance(job.get("esito"), dict) else {}
    return (str(job.get("destinazione", "")), str(esito.get("file", "")))


class Diario:
    """Le pagine illustrate, append-only, con tetto e persistenza JSON."""

    def __init__(self, max_voci: int = MAX_VOCI):
        self.max_voci = max(1, int(max_voci))
        self._voci: list = []

    def add(self, voce: dict) -> dict:
        if not isinstance(voce, dict) or not voce.get("id"):
            raise ValueError("voce non valida: serve almeno un id")
        if any(v.get("id") == voce["id"] for v in self._voci):
            return voce
        self._voci.append(voce)
        del self._voci[:max(0, len(self._voci) - self.max_voci)]
        return voce

    def aggiorna_file(self, voce_id: str, file: str) -> bool:
        """Scrive il file disegnato sulla voce. False se la voce non c'è."""
        for v in self._voci:
            if v.get("id") == str(voce_id):
                v["file"] = str(file or "")
                return True
        return False

    def list(self, limit: int | None = None) -> list:
        voci = list(reversed(self._voci))
        if limit is None:
            return voci
        return voci[:max(0, int(limit))]

    def __len__(self) -> int:
        return len(self._voci)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"voci": self._voci}, f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path: str, max_voci: int = MAX_VOCI) -> "Diario":
        diario = cls(max_voci=max_voci)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return diario
        for v in ((data or {}).get("voci") or []):
            if isinstance(v, dict) and v.get("id"):
                diario.add(v)
        return diario

# SPDX-License-Identifier: Apache-2.0
"""Feed delle due influencer: la timeline dei loro post.

Perché un feed e non "la memoria": la memoria condivisa (Hermes) ricorda i FATTI
della stanza; il feed è la VETRINA — i contenuti che Anna e Aurora pubblicano e
le reazioni dell'una all'altra. Sono due cose diverse: un post non è un ricordo,
è un artefatto della vetrina.

Qui vive la parte decidibile e testabile: la validazione del post, la timeline
append-only con un tetto, e la persistenza su file (JSON). Nessuna dipendenza da
Flask, nessun modello, nessuna rete: il generatore (Fase 2) e il loop (Fase 3)
si appoggiano a questo senza portarsi dietro nient'altro.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

# ── Limiti espliciti: il feed è una vetrina, non un archivio. ────────────────
MAX_POSTS = 200
MAX_CAPTION_CHARS = 500
MAX_PROMPT_CHARS = 1000
MAX_AUTHOR_CHARS = 64

KIND_POST = "post"
KIND_REACTION = "reaction"
KINDS = (KIND_POST, KIND_REACTION)

# Le due influencer: l'autore si normalizza in minuscolo.
AUTHORS = ("anna", "aurora")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _nuovo_id() -> str:
    return uuid.uuid4().hex[:12]


def nuovo_post(author: str, caption: str, *, kind: str = KIND_POST,
               image_prompt: str = "", reply_to: str = "",
               adesso: str = "") -> dict:
    """Costruisce un post valido. Solleva ValueError se autore o didascalia non vanno.

    - `author` deve essere una delle due influencer (normalizzato in minuscolo).
    - `caption` è la didascalia (una battuta, non un saggio: si tronca).
    - `kind` è "post" o "reaction"; una reazione DEVE indicare `reply_to`.
    - `image_prompt` è il prompt per l'immagine (Fase 4), facoltativo.
    """
    autore = str(author or "").strip().lower()[:MAX_AUTHOR_CHARS]
    didascalia = " ".join(str(caption or "").split())
    if autore not in AUTHORS:
        raise ValueError(f"autore non riconosciuto: {author!r} (attesi: {', '.join(AUTHORS)})")
    if not didascalia:
        raise ValueError("didascalia vuota: un post senza testo non esiste")
    tipo = kind if kind in KINDS else KIND_POST
    risposta = str(reply_to or "").strip()[:32]
    if tipo == KIND_REACTION and not risposta:
        raise ValueError("una reazione deve indicare reply_to (a quale post risponde)")
    return {
        "id": _nuovo_id(),
        "author": autore,
        "caption": didascalia[:MAX_CAPTION_CHARS],
        "kind": tipo,
        "image_prompt": " ".join(str(image_prompt or "").split())[:MAX_PROMPT_CHARS],
        "reply_to": risposta,
        "ts": adesso or _now(),
    }


class Feed:
    """La timeline append-only dei post, con un tetto e persistenza JSON.

    L'ordine è quello di arrivo; `list()` li ritorna dal più recente al più
    vecchio (come una bacheca). `save()`/`load()` tengono la timeline sul disco,
    così i post sopravvivono ai riavvii del control-plane.
    """

    def __init__(self, max_posts: int = MAX_POSTS):
        self.max_posts = int(max_posts)
        self._posts: list[dict] = []

    def add(self, post: dict) -> dict:
        """Aggiunge un post valido. Un id già presente non si riaggiunge."""
        if not isinstance(post, dict) or not post.get("id"):
            raise ValueError("post non valido: serve almeno un id")
        if any(p.get("id") == post["id"] for p in self._posts):
            return post  # idempotente: stesso id, nessun doppione
        self._posts.append(post)
        # Il tetto è sulla timeline: i post più vecchi cadono.
        del self._posts[:max(0, len(self._posts) - self.max_posts)]
        return post

    def list(self, limit: int | None = None) -> list[dict]:
        """I post dal più recente al più vecchio (limite opzionale)."""
        posti = list(reversed(self._posts))
        if limit is None:
            return posti
        return posti[:max(0, int(limit))]

    def get(self, post_id: str) -> dict | None:
        for p in self._posts:
            if p.get("id") == post_id:
                return dict(p)
        return None

    def __len__(self) -> int:
        return len(self._posts)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"posts": self._posts}, f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path: str, max_posts: int = MAX_POSTS) -> "Feed":
        feed = cls(max_posts=max_posts)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return feed
        for p in ((data or {}).get("posts") or []):
            if isinstance(p, dict) and p.get("id"):
                feed.add(p)
        return feed

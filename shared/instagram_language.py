# SPDX-License-Identifier: Apache-2.0
"""Editable language hints for short Instagram greetings and slang."""

from __future__ import annotations

import json
import re
from pathlib import Path


_EDGE = re.compile(r"^[\s!?.,;:'\"…😂🤣😊👋🔥❤️]+|[\s!?.,;:'\"…😂🤣😊👋🔥❤️]+$")
_SPACE = re.compile(r"\s+")


def normalize_slang(text: str) -> str:
    return _SPACE.sub(" ", _EDGE.sub("", str(text or "").casefold())).strip()


def load_codex(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {normalize_slang(key): value for key, value in data.items()
            if normalize_slang(key) and isinstance(value, dict)}


def language_hint(text: str, codex: dict) -> dict | None:
    normalized = normalize_slang(text)
    # Il codex interviene solo sui messaggi corti: in una frase completa il
    # modello ha abbastanza contesto e una parola iniziale non deve dominarla.
    if not normalized or len(normalized.split()) > 3 or len(normalized) > 24:
        return None
    entry = codex.get(normalized)
    if not isinstance(entry, dict):
        return None
    return {"term": normalized, "language": str(entry.get("language") or ""),
            "hint": str(entry.get("hint") or "")}


_NON_LETTER = re.compile(r"[^a-zà-ÿ0-9]+")

# Micro-scambi "chi chiede / chi risponde" dove il modello tende a invertire le
# parti: risposte deterministiche che non costano una chiamata all'LLM.
_STATUS_REPLIES = {
    "bene tu": "Bene anch'io, grazie 😊 Che fai di bello?",
    "bene e tu": "Bene anch'io, grazie 😊 Che fai di bello?",
    "bene grazie tu": "Bene anch'io, grazie 😊 Che fai di bello?",
    "bene grazie e tu": "Bene anch'io, grazie 😊 Che fai di bello?",
}


def fast_reply(text: str, codex: dict) -> str:
    """Risposta deterministica per saluti e micro-domande; '' = decide il modello.

    Copre le inversioni «bene, tu?» e i saluti presenti nel codex con un campo
    ``reply`` (lingua appropriata). L'abbinamento è esatto sull'intero messaggio
    normalizzato, quindi una frase più lunga o composta non scatta mai e resta
    al modello.
    """
    key = _NON_LETTER.sub(" ", str(text or "").casefold()).strip()
    if key in _STATUS_REPLIES:
        return _STATUS_REPLIES[key]
    entry = codex.get(key)
    if isinstance(entry, dict):
        reply = str(entry.get("reply") or "").strip()
        if reply:
            return reply
    return ""

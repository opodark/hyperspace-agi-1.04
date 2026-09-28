# SPDX-License-Identifier: Apache-2.0
"""Turn recent social conversations into privacy-preserving dream prompts."""

from __future__ import annotations

import re


_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_MENTION = re.compile(r"(?<!\w)@[A-Za-z0-9_.]{1,64}")
_SPACE = re.compile(r"\s+")
_TRIVIAL = re.compile(
    r"^(?:ciao|hey|hi|hello|ei|ehi|buongiorno|buonasera|come stai|ok|grazie)[!?. ]*$",
    re.IGNORECASE,
)


def social_dream_inspirations(turns, *, limit: int = 5, max_chars: int = 240) -> list[str]:
    """Return recent distinct social messages without authors, handles or URLs.

    ``turns`` is expected newest-first, like ``ConversationLog.list()``. Chat IDs
    are used only to diversify the sample and never appear in the returned text.
    """
    result: list[str] = []
    seen_text: set[str] = set()
    seen_chats: set[str] = set()
    allowed = {"instagram", "telegram", "web", "webui"}
    for turn in turns or ():
        channel = str(turn.get("channel") or "").lower()
        surface = str(turn.get("surface") or "").lower()
        if channel not in allowed and surface not in allowed:
            continue
        chat = str(turn.get("chat") or "")
        if chat and chat in seen_chats:
            continue
        for message in reversed(turn.get("messages") or []):
            text = _SPACE.sub(" ", str(message.get("text") or "")).strip()
            if len(text) < 8 or _TRIVIAL.match(text):
                continue
            text = _URL.sub("[link]", _MENTION.sub("una persona", text))[:max_chars].rstrip()
            key = text.casefold()
            if key in seen_text:
                continue
            result.append(text)
            seen_text.add(key)
            if chat:
                seen_chats.add(chat)
            break
        if len(result) >= max(0, int(limit)):
            break
    return result

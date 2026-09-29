# SPDX-License-Identifier: Apache-2.0
"""Readable companion to Comfy's experimental typographic illustrations."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_TYPOGRAPHY = re.compile(
    r"\b(?:poesi\w*|vers\w*|testo\s+scritto|scritt\w*|letter\w*|"
    r"tipografi\w*|calligrafi\w*|handwrit\w*|typograph\w*)\b", re.IGNORECASE)
_QUOTED = re.compile(r'[«“"]([^»”"]{4,160})[»”"]')


def readable_excerpt(prompt: str, text: str) -> str:
    """Return exact source words only when the illustration intends typography."""
    if not _TYPOGRAPHY.search(str(prompt or "")):
        return ""
    source = " ".join(str(text or "").split())
    if not source:
        return ""
    for match in _QUOTED.finditer(str(prompt or "")):
        quote = " ".join(match.group(1).split())
        if quote.casefold() in source.casefold():
            return source[source.casefold().index(quote.casefold()):][:len(quote)]
    return source[:155].rstrip() + ("…" if len(source) > 155 else "")


def _lines(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def compose_readable(source: str | Path, target: str | Path, text: str) -> None:
    """Preserve the artwork, add a high-contrast exact-text panel (Instagram 4:5)."""
    with Image.open(source) as original:
        art = ImageOps.fit(original.convert("RGB"), (1024, 1024))
    canvas = Image.new("RGB", (1024, 1280), "#f4f0e9")
    canvas.paste(art, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 1024, 1024, 1280), fill="#17232b")
    for size in (40, 36, 32, 28):
        font = ImageFont.load_default(size=size)
        lines = _lines(draw, text, font, 920)
        if len(lines) <= 4:
            break
    if len(lines) > 4:
        visible = " ".join(lines[:4])
        while visible and len(_lines(draw, visible + "…", font, 920)) > 4:
            visible = visible.rsplit(" ", 1)[0] if " " in visible else ""
        lines = _lines(draw, visible + "…", font, 920) if visible else []
    for index, line in enumerate(lines[:4]):
        draw.text((52, 1040 + index * 54), line, font=font, fill="#ffffff")
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "JPEG", quality=92, optimize=True)

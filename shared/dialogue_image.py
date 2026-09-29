# SPDX-License-Identifier: Apache-2.0
"""Card del dialogo fra le due sorelle: il botta-e-risposta come immagine.

Il dialogo (Anna chiede / Aurora risponde) nasce nel feed (`post_loop` +
`post_gen`) come due didascalie distinte. Qui le si compone in un'unica card
pubblicabile su Instagram, senza passare da ComfyUI: è un artefatto tipografico,
come `shared/poetry_image.py` ma senza immagine di partenza.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_AUTHOR_LABEL = {"anna": "Anna", "aurora": "Aurora"}
_AUTHOR_COLOR = {"anna": "#00f5ff", "aurora": "#ff8cd8"}


def _wrap(draw, text: str, font, width: int) -> list[str]:
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


def compose_dialogue(target: str | Path, turns, *,
                     size: tuple[int, int] = (1080, 1080)) -> None:
    """Disegna una card 1:1 con i messaggi del dialogo (firma + testo).

    `turns` è una lista di `(autore, testo)` nell'ordine in cui le sorelle si
    rispondono. Il testo viene spezzato a larghezza fissa e troncato a un
    massimo di righe per messaggio, così la card non esplode mai.
    """
    width, height = size
    canvas = Image.new("RGB", (width, height), "#0b0e14")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=34)
    big = ImageFont.load_default(size=46)
    draw.text((width // 2, 48), "Anna e Aurora", font=big, fill="#8feaff", anchor="ma")
    draw.text((width // 2, 102), "si rispondono", font=font, fill="#7d8694", anchor="ma")
    y = 168
    for author, text in turns:
        autore = str(author or "").strip().lower()
        label = _AUTHOR_LABEL.get(autore, str(author).capitalize() or "?")
        color = _AUTHOR_COLOR.get(autore, "#ffffff")
        draw.text((56, y), label, font=big, fill=color)
        y += 60
        for line in _wrap(draw, " ".join(str(text or "").split()), font, width - 112)[:8]:
            draw.text((56, y), line, font=font, fill="#d7dde6")
            y += 46
        y += 30
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "JPEG", quality=92, optimize=True)

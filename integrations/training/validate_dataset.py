#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate an image+caption identity dataset before spending GPU hours."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--min-images", type=int, default=20)
    args = parser.parse_args()
    root = args.dataset.expanduser().resolve()
    if not root.is_dir():
        print(f"ERRORE: dataset assente: {root}")
        return 2
    images = sorted(p for p in root.rglob("*") if p.suffix.lower() in EXTENSIONS)
    errors = []
    ratios = set()
    for image in images:
        caption = image.with_suffix(".txt")
        if not caption.is_file() or not caption.read_text(encoding="utf-8").strip():
            errors.append(f"caption mancante o vuota: {image.name}")
        try:
            with Image.open(image) as opened:
                width, height = opened.size
                if min(width, height) < 512:
                    errors.append(f"risoluzione troppo bassa {width}x{height}: {image.name}")
                ratios.add(round(width / height, 1))
        except Exception as error:
            errors.append(f"immagine illeggibile {image.name}: {error}")
    if len(images) < args.min_images:
        errors.append(f"solo {len(images)} immagini; minimo consigliato {args.min_images}")
    if len(ratios) < 3:
        errors.append("servono almeno tre rapporti d'aspetto per evitare overfitting compositivo")
    print(f"immagini={len(images)} rapporti={sorted(ratios)} errori={len(errors)}")
    for error in errors:
        print(f"- {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())


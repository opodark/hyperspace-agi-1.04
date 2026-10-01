# SPDX-License-Identifier: Apache-2.0
"""Traduzione offline delle scene per i checkpoint che comprendono meglio l'inglese."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request


LOG = logging.getLogger(__name__)


def traduci_scena_immagine(testo: str, *, base_url: str | None = None,
                           timeout: float = 12.0) -> str:
    """Traduce italiano→inglese con LibreTranslate; in errore conserva l'originale.

    Il fallback è intenzionalmente fail-open: un traduttore spento non deve bloccare
    Telegram, Instagram o la coda ComfyUI. Nessun LLM conversazionale interviene e
    nessun tag tecnico viene affidato alla traduzione: qui entra soltanto la scena.
    """
    originale = " ".join(str(testo or "").split())
    if not originale:
        return ""
    url = (base_url if base_url is not None else
           os.getenv("LIBRETRANSLATE_URL", "http://libretranslate:5000")).rstrip("/")
    if not url:
        return originale
    corpo = json.dumps({"q": originale, "source": "it", "target": "en",
                        "format": "text"}, ensure_ascii=False).encode("utf-8")
    richiesta = urllib.request.Request(
        f"{url}/translate", data=corpo,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(richiesta, timeout=max(0.2, float(timeout))) as risposta:
            dato = json.loads(risposta.read().decode("utf-8"))
        tradotto = " ".join(str(dato.get("translatedText") or "").split())
        return tradotto or originale
    except (OSError, ValueError, TypeError, urllib.error.URLError) as errore:
        LOG.warning("Traduzione immagine non disponibile; uso la scena originale: %s", errore)
        return originale

"""Primitive piccole e testabili per le route amministrative di rete."""
from __future__ import annotations

import hmac
from urllib.parse import urlsplit


def token_authorized(provided: str | None, expected: str | None) -> bool:
    """Confronta due token senza scorciatoie insicure o default vuoti."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def normalize_http_base(value: str | None) -> str:
    """Valida un URL HTTP(S) base e lo restituisce senza slash finale.

    Le route bottle compongono autonomamente il path: userinfo, query,
    fragment e path non-root vengono rifiutati per evitare destinazioni
    ambigue o credenziali accidentalmente incorporate nell'URL.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("URL mancante")
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL non valido: serve una base http:// o https://")
    if parsed.username or parsed.password:
        raise ValueError("URL non valido: userinfo non consentita")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("URL non valido: usa solo scheme, host e porta")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("URL non valido: porta non valida") from error
    return value.rstrip("/")

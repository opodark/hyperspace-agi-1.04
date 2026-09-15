"""Primitive piccole e testabili per le route amministrative di rete."""
from __future__ import annotations

import hmac
import time
from urllib.parse import urlsplit


def token_authorized(provided: str | None, expected: str | None) -> bool:
    """Confronta due token senza scorciatoie insicure o default vuoti."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def sign_client_ip(secret: str, ip: str, ts: str) -> str:
    """HMAC-SHA256 di (ip, ts) con `secret`.

    Usata dal federation gateway per attestare al control-plane l'IP
    pubblico reale del chiamante: senza questo, dietro il gateway il CP
    vedrebbe solo il suo IP Docker interno per OGNI chiamante, e il
    rate-limit per-IP delle route bottle (vedi shared/bottle.py) collasserebbe
    su un unico contatore condiviso da tutto il traffico pubblico. Il
    gateway sa solo firmare: la verifica vive esclusivamente lato CP
    (verify_client_ip qui sotto), stessa disciplina del resto di questo
    modulo — un'unica fonte di verità per le decisioni di sicurezza."""
    return hmac.new(secret.encode(), f"{ip}|{ts}".encode(), "sha256").hexdigest()


def verify_client_ip(secret: str | None, ip: str, ts: str, signature: str,
                      *, max_skew_s: int = 30, now: float | None = None) -> bool:
    """True se `signature` e' una firma HMAC valida e recente di (ip, ts).

    Il secret sotto i 32 caratteri conta come "non configurato" e fa
    fallire la verifica (stessa soglia usata per NETWORK_ADMIN_TOKEN):
    chi chiama deve poter ricadere sull'IP di connessione diretta senza
    un controllo esplicito separato.
    """
    if not secret or len(secret) < 32:
        return False
    if not ip or not ts or not signature:
        return False
    try:
        skew = abs((now if now is not None else time.time()) - float(ts))
    except (TypeError, ValueError):
        return False
    if skew > max_skew_s:
        return False
    expected = sign_client_ip(secret, ip, ts)
    return hmac.compare_digest(expected, signature)


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

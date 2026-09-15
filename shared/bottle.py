"""'Bottiglie' — annunci di rendez-vous firmati e a prova di lavoro.

Sostituisce l'idea originale (pubblicare su Pastebin e bacheche pubbliche
generiche) con qualcosa di legittimo: un annuncio firmato con l'identita'
ECDSA del nodo (vedi shared/identity.py — nessuna nuova primitiva
crittografica, si riusa quella gia' in produzione) piu' un costo
computazionale (proof-of-work, come Bitmessage) per rendere costoso
inondare un relay di annunci falsi. I relay sono endpoint dedicati che
questo progetto controlla (una rotta sul control-plane, vedi
control-plane/main.py — /bottles/*), non piattaforme terze non pensate per
questo, quindi niente ban/ToS/pattern da dead-drop-resolver.

Una bottiglia e':
    {"pubkey": <hex>, "endpoint": <url>, "ts": <unix>, "nonce": <int>,
     "signature": <hex>}

Valida quando: (1) la firma ECDSA torna su tutto il payload tranne
'signature' — vedi shared/identity.py:verify_message — (2) sha256 del
payload (esclusi 'nonce' incluso e 'signature') ha almeno N bit a zero in
testa, (3) non e' piu' vecchia di max_age_s. Il nonce fa parte di cio' che
viene firmato: non si puo' scambiare un nonce dopo la firma senza invalidarla.
"""
from __future__ import annotations

import hashlib
import json
import time

from shared.network_security import normalize_http_base

DEFAULT_DIFFICULTY_BITS = 20
DEFAULT_MAX_AGE_S = 3600
MAX_BOTTLE_BYTES = 16 * 1024


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()


def _leading_zero_bits(digest: bytes) -> int:
    count = 0
    for byte in digest:
        if byte == 0:
            count += 8
            continue
        count += 8 - byte.bit_length()
        break
    return count


def _pow_digest(base: dict, nonce: int) -> bytes:
    return hashlib.sha256(_canonical({**base, "nonce": nonce})).digest()


def find_nonce(base: dict, difficulty_bits: int = DEFAULT_DIFFICULTY_BITS,
               max_attempts: int = 50_000_000) -> int:
    """Cerca un nonce che porti l'hash ad avere >= difficulty_bits zeri
    iniziali. max_attempts e' una protezione contro un loop infinito per
    errore, non un limite pensato per essere raggiunto in uso normale."""
    nonce = 0
    while nonce < max_attempts:
        if _leading_zero_bits(_pow_digest(base, nonce)) >= difficulty_bits:
            return nonce
        nonce += 1
    raise TimeoutError(f"nessun nonce trovato entro {max_attempts} tentativi "
                        f"(difficulty_bits={difficulty_bits} troppo alto?)")


def pow_satisfied(bottle: dict, difficulty_bits: int = DEFAULT_DIFFICULTY_BITS) -> bool:
    nonce = bottle.get("nonce")
    if not isinstance(nonce, int) or isinstance(nonce, bool):
        return False
    base = {k: v for k, v in bottle.items() if k not in ("nonce", "signature")}
    return _leading_zero_bits(_pow_digest(base, nonce)) >= difficulty_bits


def make_bottle(pubkey_hex: str, endpoint: str, private_key,
                 difficulty_bits: int = DEFAULT_DIFFICULTY_BITS) -> dict:
    """Crea e firma una bottiglia. Blocca per il tempo del mining — vedi
    docs/network-panel.md per i tempi misurati a diverse difficolta'."""
    from shared.identity import sign_message
    endpoint = normalize_http_base(endpoint)
    base = {"pubkey": pubkey_hex, "endpoint": endpoint, "ts": int(time.time())}
    nonce = find_nonce(base, difficulty_bits)
    return sign_message({**base, "nonce": nonce}, private_key)


def verify_bottle(bottle: dict, difficulty_bits: int = DEFAULT_DIFFICULTY_BITS,
                   max_age_s: int = DEFAULT_MAX_AGE_S) -> tuple[bool, str]:
    """Ritorna (valida, motivo). Il motivo e' sempre presente, anche
    quando valida=True (utile nei log del relay)."""
    from shared.identity import verify_message
    if not isinstance(bottle, dict):
        return False, "non e' un oggetto"
    try:
        if len(_canonical(bottle)) > MAX_BOTTLE_BYTES:
            return False, "bottiglia troppo grande"
    except (TypeError, ValueError):
        return False, "contenuto non serializzabile"
    pubkey = bottle.get("pubkey")
    signature = bottle.get("signature")
    if not isinstance(pubkey, str) or len(pubkey) not in {66, 130}:
        return False, "pubkey mancante o non valida"
    if not isinstance(signature, str) or not (16 <= len(signature) <= 256):
        return False, "firma mancante o non valida"
    if not isinstance(bottle.get("endpoint"), str) or not bottle["endpoint"]:
        return False, "endpoint mancante o non valido"
    if len(bottle["endpoint"]) > 2048:
        return False, "endpoint mancante o non valido"
    try:
        normalize_http_base(bottle["endpoint"])
    except ValueError:
        return False, "endpoint mancante o non valido"
    ts = bottle.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return False, "ts mancante o non numerico"
    if ts > time.time() + 60:  # tolleranza per piccoli disallineamenti d'orologio
        return False, "ts nel futuro"
    if time.time() - ts > max_age_s:
        return False, "bottiglia scaduta"
    if not pow_satisfied(bottle, difficulty_bits):
        return False, "proof-of-work non soddisfatto"
    if not verify_message(bottle):
        return False, "firma non valida"
    return True, "ok"

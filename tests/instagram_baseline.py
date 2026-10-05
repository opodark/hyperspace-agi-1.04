# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini Instagram, per confrontarle dopo un'estrazione.

Non e' un test: e' lo strumento che ha prodotto `tests/fixtures/instagram_baseline.json`.
Va eseguito con il server gia' in ascolto:

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/instagram_baseline.py

Motivazione: `main.py` non e' importabile in un test senza effetti collaterali
(scrive identita', apre il database, crea l'outbox), quindi l'unico modo per
avere un "verde prima" dell'estrazione di cp/instagram.py e' fotografare le
risposte del vero server. Dopo lo spostamento si rilancia e si confronta.

I quattro confini scelti sono quelli con effetti verso l'esterno:
  1. verifica del webhook in GET (challenge)
  2. firma valida in POST (accettato)
  3. firma sbagliata in POST (rifiutata)
  4. stato dell'outbox delle risposte
piu' un POST con payload di dispatch, che attraversa VIP, memoria e accodamento.
"""
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
SECRET = os.environ.get("INSTAGRAM_APP_SECRET", "baseline-secret")
VERIFY = os.environ.get("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "baseline-verify")
ADMIN = os.environ.get("NETWORK_ADMIN_TOKEN", "")

# ID fittizi: il dispatcher scarta gli eventi che arrivano dall'account del bot,
# quindi il mittente deve essere diverso da INSTAGRAM_USER_ID.
MITTENTE = "999000111"
MESSAGE_ID = "mid.baseline.1"

DISPATCH = {
    "object": "instagram",
    "entry": [{
        "id": "0",
        "messaging": [{
            "sender": {"id": MITTENTE, "username": "baseline_probe"},
            "recipient": {"id": "555"},
            "timestamp": 1700000000,
            "message": {"mid": MESSAGE_ID, "text": "ciao, disegnami un gatto"},
        }],
    }],
}


def _get(path: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(BASE_URL + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as risposta:
            return {"status": risposta.status, "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _post(path: str, payload: dict, headers: dict) -> dict:
    corpo = json.dumps(payload).encode("utf-8")
    richiesta = urllib.request.Request(BASE_URL + path, data=corpo,
                                       headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status, "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _firma(payload: dict, segreto: str) -> str:
    grezzo = json.dumps(payload).encode("utf-8")
    return "sha256=" + hmac.new(segreto.encode("utf-8"), grezzo, hashlib.sha256).hexdigest()


def raccogli() -> dict:
    return {
        "webhook_verify_valido": _get(
            f"/instagram/webhook?hub.mode=subscribe&hub.verify_token={VERIFY}"
            f"&hub.challenge=chalenge-di-prova"),
        "webhook_verify_sbagliato": _get(
            "/instagram/webhook?hub.mode=subscribe&hub.verify_token=token-sbagliato"
            "&hub.challenge=chalenge-di-prova"),
        "webhook_firma_valida": _post("/instagram/webhook", DISPATCH,
                                      {"X-Hub-Signature-256": _firma(DISPATCH, SECRET)}),
        "webhook_firma_sbagliata": _post("/instagram/webhook", DISPATCH,
                                        {"X-Hub-Signature-256": "sha256=" + "0" * 64}),
        "webhook_senza_firma": _post("/instagram/webhook", DISPATCH, {}),
        "replies_status": _get("/instagram/replies/status"),
        "vips": _get("/instagram/vips", {"X-Hyperspace-Network-Token": ADMIN}),
        "vips_senza_token": _get("/instagram/vips"),
        "memory_clear_inesistente": _post("/instagram/memory/clear", {"scoped_id": "404404"},
                                          {"X-Hyperspace-Network-Token": ADMIN}),
        "vips_creator_scoped_id_malformato": _post(
            "/instagram/vips/creator", {"scoped_id": "non-un-numero"},
            {"X-Hyperspace-Network-Token": ADMIN}),
    }


# Campi che cambiano a ogni esecuzione e che quindi non possono far fallire
# un confronto: gli orari sono il caso principale (`updated_at` nell'outbox).
VOLATILI = ("updated_at", "created_at", "received_at", "last_seen", "timestamp")


def _azzera_orari(nodo) -> None:
    if isinstance(nodo, dict):
        for chiave, valore in nodo.items():
            if chiave in VOLATILI:
                nodo[chiave] = "<orario>"
            else:
                _azzera_orari(valore)
    elif isinstance(nodo, list):
        for voce in nodo:
            _azzera_orari(voce)


def _appiattisci(risposta: dict) -> str:
    """Rende comparabili due risposte.

    Gli ordini dei dizionari non contano, e i campi di orario vengono azzerati:
    altrimenti ogni run produrrebbe una differenza che non è un difetto. Il body
    può essere JSON oppure testo puro (il challenge del webhook lo e'), quindi
    il confronto passa dalla stessa prova in entrambi i casi.
    """
    corpo = risposta.get("body", "")
    try:
        dati = json.loads(corpo)
    except (json.JSONDecodeError, ValueError):
        return f"{risposta['status']} testo:{corpo}"
    _azzera_orari(dati)
    return f"{risposta['status']} " + json.dumps(dati, sort_keys=True)


def confronta(vecchio: dict, nuovo: dict) -> int:
    problemi = 0
    for chiave in sorted(set(vecchio) | set(nuovo)):
        a, b = vecchio.get(chiave), nuovo.get(chiave)
        if a is None or b is None:
            print(f"  SOLO IN UNO   {chiave}: {a!r} / {b!r}")
            problemi += 1
        elif _appiattisci(a) != _appiattisci(b):
            print(f"  DIVERSO       {chiave}")
            print(f"      prima: {_appiattisci(a)[:200]}")
            print(f"      dopo:  {_appiattisci(b)[:200]}")
            problemi += 1
        else:
            print(f"  uguale        {chiave}  ({_appiattisci(a)[:56]})")
    return problemi


def main() -> int:
    if "--confronta" in sys.argv:
        vecchio = json.loads(Path("tests/fixtures/instagram_baseline.json").read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0

    risultato = raccogli()
    destinazione = Path("tests/fixtures/instagram_baseline.json")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(json.dumps(risultato, indent=2, sort_keys=True) + "\n", "utf-8")
    for chiave, valore in sorted(risultato.items()):
        print(f"  {valore['status']}  {chiave}")
    print(f"\n  scritto {destinazione}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

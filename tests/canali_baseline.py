# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini dei canali, per confrontarle dopo
un'estrazione.

Terza baseline del gruppo, con la stessa regola delle altre due: `main.py` non e'
importabile in un test senza effetti collaterali, quindi l'unico modo per avere un
"verde prima" di `cp/canali.py` e' fotografare il server vero.

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/canali_baseline.py
    ... --confronta

Le dieci route `/channel/*` hanno due interlocutori diversi e li due hanno
contratti diversi. Il **driver** le chiama per ogni decisione e per ogni
risposta, e conta sui codici: `401` senza token significa "non sei autorizzato" e
`503` significa "nessun canale configurato", e un driver che li confonde smette
di parlare con la persona. L'**operatore**, invece, le guarda da `/channels` e da
`/channel/status` per capire "perché il bot non ha risposto", e lì conta che non
escano segreti: i nomi dei canali sì, i token mai.

Quindi qui si fotografano tre cose: i codici del guard, il fatto che `/channels` e
`/channel/status` non filtrino nulla, e le risposte "non c'e' niente" — che
qui sono 200 con `{"ok": true}` e liste vuote, non 404 e non 204. `/channel/outbox`
a coda vuota risponde `200 {"messages": []}` e `/channel/commands` risponde
`200 {"commands": []}`: il driver deve poter distinguere "nessun ordine" da "rotta
rotta", e un 404 lo farebbe smettere di chiedere.

Un caso da non confondere: `/channel/vision` con corpo vuoto risponde **413**, non
400. La rotta guarda la foto prima del resto, e "foto assente o troppo grande" e'
un 413. Se qualcuno aspetta un 400 li' sbaglia il cliente, quindi e' nel fixture.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
CHANNEL_CLIENTS = os.environ.get("CHANNEL_CLIENTS", "")

VOLATILI = ("ts", "timestamp", "at", "last_seen", "ultimo", "ultima", "creato_ts",
            "preso_ts", "aggiornato_ts", "duration_ms", "durata_ms")


def _token_canale() -> str:
    """Il primo token valido. Si chiede a `shared.channel.parse_clients` invece di
    rifare il tagliamento a mano: la forma è `nome=token` separati da `;` e il
    token sotto i 32 caratteri viene scartato, quindi un parser qui sbagliato
    restituirebbe 503 su tutto e sembrerebbe un difetto del server."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.channel import parse_clients
    canali, _problemi = parse_clients(CHANNEL_CLIENTS)
    return next(iter(canali.values()), "")


def _get(path: str, headers: dict | None = None) -> dict:
    richiesta = urllib.request.Request(BASE_URL + path, headers=headers or {})
    try:
        with urllib.request.urlopen(richiesta, timeout=20) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _post(path: str, payload: dict, headers: dict | None = None) -> dict:
    corpo = json.dumps(payload).encode("utf-8")
    richiesta = urllib.request.Request(
        BASE_URL + path, data=corpo,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def raccogli() -> dict:
    canale = {"X-Hyperspace-Channel-Token": _token_canale()}
    return {
        # Il guard, che è la cosa che il driver conta.
        "outbox_senza_token": _get("/channel/outbox"),
        "ingest_senza_token": _post("/channel/ingest", {"messages": []}),
        "commands_senza_token": _get("/channel/commands"),
        "reply_senza_token": _post("/channel/reply", {"text": "ciao"}),

        # Le due rotte che l'operatore guarda: qui conta che non escano segreti.
        "channels_overview": _get("/channels", canale),
        "channel_status": _get("/channel/status", canale),

        # L'outbox a vuoto: come /image/jobs, una risposta normale.
        "outbox_vuoto": _get("/channel/outbox", canale),
        "outbox_ack_inesistente": _post("/channel/outbox/ack", {"id": "non-esiste"}, canale),

        # Le rotte di stato con dati che non esistono: devono dire 404, non 500.
        "state_inesistente": _post("/channel/state", {"id": "non-esiste"}, canale),
        "result_inesistente": _post("/channel/result", {"id": "non-esiste", "ok": True}, canale),
        "vision_corpo_vuoto": _post("/channel/vision", {}, canale),
        "ingest_corpo_vuoto": _post("/channel/ingest", {}, canale),
        "commands_vuoto": _get("/channel/commands", canale),
    }


def _azzera_orari(nodo) -> None:
    if isinstance(nodo, dict):
        for chiave, valore in nodo.items():
            if chiave in VOLATILI or chiave.endswith("_ts"):
                nodo[chiave] = "<orario>"
            else:
                _azzera_orari(valore)
    elif isinstance(nodo, list):
        for voce in nodo:
            _azzera_orari(voce)


def _appiattisci(risposta: dict) -> str:
    corpo = risposta.get("body", "")
    if not corpo.strip():
        return f"{risposta['status']} <vuoto>"
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
            print(f"      prima: {_appiattisci(a)[:220]}")
            print(f"      dopo:  {_appiattisci(b)[:220]}")
            problemi += 1
        else:
            print(f"  uguale        {chiave}  ({_appiattisci(a)[:56]})")
    return problemi


def main() -> int:
    if "--confronta" in sys.argv:
        vecchio = json.loads(Path("tests/fixtures/canali_baseline.json").read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0

    risultato = raccogli()
    destinazione = Path("tests/fixtures/canali_baseline.json")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(json.dumps(risultato, indent=2, sort_keys=True) + "\n", "utf-8")
    for chiave, valore in sorted(risultato.items()):
        print(f"  {valore['status']}  {chiave}")
    print(f"\n  scritto {destinazione}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
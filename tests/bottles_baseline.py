# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini delle bottiglie, per confrontarle dopo un'estrazione.

Non e' un test: e' lo strumento che ha prodotto `tests/fixtures/bottles_baseline.json`.
Va eseguito con il server gia' in ascolto:

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/bottles_baseline.py
    ... --confronta

Una bottiglia e' il passaporto di un nodo: firmato con la sua chiave, e con dentro
una proof-of-work, cosi' pubblicarne una costa. Il control-plane la verifica prima
di accoglierla, e la crea per se' quando un nodo si annuncia.

Le tre rotte e cosa provano:

  /bottles/list     cosa ha in giro adesso, e con che difficolta'
  /bottles/publish  la verifica: una bottiglia malformata o mal firmata viene rifiutata
  /bottles/announce la creazione lato server, e le sue guardie

L'ordine mette per primi i casi che rispondono subito e il caso felice per ultimo,
perche' creare una bottiglia costa circa un secondo e mezzo di proof-of-work: se il
server e' rotto, i primi casi dicono gia' perche'.

Sul caso felice: la risposta contiene un `nonce` e una firma, diverse a ogni
esecuzione per costruzione — sono cio'' la prova che la proof-of-work e' stata rifatta,
non un difetto. Vengono azzerate insieme agli orari. Non e' una normalizzazione
comoda: e' quello che rende la baseline capace di notare che la firma e' sparita,
il che sarebbe il modo silenzioso in cui questa rotta smette di proteggere qualcosa.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
ADMIN = os.environ.get("NETWORK_ADMIN_TOKEN", "")

SOTTOPESI = {"X-Hyperspace-Network-Token": ADMIN} if ADMIN else {}

# Una bottiglia ben formata nella sua struttura, ma con valori che non reggono:
# nessuna firma, nessuna proof-of-work. Serve per vedere il messaggio che il
# server dà quando rifiuta, che è diverso per "manca tutto" e per "c'è ma è falso".
# La forma e' quella giusta di proposito: `verify_bottle` controlla prima che la
# pubkey sia 66 o 130 caratteri e che la firma sia lunga 16-256, e una bottiglia
# malformata morirebbe li'. Per arrivare al controllo che conta — la firma —
# questa ha tutto della forma giusta e niente del contenuto.
BOTTIGLIA_FALSA = {
    "pubkey": "04" + "ab" * 64,          # 130 caratteri: chiave pubblica non compressa
    "signature": "cd" * 32,              # lunghezza plausibile, ma non firma niente
    "endpoint": "http://nodo-che-non-esiste.example:11434",
    "ts": int(time.time()),   # adesso: con un orario fisso la bottiglia
                             # scaderebbe, e morirebbe sul controllo dell'eta'
                             # invece che su quello della firma
    "nonce": 0,
}

# Sopra MAX_BOTTLE_BYTES (16 KiB): la rotta deve dire "troppo grande" prima ancora di
# guardare la firma. E' un controllo che si vede solo se si lo provoca.
GOMMA = "x" * (17 * 1024)


def _richiesta(metodo: str, path: str, payload=None, headers: dict | None = None,
               grezzo: bytes | None = None) -> dict:
    dati = grezzo if grezzo is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None)
    testate = {"Content-Type": "application/json", **SOTTOPESI, **(headers or {})}
    richiesta = urllib.request.Request(BASE_URL + path, data=dati,
                                       headers=testate, method=metodo)
    try:
        with urllib.request.urlopen(richiesta, timeout=60) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _get(path: str) -> dict:
    return _richiesta("GET", path)


def _post(path: str, payload=None, headers: dict | None = None, grezzo: bytes | None = None) -> dict:
    return _richiesta("POST", path, payload, headers, grezzo)


def raccogli() -> dict:
    """Esegue le richieste nell'ordine in cui sono scritte: l'elenco cambia dopo
    l'annuncio, quindi l'ordine non e' un dettaglio."""
    caso = {}

    # all'inizio non gira niente
    caso["lista_vuota"] = _get("/bottles/list")

    # la verifica. Il corpo vuoto e la bottiglia falsa danno due messaggi diversi,
    # e sono due controlli diversi: il primo e' "non hai mandato niente", il
    # secondo e' "ho provato a verificarlo e non regge".
    caso["publish_corpo_vuoto"] = _post("/bottles/publish", {})
    caso["publish_bottiglia_falsa"] = _post("/bottles/publish", BOTTIGLIA_FALSA)
    caso["publish_troppo_grande"] = _post("/bottles/publish",
                                          {"riempimento": GOMMA})

    # annuncio: le guardie, che rispondono subito
    caso["announce_senza_token"] = _post("/bottles/announce", {}, headers={"X-Hyperspace-Network-Token": ""})
    caso["announce_endpoint_dal_client"] = _post("/bottles/announce",
                                                 {"endpoint": "http://attaccato.example"})
    caso["announce_relay_dal_client"] = _post("/bottles/announce",
                                              {"relay_url": "http://attaccato.example"})
    caso["announce_corpo_non_oggetto"] = _post("/bottles/announce", ["non", "un", "oggetto"])

    # e il caso felice, per ultimo: costa una proof-of-work
    caso["announce_ok"] = _post("/bottles/announce", {})
    caso["lista_dopo_annuncio"] = _get("/bottles/list")

    return caso


# `nonce` e `sig` cambiano a ogni esecuzione per costruzione: sono la prova che la
# proof-of-work e' stata rifatta. Azzerarli e' il prezzo per poter notare la loro
# sparizione, che e' il guasto silenzioso di questa rotta.
VOLATILI = ("ts", "at", "last_seen", "ultimo", "ultima", "created_at", "updated_at",
            "added_at", "generated_at", "nonce", "sig", "signature", "uptime",
            "uptime_s",
            "duration_ms", "elapsed_ms", "_ts", "sampled_at", "last_status")


def _azzera_orari(nodo) -> None:
    if isinstance(nodo, dict):
        for chiave, valore in nodo.items():
            if chiave in VOLATILI or chiave.endswith("_ts"):
                nodo[chiave] = "<variabile>"
            else:
                _azzera_orari(valore)
    elif isinstance(nodo, list):
        for voce in nodo:
            _azzera_orari(voce)


def _normalizza(testo: str) -> str:
    """Il contenuto e' JSON quasi sempre; dove non lo e' resta grezzo."""
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera_orari(dati)
    return json.dumps(dati, sort_keys=True)


def _norm(testo: str) -> str:
    return testo.replace("\n", "\\n")


def confronta(vecchio: dict, nuovo: dict) -> int:
    problemi = 0
    for chiave in sorted(set(vecchio) | set(nuovo)):
        prima = vecchio.get(chiave, {})
        dopo = nuovo.get(chiave, {})
        a = (prima.get("status"), _normalizza(prima.get("body", "")))
        b = (dopo.get("status"), _normalizza(dopo.get("body", "")))
        if a == b:
            print(f"  uguale        {chiave:<28} {a[0]} {_norm(a[1])[:70]!r}")
            continue
        problemi += 1
        print(f"  DIVERSO       {chiave}")
        print(f"      prima:  {a[0]} {_norm(a[1])[:150]!r}")
        print(f"      dopo:   {b[0]} {_norm(b[1])[:150]!r}")
    return problemi


def _stampa(risultato: dict, destinazione) -> int:
    crash = sorted(k for k, v in risultato.items() if v["status"] == 500)
    if crash:
        print(f"  ATTENZIONE: {len(crash)} rotte in 500, la fixture NON e' stata scritta:")
        for chiave in crash:
            print(f"    {chiave}: {risultato[chiave]['body'][:120]!r}")
        return 1
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(json.dumps(risultato, indent=2, sort_keys=True) + "\n", "utf-8")
    for chiave, valore in sorted(risultato.items()):
        print(f"  {valore['status']}  {chiave:<28} {_norm(_normalizza(valore['body']))[:70]!r}")
    print(f"\n  scritto {destinazione}")
    return 0


def main() -> int:
    from pathlib import Path

    destinazione = Path("tests/fixtures/bottles_baseline.json")
    if "--confronta" in sys.argv:
        vecchio = json.loads(destinazione.read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0
    return _stampa(raccogli(), destinazione)


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini della memoria, per confrontarli dopo un'estrazione.

Non e' un test: e' lo strumento che ha prodotto `tests/fixtures/memoria_baseline.json`.
Va eseguito con il server gia' in ascolto:

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/memoria_baseline.py
    ... --confronta

La memoria ha due backend — `legacy` (un file) e `hermes` (un archivio esterno) — e
questa baseline gira con `legacy`, che e' il default. E' una scelta, non una
limite: i due backend prendono strade diverse nella stessa rotta, e verificarle
entrambe richiederebbe un archivio Hermes in piedi. Quello che resta verificato e'
che il ramo giusto viene preso, e si vede dai messaggi: ogni rotta dice quale
backend ha risposto.

Per quello che riguarda i contenuti, la baseline azzera quasi tutto. Una voce di
memoria contiene testo libero, timestamp e il nome di chi l'ha scritta, e tutto
cambia fra un'esecuzione e l'altra: un confronto letterale segnalerebbe come
differenza la prova che il canale funziona. Quindi qui si verifica la FORMA — quali
campi ci sono, quali errori escono, quale backend risponde — non il contenuto.

Un caso in piu' che vale la pena: la ricerca. E' l'unica rotta che accetta un
input e lo usa davvero, quindi e' l'unica dove un'estrazione potrebbe passare un
parametro al posto sbagliato senza che nessuno se ne accorga. Il caso chiede una
parola e verifica che torni qualcosa di definito anche quando non trova niente.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")


def _richiesta(metodo: str, path: str, payload=None) -> dict:
    dati = json.dumps(payload).encode("utf-8") if payload is not None else None
    richiesta = urllib.request.Request(BASE_URL + path, data=dati,
                                       headers={"Content-Type": "application/json"},
                                       method=metodo)
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _get(path: str) -> dict:
    return _richiesta("GET", path)


def _post(path: str, payload=None) -> dict:
    return _richiesta("POST", path, payload)


def raccogli() -> dict:
    caso = {}

    # la lettura e le statistiche: il caso normale, su un archivio vuoto
    caso["leggi"] = _get("/memory")
    caso["leggi_con_limite"] = _get("/memory?limit=1")
    caso["statistiche"] = _get("/memory/stats")

    # la scrittura: senza voce e con voce non-oggetto, che sono due errori diversi
    caso["push_vuoto"] = _post("/memory/push", {})
    caso["push_non_oggetto"] = _post("/memory/push", {"entry": "non un oggetto"})

    # la ricerca: l'unica rotta che usa davvero l'input
    caso["cerca_vuota"] = _post("/memory/search", {})
    caso["cerca_parola"] = _post("/memory/search", {"query": "baseline", "limit": 5})

    # sincronizzazione e ciclo di vita: il ciclo di vita e' solo con Hermes, e
    # dire "non e' disponibile" e' una risposta corretta, non un'assenza
    caso["sync"] = _post("/memory/sync", {})
    caso["lifecycle"] = _post("/memory/lifecycle", {})

    # e una scrittura vera, che e' il caso per cui questa baseline esiste: senza,
    # si verificherebbe solo che le rotte rispondono a memoria vuota, che e' anche
    # cio' che fanno quando il file e' sparito. Scrivere, rileggere e ritrovare
    # quello che si e' appena scritto e' l'unico modo di sapere che il canale
    # funziona in entrambe le direzioni.
    caso["push"] = _post("/memory/push", {"entry": {"text": "voce di baseline",
                                                    "who": "baseline"}})
    caso["leggi_dopo_push"] = _get("/memory?limit=5")
    caso["cerca_dopo_push"] = _post("/memory/search", {"query": "baseline", "limit": 5})
    caso["statistiche_dopo_push"] = _get("/memory/stats")

    return caso


VOLATILI = ("ts", "at", "created_at", "updated_at", "last_used", "uses", "count",
            "mtime", "size", "bytes", "free", "used")


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


def _azzera_contenuti(nodo) -> None:
    """Le voci di memoria hanno testo libero e nomi di file: si azzerano.

    Non e' una scorciatoia: e' cio' che rende la baseline capace di notare che il
    campo `text` e' sparito — che sarebbe un difetto vero — mentre lascia notare
    che il campo c'e' ancora e cambia valore.
    """
    if isinstance(nodo, dict):
        for chiave in ("text", "who", "source", "note"):
            if chiave in nodo and isinstance(nodo[chiave], str):
                nodo[chiave] = "<testo>"
        for chiave in ("file", "path", "filename"):
            if chiave in nodo and isinstance(nodo[chiave], str):
                nodo[chiave] = "<file>"
        for valore in nodo.values():
            _azzera_contenuti(valore)
    elif isinstance(nodo, list):
        for voce in nodo:
            _azzera_contenuti(voce)


def _normalizza(testo: str) -> str:
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera_orari(dati)
    _azzera_contenuti(dati)
    if isinstance(dati, dict) and isinstance(dati.get("entries"), list):
        dati["total"] = f"<{len(dati['entries'])} voci>"
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

    destinazione = Path("tests/fixtures/memoria_baseline.json")
    if "--confronta" in sys.argv:
        vecchio = json.loads(destinazione.read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0
    return _stampa(raccogli(), destinazione)


if __name__ == "__main__":
    raise SystemExit(main())

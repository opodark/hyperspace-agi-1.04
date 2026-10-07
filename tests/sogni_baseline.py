# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini dei sogni, per confrontarli dopo un'estrazione.

Non e' un test: e' lo strumento che ha prodotto `tests/fixtures/sogni_baseline.json`.
Va eseguito con il server gia' in ascolto:

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/sogni_baseline.py
    ... --confronta

I sogni sono di due famiglie, ed è la distinzione che rende leggibile questo
dominio:

  /persona/dreams*        i sogni di identità — cosa ha pensato di sé
  /dreams*                i sogni di un NODO — cosa ha osservato della rete
  /development-dreams*    i sogni di sviluppo — cosa proverebbe a cambiare

Le rotte sono quasi tutte di lettura, e le guardie contano più del contenuto: una
pagina che elenca i sogni non deve mai scriverli, e una che ne richiede la
revisione non deve poterlo fare senza il token.

I casi sono quasi tutti percorsi di rifiuto, e non per pigrizia. Il dominio gira
di notte: in un ambiente di prova non c'e' un sogno da mostrare, e un percorso
felice qui richiederebbe un modello e una notte. Quello che la baseline verifica
e' che ogni rotta risponda qualcosa di definito anche a vuoto — che e' la parte
che un'estrazione puo' rompere, spostando una guardia o un nome.

Il caso che prova il percorso felice e' uno solo: il riepilogo dei sogni di identita'
su un dominio che non ne ha. Se risponde 200 con un elenco vuoto, la rotta e' viva,
ha letto lo stato giusto e non ha sollevato niente.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
ADMIN = os.environ.get("NETWORK_ADMIN_TOKEN", "")

SOTTOPESI = {"X-Hyperspace-Network-Token": ADMIN} if ADMIN else {}
DREAM_TOKEN = os.environ.get("PERSONA_DREAM_TOKEN", "")


def _richiesta(metodo: str, path: str, payload=None, headers: dict | None = None) -> dict:
    dati = json.dumps(payload).encode("utf-8") if payload is not None else None
    testate = {"Content-Type": "application/json", **(headers if headers is not None else SOTTOPESI)}
    richiesta = urllib.request.Request(BASE_URL + path, data=dati,
                                       headers=testate, method=metodo)
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _get(path: str, headers: dict | None = None) -> dict:
    return _richiesta("GET", path, headers=headers)


def _post(path: str, payload=None, headers: dict | None = None) -> dict:
    return _richiesta("POST", path, payload, headers)


def raccogli() -> dict:
    caso = {}

    # i sogni di identita': se non sono inizializzati devono dirlo (503), non
    # restituire un elenco vuoto che sembrerebbe "non hai sognato"
    caso["persona_dreams"] = _get("/persona/dreams")
    caso["persona_dream"] = _post("/persona/dream")
    caso["persona_dream_review"] = _post("/persona/dreams/inesistente/review", {})

    # i sogni di un nodo: le rotte contattano il nodo, quindi su una rete vuota
    # rispondono che il nodo non c'e' — e quello e' il comportamento giusto
    caso["dream_node_status"] = _get("/dreams/status")
    caso["dream_node_list"] = _get("/dreams")
    caso["dream_node_insights"] = _get("/dreams/insights")
    caso["dream_node_review"] = _post("/dreams/inesistente/review", {})

    # i sogni di sviluppo
    caso["dev_dream_status"] = _get("/development-dreams/status")
    caso["dev_dream_list"] = _get("/development-dreams")
    caso["dev_dream_review"] = _post("/development-dreams/inesistente/review", {})
    caso["dev_dream_run"] = _post("/development-dreams/run", {})

    return caso


VOLATILI = ("ts", "at", "last_seen", "ultimo", "ultima", "created_at", "updated_at",
            "uptime", "duration_ms", "elapsed_ms", "_ts", "sampled_at", "started_at",
            "finished_at", "ran_at", "next_run", "generated_at", "cycle")


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
    """Il contenuto e' JSON quasi sempre; dove non lo e' resta grezzo.

    Un sogno contiene un testo libero e i nomi dei file che ha prodotto, quindi
    non e' tutto volatile da azzerare: si lascia il testo com'e'. L'identita' del
    nodo invece cambia a ogni esecuzione, e quella si azzera.
    """
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera_orari(dati)

    def azzera_id(nodo) -> None:
        if isinstance(nodo, dict):
            for chiave in ("node_id", "id"):
                valore = nodo.get(chiave)
                if isinstance(valore, str) and len(valore) > 8:
                    nodo[chiave] = "<identita'>"
            for v in nodo.values():
                azzera_id(v)
        elif isinstance(nodo, list):
            for v in nodo:
                azzera_id(v)

    azzera_id(dati)
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

    destinazione = Path("tests/fixtures/sogni_baseline.json")
    if "--confronta" in sys.argv:
        vecchio = json.loads(destinazione.read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0
    return _stampa(raccogli(), destinazione)


if __name__ == "__main__":
    raise SystemExit(main())

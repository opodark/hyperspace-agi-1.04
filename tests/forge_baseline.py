# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini del forge, per confrontarli dopo un'estrazione.

Non e' un test: e' lo strumento che ha prodotto `tests/fixtures/forge_baseline.json`.
Va eseguito con il server gia' in ascolto:

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/forge_baseline.py
    ... --confronta

Il forge e' il magazzino degli artefatti: tool e skill che il control-plane puo'
scrivere su disco e poi usare.

La cosa che questa baseline ha scoperto, e che vale piu' dei numeri: il forge non
protegge tutto con il token, e non e' un oversight — e' che il token protegge il
PASSAGGIO DI STATO, non la scrittura. Creare una bozza e correggerla si puo' fare
senza token; quello che richiede `FORGE_ADMIN_TOKEN` e' approvare, e importare
l'ECC. Il motivo e' che una bozza non viene eseguita: senza approvazione resta
in `review` e nessun modulo la carica. Il confine quindi non e' "chi puo' scrivere"
ma "chi puo' rendere eseguibile", ed e' la seconda domanda.

Percio' i casi non-token di questa baseline non sono tutti 403, e non e' un
errore dei test: e' il modello di sicurezza. Se un'estrazione li rendesse tutti
403 sarebbe piu' rigido, e se li rendesse tutti 200 sarebbe una regressione —
l'approvazione diventerebbe libera. Sono due difetti opposti, e servono casi
separati per distinguerli.

L'id di un artefatto ha un suffisso casuale, quindi la baseline lo prende dalla
risposta della creazione e lo riusa: senza, tutte le richieste successive
punterebbero a un id che non esiste, e i 404 che ne seguono sembrerebbero il
comportamento giusto.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
FORGE_TOKEN = os.environ.get("FORGE_ADMIN_TOKEN", "")
ARTIFACT_ID = "baseline-prova"

AUTORIZZATO = {"X-Hyperspace-Forge-Token": FORGE_TOKEN} if FORGE_TOKEN else {}
NESSUNO = {"X-Hyperspace-Forge-Token": ""}


def _richiesta(metodo: str, path: str, payload=None, headers: dict | None = None) -> dict:
    dati = json.dumps(payload).encode("utf-8") if payload is not None else None
    richiesta = urllib.request.Request(BASE_URL + path, data=dati,
                                       headers={"Content-Type": "application/json",
                                                **(headers or {})},
                                       method=metodo)
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _get(path: str, headers: dict | None = None) -> dict:
    return _richiesta("GET", path, headers=headers)


def _post(path: str, payload, headers: dict | None = None) -> dict:
    return _richiesta("POST", path, payload, headers)


def raccogli() -> dict:
    caso = {}

    # le rotte di lettura, che sono pubbliche
    caso["lista"] = _get("/forge/artifacts")
    caso["config"] = _get("/forge/config")

    # le validazioni, che sono distinte fra loro e vanno distinte anche qui:
    # un messaggio d'errore che si confonde con un altro e' un errore che non si
    # nota quando si rompe
    caso["create_tipo_sbagliato"] = _post("/forge/artifacts", {"type": "inesistente"})
    caso["create_nome_vuoto"] = _post("/forge/artifacts", {"type": "skill", "name": "  "})
    caso["status_valore_sbagliato"] = _post("/forge/artifacts/qualunque/status",
                                            {"status": "inesistente"}, AUTORIZZATO)
    caso["genera_tipo_sbagliato"] = _post("/forge/generate", {"type": "inesistente"},
                                         AUTORIZZATO)

    # il percorso che scrive davvero. L'id lo prendo dalla risposta: ha un
    # suffisso casuale, e puntare a uno scelto a mano darebbe 404 ovunque.
    creato = _post("/forge/artifacts",
                   {"type": "skill", "name": "baseline-prova", "source": "# prima\n"})
    caso["create"] = creato
    caso["genera_senza_descrizione"] = _post("/forge/generate",
                                             {"type": "skill", "name": "prova"})
    try:
        artefatto = json.loads(creato.get("body", "")).get("id", "nessuno")
    except (json.JSONDecodeError, ValueError):
        artefatto = "nessuno"
    caso["leggi_con_get"] = _get(f"/forge/artifacts/{artefatto}", AUTORIZZATO)
    caso["aggiorna"] = _richiesta("PUT", f"/forge/artifacts/{artefatto}",
                                  {"source": "# seconda\n"})
    caso["aggiorna_inesistente"] = _richiesta("PUT", "/forge/artifacts/non-esiste",
                                              {"source": "# tentativo\n"})
    caso["stato"] = _post(f"/forge/artifacts/{artefatto}/status",
                          {"status": "review"}, AUTORIZZATO)
    caso["lista_dopo"] = _get("/forge/artifacts")

    # e il passaggio che il token protegge davvero: approvare e importare
    caso["status_senza_token"] = _post(f"/forge/artifacts/{artefatto}/status",
                                      {"status": "approved"}, NESSUNO)
    caso["import_senza_token"] = _post("/forge/import/ecc", {}, NESSUNO)

    return caso


VOLATILI = ("ts", "at", "created_at", "updated_at", "mtime", "imported_at", "reviewed_at")


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

    Il percorso assoluto del forge contiene la directory di lavoro, che cambia da
    una macchina all'altra: quello si azzera, il resto no.
    """
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    # L'id di un artefatto e' il suo slug piu' un suffisso casuale. Non e' un
    # difetto: e' cio' che evita che due artefatti con lo stesso nome si
    # sovrapongano. Ma allora ogni esecuzione ne produce uno diverso, e la
    # baseline lo deve sapere — altrimenti segnalerebbe come cambiamento la
    # prova che il percorso di scrittura funziona.
    def azzera_id(nodo) -> None:
        if isinstance(nodo, dict):
            valore = nodo.get("id")
            if isinstance(valore, str) and "-" in valore:
                nodo["id"] = valore.rsplit("-", 1)[0] + "-<casuale>"
            for v in nodo.values():
                azzera_id(v)
        elif isinstance(nodo, list):
            for v in nodo:
                azzera_id(v)

    _azzera_orari(dati)
    azzera_id(dati)
    testo = json.dumps(dati, sort_keys=True)
    radice = os.environ.get("FORGE_DIR", "")
    if radice:
        testo = testo.replace(radice, "<forge>")
    return testo


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

    destinazione = Path("tests/fixtures/forge_baseline.json")
    if "--confronta" in sys.argv:
        vecchio = json.loads(destinazione.read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0
    return _stampa(raccogli(), destinazione)


if __name__ == "__main__":
    raise SystemExit(main())

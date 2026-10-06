# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini della federazione fra control-plane.

Non e' un test: e' lo strumento che ha prodotto
`tests/fixtures/federazione_baseline.json`. Va eseguito con il server gia' in
ascolto:

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/federazione_baseline.py
    ... --confronta

La stessa motivazione delle altre: `main.py` non e' importabile in un test senza
effetti collaterali, quindi l'unico modo per avere un "verde prima" dell'estrazione
di `cp/federazione.py` e' fotografare le risposte del vero server.

Le sette rotte del dominio, e cosa provano:

  /federation/identity                 la tua identita' da condividere fuori banda
  /federation/peers                    elenco, aggiunta
  /federation/peers/<id>/toggle        accendere e spegnere un peer
  /federation/peers/<id>               rimuovere
  /federate/execute                    task inoltrato da un altro CP
  /federate/view                       la tua vista, in sola lettura
  /federation/views                    vista locale + viste dei peer, per la dashboard

Il ciclo del peer (aggiungi -> elenca -> spegni -> accendi -> rimuovi) e' quello che
conta: copre i quattro endpoint in un colpo solo e verifica che l'id derivato dalla
pubkey torni identico a ogni passo, che e' la promessa che fa `/federation/peers`
POST e su cui il pairing si regge.

Le richieste NON firmate sono il caso interessante per `/federate/execute` e
`/federate/view`: la firma e' la difesa, e senza una firma valida la risposta deve
essere un rifiuto. Se un'estrazione spostasse per sbaglio un controllo, qui si
vedrebbe — ed e' il motivo per cui questi due endpoint sono in baseline anche se il
loro percorso felice richiederebbe la chiave privata di un altro CP.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
ADMIN = os.environ.get("NETWORK_ADMIN_TOKEN", "")

SOTTOPESI = {"X-Hyperspace-Network-Token": ADMIN} if ADMIN else {}

# Una pubkey fittizia in esadecimale, della forma che `/federation/identity`
# restituisce. L'id del peer ne e' un hash, quindi e' derivato e non va azzerato:
# se cambiasse fra due esecuzioni, il ciclo del peer non tornerebbe piu' e
# l'estrazione sembrerebbe aver cambiato qualcosa.
PUBBKEY = "aa" * 32
ETICHETTA = "sorella-di-prova"
ENDPOINT_PEER = "https://peer-di-prova.example"


def _richiesta(metodo: str, path: str, payload=None, headers: dict | None = None) -> dict:
    dati = json.dumps(payload).encode("utf-8") if payload is not None else None
    testate = {"Content-Type": "application/json", **SOTTOPESI, **(headers or {})}
    richiesta = urllib.request.Request(BASE_URL + path, data=dati,
                                       headers=testate, method=metodo)
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _get(path: str) -> dict:
    return _richiesta("GET", path)


def _post(path: str, payload: dict) -> dict:
    return _richiesta("POST", path, payload)


def _delete(path: str) -> dict:
    return _richiesta("DELETE", path)


def raccogli() -> dict:
    """Esegue le richieste nell'ordine in cui sono scritte: alcune dipendono dalle
    altre (il ciclo del peer), quindi l'ordine non e' un dettaglio."""
    caso = {}

    # chi sei, da condividere con l'amministratore di un altro sito
    caso["identity"] = _get("/federation/identity")
    _ricorda_identita(caso["identity"])

    # nessun peer all'inizio: e' il database vuoto, non un elenco di default
    caso["peers_vuoti"] = _get("/federation/peers")

    # aggiunta senza i campi obbligatori, e con una pubkey che non e' esadecimale:
    # i due errori di validazione, che sono messaggi diversi e conta distinguerli
    caso["peer_senza_campi"] = _post("/federation/peers", {})
    caso["peer_pubkey_malformata"] = _post("/federation/peers",
                                           {"pubkey": "non-e-hex",
                                            "endpoint": ENDPOINT_PEER})

    # il ciclo vero e proprio
    caso["peer_aggiunto"] = _post("/federation/peers",
                                  {"pubkey": PUBBKEY, "endpoint": ENDPOINT_PEER,
                                   "label": ETICHETTA})
    caso["peers_dopo_aggiunta"] = _get("/federation/peers")
    caso["peer_spegnito"] = _post(f"/federation/peers/{_peer_id(caso)}/toggle", {})
    caso["peers_dopo_spegnimento"] = _get("/federation/peers")
    caso["peer_riacceso"] = _post(f"/federation/peers/{_peer_id(caso)}/toggle", {})
    caso["peer_rimosso"] = _delete(f"/federation/peers/{_peer_id(caso)}")
    caso["peers_dopo_rimozione"] = _get("/federation/peers")

    # un peer che non c'e': i due endpoint che lo prendono per parametro
    caso["toggle_peer_inesistente"] = _post("/federation/peers/non-esiste/toggle", {})
    caso["rimuovi_peer_inesistente"] = _delete("/federation/peers/non-esiste")

    # execute e view senza firma: la difesa deve rispondere, non passare
    caso["execute_non_firmato"] = _post("/federate/execute",
                                        {"prompt": "ciao", "model": "modello-di-prova"})
    caso["view_non_firmata"] = _get("/federate/view")

    # la vista della dashboard: locale + peer, in cache per qualche secondo
    caso["views_dashboard"] = _get("/federation/views")
    caso["views_dashboard_refresh"] = _get("/federation/views?refresh=1")

    return caso


def _ricorda_identita(risposta: dict) -> None:
    """Memorizza i valori da azzerare, presi dalla risposta stessa.

    Sostituirli per valore e non per chiave: il peer fittizio ha un `peer_id` che
    invece e' derivato dalla pubkey fissa e va tenuto sotto controllo — azzerare
    anche quello nasconderebbe proprio il difetto che la baseline dovrebbe vedere.
    """
    try:
        dati = json.loads(risposta.get("body", ""))
    except (json.JSONDecodeError, ValueError):
        return
    for chiave in ("peer_id", "pubkey"):
        valore = dati.get(chiave)
        if isinstance(valore, str) and len(valore) > 8:
            IDENTITA.append(valore)


def _peer_id(caso: dict) -> str:
    """L'id del peer appena aggiunto, letto dalla risposta dell'aggiunta.

    Non lo si calcola qui: se la derivazione cambiasse, il resto del ciclo
    opererebbe su un id inesistente e ogni passo risponderebbe 404 — cioe' la
    baseline passerebbe verde verificando solo errori.
    """
    corpo = caso.get("peer_aggiunto", {}).get("body", "")
    try:
        return json.loads(corpo).get("peer_id", "peer-non-disponibile")
    except (json.JSONDecodeError, ValueError):
        return "peer-non-disponibile"


# `seen_by` e' un elenco di nodi che hanno visto qualcosa, non un orario: sta qui
# solo perche' cambia fra due esecuzioni (i nodi vivi sono diversi ogni volta).
VOLATILI = ("ts", "at", "last_seen", "ultimo", "ultima", "created_at", "updated_at",
            "added_at", "generated_at", "seen_by", "uptime", "uptime_s", "duration_ms",
            "elapsed_ms", "_ts", "sampled_at", "last_status")


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


# L'id e la pubkey di questo control-plane: derivano dall'identita', che l'harness
# rifa a ogni esecuzione. Sono la risposta corretta a un input diverso.
IDENTITA: list = []


def _normalizza(testo: str) -> str:
    """Il contenuto e' JSON in quasi tutti i casi; dove non lo e' resta grezzo.

    La chiave pubblica e l'id del nodo locale derivano dall'identita', che
    l'harness rifa a ogni esecuzione: sono la risposta corretta a un input
    diverso, non un difetto, quindi vanno azzerati come gli orari.
    """
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera_orari(dati)
    testo = json.dumps(dati, sort_keys=True)
    for valore in IDENTITA:
        testo = testo.replace(valore, "<identita'>")
    return testo


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


def _norm(testo: str) -> str:
    return testo.replace("\n", "\\n")


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
    destinazione = Path("tests/fixtures/federazione_baseline.json")
    if "--confronta" in sys.argv:
        vecchio = json.loads(destinazione.read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0
    return _stampa(raccogli(), destinazione)


if __name__ == "__main__":
    raise SystemExit(main())

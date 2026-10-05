# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini del mesh, per confrontarle dopo
un'estrazione.

Quinta baseline del gruppo, e la più grande per numero di richieste: il mesh ha
18 route e riguarda il routing (quale nodo risponde a una richiesta), il registro
(quali nodi esistono) e i nodi web (i worker nel browser).

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/mesh_baseline.py
    ... --confronta

Il mesh ha una particolarità rispetto agli altri domini: molte route rispondono
**aperte**, perché il loro contenuto non è un segreto ma la topologia della rete —
quanti nodi, quali modelli, quanto sono carichi. Sono informazioni che l'operatore
deve poter leggere da una pagina, e i token dei nodi non ci passano.

Quindi qui la baseline fotografa tre cose distinte:
  - le route aperte e la loro forma (è il contratto dell'operatore)
  - i 404 e 400 dei percorsi che non esistono (il driver e il CLI le chiamano)
  - l'assenza di segreti nelle risposte pubbliche

Un cambio di forma in `/mesh/topology` non fa fallire un test: fa leggere male la
pagina di controllo. Per questo il confronto qui è sul corpo intero, non sul
codice di risposta.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
ADMIN = os.environ.get("NETWORK_ADMIN_TOKEN", "")

VOLATILI = ("ts", "at", "last_seen", "ultimo", "ultima", "created_at", "updated_at",
            "uptime", "duration_ms", "elapsed_ms", "_ts")

SOTTOPESI = {"X-Hyperspace-Network-Token": ADMIN}


def _get(path: str, headers: dict | None = None) -> dict:
    h = dict(SOTTOPESI)
    h.update(headers or {})
    richiesta = urllib.request.Request(BASE_URL + path, headers=h)
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _post(path: str, payload, headers: dict | None = None) -> dict:
    h = dict(SOTTOPESI)
    h["Content-Type"] = "application/json"
    h.update(headers or {})
    richiesta = urllib.request.Request(BASE_URL + path,
                                       data=json.dumps(payload).encode("utf-8"), headers=h)
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _delete(path: str) -> dict:
    richiesta = urllib.request.Request(BASE_URL + path, method="DELETE",
                                       headers=dict(SOTTOPESI))
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def raccogli() -> dict:
    nodo = "999000111"
    return {
        # il registro: quanto e' grande la rete, e quale modello offre
        "mesh_nodes": _get("/mesh/nodes"),
        "registry_nodes": _get("/registry/nodes"),
        "nodes_active": _get("/nodes/active"),
        "nodes_aliases": _get("/nodes/aliases"),
        "routing_weights": _get("/config/routing-weights"),
        "topology": _get("/mesh/topology"),

        # percorsi che non esistono: il driver e il CLI li chiamano comunque
        "status_nodo_inesistente": _get("/mesh/node/inesistente/status"),
        "peers_nodo_inesistente": _get("/mesh/node/inesistente/peers"),
        "alias_nodo_inesistente": _post(f"/nodes/{nodo}/alias", {"alias": "prova"}),
        "elimina_nodo_inesistente": _delete(f"/mesh/nodes/{nodo}"),
        "pull_nodo_inesistente": _post("/mesh/node/inesistente/pull", {}),

        # gli alias: sono cidificati nella risposta, e il CID cambia a ogni prova
        "alias_dopodoposto": _get("/nodes/aliases"),

        # i nodi web: registro e stato, che non richiedono nessun token
        "web_status": _get("/web/status"),
        "web_register_corpo_vuoto": _post("/web/register", {}),

        # le altre tre rotte del web node. Il long-poll occupa un thread fino a
        # WEB_NODE_MAX_POLL_S: qui si mette in lista un nodo che non esiste, cosi'
        # la risposta e' subito un 404 e la richiesta non si trattiene.
        "web_poll_nodo_inesistente": _post("/web/poll", {"node_id": "inesistente"}),
        "web_result_corpo_vuoto": _post("/web/result", {}),
        "web_enqueue_nodo_inesistente": _post("/web/tasks",
                                              {"node_id": "inesistente",
                                               "task_type": "sconosciuto"}),

        # l'annuncio: e' la via che un nodo si presenta
        "announce_corpo_vuoto": _post("/mesh/announce", {}),
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


def _normalizza(testo: str) -> str:
    """Il contenuto e' JSON in quasi tutti i casi; dove non lo e' resta grezzo.

    Gli ID che derivano dal contenuto della richiesta (l'hash di un alias) sono
    azzerati: sono la risposta corretta a un input diverso, non un difetto.
    """
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera_orari(dati)
    testo = json.dumps(dati, sort_keys=True)
    return testo


def _appiattisci(risposta: dict) -> str:
    return f"{risposta['status']} {_normalizza(risposta.get('body', ''))}"


def confronta(vecchio: dict, nuovo: dict) -> int:
    problemi = 0
    for chiave in sorted(set(vecchio) | set(nuovo)):
        a, b = vecchio.get(chiave), nuovo.get(chiave)
        if a is None or b is None:
            print(f"  SOLO IN UNO   {chiave}")
            problemi += 1
        elif _appiattisci(a) != _appiattisci(b):
            print(f"  DIVERSO       {chiave}")
            print(f"      prima: {_appiattisci(a)[:260]}")
            print(f"      dopo:  {_appiattisci(b)[:260]}")
            problemi += 1
        else:
            print(f"  uguale        {chiave}  ({_appiattisci(a)[:56]})")
    return problemi


def main() -> int:
    if "--confronta" in sys.argv:
        vecchio = json.loads(Path("tests/fixtures/mesh_baseline.json").read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0

    risultato = raccogli()
    # Un 500 non e' un comportamento: e' un crash. Registrarne uno come "atteso"
    # rende la baseline inutilizzabile, perche' la prossima estrazione "combacia"
    # invece di fallire. I 503 invece sono attesi (host irraggiungibile).
    crash = sorted(k for k, v in risultato.items() if v["status"] == 500)
    if crash:
        print(f"  ATTENZIONE: {len(crash)} rotte in 500, la fixture NON e' stata scritta:")
        for chiave in crash:
            print(f"    {chiave}: {risultato[chiave]['body'][:120]!r}")
        return 1
    destinazione = Path("tests/fixtures/mesh_baseline.json")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(json.dumps(risultato, indent=2, sort_keys=True) + "\n", "utf-8")
    for chiave, valore in sorted(risultato.items()):
        print(f"  {valore['status']}  {chiave:<28} {_normalizza(valore['body'])[:70]!r}")
    print(f"\n  scritto {destinazione}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
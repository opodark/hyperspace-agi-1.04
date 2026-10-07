# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini dei tool, per confrontarli dopo un'estrazione.

Non e' un test: e' lo strumento che ha prodotto `tests/fixtures/tool_baseline.json`.
Va eseguito con il server gia' in ascolto:

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/tool_baseline.py
    ... --confronta

I tool sono il punto in cui il control-plane fa qualcosa invece di rispondere: una
ricerca sul web, una nota sulla persona, una shell. Quindi la baseline qui non puo'
limitarsi a "la rotta risponde 200" — le guardie sono il contenuto.

Le tre rotte:

  /tools/execute  esegue un tool senza passare dal modello, per un chiamante esterno
                  (un Tool custom di Open WebUI, per esempio)
  /mcp            il protocollo MCP: un chiamante che parla JSON-RPC e riceve i tool
  /mcp/status     la diagnostica, che per contratto non contiene mai un token

I tre errori che vengono provati per primo sono le guardie, non l'esecuzione:

  - `/tools/execute` senza token di amministrazione. La rotta esegue TUTTI i tool
    pubblicati, connettori compresi: senza un gate, chiunque raggiunga la porta
    potrebbe mandare email a nome dell'organizzazione.
  - `/mcp` senza policy valida: `McpAuthPolicy` e' un allowlist, e una richiesta
    anonima non ottiene nemmeno una riga di log.
  - un tool che non esiste: l'errore deve dire che il tool non c'e', non restituire
    un risultato vuoto che il chiamante leggerebbe come successo.

Un caso in piu' che vale la pena: `get_mesh_status` e' l'unico tool che si puo'
eseguire davvero in questa baseline senza rete, credenziali o permessi. Se torna
un risultato vero, il percorso di esecuzione funziona — e non basta provare gli
errori per sapere che il resto del dominio fa qualcosa.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
ADMIN = os.environ.get("NETWORK_ADMIN_TOKEN", "")

# L'header del token di amministrazione di rete. Il nome e' in `cp/http.py`
# (`_NETWORK_ADMIN_HEADER`) e non e' quello che verrebbe fuori a mente: sbagliarlo
# fa rispondere 401 a tutto, e la baseline resta verde senza aver provato niente.
SOTTOPESI = {"X-Hyperspace-Network-Token": ADMIN} if ADMIN else {}


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


def _get(path: str, headers: dict | None = None) -> dict:
    return _richiesta("GET", path, headers=headers)


def _post(path: str, payload, headers: dict | None = None) -> dict:
    return _richiesta("POST", path, payload, headers)


def raccogli() -> dict:
    """Esegue le richieste nell'ordine in cui sono scritte: alcune dipendono dalle
    altre, quindi l'ordine non e' un dettaglio."""
    caso = {}

    # le guardie, per prime: rispondono subito e dicono subito se il percorso e'
    # protetto. Una baseline che comincia dall'esecuzione aspetta trenta secondi
    # per dirti che il token era sbagliato.
    caso["execute_senza_token"] = _post("/tools/execute", {"tool_name": "get_mesh_status"},
                                        headers={"X-Hyperspace-Network-Token": ""})
    caso["execute_corpo_vuoto"] = _post("/tools/execute", {})
    caso["execute_tool_inesistente"] = _post("/tools/execute", {"tool_name": "nessun_tool_questo"})
    caso["execute_non_oggetto"] = _post("/tools/execute", ["non", "un", "oggetto"])

    # l'unico tool eseguibile qui senza rete, credenziali o permessi: e' l'unico
    # che verifica davvero che il cammino dell'esecuzione funzioni
    caso["execute_get_mesh_status"] = _post("/tools/execute", {"tool_name": "get_mesh_status"})

    # la diagnostica MCP: non contiene mai un token, per contratto
    caso["mcp_status"] = _get("/mcp/status")

    # il protocollo: senza policy valida non deve ottenere nulla
    caso["mcp_anonimo"] = _post("/mcp", {"jsonrpc": "2.0", "id": 1,
                                         "method": "tools/list"}, headers={})
    caso["mcp_metodo_ignoto"] = _post("/mcp", {"jsonrpc": "2.0", "id": 2,
                                               "method": "tools/nessun_metodo"})

    return caso


VOLATILI = ("ts", "at", "last_seen", "ultimo", "ultima", "created_at", "updated_at",
            "uptime", "duration_ms", "elapsed_ms", "_ts", "sampled_at", "started_at")


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

    `get_mesh_status` contiene il conteggio dei nodi attivi e il ciclo del battito,
    che in un ambiente di prova cambiano da una esecuzione all'altra: sono
    l'ambiente, non il comportamento.
    """
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera_orari(dati)
    if isinstance(dati, dict):
        for chiave in ("nodes", "nodes_active"):
            if isinstance(dati.get(chiave), list):
                dati[chiave] = f"<{len(dati[chiave])} nodi>"
        for chiave in ("cycle", "last_tick"):
            if chiave in dati:
                dati[chiave] = "<variabile>"
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

    destinazione = Path("tests/fixtures/tool_baseline.json")
    if "--confronta" in sys.argv:
        vecchio = json.loads(destinazione.read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0
    return _stampa(raccogli(), destinazione)


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali dei confini della coda immagini, per confrontarle dopo
un'estrazione.

Gemello di `tests/instagram_baseline.py`, e con lo stesso scopo: `main.py` non e'
importabile in un test senza effetti collaterali, quindi l'unico modo per avere un
"verde prima" dell'estrazione di `cp/immagini.py` e' fotografare il server vero.

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/immagini_baseline.py
    ... --confronta

Le sei route di `/image/*` hanno un contratto con l'esterno che nessun test
guardava: e' il ponte ComfyUI che chiede il prossimo job (`/image/jobs`) e
riporta l'esito (`/image/result`). I due codici che contano qui sono il 204 a
coda vuota — che e' la risposta normale di un ponte in attesa, non un errore — e
il fatto che `/image/result` debba rifiutare un percorso che esce dal volume delle
immagini, perche' quell'URL finisce anche su Instagram.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
CHANNEL_CLIENTS = os.environ.get("CHANNEL_CLIENTS", "")

# `id` è nel gruppo perché la coda genera un id casuale per ogni job: qui si
# guarda la FORMA della risposta e il comportamento, non quale job è uscito.
# Senza questo, ogni run produrrebbe sei differenze che non sono difetti.
VOLATILI = ("id", "ts", "updated_at", "created_at", "received_at", "last_seen",
            "timestamp", "claimed_at", "started_at", "creato_ts", "preso_ts",
            "durata_ms", "consegnato_ts")


def _token_canale() -> str:
    """Il primo token valido di CHANNEL_CLIENTS.

    Si chiede a `shared.channel.parse_clients` invece di rifare il tagliamento a
    mano: la forma è `nome=token` separati da `;` e il token scarta sotto i 32
    caratteri, quindi un parser qui sbaglierebbe in silenzio restituendo 503
    (canali non configurati) su tutto, e sembrerebbe un difetto del server.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.channel import parse_clients
    canali, _problemi = parse_clients(CHANNEL_CLIENTS)
    return next(iter(canali.values()), "")


def _canale() -> dict:
    """L'intestazione del canale: senza, `/image/*` risponde 503 per canali
    disattivati o non configurati, che non è quello che si vuole osservare."""
    return {"X-Hyperspace-Channel-Token": _token_canale()}


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


def _genera(prompt: str, canale: dict) -> dict:
    """Accoda un job e torna con la risposta di /image/generate."""
    return _post("/image/generate", {"prompt": prompt, "larghezza": 512,
                                     "altezza": 512, "canale": "prova"}, canale)


def _job_id(risposta: dict) -> str:
    try:
        return (json.loads(risposta.get("body", "{}")) or {}).get("job", {}).get("id", "")
    except (json.JSONDecodeError, ValueError, AttributeError):
        return ""


def raccogli() -> dict:
    canale = _canale()
    fuori_volume = {}
    dentro_volume = {}
    esito = {}

    # 1. La coda vuota risponde 204: è quello che vede un ponte in attesa, ed è
    #    la risposta più facile da sbagliare trasformandola in un errore.
    coda_vuota = _get("/image/jobs", canale)

    # 2. Senza token del canale, tutto il gruppo risponde 401.
    senza_token = _get("/image/jobs")

    # 3. Il giro completo del ponte: accodare, prendere, chiudere.
    accodato = _genera("un faro di notte", canale)
    preso = _get("/image/jobs", canale)
    primo = _job_id(accodato)
    if primo:
        esito = _post("/image/result", {"id": primo, "ok": True,
                                        "file": "bridge_00001_.jpg"}, canale)

    # 4. Path traversal e percorso lecito: su DUE job distinti, perché chiudere
    #    due volte lo stesso job non sovrascrive niente (la coda lo ignora) e il
    #    confronto risulterebbe uguale qualunque cosa sia successo al primo.
    #
    #    Qui non si guarda il codice di risposta ma il `file` finito nell'esito:
    #    /image/result accetta il percorso che il ponte ha scritto, e la difesa
    #    vera è in `_percorso_disegno_servibile`, che decide se quel percorso
    #    diventerà un URL pubblicabile. Se qui il 200 è uguale in entrambi i casi,
    #    la difesta è che quella funzione lo rifiuta — e lo si verifica sotto.
    per_fuori = _genera("disegno con percorso che esce dal volume", canale)
    if _job_id(per_fuori):
        fuori_volume = _post("/image/result",
                             {"id": _job_id(per_fuori), "ok": True,
                              "file": "../../../etc/passwd"}, canale)
    per_dentro = _genera("disegno con percorso lecito", canale)
    if _job_id(per_dentro):
        dentro_volume = _post("/image/result",
                              {"id": _job_id(per_dentro), "ok": True,
                               "file": "HyperSpace/bridge_00002_.jpg"}, canale)

    return {
        "coda_vuota_204": coda_vuota,
        "senza_token": senza_token,
        "accodato": accodato,
        "job_preso": preso,
        "esito_ok": esito,
        "esito_fuori_volume": fuori_volume,
        "esito_dentro_volume": dentro_volume,
        "stato": _get("/image/status", canale),
        "job_inesistente": _get("/image/job/non-esiste", canale),
        "defer_inesistente": _post("/image/defer", {"id": "non-esiste"}, canale),
        "genera_corpo_vuoto": _post("/image/generate", {}, canale),
    }


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
    """Rende comparabili due risposte: gli ordini dei dizionari non contano, i
    campi di orario vengono azzerati, e il body puo' essere JSON oppure vuoto."""
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
            print(f"      prima: {_appiattisci(a)[:200]}")
            print(f"      dopo:  {_appiattisci(b)[:200]}")
            problemi += 1
        else:
            print(f"  uguale        {chiave}  ({_appiattisci(a)[:56]})")
    return problemi


def main() -> int:
    if "--confronta" in sys.argv:
        vecchio = json.loads(Path("tests/fixtures/immagini_baseline.json").read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0

    risultato = raccogli()
    destinazione = Path("tests/fixtures/immagini_baseline.json")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(json.dumps(risultato, indent=2, sort_keys=True) + "\n", "utf-8")
    for chiave, valore in sorted(risultato.items()):
        print(f"  {valore['status']}  {chiave}")
    print(f"\n  scritto {destinazione}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
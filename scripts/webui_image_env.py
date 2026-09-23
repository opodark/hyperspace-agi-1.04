#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Le variabili con cui Open WebUI disegna: derivate dal grafo del repo.

Perché uno script e non due JSON incollati in `.env`: Open WebUI vuole il grafo in
formato API (`COMFYUI_WORKFLOW`) e la mappa di quali campi riempire
(`COMFYUI_WORKFLOW_NODES`). Quel grafo esiste già — è quello che il ponte manda a
ComfyUI (`shared/image_jobs.py::workflow()`), con i suoi id fissi (451-470) e i
suoi pesi (`MODELLO_DEFAULT`). Tenerne due copie significa vederle divergere in
silenzio, e il sintomo sarebbe un job che fallisce **dopo minuti di sampling**:
è la lezione di `tests/test_comfyui_modelli.py`.

Qui non si inventa niente: si legge il grafo dal repo e se ne ricavano le variabili.
`--check` dice se `.env` è ancora allineato a quel grafo.

Uso:
  python scripts/webui_image_env.py                 # mostra le variabili
  python scripts/webui_image_env.py --write         # le scrive in .env e .env.windows
  python scripts/webui_image_env.py --check         # 0 se .env e' allineato al grafo
  python scripts/webui_image_env.py --size 512x512  # un'altra dimensione di default

La base URL punta al gateway del repo (`integrations/comfyui/webui_gateway.py`), non
a ComfyUI: è lì che la scheda si libera **prima** del diffusion (`shared/gpu_budget.py`).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import MODELLO_DEFAULT, workflow  # noqa: E402

BASE_URL_DEFAULT = "http://host.docker.internal:8189"
WEBUI_URL_DEFAULT = "http://127.0.0.1:3000"
# Lo stesso default di docker-compose.yml / docker-compose.windows.yml: serve solo a
# firmare un token di amministratore verso l'API locale, per applicare i valori
# senza passare a mano dal pannello.
CHIAVE_DEFAULT = "hyperspace-secret-key-32chars-ok!"
LATO_DEFAULT = 768
PASSI_DEFAULT = 25

# La mappa dei campi. `key` è il nome dell'input DENTRO il nodo, non un percorso:
# `_apply_workflow_nodes` di Open WebUI fa `workflow[node_id]['inputs'][node.key]`.
#
# Due assenze sono volute:
#   - `negative_prompt` NON si mappa: Open WebUI lo manda solo se l'utente lo scrive,
#     e quando manca sarebbe `None` — cioè `null` al posto della stringa che il nodo
#     di Qwen si aspetta. Il negativo negativo di questo grafo sta dentro il prompt
#     ("no text, no watermark, no logos"), come spiega `shared/image_jobs.py`;
#   - `model` NON si mappa: il diffusion è fissato dal grafo (`UnetLoaderGGUF` con
#     il GGUF verificato dal manifest). Mappandolo, un modello di *chat* scelto per
#     sbaglio nella UI finirebbe in `unet_name`, e l'errore arriverebbe a
#     generazione avviata.
NODI = [
    {"type": "prompt", "node_ids": ["452"], "key": "prompt"},
    {"type": "width", "node_ids": ["456"], "key": "width"},
    {"type": "height", "node_ids": ["456"], "key": "height"},
    {"type": "steps", "node_ids": ["458"], "key": "steps"},
    {"type": "seed", "node_ids": ["458"], "key": "seed"},
]

# Le chiavi che questo script possiede: sono le uniche che riscrive in .env.
CHIAVI = ("ENABLE_IMAGE_GENERATION", "IMAGE_GENERATION_ENGINE", "COMFYUI_BASE_URL",
          "COMFYUI_API_KEY", "COMFYUI_WORKFLOW", "COMFYUI_WORKFLOW_NODES",
          "IMAGE_SIZE", "IMAGE_STEPS", "IMAGE_GENERATION_MODEL")

INTESTAZIONE_ENV = ("# --- Immagini dentro Open WebUI (motore ComfyUI). Derivate dal grafo del "
                    "repo: python scripts/webui_image_env.py --write ---")


def job_default(larghezza: int, altezza: int, passi: int) -> dict:
    """Il job da cui si legge il grafo: gli id sono fissi, i valori li sovrascrive la UI."""
    return {"id": "webui", "prompt": "", "negativo": "", "larghezza": larghezza,
            "altezza": altezza, "passi": passi, "seed": 0, "modello": ""}


def misura(testo: str, *, lato_default: int = LATO_DEFAULT) -> tuple[int, int]:
    """`WxH` come lo scrive Open WebUI (`IMAGE_SIZE`): `768x768` o `1024x768`."""
    testo = str(testo or "").strip().lower().replace(" ", "")
    if not testo:
        return lato_default, lato_default
    parti = testo.split("x", 1)
    if len(parti) != 2 or not all(parte.isdigit() for parte in parti):
        raise ValueError(f"misura non valida: {testo!r} (atteso WxH, es. 768x768)")
    larghezza, altezza = (max(64, int(parte)) for parte in parti)
    return larghezza, altezza


def variabili(*, base_url: str = BASE_URL_DEFAULT, size: str = "",
              passi: int = PASSI_DEFAULT) -> dict:
    """Le variabili di `.env`, con il grafo preso dal repo (non riscritto qui)."""
    larghezza, altezza = misura(size)
    grafo = workflow(job_default(larghezza, altezza, passi))
    for nodo in NODI:
        for node_id in nodo["node_ids"]:
            if node_id not in grafo:
                raise ValueError(f"il grafo non ha il nodo {node_id}: {sorted(grafo)}")
    compatto = {"ensure_ascii": False, "separators": (",", ":")}
    return {
        "ENABLE_IMAGE_GENERATION": "true",
        "IMAGE_GENERATION_ENGINE": "comfyui",
        "COMFYUI_BASE_URL": str(base_url or BASE_URL_DEFAULT).rstrip("/"),
        "COMFYUI_API_KEY": "",
        "COMFYUI_WORKFLOW": json.dumps(grafo, **compatto),
        "COMFYUI_WORKFLOW_NODES": json.dumps(NODI, **compatto),
        "IMAGE_SIZE": f"{larghezza}x{altezza}",
        "IMAGE_STEPS": str(int(passi)),
        "IMAGE_GENERATION_MODEL": MODELLO_DEFAULT["unet"],
    }


def leggi(path: Path) -> tuple[list[str], str]:
    """Righe e stile di a-capo del file: quello che c'è non si cambia.

    Si legge con `newline=""`: `read_text` tradurrebbe i CRLF in LF e il file
    riscritto cambiarebbe stile — su un `.env` condiviso è un diff inutile su ogni
    riga (e `tests/test_webui_image_env.py` lo verifica).
    """
    if not path.exists():
        return [], "\r\n"
    with path.open(encoding="utf-8", newline="") as maniglia:
        testo = maniglia.read()
    return testo.splitlines(), ("\r\n" if "\r\n" in testo else "\n")


def applica(righe: list[str], valori: dict) -> tuple[list[str], list[str], list[str]]:
    """Le righe con le chiavi aggiornate; il resto del file non si tocca.

    Un file riscritto da zero perderebbe commenti e ordine, che in `.env` sono
    informazione: si sostituisce riga per riga e si appende solo ciò che manca.
    Restituisce (righe, aggiornate, aggiunte).
    """
    fuori, viste, aggiornate = [], set(), []
    for riga in righe:
        spoglia = riga.strip()
        if not spoglia or spoglia.startswith("#") or "=" not in spoglia:
            fuori.append(riga)
            continue
        chiave = spoglia.split("=", 1)[0].strip()
        if chiave not in valori:
            fuori.append(riga)
            continue
        if chiave in viste:
            # Una chiave ripetuta si TOGLIE: in un file di env vince l'ultima
            # occorrenza, quindi una copia vecchia lasciata lì annullerebbe in
            # silenzio il valore appena scritto.
            continue
        viste.add(chiave)
        nuova = f"{chiave}={valori[chiave]}"
        if riga != nuova:
            aggiornate.append(chiave)
        fuori.append(nuova)
    aggiunte = [chiave for chiave in valori if chiave not in viste]
    if aggiunte:
        fuori.append(INTESTAZIONE_ENV)
        fuori.extend(f"{chiave}={valori[chiave]}" for chiave in aggiunte)
    return fuori, aggiornate, aggiunte


def scrivi(path: Path, righe: list[str], a_capo: str) -> None:
    """Riscrive il file con l'a-capo che aveva (niente traduzioni implicite)."""
    with path.open("w", encoding="utf-8", newline="") as maniglia:
        maniglia.write(a_capo.join(righe) + a_capo)


def _differenze(righe: list[str], valori: dict) -> list[str]:
    """Cosa non torna nell'ordine del grafo: chiavi mancanti o diverse."""
    presenti = {}
    for riga in righe:
        spoglia = riga.strip()
        if not spoglia or spoglia.startswith("#") or "=" not in spoglia:
            continue
        chiave, valore = spoglia.split("=", 1)
        presenti[chiave.strip()] = valore.strip()
    problemi = []
    for chiave, atteso in valori.items():
        if chiave not in presenti:
            problemi.append(f"manca {chiave}")
        elif presenti[chiave] != atteso:
            problemi.append(f"{chiave} non e' quello del grafo del repo")
    return problemi


def valori_per_api(valori: dict) -> dict:
    """Le stesse variabili con i tipi che l'API della WebUI si aspetta.

    In `.env` è tutto testo; il pannello Images parla invece con dei tipi veri
    (booleano, intero e una lista di oggetti), e mandargli le stringhe sarebbe un 422.
    """
    fuori = dict(valori)
    fuori["ENABLE_IMAGE_GENERATION"] = str(valori["ENABLE_IMAGE_GENERATION"]).lower() == "true"
    fuori["IMAGE_STEPS"] = int(valori["IMAGE_STEPS"])
    fuori["COMFYUI_WORKFLOW_NODES"] = json.loads(valori["COMFYUI_WORKFLOW_NODES"])
    return fuori


def _valori_env_file(path: Path) -> dict:
    """Le chiavi di un `.env` (l'ultima occorrenza vince, come fa Compose)."""
    valori = {}
    for riga in leggi(path)[0]:
        spoglia = riga.strip()
        if not spoglia or spoglia.startswith("#") or "=" not in spoglia:
            continue
        chiave, valore = spoglia.split("=", 1)
        valori[chiave.strip()] = valore.strip().strip('"')
    return valori


def _db_webui(env_dati: dict) -> Path | None:
    """Dov'è `webui.db`: si legge `HS_DATA_DIR` dal .env, come fa il compose."""
    radice = str(env_dati.get("HS_DATA_DIR", "") or "").strip()
    if not radice:
        return None
    candidato = Path(radice) / "open-webui" / "webui.db"
    return candidato if candidato.exists() else None


def _utente_admin(db: Path) -> str:
    """L'id dell'utente admin: il token si firma per lui, la password non serve."""
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        riga = con.execute("select id from user where role = 'admin' "
                           "order by created_at limit 1").fetchone()
    finally:
        con.close()
    if not riga:
        raise ValueError(f"nessun utente admin in {db}")
    return str(riga[0])


def _token_admin(user_id: str, chiave: str, *, durata_s: int = 600) -> str:
    """Un JWT HS256 come quelli che firma Open WebUI, con la sola libreria standard."""
    import base64
    import hashlib
    import hmac
    import time

    def b64(dati: bytes) -> bytes:
        return base64.urlsafe_b64encode(dati).rstrip(b"=")

    intestazione = b64(json.dumps({"alg": "HS256", "typ": "JWT"},
                                  separators=(",", ":")).encode())
    corpo = b64(json.dumps({"id": user_id, "exp": int(time.time()) + durata_s},
                           separators=(",", ":")).encode())
    firma = b64(hmac.new(chiave.encode(), intestazione + b"." + corpo, hashlib.sha256).digest())
    return (intestazione + b"." + corpo + b"." + firma).decode()


def _chiama(url: str, *, dati=None, token: str = "", metodo: str = "GET",
            timeout: float = 120.0) -> tuple[int, dict]:
    import urllib.error

    corpo = json.dumps(dati).encode() if dati is not None else None
    richiesta = urllib.request.Request(url, data=corpo, method=metodo, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as risposta:
            testo = risposta.read().decode("utf-8", "replace")
            return risposta.status, (json.loads(testo) if testo.strip() else {})
    except urllib.error.HTTPError as errore:
        corpo_errore = errore.read().decode("utf-8", "replace")[:400]
        return int(errore.code), {"errore": corpo_errore}


def applica_alla_webui(valori: dict, *, webui_url: str, chiave: str, db: Path) -> dict:
    """Manda i valori all'API admin della WebUI: la stessa strada del pannello Images.

    Perché non un UPDATE nel database: Open WebUI tiene questa configurazione in
    memoria accanto a quella su disco, e il pannello la aggiorna passando da questa
    rotta. Scrivendo il database a mano le due copie resterebbero diverse — cioè
    esattamente il difetto che questo script esiste per evitare (una variabile che
    sembra impostata e non ha effetto).
    """
    token = _token_admin(_utente_admin(db), chiave)
    base = f"{str(webui_url).rstrip('/')}/api/v1/images/config"
    stato, attuale = _chiama(base, token=token)
    if stato != 200:
        raise RuntimeError(f"GET /api/v1/images/config -> HTTP {stato} "
                           f"{str(attuale.get('errore', ''))[:200]}")
    # La rotta di scrittura è `/config/update` (POST): è quella che chiama il pannello
    # Images dell'Admin.
    stato, dopo = _chiama(f"{base}/update",
                          dati={**attuale, **valori_per_api(valori)},
                          token=token, metodo="POST")
    if stato != 200:
        raise RuntimeError(f"POST /api/v1/images/config -> HTTP {stato} "
                           f"{str(dopo.get('errore', ''))[:300]}")
    return dopo



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Deriva dal grafo del repo le variabili con cui Open WebUI "
                    "genera le immagini (motore ComfyUI).")
    parser.add_argument("--write", action="store_true",
                        help="scrive in .env e .env.windows (solo le chiavi di questo script)")
    parser.add_argument("--check", action="store_true",
                        help="esce 1 se i file non sono allineati al grafo")
    parser.add_argument("--file", action="append", default=[],
                        help="un file preciso da leggere/scrivere (ripetibile)")
    parser.add_argument("--base-url", default=BASE_URL_DEFAULT,
                        help=f"dove sta il gateway immagini (default {BASE_URL_DEFAULT})")
    parser.add_argument("--size", default="", help="misura di default WxH (default 768x768)")
    parser.add_argument("--steps", type=int, default=PASSI_DEFAULT,
                        help=f"passi di default (default {PASSI_DEFAULT})")
    parser.add_argument("--apply", action="store_true",
                        help="manda i valori alla WebUI accesa (API admin, come il pannello)")
    parser.add_argument("--webui", default=WEBUI_URL_DEFAULT,
                        help=f"base della WebUI per --apply (default {WEBUI_URL_DEFAULT})")
    args = parser.parse_args(argv)

    valori = variabili(base_url=args.base_url, size=args.size, passi=args.steps)

    if args.apply:
        env_dati = _valori_env_file(ROOT / ".env")
        db = _db_webui(env_dati)
        if db is None:
            print("[webui-env] non trovo webui.db (HS_DATA_DIR in .env): niente da applicare")
            return 1
        chiave = env_dati.get("WEBUI_SECRET_KEY") or CHIAVE_DEFAULT
        try:
            dopo = applica_alla_webui(valori, webui_url=args.webui, chiave=chiave, db=db)
        except Exception as errore:  # noqa: BLE001
            print(f"[webui-env] applicazione non riuscita: {str(errore)[:300]}")
            return 1
        print("[webui-env] applicato alla WebUI (i valori ora valgono subito, senza riavvio):")
        for chiave_nome in CHIAVI:
            print(f"  {chiave_nome}={str(dopo.get(chiave_nome))[:90]}")
        return 0

    if args.check:
        problemi = []
        for nome in (args.file or [".env", ".env.windows"]):
            path = ROOT / nome
            if not path.exists():
                continue
            righe, _ = leggi(path)
            for problema in _differenze(righe, valori):
                problemi.append(f"{nome}: {problema}")
        for problema in problemi:
            print(f"[webui-env] {problema}")
        if problemi:
            print("[webui-env] allinea con: python scripts/webui_image_env.py --write")
            return 1
        print("[webui-env] ok: le variabili immagine seguono il grafo del repo")
        return 0

    if args.write:
        for nome in (args.file or [".env", ".env.windows"]):
            path = ROOT / nome
            if not path.exists() and nome != ".env":
                print(f"[webui-env] {nome} non c'è: saltato")
                continue
            righe, a_capo = leggi(path)
            nuove, aggiornate, aggiunte = applica(righe, valori)
            scrivi(path, nuove, a_capo)
            print(f"[webui-env] {nome}: {len(aggiornate)} aggiornate, "
                  f"{len(aggiunte)} aggiunte")
        print("[webui-env] ora ricostruisci la WebUI: docker compose -f "
              "docker-compose.windows.yml up -d --no-deps open-webui")
        return 0

    for chiave in CHIAVI:
        print(f"{chiave}={valori[chiave]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


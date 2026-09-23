#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""La porta da cui Open WebUI chiede un'immagine: qui la scheda si libera prima.

Open WebUI 0.11 sa chiamare ComfyUI da sé — motore `comfyui`: `POST /prompt`, il
WebSocket `/ws` su cui aspetta la fine dell'esecuzione, poi `/history` e `/view`
per prendere il file. Quella strada però **salta la regola del progetto** "una
scheda, un modello": la decisione sta in `shared/gpu_budget.py`, e il suo unico
gancio è il control-plane, che la WebUI non attraversa.

Il 2026-09-22 la contesa si è presentata come `CUDA error: unknown error`: 6170 MiB
tenuti da Ollama (`OLLAMA_KEEP_ALIVE=12h`) su 8151, 1730 liberi — il diffusion non
ci stava. E dopo quell'errore ComfyUI **non riparte da solo**: la coda resta con
`queue_running` vuoto e i prompt entrano in `pending` per sempre (`docs/comfyui.md`).

Questo processo sta davanti a ComfyUI e fa due cose sole:

  1. su `POST /prompt` chiede **prima** a Ollama cosa tiene in scheda (`/api/ps`) e
     glielo fa scaricare (`keep_alive: 0`): la memoria si libera prima che ComfyUI
     cominci a caricare i pesi, che è l'unico ordine che evita l'OOM. Se Ollama non
     risponde il job passa lo stesso e il motivo resta nei log, come fa il
     control-plane in `_libera_scheda_per_immagine` (non blocca mai);
  2. tutto il resto (`/history/*`, `/view`, `/system_stats`, `/object_info/*`,
     `/api/upload/image`, il WebSocket `/ws`) è inoltrato così com'è.

Qui non si decide niente sull'immagine: non si tocca il prompt, non si giudica il
risultato, non si scegli il modello. È un guardiano di memoria, non un filtro —
stessa disciplina di `comfy_bridge.py` e la tabella di
`docs/comfyui.md#cosa-filtra-e-cosa-no`.

Dipendenze: `fastapi`, `uvicorn[standard]`, `httpx` — le stesse che usano
`registry/`, `node/` e `infra-ui/`: non si aggiunge niente allo stack.

Config (variabili d'ambiente):
  COMFY_URL            API di ComfyUI da mettere davanti (default http://127.0.0.1:8188)
  OLLAMA_RAW_BASE_URL  Ollama sull'host (default http://127.0.0.1:11434)
  WEBUI_GATEWAY_PORT   porta di ascolto (default 8189)
  IMAGE_FREE_GPU       "false" spegne lo scarico: è lo stesso interruttore del CP
  CHANNEL_MODEL        modello del canale (serve solo a decidere *se* si scarica:
                       quello che si scarica è ciò che `/api/ps` dichiara residente)

Uso:
  python integrations/comfyui/webui_gateway.py --check   # verifica e basta
  python integrations/comfyui/webui_gateway.py           # in ascolto su 127.0.0.1:8189

Nella WebUI si punta **qui** e non a ComfyUI: `COMFYUI_BASE_URL=http://host.docker.internal:8189`
(le variabili le scrive `scripts/webui_image_env.py`).
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import sys
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# `integrations/` non è un pacchetto Python nel repo: il modulo del ponte si carica
# per percorso. Si riusa `_verifiche` invece di riscriverlo perché il controllo è lo
# stesso — i file del grafo devono ESSERE nella lista che ComfyUI dichiara — e una
# seconda copia divergerebbe in silenzio (la lezione di `tests/test_comfyui_modelli.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402
from comfy_bridge import _verifiche  # noqa: E402
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

from shared import gpu_budget  # noqa: E402
from shared.single_instance import AlreadyRunning, SingleInstance  # noqa: E402

COMFY_DEFAULT = "http://127.0.0.1:8188"
OLLAMA_DEFAULT = "http://127.0.0.1:11434"
PORT_DEFAULT = 8189
# Il lucchetto dell'istanza singola: due gateway sono due punti che credono di avere
# la scheda sotto controllo. Si sposta con WEBUI_GATEWAY_LOCK_FILE (serve ai test, e
# a chi tiene il repo su un altro disco).
LOCK_FILE = Path(os.environ.get("WEBUI_GATEWAY_LOCK_FILE")
                 or (Path(__file__).resolve().parents[2] / "data" / "webui-gateway.lock"))

# Cosa si lascia passare: la superficie che il motore `comfyui` di Open WebUI usa, e
# nient'altro. Un proxy aperto a tutto è una porta aperta sul processo che tiene la
# scheda, quindi la lista è corta di proposito (e `..` è rifiutato).
PERCORSI = ("prompt", "history", "view", "system_stats", "object_info", "queue",
            "interrupt", "free", "api/")
# Intestazioni che descrivono *questa* connessione, non la richiesta (RFC 7230,
# hop-by-hop). In risposta si tolgono anche `content-encoding`/`content-length`:
# httpx ha già decompresso il corpo e li ricalcola da sé.
DA_NON_INOLTRARE = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "transfer-encoding", "upgrade", "host", "content-length",
    "accept-encoding",
}
DA_NON_RESTITUIRE = DA_NON_INOLTRARE | {"content-encoding", "content-length"}


def log(messaggio: str) -> None:
    print(f"[comfy-gw] {messaggio}", flush=True)


def modelli_residenti(ps: dict | None) -> list[str]:
    """I modelli che Ollama tiene davvero in scheda, letti da `/api/ps`.

    Un modello caricato solo sulla CPU non occupa VRAM e non serve scaricarlo:
    restano le voci con `size_vram` > 0, più quelle dove il campo manca (non
    sapendolo si preferisce liberare: l'errore da evitare è l'OOM).
    """
    nomi: list[str] = []
    for voce in ((ps or {}).get("models") or []):
        if not isinstance(voce, dict):
            continue
        vram = voce.get("size_vram")
        if isinstance(vram, int) and vram <= 0:
            continue
        nome = str(voce.get("name") or voce.get("model") or "").strip()
        if nome and nome not in nomi:
            nomi.append(nome)
    return nomi


def da_scaricare(ps: dict | None, *, env: dict | None = None) -> list[str]:
    """Cosa scaricare prima di un'immagine. Lista vuota = non si tocca la scheda.

    L'interruttore è lo stesso del control-plane (`IMAGE_FREE_GPU`): spento, o
    nessun modello di canale dichiarato, significa non scaricare niente. Acceso, si
    scarica **tutto** ciò che risulta residente: la scheda è una sola e non si sa
    quale modello ha in mano la chat in quel momento.
    """
    if not gpu_budget.da_scaricare(env):
        return []
    return modelli_residenti(ps)


def percorso_consentito(percorso: str) -> bool:
    """Il percorso da inoltrare è uno di quelli previsti, e senza risalite?"""
    pulito = str(percorso or "").strip().lstrip("/")
    if not pulito or ".." in pulito or "\\" in pulito:
        return False
    return pulito.startswith(PERCORSI)


def intestazioni_inoltro(intestazioni, *, risposta: bool = False) -> dict:
    """Le intestazioni da inoltrare: senza quelle di connessione, senza valori vuoti.

    Due casi veri, entrambi osservati il 2026-09-23 al primo tentativo:
      - Open WebUI manda `Authorization: Bearer ` anche quando la sua COMFYUI_API_KEY
        è vuota; httpx rifiuta un valore con lo spazio in coda (`Illegal header
        value`) e la richiesta non parte nemmeno. Un token vuoto non è un token: si
        toglie l'intestazione;
      - i valori si passano senza spazi ai bordi, che è la stessa regola vista da
        httpx (e da ComfyUI, che risponde 400 sulle intestazioni malformate).
    """
    vietate = DA_NON_RESTITUIRE if risposta else DA_NON_INOLTRARE
    fuori = {}
    for chiave, valore in (intestazioni or {}).items():
        nome = chiave.lower()
        if nome in vietate:
            continue
        valore = str(valore).strip()
        if not valore:
            continue
        if nome == "authorization" and valore.lower() == "bearer":
            continue
        fuori[chiave] = valore
    return fuori


def url_inoltro(base: str, percorso: str, query: str = "") -> str:
    """L'URL a monte, senza barre sdoppiate e con la query di partenza."""
    url = f"{str(base or '').rstrip('/')}/{str(percorso or '').lstrip('/')}"
    return f"{url}?{query}" if query else url


async def libera_scheda(client: httpx.AsyncClient, base_ollama: str) -> list[str]:
    """Fa posto in scheda prima di inoltrare un `/prompt`.

    Non solleva mai: se Ollama non risponde il job passa lo stesso (lo scarico serve
    a non far fallire il diffusion, non è un requisito per disegnare) e il motivo
    resta nei log — la stessa scelta del control-plane.
    """
    if not gpu_budget.da_scaricare():
        return []
    base = str(base_ollama or OLLAMA_DEFAULT).rstrip("/")
    try:
        risposta = await client.get(f"{base}/api/ps", timeout=5.0)
        residenti = modelli_residenti(risposta.json() if risposta.status_code == 200 else {})
    except Exception as errore:  # noqa: BLE001
        log(f"Ollama non raggiungibile su {base} ({str(errore)[:120]}): si disegna lo stesso")
        return []
    if not residenti:
        log("scheda gia' libera: nessun modello residente")
        return []
    for modello in residenti:
        try:
            esito = await client.post(f"{base}{gpu_budget.SCARICA_PATH}",
                                      json=gpu_budget.richiesta_scarico(modello),
                                      timeout=60.0)
            log(gpu_budget.descrivi_esito(esito.status_code, modello=modello))
        except Exception as errore:  # noqa: BLE001
            log(gpu_budget.descrivi_esito(0, errore=str(errore)[:120], modello=modello))
    return residenti


async def _dal_client(websocket: WebSocket, remoto) -> None:
    """Open WebUI -> ComfyUI."""
    try:
        while True:
            messaggio = await websocket.receive()
            if messaggio.get("type") == "websocket.disconnect":
                return
            testo = messaggio.get("text")
            if testo is not None:
                await remoto.send(testo)
                continue
            binario = messaggio.get("bytes")
            if binario is not None:
                await remoto.send(binario)
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed, RuntimeError):
        return


async def _dal_remoto(remoto, websocket: WebSocket) -> None:
    """ComfyUI -> Open WebUI (comprese le anteprime binarie, che passano intere)."""
    try:
        async for messaggio in remoto:
            if isinstance(messaggio, (bytes, bytearray)):
                await websocket.send_bytes(bytes(messaggio))
            else:
                await websocket.send_text(str(messaggio))
    except (websockets.exceptions.ConnectionClosed, WebSocketDisconnect, RuntimeError):
        return


@asynccontextmanager
async def _client_vivo(app: FastAPI):
    """Un solo client HTTP per tutte le richieste: `/view` serve file da megabyte."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        app.state.client = client
        yield


def crea_app(comfy_url: str, ollama_url: str) -> FastAPI:
    """L'app: inoltro con il gancio prima di `/prompt`, e il tunnel per `/ws`."""
    app = FastAPI(title="HyperSpace image gateway (Open WebUI -> ComfyUI)",
                  docs_url=None, redoc_url=None, lifespan=_client_vivo)
    base = str(comfy_url or COMFY_DEFAULT).rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")

    @app.api_route("/{percorso:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def inoltra(percorso: str, richiesta: Request):
        if not percorso_consentito(percorso):
            return JSONResponse({"error": f"percorso non previsto da questo ponte: {percorso}"},
                                status_code=404)
        if percorso.strip("/").lower() == "prompt" and richiesta.method == "POST":
            # Il punto di tutto il processo: si fa posto PRIMA che ComfyUI carichi i pesi.
            await libera_scheda(richiesta.app.state.client, ollama_url)
        corpo = await richiesta.body()
        try:
            a_monte = await richiesta.app.state.client.request(
                richiesta.method,
                url_inoltro(base, percorso, str(richiesta.url.query)),
                content=corpo or None,
                headers=intestazioni_inoltro(richiesta.headers))
        except httpx.HTTPError as errore:
            log(f"ComfyUI non raggiungibile su {base}: {str(errore)[:160]}")
            return JSONResponse({"error": f"ComfyUI non raggiungibile su {base}"}, status_code=502)
        return Response(content=a_monte.content, status_code=a_monte.status_code,
                        headers=intestazioni_inoltro(a_monte.headers, risposta=True))

    @app.websocket("/ws")
    async def tunnel(websocket: WebSocket):
        """Il tunnel verso il WebSocket di ComfyUI.

        Open WebUI aspetta lì il messaggio `executing` con `node: null` che dice
        "esecuzione finita": senza questo tunnel la generazione non finirebbe mai
        (`comfyui_create_image` non ha un ripiego HTTP). Non si legge niente di quel
        flusso — si copia nei due versi.
        """
        await websocket.accept()
        query = websocket.url.query
        a_monte = f"{ws_base}/ws" + (f"?{query}" if query else "")
        try:
            async with websockets.connect(a_monte, max_size=None) as remoto:
                attese = [asyncio.create_task(_dal_client(websocket, remoto)),
                          asyncio.create_task(_dal_remoto(remoto, websocket))]
                try:
                    await asyncio.wait(attese, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for attesa in attese:
                        attesa.cancel()
        except Exception as errore:  # noqa: BLE001
            log(f"WebSocket chiuso: {str(errore)[:160]}")

    return app


def _problemi_scheda(base_ollama: str) -> list:
    """Ollama: se lo scarico è acceso deve rispondere, o l'OOM resta possibile."""
    if not gpu_budget.da_scaricare():
        return []
    base = str(base_ollama or OLLAMA_DEFAULT).rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/ps", timeout=5) as risposta:
            residenti = modelli_residenti(json.loads(risposta.read().decode("utf-8", "replace")))
    except Exception as errore:  # noqa: BLE001
        return [f"Ollama non risponde su {base} ({str(errore)[:80]}): la scheda non si puo' "
                "liberare prima del diffusion (IMAGE_FREE_GPU=false per non dipenderne)"]
    log(f"Ollama {base}: {len(residenti)} modelli in scheda"
        + (f" ({', '.join(residenti)})" if residenti else ""))
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Gateway Open WebUI -> ComfyUI: la scheda si libera prima del diffusion.")
    parser.add_argument("--check", action="store_true", help="verifica e basta")
    parser.add_argument("--comfy", default=os.getenv("COMFY_URL", COMFY_DEFAULT))
    parser.add_argument("--ollama", default=os.getenv("OLLAMA_RAW_BASE_URL", OLLAMA_DEFAULT))
    parser.add_argument("--host", default=os.getenv("WEBUI_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.getenv("WEBUI_GATEWAY_PORT", str(PORT_DEFAULT))))
    args = parser.parse_args(argv)

    # Le stesse verifiche del ponte: i file del grafo devono essere nella lista che
    # ComfyUI dichiara, o il job fallisce dopo minuti di sampling (2026-09-22).
    problemi = _verifiche(args.comfy, "")
    problemi += _problemi_scheda(args.ollama)
    if problemi:
        log("PROBLEMI:")
        for problema in problemi:
            log(f"  - {problema}")
    else:
        log("pronto: ComfyUI raggiungibile e scheda sotto controllo")
    if args.check:
        return 1 if problemi else 0

    # Due gateway sono due processi che credono di avere la scheda sotto controllo.
    try:
        lucchetto = SingleInstance(LOCK_FILE, label="gateway immagini WebUI").acquire()
        atexit.register(lucchetto.release)
    except AlreadyRunning as e:
        log(f"{e}: il secondo gateway non parte")
        return 1

    log(f"in ascolto su http://{args.host}:{args.port} -> {args.comfy}")
    log(f"nella WebUI: COMFYUI_BASE_URL=http://host.docker.internal:{args.port}")
    uvicorn.run(crea_app(args.comfy, args.ollama), host=args.host, port=args.port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Ponte fra HyperSpace e ComfyUI: tira i job immagini e li esegue.

Perché un ponte e non una chiamata del control-plane: ComfyUI ascolta su
`127.0.0.1` e non è raggiungibile da dentro un container. La direzione è quella
dei driver di canale: **il client tira**. Il ponte non decide niente — non sceglie
il prompt, non giudica l'immagine, non parla nel canale: prende un job, esegue il
grafo, riferisce. Chi decide è il control-plane, e resta lì.

Questo file vive sulla macchina che esegue ComfyUI: win11 (Qwen-Image) o il Mac
(checkpoint per gli sketch e per il volto virtuale di Anna). BRIDGE_MODEL dice
quali famiglie esegue, e possono essere più di una: `sdxl-turbo,sd15`.

Config (variabili d'ambiente):
  CHANNEL_URL     base del control-plane (default http://127.0.0.1:8085)
  CHANNEL_TOKEN   token del canale "comfy" (obbligatorio)
  COMFY_URL       API di ComfyUI (default http://127.0.0.1:8188)
  COMFY_OUTPUT_DIR  cartella output di ComfyUI (per tradurre il nome del file in
                  un percorso reale; se manca, si riporta solo il nome relativo)
  BRIDGE_MODEL    famiglia di modello che questo ponte sa eseguire
                  (es. sdxl-turbo per il Mac); vuoto = qualunque job
  BRIDGE_POLL_S   intervallo fra due giri a vuoto (default 5 s)
  BRIDGE_TIMEOUT_S  tetto di attesa di UNA generazione (default 1800 s: con
                  `fix: 2` una variante di Anna rende in ~900 s, e a 900 s il
                  ponte si arrendeva 2 s prima che il file uscisse — 2026-09-30,
                  job 89602917b708 -> bridge_00085_)
  BRIDGE_OLLAMA_URL  Ollama locale da liberare prima degli sketch sul Mac
  BRIDGE_MIN_FREE_GB memoria libera richiesta prima di avviare ComfyUI (default 4)

Uso:
  python integrations/comfyui/comfy_bridge.py --check    # non genera nulla
  python integrations/comfyui/comfy_bridge.py --once     # un job e esce
  python integrations/comfyui/comfy_bridge.py            # in attesa, in ciclo
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.image_jobs import (FAMIGLIA_DEFAULT, FAMIGLIA_SD15, FAMIGLIE,  # noqa: E402
                               FIX_UPSCALER, MODELLO_DEFAULT,
                               RICETTE_CHECKPOINT, famiglie_capaci,
                               immagini_da_history, usa_checkpoint, workflow)
from shared.single_instance import AlreadyRunning, SingleInstance  # noqa: E402

# Il lucchetto dell'istanza singola: si può puntare altrove con
# COMFY_BRIDGE_LOCK_FILE (serve ai test, e a chi tiene il repo su un altro disco).
LOCK_FILE = Path(os.environ.get("COMFY_BRIDGE_LOCK_FILE")
                 or (Path(__file__).resolve().parents[2] / "data" / "comfy-bridge.lock"))

CONTROL_PLANE_DEFAULT = "http://127.0.0.1:8085"
COMFY_DEFAULT = "http://127.0.0.1:8188"
# La cartella dell'app desktop: si usa solo per dare un percorso leggibile al
# file prodotto, non serve a generare.
OUTPUT_DESKTOP = (Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" /
                  "ComfyUI-Installs" / "ComfyUI" / "ComfyUI" / "output")


def _output_default() -> str:
    """La cartella output di default del ponte.

    Su Windows (LOCALAPPDATA presente) è quella dell'app desktop: il driver
    Telegram usa lì il percorso ASSOLUTO per consegnare il file. Altrove (es. il
    Mac) è vuota, così il ponte riferisce il percorso RELATIVO
    (HyperSpace/bridge_...jpg, o .png su host legacy) e /diario/immagini lo serve dal volume montato
    (DIARIO_IMMAGINI_DIR) — un percorso assoluto del Mac non sarebbe leggibile
    dal control-plane in container.
    """
    if os.environ.get("LOCALAPPDATA"):
        return str(OUTPUT_DESKTOP)
    return ""
PREFISSO = "HyperSpace/bridge"


def log(messaggio: str) -> None:
    print(f"[comfy] {messaggio}", flush=True)


def _richiesta(url: str, *, payload=None, timeout: float = 30.0,
               headers: dict | None = None) -> tuple[int, dict]:
    """GET/POST JSON con la libreria standard: non si aggiungono dipendenze."""
    dati = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    richiesta = urllib.request.Request(
        url, data=dati,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if dati is not None else "GET")
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as risposta:
            corpo = risposta.read().decode("utf-8", errors="replace")
            return risposta.status, (json.loads(corpo) if corpo.strip() else {})
    except urllib.error.HTTPError as errore:
        corpo = errore.read().decode("utf-8", errors="replace")[:400]
        return int(errore.code), {"errore": corpo}
    except urllib.error.URLError as errore:
        return 0, {"errore": f"non raggiungibile: {errore.reason}"}
    except TimeoutError:
        return 0, {"errore": f"timeout dopo {timeout}s"}
    except OSError as errore:
        # Il control-plane che sparisce A METÀ richiesta non passa da URLError: su
        # Windows arriva qui come WinError 10053 ("connessione interrotta dal
        # software del computer host"). Senza questo ramo l'eccezione usciva dal
        # ciclo e **il ponte moriva** — con la coda piena e nessuno che la esegue,
        # cioè in silenzio. Osservato davvero il 2026-09-22 reconstruendo il CP.
        return 0, {"errore": f"connessione interrotta: {errore}"}


def _file_attesi(modello: str) -> list[tuple[str, str, str]]:
    """I file che devono esistere in ComfyUI perché i job di questo ponte riescano.

    Ritorna triple (nodo, campo, nome file). Perché non basta "ComfyUI dichiara
    almeno un file": un checkpoint mancante passava quel controllo, il ponte
    diceva «pronto» e ogni job di quella famiglia falliva — con l'errore vero
    dentro ComfyUI, invisibile da fuori. Con una seconda famiglia di checkpoint
    (`sd15`, il volto di Anna) quel buco diventerebbe il modo normale di scoprire
    che il modello non è stato scaricato.

    I nodi dipendono dalla famiglia: i checkpoint usano `CheckpointLoaderSimple`
    (UNet + CLIP + VAE in un file solo), Qwen-Image usa `UnetLoaderGGUF` +
    `CLIPLoader` + `VAELoader`. Un ponte con più famiglie (`sdxl-turbo,sd15`) le
    verifica tutte, perché è l'elenco dei file a decidere se un job riesce.

    Anna (`sd15`) ne ha due: il checkpoint e l'ingranditore del fix, che è il
    formato dei suoi ritratti (512×768 + ingranditore neurale → 1024×1536). Un
    `fix: 2` chiesto su un'altra famiglia userebbe lo stesso file, ma non è il
    formato dichiarato di nessuno: qui non si pretende e resta scoperto — il job
    fallirebbe dentro ComfyUI.
    """
    famiglie = famiglie_capaci(modello) or FAMIGLIE
    attesi: list[tuple[str, str, str]] = []
    for famiglia in famiglie:
        ricetta = RICETTE_CHECKPOINT.get(famiglia)
        if ricetta:
            attesi.append(("CheckpointLoaderSimple", "ckpt_name", ricetta["ckpt"]))
            if famiglia == FAMIGLIA_SD15:
                # `model_name`: il campo d'ingresso di UpscaleModelLoader, non
                # `upscale_model` (che è il nome del suo OUTPUT, e chiederlo
                # faceva dire al preflight «ComfyUI non dichiara nessun file»).
                attesi.append(("UpscaleModelLoader", "model_name", FIX_UPSCALER))
        elif famiglia == FAMIGLIA_DEFAULT:
            attesi.append(("UnetLoaderGGUF", "unet_name", MODELLO_DEFAULT["unet"]))
            attesi.append(("CLIPLoader", "clip_name", MODELLO_DEFAULT["clip"]))
            attesi.append(("VAELoader", "vae_name", MODELLO_DEFAULT["vae"]))
    return attesi


def _elenco_file(scelte) -> list:
    """I nomi che ComfyUI dichiara per un input a elenco, in entrambe le forme.

    ComfyUI dichiara le scelte di un input in due modi, e convivono: la forma
    storica mette la lista al primo posto (`[["a.ckpt", "b.ckpt"], {...}]`), quella
    nuova mette un sentinella `COMBO` con le opzioni dentro il secondo
    (`["COMBO", {"options": ["x.pth"]}]` — è così che si dichiara
    `UpscaleModelLoader` su ComfyUI 0.37.4). Leggere solo la prima fa dire al
    preflight «ComfyUI non dichiara nessun file» **con i file presenti**: misurato
    il 2026-09-30 sui due R-ESRGAN appena installati. E non è un avviso: un file
    che risulta assente ferma l'avvio del ponte.
    """
    if not scelte:
        return []
    prima = scelte[0]
    if isinstance(prima, list):
        return list(prima)
    if prima == "COMBO" and len(scelte) > 1 and isinstance(scelte[1], dict):
        return list(scelte[1].get("options") or [])
    return []


def _verifiche(comfy_url: str, output_dir: str, modello: str = "") -> list:
    """Cosa manca perché una generazione possa riuscire. Vuoto = si può fare."""
    problemi = []
    stato, dati = _richiesta(f"{comfy_url}/system_stats", timeout=10)
    if stato != 200:
        problemi.append(f"ComfyUI non risponde su {comfy_url}: HTTP {stato} "
                        f"{dati.get('errore', '')}")
        return problemi
    sistema = (dati or {}).get("system") or {}
    dispositivo = ((dati or {}).get("devices") or [{}])[0]
    log(f"ComfyUI {sistema.get('comfyui_version', '?')} | {dispositivo.get('name', '?')} "
        f"| VRAM libera {round((dispositivo.get('vram_free') or 0) / 1073741824, 2)} GB")
    # Una famiglia scritta male (BRIDGE_MODEL=sd-15) non fa fallire niente: il
    # control-plane non offre mai job di quella famiglia, quindi il ponte resta
    # in attesa per sempre e sembra che non ci sia lavoro. Un errore di battitura
    # deve dire di essere un errore di battitura.
    for famiglia in famiglie_capaci(modello):
        if famiglia not in FAMIGLIE:
            problemi.append(f"BRIDGE_MODEL={famiglia}: famiglia sconosciuta (una di: "
                            f"{', '.join(FAMIGLIE)}) — nessun job la userà mai")
    # I file dichiarati nel grafo devono ESSERE lì: si chiede a ComfyUI l'elenco
    # delle sue scelte, che è la stessa cosa che vede il grafo.
    disponibili_per_nodo: dict = {}
    for nodo, campo, nome in _file_attesi(modello):
        if (nodo, campo) not in disponibili_per_nodo:
            stato, info = _richiesta(f"{comfy_url}/object_info/{nodo}", timeout=10)
            nodo_info = ((info or {}).get(nodo) or {})
            scelte = (((nodo_info.get("input") or {}).get("required") or {}).get(campo) or [])
            disponibili_per_nodo[(nodo, campo)] = _elenco_file(scelte)
            if not disponibili_per_nodo[(nodo, campo)]:
                problemi.append(f"{nodo}.{campo}: ComfyUI non dichiara nessun file")
            else:
                log(f"{nodo}.{campo}: {len(disponibili_per_nodo[(nodo, campo)])} file disponibili")
        disponibili = disponibili_per_nodo[(nodo, campo)]
        if disponibili and nome not in disponibili:
            problemi.append(f"{nodo}.{campo}: manca «{nome}» — installalo con "
                            "integrations/comfyui/install-model.sh (su Windows: "
                            "install-model.ps1)")
    if output_dir and not Path(output_dir).is_dir():
        problemi.append(f"cartella di output non trovata: {output_dir}")
    return problemi


def motivo_fallimento(messaggi) -> str:
    """Il perché di un fallimento, leggibile: tipo, nodo e messaggio dell'eccezione.

    Perché non si prende il dump dei messaggi: il dump si tronca a 300 caratteri e
    l'informazione utile — l'eccezione — sta **in fondo**. Il 2026-09-22 la scheda ha
    fallito con `CUDA error: unknown error` e quello che è arrivato al control-plane
    era `'execution_start' | 'execution_cached' | 'execution_error', {'prompt_i…`:
    il motivo vero non c'era più.

    E c'è un caso che *non* è un guasto: `execution_interrupted`. Il 2026-09-30, mentre
    si tarava il sampler sui numeri dei demo, cinque job sono stati annullati dalla coda
    di ComfyUI e il control-plane ha registrato cinque volte il dump troncato — cioè un
    errore del grafo che non esisteva. Chi legge deve poter distinguere «rilancia» da
    «ripara»: l'annullamento ha una frase sua, il dump resta il ripiego per ciò che non
    sappiamo leggere.
    """
    for messaggio in messaggi or []:
        if not isinstance(messaggio, (list, tuple)) or len(messaggio) < 2:
            continue
        tipo_messaggio = str(messaggio[0])
        if tipo_messaggio == "execution_interrupted":
            return ("annullato da fuori: qualcuno ha interrotto il job in ComfyUI (coda o "
                    "interfaccia) — non è un guasto del grafo, il job si può rilanciare")[:280]
        if tipo_messaggio != "execution_error" or not isinstance(messaggio[1], dict):
            continue
        dettaglio = messaggio[1]
        tipo = str(dettaglio.get("exception_type") or "errore")
        testo = " ".join(str(dettaglio.get("exception_message") or "").split())
        nodo = str(dettaglio.get("node_type") or dettaglio.get("node_id") or "")
        pezzi = [tipo]
        if nodo:
            pezzi.append(f"in {nodo}")
        motivo = " ".join(pezzi)
        if testo:
            motivo = f"{motivo}: {testo}"
        return motivo[:280]
    dump = [str(m) for m in (messaggi or [])]
    return f"ComfyUI ha fallito: {' | '.join(dump)}"[:280]


def esegui_job(job: dict, *, comfy_url: str = COMFY_DEFAULT, output_dir: str = "",
               timeout_s: float = 1800.0, prefisso: str = PREFISSO) -> tuple:
    """Esegue UN job su ComfyUI. Ritorna (ok, percorso_file, errore).

    Non solleva: chi chiama deve poter RIFERIRE un fallimento al control-plane,
    non morire con lui.
    """
    # The project node saves JPEG directly. Hosts not yet updated continue to
    # work with ComfyUI's built-in PNG saver until the node is installed.
    node_status, node_info = _richiesta(
        f"{comfy_url}/object_info/HyperSpaceSaveJPEG", timeout=10)
    jpeg = node_status == 200 and "HyperSpaceSaveJPEG" in node_info
    grafo = workflow(job, prefisso=prefisso, jpeg=jpeg)
    if not jpeg:
        log("HyperSpaceSaveJPEG assente: uso PNG finché il nodo non è installato")
    stato, dati = _richiesta(f"{comfy_url}/prompt",
                             payload={"prompt": grafo, "client_id": "hyperspace-bridge"},
                             timeout=30)
    if stato != 200:
        return False, "", f"ComfyUI ha rifiutato il grafo: HTTP {stato} {dati.get('errore', '')}"
    prompt_id = str(dati.get("prompt_id") or "")
    if not prompt_id:
        return False, "", f"ComfyUI non ha restituito un prompt_id: {str(dati)[:200]}"
    log(f"job {job['id']} in esecuzione su ComfyUI (prompt_id={prompt_id})")

    scadenza = time.monotonic() + max(30.0, float(timeout_s))
    while time.monotonic() < scadenza:
        stato, storico = _richiesta(f"{comfy_url}/history/{prompt_id}", timeout=15)
        run = (storico or {}).get(prompt_id) if stato == 200 else None
        if not run:
            time.sleep(2)          # ancora in coda, o in esecuzione
            continue
        esito = run.get("status") or {}
        if str(esito.get("status_str") or "") == "error":
            # Il motivo leggibile, non il dump: vedi motivo_fallimento().
            return False, "", motivo_fallimento(esito.get("messages"))
        file_prodotti = immagini_da_history(run)
        if not file_prodotti:
            time.sleep(2)
            continue
        relativo = file_prodotti[0]
        percorso = str(Path(output_dir) / relativo) if output_dir else relativo
        return True, percorso, ""
    return False, "", f"nessuna immagine entro {int(timeout_s)}s"


def prepara_memoria_mac(comfy_url: str, ollama_url: str, *, min_free_gb: float = 4,
                        timeout_s: float = 90) -> tuple[bool, str]:
    """Unload locally resident Ollama models, then check actual free unified RAM."""
    base = ollama_url.rstrip("/")
    stato, dati = _richiesta(f"{base}/api/ps", timeout=10)
    if stato != 200:
        # Ollama spento e' precisamente lo stato desiderato mentre ComfyUI usa
        # la memoria unificata. La verifica decisiva resta system_stats sotto.
        log(f"Ollama non raggiungibile (HTTP {stato}): verifico direttamente la memoria ComfyUI")
        dati = {"models": []}
    for model in dati.get("models") or []:
        name = str(model.get("name") or model.get("model") or "")
        if not name:
            continue
        stato, _ = _richiesta(f"{base}/api/generate",
                              payload={"model": name, "keep_alive": 0, "stream": False},
                              timeout=30)
        if stato != 200:
            return False, f"impossibile scaricare {name} da Ollama (HTTP {stato})"
        log(f"modello Ollama scaricato: {name}")
    deadline = time.monotonic() + timeout_s
    cache_released = False
    while time.monotonic() < deadline:
        stato, stats = _richiesta(f"{comfy_url}/system_stats", timeout=10)
        if stato != 200:
            return False, f"ComfyUI non raggiungibile (HTTP {stato})"
        free = int(((stats.get("devices") or [{}])[0]).get("vram_free") or 0)
        if free >= min_free_gb * 1073741824:
            return True, ""
        if not cache_released:
            # ComfyUI can retain the previous checkpoint in unified memory.
            # Ask its local worker to release it before retrying the measurement.
            _richiesta(f"{comfy_url}/free",
                       payload={"unload_models": True, "free_memory": True}, timeout=10)
            cache_released = True
        time.sleep(3)
    return False, f"memoria libera sotto {min_free_gb:g} GB"


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Ponte HyperSpace -> ComfyUI.")
    parser.add_argument("--check", action="store_true", help="verifica e basta")
    parser.add_argument("--once", action="store_true", help="esegui un job ed esci")
    parser.add_argument("--url", default=os.getenv("CHANNEL_URL", CONTROL_PLANE_DEFAULT))
    parser.add_argument("--token", default=os.getenv("CHANNEL_TOKEN", ""))
    parser.add_argument("--comfy", default=os.getenv("COMFY_URL", COMFY_DEFAULT))
    parser.add_argument("--output",
                        default=os.getenv("COMFY_OUTPUT_DIR") or _output_default())
    parser.add_argument("--model", default=os.getenv("BRIDGE_MODEL", ""),
                        help="famiglie di modello che questo ponte sa eseguire, "
                             "separate da virgola (es. «sdxl-turbo,sd15» per il Mac: "
                             "Pony per gli sketch e ChickMixFlat per il volto di "
                             "Anna); vuoto = qualunque job")
    parser.add_argument("--poll", type=float, default=float(os.getenv("BRIDGE_POLL_S", "5")))
    # 1800 e non 900: la ricetta dei demo (512×768 + fix 2× + 30 passi) rende in
    # ~900 s, quindi un tetto da 900 s dichiara fallito un job 2 s prima che il
    # file esca — e quel file, che è buono, resta senza nessuno che lo riferisca
    # (2026-09-30, job 89602917b708 -> bridge_00085_). Il claim del
    # control-plane deve restare più lungo: `DEFAULT_CLAIM_TTL_S` in
    # shared/image_jobs.py, che un test lega a questo valore.
    parser.add_argument("--timeout", type=float,
                        default=float(os.getenv("BRIDGE_TIMEOUT_S", "1800")))
    parser.add_argument("--ollama", default=os.getenv("BRIDGE_OLLAMA_URL", "http://127.0.0.1:11434"))
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")
    intestazioni = {"X-Hyperspace-Channel-Token": args.token} if args.token else {}

    problemi = _verifiche(args.comfy, args.output, args.model)
    stato, salute = _richiesta(f"{base}/image/status", timeout=10, headers=intestazioni)
    if stato == 200:
        log(f"control-plane {base}: ok (in coda {salute.get('in_coda')}, "
            f"in esecuzione {salute.get('in_esecuzione')})")
    elif stato in (401, 403):
        problemi.append(f"token rifiutato dal control-plane (HTTP {stato}): genera con "
                        "`python scripts/channel_token.py comfy --write` e usa lo stesso "
                        "valore qui (CHANNEL_TOKEN)")
    elif stato == 503:
        problemi.append("il control-plane ha i canali disattivati (CHANNEL_ENABLED=false) "
                        "o non ha nessun token in CHANNEL_CLIENTS")
    else:
        problemi.append(f"control-plane non raggiungibile su {base}: HTTP {stato} "
                        f"{salute.get('errore', '')}")

    # Il tetto si legge all'avvio, non si deduce: quando è troppo corto il ponte
    # riferisce fallito un job che ComfyUI sta finendo di scrivere, e nel diario
    # resta "nessuna immagine entro Ns" — che sembra un guasto del modello.
    log(f"tetto di attesa per una generazione: {args.timeout:g}s")

    if problemi:
        log("PROBLEMI:")
        for problema in problemi:
            log(f"  - {problema}")
    else:
        log("pronto: ponte configurato e ComfyUI raggiungibile")
    if args.check:
        return 1 if problemi else 0
    if not args.token:
        log("CHANNEL_TOKEN mancante: senza token il control-plane non serve i job")
        return 1
    # Due ponti sulla stessa ComfyUI non litigano: prendono entrambi un job e la
    # scheda li esegue in parallelo, il doppio del tempo per ognuno. Il lucchetto
    # (del sistema operativo, quindi sparisce con il processo) lo impedisce.
    if not args.once:
        try:
            _lucchetto = SingleInstance(LOCK_FILE, label="ponte ComfyUI").acquire()
            atexit.register(_lucchetto.release)
        except AlreadyRunning as e:
            log(f"{e}: il secondo ponte non parte (due job in parallelo su una scheda sola)")
            return 1

    while True:
        query = f"?famiglia={urllib.parse.quote(args.model)}" if args.model else ""
        stato, dati = _richiesta(f"{base}/image/jobs{query}", timeout=20,
                                 headers=intestazioni)
        job = (dati or {}).get("job") if stato == 200 else None
        if stato not in (200, 204):
            log(f"lettura dei job fallita: HTTP {stato} {dati.get('errore', '')}")
        if not job:
            if args.once:
                log("niente da fare")
                return 0
            time.sleep(max(1.0, args.poll))
            continue
        iniziato = time.monotonic()
        log(f"job {job['id']}: {job['larghezza']}x{job['altezza']} passi={job['passi']} "
            f"seed={job['seed']} da={job['richiedente'] or '?'}")
        if any(usa_checkpoint(famiglia) for famiglia in famiglie_capaci(args.model)):
            ready, reason = prepara_memoria_mac(
                args.comfy, args.ollama,
                min_free_gb=float(os.getenv("BRIDGE_MIN_FREE_GB", "4")))
            if not ready:
                log(f"job {job['id']} rinviato: {reason}")
                _richiesta(f"{base}/image/defer", payload={"id": job["id"]},
                           timeout=20, headers=intestazioni)
                if args.once:
                    return 1
                time.sleep(max(10.0, args.poll))
                continue
        ok, percorso, errore = esegui_job(job, comfy_url=args.comfy,
                                          output_dir=args.output, timeout_s=args.timeout)
        durata = int((time.monotonic() - iniziato) * 1000)
        result = {"id": job["id"], "ok": ok, "file": percorso,
                  "errore": errore, "durata_ms": durata}
        while True:
            reported, reply = _richiesta(f"{base}/image/result", payload=result,
                                         timeout=20, headers=intestazioni)
            if reported == 200:
                break
            if reported in (401, 403, 404):
                log(f"risultato job {job['id']} rifiutato: HTTP {reported} {reply}")
                break
            log(f"control-plane non disponibile per il risultato di {job['id']}; riprovo")
            time.sleep(max(5.0, args.poll))
        log(f"{'fatto' if ok else 'fallito'} in {durata / 1000:.1f}s: "
            f"{percorso or errore}")
        if args.once:
            return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

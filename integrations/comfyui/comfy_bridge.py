#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Ponte fra HyperSpace e ComfyUI: tira i job immagini e li esegue.

Perché un ponte e non una chiamata del control-plane: ComfyUI ascolta su
`127.0.0.1` e non è raggiungibile da dentro un container. La direzione è quella
dei driver di canale: **il client tira**. Il ponte non decide niente — non sceglie
il prompt, non giudica l'immagine, non parla nel canale: prende un job, esegue il
grafo, riferisce. Chi decide è il control-plane, e resta lì.

Questo file vive solo sulla macchina con la scheda (win11), come i nodi.

Config (variabili d'ambiente):
  CHANNEL_URL     base del control-plane (default http://127.0.0.1:8085)
  CHANNEL_TOKEN   token del canale "comfy" (obbligatorio)
  COMFY_URL       API di ComfyUI (default http://127.0.0.1:8188)
  COMFY_OUTPUT_DIR  cartella output di ComfyUI (per tradurre il nome del file in
                  un percorso reale; se manca, si riporta solo il nome relativo)
  BRIDGE_POLL_S   intervallo fra due giri a vuoto (default 5 s)
  BRIDGE_TIMEOUT_S  tetto di attesa di UNA generazione (default 900 s)

Uso:
  python integrations/comfyui/comfy_bridge.py --check    # non genera nulla
  python integrations/comfyui/comfy_bridge.py --once     # un job e esce
  python integrations/comfyui/comfy_bridge.py            # in attesa, in ciclo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.image_jobs import immagini_da_history, workflow  # noqa: E402

CONTROL_PLANE_DEFAULT = "http://127.0.0.1:8085"
COMFY_DEFAULT = "http://127.0.0.1:8188"
# La cartella dell'app desktop: si usa solo per dare un percorso leggibile al
# file prodotto, non serve a generare.
OUTPUT_DESKTOP = (Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" /
                  "ComfyUI-Installs" / "ComfyUI" / "ComfyUI" / "output")
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


def _verifiche(comfy_url: str, output_dir: str) -> list:
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
    # I file dichiarati nel grafo devono ESSERE lì: si chiede a ComfyUI l'elenco
    # delle sue scelte, che è la stessa cosa che vede il grafo.
    for nodo, campo in (("UnetLoaderGGUF", "unet_name"), ("CLIPLoader", "clip_name"),
                        ("VAELoader", "vae_name")):
        stato, info = _richiesta(f"{comfy_url}/object_info/{nodo}", timeout=10)
        nodo_info = ((info or {}).get(nodo) or {})
        scelte = (((nodo_info.get("input") or {}).get("required") or {}).get(campo) or [])
        disponibili = scelte[0] if scelte and isinstance(scelte[0], list) else []
        if not disponibili:
            problemi.append(f"{nodo}.{campo}: ComfyUI non dichiara nessun file")
        else:
            log(f"{nodo}.{campo}: {len(disponibili)} file disponibili")
    if output_dir and not Path(output_dir).is_dir():
        problemi.append(f"cartella di output non trovata: {output_dir}")
    return problemi


def esegui_job(job: dict, *, comfy_url: str = COMFY_DEFAULT, output_dir: str = "",
               timeout_s: float = 900.0, prefisso: str = PREFISSO) -> tuple:
    """Esegue UN job su ComfyUI. Ritorna (ok, percorso_file, errore).

    Non solleva: chi chiama deve poter RIFERIRE un fallimento al control-plane,
    non morire con lui.
    """
    grafo = workflow(job, prefisso=prefisso)
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
            messaggi = [str(m) for m in (esito.get("messages") or [])]
            return False, "", ("ComfyUI ha fallito: " + " | ".join(messaggi))[:300]
        file_prodotti = immagini_da_history(run)
        if not file_prodotti:
            time.sleep(2)
            continue
        relativo = file_prodotti[0]
        percorso = str(Path(output_dir) / relativo) if output_dir else relativo
        return True, percorso, ""
    return False, "", f"nessuna immagine entro {int(timeout_s)}s"


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
                        default=os.getenv("COMFY_OUTPUT_DIR") or str(OUTPUT_DESKTOP))
    parser.add_argument("--poll", type=float, default=float(os.getenv("BRIDGE_POLL_S", "5")))
    parser.add_argument("--timeout", type=float,
                        default=float(os.getenv("BRIDGE_TIMEOUT_S", "900")))
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")
    intestazioni = {"X-Hyperspace-Channel-Token": args.token} if args.token else {}

    problemi = _verifiche(args.comfy, args.output)
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

    while True:
        stato, dati = _richiesta(f"{base}/image/jobs", timeout=20, headers=intestazioni)
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
        ok, percorso, errore = esegui_job(job, comfy_url=args.comfy,
                                          output_dir=args.output, timeout_s=args.timeout)
        durata = int((time.monotonic() - iniziato) * 1000)
        _richiesta(f"{base}/image/result",
                   payload={"id": job["id"], "ok": ok, "file": percorso,
                            "errore": errore, "durata_ms": durata},
                   timeout=20, headers=intestazioni)
        log(f"{'fatto' if ok else 'fallito'} in {durata / 1000:.1f}s: "
            f"{percorso or errore}")
        if args.once:
            return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


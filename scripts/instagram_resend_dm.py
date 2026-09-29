#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reinvia i disegni Instagram in DM rimasti non consegnati.

Serve quando il tentativo di consegna è fallito (tunnel giù, URL sbagliato) e
il disegno è rimasto in coda con `consegnato=false`: la consegna del control-plane
è un tentativo unico, e per gli Instagram DM nessun driver rilegge l'outbox.

Uso (dentro il container control-plane, dove vivono connettore ed env):
    docker exec hyperspace_control_plane python /repo/scripts/instagram_resend_dm.py
    docker exec hyperspace_control_plane python /repo/scripts/instagram_resend_dm.py --send
    docker exec hyperspace_control_plane python /repo/scripts/instagram_resend_dm.py --send 2119819cfb8f d09594408a97

Senza `--send` non invia nulla: elenca soltanto i job scelti e l'URL che userebbe.
Con `--send`, per ogni job: invia il DM col connettore Instagram e dichiara la
consegna al control-plane (`POST /channel/outbox/ack`), così la coda non mente.

L'URL usa il percorso relativo *completo* (`HyperSpace/bridge_00048_.jpg`): è
quello che serve `GET /instagram/media/<token>/<path:nome>`.

Attenzione: Meta rifiuta gli allegati serviti da host dietro Cloudflare
(`error_subcode=2018007`) senza nemmeno scaricarli, quindi finché
`INSTAGRAM_PUBLIC_BASE_URL` punta al tunnel questo script non può consegnare.
Dettagli, prove e alternative in `docs/instagram-dm-media.md`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, "/app")

import requests  # noqa: E402

JOBS_FILE = Path(os.getenv("IMAGE_JOBS_FILE", "/app/data/image-jobs.json"))
IMMAGINI_DIR = os.getenv("DIARIO_IMMAGINI_DIR", "/app/comfy-output")
CP_URL = os.getenv("CP_URL", "http://127.0.0.1:8085").rstrip("/")
HEADER = "X-Hyperspace-Channel-Token"


def percorso_servibile(file: str) -> str:
    """Percorso relativo servibile, oppure "" se il file manca o esce dal volume."""
    rel = str(file or "").strip().lstrip("/")
    if not rel:
        return ""
    radice = os.path.realpath(IMMAGINI_DIR)
    pieno = os.path.realpath(os.path.join(radice, rel))
    if not pieno.startswith(radice + os.sep) or not os.path.isfile(pieno):
        return ""
    return rel


def url_pubblico(rel: str) -> str:
    base = os.getenv("INSTAGRAM_PUBLIC_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("INSTAGRAM_MEDIA_TOKEN", "").strip()
    if not base or not token:
        raise SystemExit("INSTAGRAM_PUBLIC_BASE_URL o INSTAGRAM_MEDIA_TOKEN mancanti")
    return f"{base}/instagram/media/{quote(token, safe='')}/{quote(rel, safe='/')}"


def token_canale() -> str:
    """Primo token di CHANNEL_CLIENTS: serve solo per l'ack al control-plane."""
    for voce in os.getenv("CHANNEL_CLIENTS", "").split(";"):
        if "=" in voce:
            return voce.split("=", 1)[1].strip()
    return ""


def da_reinviare(jobs: dict, ids: list[str]) -> list[dict]:
    scelti = []
    for job in jobs.values():
        if job.get("canale") != "instagram" or not job.get("destinazione"):
            continue
        if job.get("stato") != "done" or job.get("consegnato"):
            continue
        if ids and str(job.get("id")) not in ids:
            continue
        scelti.append(job)
    return scelti


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="id dei job (default: tutti i non consegnati)")
    ap.add_argument("--send", action="store_true", help="invia davvero i DM")
    args = ap.parse_args()

    dati = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    jobs = dati.get("jobs") if isinstance(dati.get("jobs"), dict) else dati
    scelti = da_reinviare(jobs or {}, args.ids)
    if not scelti:
        print("Nessun disegno in DM da reinviare.")
        return 0

    # Import pigro: `connectors` vive solo dentro il container (/app), mentre il
    # modulo resta importabile dai test per le funzioni pure.
    from connectors.instagram import InstagramConnector

    canale = token_canale()
    connettore = InstagramConnector()
    esito = 0
    for job in scelti:
        file = (job.get("esito") or {}).get("file")
        print(f"{job['id']} -> destinatario {job.get('destinazione')} | {file}")
        rel = percorso_servibile(file)
        if not rel:
            print("   saltato: file non servibile (assente o fuori dal volume)")
            esito = 1
            continue
        url = url_pubblico(rel)
        print(f"   URL: {url}")
        if not args.send:
            continue
        try:
            risultato = connettore.execute("instagram_send_image",
                                           {"recipient_id": str(job["destinazione"]),
                                            "image_url": url})
        except Exception as error:  # il connettore solleva sugli HTTP >= 400
            print(f"   non consegnato: {str(error)[:200]}")
            esito = 1
            continue
        if not str(risultato or "").lstrip().startswith("{"):
            print(f"   non consegnato: {str(risultato)[:200]}")
            esito = 1
            continue
        print(f"   consegnato: {str(risultato)[:140]}")
        if canale:
            ack = requests.post(f"{CP_URL}/channel/outbox/ack", json={"id": job["id"]},
                                headers={HEADER: canale}, timeout=15)
            print(f"   ack: HTTP {ack.status_code}")
        else:
            print("   ack saltato: CHANNEL_CLIENTS vuoto (la coda resterebbe non consegnata)")
    if not args.send:
        print("\n(dry-run: nessun DM inviato — aggiungi --send per inviare)")
    return esito


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Consegna i disegni Instagram in DM appoggiandosi a un host che Meta accetta.

Meta rifiuta gli allegati serviti dagli host tunnel — il `cloudflared` di questo
repo, i quick tunnel, i Funnel Tailscale — con `error_subcode=2018007` e **senza
nemmeno scaricarli**: prove e controlli in `docs/instagram-dm-media.md`. Questo
script carica il JPEG su `files.catbox.moe` e manda il DM con quell'URL, poi
conferma la consegna al control-plane (`POST /channel/outbox/ack`), così la coda
non mente.

Uso (dentro il container control-plane, dove vivono connettore ed env):
    docker exec hyperspace_control_plane python /repo/scripts/instagram_send_drawings_via_catbox.py
    docker exec hyperspace_control_plane python /repo/scripts/instagram_send_drawings_via_catbox.py --send

Senza `--send` elenca soltanto i disegni scelti e il file che caricherebbe.
Il link pubblicato su catbox è pubblico (non indicizzato): è un ponte finché non
si serve da un host proprio accettato (l'hosting Aruba va bene, vedi
`scripts/instagram_upload_aruba.py`).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

JOBS_FILE = Path(os.getenv("IMAGE_JOBS_FILE", "/app/data/image-jobs.json"))
IMMAGINI_DIR = Path(os.getenv("DIARIO_IMMAGINI_DIR", "/app/comfy-output"))
CP_URL = os.getenv("CP_URL", "http://127.0.0.1:8085").rstrip("/")
CATBOX_API = os.getenv("INSTAGRAM_CATBOX_API", "https://catbox.moe/user/api.php")
HEADER = "X-Hyperspace-Channel-Token"


def token_canale() -> str:
    """Primo token di CHANNEL_CLIENTS: serve solo per l'ack al control-plane."""
    for voce in os.getenv("CHANNEL_CLIENTS", "").split(";"):
        if "=" in voce:
            return voce.split("=", 1)[1].strip()
    return ""


def da_consegnare(jobs: dict) -> list[dict]:
    """Disegni Instagram generati e non ancora confermati consegnati."""
    return [job for job in jobs.values()
            if job.get("canale") == "instagram" and job.get("destinazione")
            and job.get("stato") == "done" and not job.get("consegnato")]


def percorso(job: dict) -> Path:
    """File da caricare, dentro il volume delle immagini del diario."""
    rel = str((job.get("esito") or {}).get("file") or "").lstrip("/")
    return IMMAGINI_DIR / rel


def carica(file: Path) -> str:
    """Carica il JPEG su catbox e restituisce l'URL pubblico."""
    with open(file, "rb") as handle:
        risposta = requests.post(
            CATBOX_API, data={"reqtype": "fileupload"},
            files={"fileToUpload": (file.name, handle, "image/jpeg")}, timeout=180)
    risposta.raise_for_status()
    url = risposta.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox: risposta inattesa {url[:120]}")
    return url


def main() -> int:
    invia = "--send" in sys.argv
    dati = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    jobs = dati.get("jobs") if isinstance(dati.get("jobs"), dict) else dati
    scelti = da_consegnare(jobs or {})
    if not scelti:
        print("Nessun disegno Instagram da consegnare.")
        return 0

    canale = token_canale()
    if invia and not canale:
        print("CHANNEL_CLIENTS vuoto: la coda resterebbe non consegnata.")
        return 1

    connettore = None
    if invia:
        # Import pigro: `connectors` vive solo dentro il container (/app), mentre
        # il modulo resta importabile dai test per le funzioni pure.
        sys.path.insert(0, "/app")
        from connectors.instagram import InstagramConnector
        connettore = InstagramConnector()

    esito = 0
    for job in scelti:
        file = percorso(job)
        print(f"{job['id']} -> destinatario {job.get('destinazione')} | {file.name}")
        if not file.is_file():
            print("   saltato: file non trovato")
            esito = 1
            continue
        if not invia:
            continue
        try:
            url = carica(file)
        except Exception as errore:
            print(f"   upload non riuscito: {str(errore)[:200]}")
            esito = 1
            continue
        print(f"   URL: {url}")
        try:
            risultato = connettore.execute(
                "instagram_send_image",
                {"recipient_id": str(job["destinazione"]), "image_url": url})
        except Exception as errore:  # il connettore solleva sugli HTTP >= 400
            print(f"   non consegnato: {str(errore)[:200]}")
            esito = 1
            continue
        if not str(risultato or "").lstrip().startswith("{"):
            print(f"   non consegnato: {str(risultato)[:200]}")
            esito = 1
            continue
        print(f"   consegnato: {str(risultato)[:140]}")
        ack = requests.post(f"{CP_URL}/channel/outbox/ack", json={"id": job["id"]},
                            headers={HEADER: canale}, timeout=15)
        print(f"   ack: HTTP {ack.status_code}")
    if not invia:
        print("\n(dry-run: nulla caricato o inviato — aggiungi --send)")
    return esito


if __name__ == "__main__":
    raise SystemExit(main())

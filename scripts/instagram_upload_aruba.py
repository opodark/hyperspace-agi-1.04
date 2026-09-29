#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Carica un JPEG su Aruba (media.zerozerocomputer.it) e stampa l'URL pubblico.

Uso:
    python scripts/instagram_upload_aruba.py percorso/immagine.jpg
    python scripts/instagram_upload_aruba.py percorso/immagine.jpg --name sogno-abc.jpg

Credenziali dall'ambiente (o .env):
    INSTAGRAM_FTP_HOST        default ftp.zerozerocomputer.it
    INSTAGRAM_FTP_USER        default 19829119@aruba.it
    INSTAGRAM_FTP_PASSWORD    obbligatoria (non si mette nel codice)
    INSTAGRAM_FTP_DIR         default media.zerozerocomputer.it
    INSTAGRAM_PUBLIC_BASE_URL default https://media.zerozerocomputer.it
"""
from __future__ import annotations

import argparse
import ftplib
import os
import sys
from pathlib import Path

FTP_HOST = os.getenv("INSTAGRAM_FTP_HOST", "ftp.zerozerocomputer.it")
FTP_USER = os.getenv("INSTAGRAM_FTP_USER", "19829119@aruba.it")
FTP_PASSWORD = os.getenv("INSTAGRAM_FTP_PASSWORD", "")
FTP_DIR = os.getenv("INSTAGRAM_FTP_DIR", "media.zerozerocomputer.it").strip("/")
PUBLIC_BASE = os.getenv("INSTAGRAM_PUBLIC_BASE_URL", "https://media.zerozerocomputer.it").rstrip("/")


def upload(local: Path, name: str) -> str:
    """Carica `local` su Aruba e restituisce l'URL pubblico."""
    if not FTP_PASSWORD:
        raise SystemExit("INSTAGRAM_FTP_PASSWORD mancante: mettilo in .env")
    remote = name or local.name
    ftp = ftplib.FTP(FTP_HOST, timeout=40)
    ftp.set_pasv(True)
    ftp.login(FTP_USER, FTP_PASSWORD)
    try:
        # La cartella esiste già se il sottodominio è configurato come directory.
        # mkd fallisce con error_perm se presente: va ignorato.
        try:
            ftp.mkd(FTP_DIR)
        except ftplib.error_perm:
            pass
        with open(local, "rb") as handle:
            ftp.storbinary(f"STOR {FTP_DIR}/{remote}", handle)
    finally:
        ftp.quit()
    return f"{PUBLIC_BASE}/{remote}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Carica un'immagine su Aruba e stampa l'URL pubblico.")
    ap.add_argument("file", type=Path, help="JPEG locale da caricare")
    ap.add_argument("--name", default="", help="nome remoto (default: nome del file locale)")
    args = ap.parse_args()
    if not args.file.is_file():
        print(f"file non trovato: {args.file}", file=sys.stderr)
        return 2
    try:
        url = upload(args.file, args.name)
    except ftplib.all_errors as error:
        print(f"upload fallito: {error}", file=sys.stderr)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Genera il token di Hermes e lo scrive dove lo cercano i due lati.

Perché uno script: lo stesso segreto serve al **bridge** (che lo legge da un file
o da `--token`) e al **control-plane** (che lo legge da `HERMES_MEMORY_TOKEN` o da
`HERMES_MEMORY_TOKEN_FILE`) — e in questo repo i due lati guardano file diversi:

  - il bridge, avviato dal launcher, legge `<repo>/data/hermes-memory.token`;
  - il control-plane, nel container, legge `/app/data/hermes-memory.token`, cioè
    `data/runtime/data/hermes-memory.token` (via `HS_DATA_DIR`);
  - un'istanza avviata a mano può puntare altrove (es. `C:\\HyperSpace\\data\\...`).

Tre file con lo stesso contenuto sono tre occasioni di divergere in silenzio: si
scrive qui, tutti insieme, e `--check` dice se sono ancora allineati. Il token è
di 64 caratteri esadecimali come quello dei canali, e il bridge ne rifiuta meno
di 32 (`scripts/hermes_memory_bridge.py`).

    python scripts/hermes_token.py --check     # stato, senza scrivere nulla
    python scripts/hermes_token.py --write     # genera e scrive in TUTTI i posti

Sul Mac il token si **copia** in `<repo>/data/hermes-memory.token`, e nel `.env`
basta puntarlo:

    MEMORY_BACKEND=hermes
    HERMES_MEMORY_URL=http://<ip-tailscale-del-windows>:8098
    HERMES_MEMORY_TOKEN_FILE=/repo/data/hermes-memory.token
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOME_FILE = "hermes-memory.token"
MIN_TOKEN_LENGTH = 32


def genera_token(nbytes: int = 32) -> str:
    """64 caratteri esadecimali, come i token dei canali."""
    return secrets.token_hex(nbytes)


def percorsi(repo: Path = ROOT, hs_data_dir: str = "", extra: str = "") -> list:
    """Dove deve esserci lo stesso token, nell'ordine in cui si legge.

    `hs_data_dir` è quello che il compose monta su /app/data: se non è dichiarato
    nel `.env` si assume `data/runtime/data`, che è il default di questo stack.
    """
    cartella = Path(hs_data_dir) if hs_data_dir else (repo / "data" / "runtime" / "data")
    elenco = [repo / "data" / NOME_FILE, cartella / NOME_FILE]
    if extra:
        percorso = Path(extra)
        # Tollerante: chi lo usa passerà la CARTELLA (come fa il launcher del
        # bridge), ma se gli si passa il file intero deve funzionare lo stesso.
        elenco.append(percorso if percorso.name.endswith(".token")
                      else percorso / NOME_FILE)
    # Niente doppioni, mantenendo l'ordine: su Windows i percorsi si ripetono
    # facilmente fra maiuscole e separatori.
    visti, unici = set(), []
    for percorso in elenco:
        chiave = str(percorso).lower().replace("/", "\\")
        if chiave not in visti:
            visti.add(chiave)
            unici.append(percorso)
    return unici


def impronta(percorso: Path) -> str:
    """Impronta del token in un file: mai il valore, solo se è lo stesso.

    Serve a rispondere a "i due lati hanno lo stesso segreto?" senza stamparlo:
    un token che finisce in un log, in una chat o in un terminale condiviso è un
    token da rigenerare.
    """
    try:
        testo = Path(percorso).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not testo:
        return ""
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()[:12]


def stato(elenco: list) -> list:
    """Una riga per percorso: presente, lunghezza e impronta."""
    righe = []
    for percorso in elenco:
        try:
            testo = Path(percorso).read_text(encoding="utf-8").strip()
        except OSError:
            righe.append({"percorso": str(percorso), "presente": False, "lunghezza": 0,
                          "impronta": ""})
            continue
        righe.append({"percorso": str(percorso), "presente": True, "lunghezza": len(testo),
                      "impronta": impronta(percorso)})
    return righe


def allineati(righe: list) -> bool:
    """Vero solo se TUTTI i file che contengono un token hanno la stessa impronta."""
    impronte = {r["impronta"] for r in righe if r["presente"] and r["impronta"]}
    return len(impronte) <= 1


def scrittura(elenco: list, token: str) -> list:
    """Scrive il token in tutti i percorsi. Ritorna quelli davvero scritti."""
    scritti = []
    for percorso in elenco:
        percorso = Path(percorso)
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(token + "\n", encoding="utf-8")
        try:                        # su POSIX il file è leggibile solo da chi lo usa
            os.chmod(percorso, 0o600)
        except OSError:
            pass
        scritti.append(percorso)
    return scritti


def leggi_env(chiave: str, percorso: Path | None = None) -> str:
    """Legge una chiave dal `.env` (una riga per chiave, i commenti si ignorano)."""
    file_env = percorso or (ROOT / ".env")
    try:
        righe = file_env.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for riga in righe:
        riga = riga.strip()
        if riga.startswith(f"{chiave}="):
            return riga.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera (o verifica) il token di Hermes per bridge e control-plane.")
    parser.add_argument("--write", action="store_true",
                        help="genera un token nuovo e lo scrive in tutti i posti")
    parser.add_argument("--check", action="store_true",
                        help="mostra lo stato senza scrivere (default se manca --write)")
    parser.add_argument("--hs-data-dir", default="",
                        help="cartella montata su /app/data (default: da HS_DATA_DIR, "
                             "altrimenti data/runtime/data)")
    parser.add_argument("--extra-file", default="",
                        help="un altro percorso da tenere allineato (es. C:\\HyperSpace\\data)")
    parser.add_argument("--from-file", default="",
                        help="NON generare: copia il token da questo file negli altri "
                             "(allinea senza ruotare)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    hs_data = args.hs_data_dir or leggi_env("HS_DATA_DIR")
    elenco = percorsi(ROOT, hs_data, args.extra_file)
    righe = stato(elenco)

    print("percorsi del token di Hermes:")
    for riga in righe:
        if riga["presente"]:
            print(f"  [ok]    {riga['percorso']} ({riga['lunghezza']} car, "
                  f"impronta {riga['impronta']})")
        else:
            print(f"  [manca] {riga['percorso']}")
    print("allineamento: " + ("i file presenti hanno lo stesso token" if allineati(righe)
                              else "ATTENZIONE, i file NON hanno lo stesso token "
                                   "(un lato risponderebbe 401)"))

    if not args.write:
        print("\n(nessuna scrittura: aggiungi --write per generare e allineare)")
        return 0 if allineati(righe) else 1

    token = genera_token()
    if args.from_file:
        try:
            token = Path(args.from_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"\nnon riesco a leggere {args.from_file}: {e}")
            return 1
        if len(token) < MIN_TOKEN_LENGTH:
            print(f"\nil file {args.from_file} non contiene un token valido "
                  f"({len(token)} caratteri, minimo {MIN_TOKEN_LENGTH})")
            return 1
        print(f"\nmodalità --from-file: NON genero niente, allineo quello esistente")
    scritti = scrittura(elenco, token)
    print("\ntoken nuovo scritto in:")
    for percorso in scritti:
        print(f"  - {percorso}")
    print(f"\ntoken ({len(token)} caratteri — è un segreto, non incollarlo dove non serve):")
    print(f"  {token}\n")
    url = leggi_env("HERMES_MEMORY_URL") or "http://host.docker.internal:8098"
    print("control-plane di QUESTA macchina (.env):")
    print(f"  HERMES_MEMORY_URL={url}")
    print("  HERMES_MEMORY_TOKEN_FILE=/app/data/hermes-memory.token")
    print("\nMac (.env), con il file copiato in <repo>/data/hermes-memory.token:")
    print("  MEMORY_BACKEND=hermes")
    print("  HERMES_MEMORY_URL=http://<ip-tailscale-del-windows>:8098")
    print("  HERMES_MEMORY_TOKEN_FILE=/repo/data/hermes-memory.token")
    print("\nE il bridge deve ascoltare anche sulla rete, non solo in locale:")
    print("  .\\scripts\\start_hermes_memory_bridge.ps1 -BindAddress 0.0.0.0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

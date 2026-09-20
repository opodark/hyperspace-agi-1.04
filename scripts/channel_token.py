# SPDX-License-Identifier: Apache-2.0
"""Genera il token di un canale e lo scrive nel .env del control-plane.

Perché uno script: avviare un canale richiede lo STESSO token in due posti (il
.env del control-plane e l'ambiente del driver). Farlo a mano è esattamente dove
si sbaglia — token corti, copiati a metà, incollati nel posto sbagliato — e
l'errore si manifesta come un 401 che non dice quale dei due lati è sbagliato.

    python scripts/channel_token.py cam4                 # stampa token e istruzioni
    python scripts/channel_token.py cam4 --write         # aggiorna anche il .env
    python scripts/channel_token.py cam4 --write --env /percorso/.env
"""
from __future__ import annotations

import argparse
import os
import re
import secrets

VAR = "CHANNEL_CLIENTS"


def genera_token(nbytes: int = 32) -> str:
    """Token a 64 caratteri esadecimali (la soglia minima è 32)."""
    return secrets.token_hex(nbytes)


def aggiorna_env(testo: str, canale: str, token: str) -> tuple[str, list[str]]:
    """Aggiunge o sostituisce la voce del canale nella riga CHANNEL_CLIENTS.

    Ritorna (nuovo_testo, canali). Una voce già presente per lo STESSO canale
    viene sostituita — rigenerare un token deve invalidare il vecchio — mentre le
    altre restano: il file è condiviso da tutti i canali.
    """
    canale = str(canale).strip().lower()
    pattern = re.compile(rf"^{re.escape(VAR)}=(.*)$", re.MULTILINE)
    trovato = pattern.search(testo)
    voci: dict = {}
    for pezzo in (trovato.group(1) if trovato else "").split(";"):
        pezzo = pezzo.strip().strip('"')
        if "=" in pezzo:
            nome, _, valore = pezzo.partition("=")
            voci[nome.strip().lower()] = valore.strip()
    voci[canale] = str(token)
    riga = f'{VAR}="' + ";".join(f"{nome}={valore}"
                                 for nome, valore in sorted(voci.items())) + '"'
    if trovato:
        return testo[:trovato.start()] + riga + testo[trovato.end():], sorted(voci)
    if testo and not testo.endswith("\n"):
        testo += "\n"
    return testo + riga + "\n", sorted(voci)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera il token di un canale HyperSpace e (opzionale) lo scrive nel .env.")
    parser.add_argument("canale", nargs="?", default="cam4",
                        help="nome del canale (default: cam4)")
    parser.add_argument("--env", default=os.getenv("ENV_FILE_PATH", ".env"),
                        help="file .env del control-plane (default: .env)")
    parser.add_argument("--write", action="store_true",
                        help="scrive/aggiorna la voce nel .env")
    args = parser.parse_args(argv)

    token = genera_token()
    print(f"canale: {args.canale.strip().lower()}")
    print(f"token:  {token}\n")

    print("1) control-plane:")
    if args.write:
        try:
            with open(args.env, "r", encoding="utf-8") as f:
                testo = f.read()
        except FileNotFoundError:
            testo = ""
        nuovo, voci = aggiorna_env(testo, args.canale, token)
        try:
            with open(args.env, "w", encoding="utf-8") as f:
                f.write(nuovo)
        except OSError as e:
            print(f"   non ho potuto scrivere {args.env}: {e}")
            print(f'   mettilo a mano: {VAR}="{args.canale.strip().lower()}={token}"')
            return 1
        print(f"   {VAR} aggiornato in {args.env} (canali: {', '.join(voci)})")
        print("   poi riavvia il control-plane, oppure salva la tab Setup -> "
              "Canali esterni")
    else:
        print(f'   {VAR}="{args.canale.strip().lower()}={token}"')
        print("   (per scriverlo da qui: aggiungi --write)")

    print("\n2) driver della piattaforma (la macchina col browser):")
    print("   set CHANNEL_URL=http://127.0.0.1:8088")
    print(f"   set CHANNEL_TOKEN={token}")
    print("\n3) verifica:  py -3 cam4_chatbot.py --check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

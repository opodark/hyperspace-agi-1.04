#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vetrina del bot Telegram: nome, about e descrizione, via Bot API.

Perché esiste: la vetrina del bot NON è il prompt. Quello che il modello legge
(l'identità dichiarata) vive in `data/persona-aurora.json` e arriva dal
control-plane: una sola copia, perché due identità divergono sempre. Qui c'è solo
ciò che legge una persona prima di scrivere: nome, about (120 caratteri) e
descrizione (512). Gli stessi campi che si impostano a mano su @BotFather, con
due differenze che contano: i limiti si vedono PRIMA di sbagliare, e dopo si
rilegge cosa il bot risponde davvero.

    python scripts/telegram_profile.py            # legge e basta (default)
    python scripts/telegram_profile.py --apply    # scrive nome/about/descrizione

Token: `TELEGRAM_BOT_TOKEN` dall'ambiente, oppure dal file indicato con
`--token-env` (default `data/telegram-bot.env`, ignorato da git: il token del bot
serve al driver, non al control-plane e nemmeno al repo).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
TOKEN_ENV_DEFAULT = ROOT / "data" / "telegram-bot.env"

# I testi della vetrina. Limiti dell'API: nome 64, about 120, descrizione 512.
NOME = "Aurora · IA di HyperSpace, voce della mesh"
ABOUT = ("IA dichiarata con un carattere: memoria condivisa, energia da una mesh "
         "di nodi. Non sono una persona.")
DESCRIZIONE = (
    "Aurora è un'IA con una personalità, non un assistente generico: conversa, "
    "ricorda ciò che le si dice e cambia con quello che riceve.\n"
    "Non vive su una sola macchina: il suo calcolo arriva da una rete di nodi, "
    "anche schede di browser aperte da chi le presta energia; la memoria è "
    "condivisa e continua, e le proposte che scrive su di sé le revisiona un umano.\n"
    "La cerchia la scelgono gli operatori: si entra per invito, non per numero.\n"
    "Non è una persona e non lo finge: se le chiedi cosa è, te lo dice."
)


def leggi_env_file(path: Path) -> dict:
    """Legge un file KEY=VALUE ignorando commenti e righe vuote."""
    valori: dict[str, str] = {}
    if not path.is_file():
        return valori
    for riga in path.read_text(encoding="utf-8").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, _, valore = riga.partition("=")
        valori[chiave.strip()] = valore.strip().strip('"').strip("'")
    return valori


def risolvi_token(percorso_env: Path) -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            or leggi_env_file(percorso_env).get("TELEGRAM_BOT_TOKEN", "").strip())


def chiama(api: str, metodo: str, **params) -> tuple[int, dict, str]:
    """Ritorna (status, json, errore_leggibile). Non solleva per un 4xx."""
    try:
        risposta = requests.post(f"{api}/{metodo}", json=params, timeout=25)
    except requests.RequestException as e:
        return 0, {}, str(e)[:160]
    try:
        return risposta.status_code, risposta.json(), ""
    except ValueError:
        return risposta.status_code, {}, risposta.text[:160]


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # I testi contengono accenti e un trattino lungo: su console Windows la
        # codifica di default li troncherebbe proprio mentre li si rilegge.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Legge (e con --apply aggiorna) la vetrina del bot Telegram.")
    parser.add_argument("--apply", action="store_true",
                        help="scrive nome, about e descrizione sul bot")
    parser.add_argument("--token-env", default=str(TOKEN_ENV_DEFAULT),
                        help=f"file con TELEGRAM_BOT_TOKEN (default: {TOKEN_ENV_DEFAULT})")
    # Override per il SECONDO bot (es. quello che gira sul MacBook): cambiano solo
    # i testi, il resto del comando resta identico.
    parser.add_argument("--name", default=NOME, help="nome del bot (max 64)")
    parser.add_argument("--about", default=ABOUT, help="about/short description (max 120)")
    parser.add_argument("--description", default=DESCRIZIONE,
                        help="descrizione nella chat vuota (max 512)")
    args = parser.parse_args(argv)

    token = risolvi_token(Path(args.token_env))
    if not token:
        print("TELEGRAM_BOT_TOKEN mancante: esporta la variabile o scrivila in "
              f"{args.token_env}")
        return 1
    api = f"https://api.telegram.org/bot{token}"

    voci = (("nome", args.name, 64), ("about", args.about, 120),
            ("descrizione", args.description, 512))
    print("limiti: " + " | ".join(f"{nome} {len(testo)}/{tetto}"
                                  for nome, testo, tetto in voci))
    fuori = [nome for nome, testo, tetto in voci if len(testo) > tetto]
    if fuori:
        print("STOP: oltre il limite: " + ", ".join(fuori))
        return 1

    status, me, errore = chiama(api, "getMe")
    if status != 200 or not me.get("ok"):
        print(f"getMe [{status}] {errore or me.get('description', '')}")
        print("token non valido? Rigenera con @BotFather -> /revoke.")
        return 1
    risultato = me["result"]
    print(f"bot: @{risultato.get('username')} (id {risultato.get('id')}), "
          f"nome attuale {risultato.get('first_name')!r}")
    privacy = risultato.get("can_read_all_group_messages")
    print(f"legge tutti i messaggi di gruppo: {privacy}")
    if privacy is False:
        print("  nota: privacy mode ATTIVA -> in gruppo vede solo menzioni e comandi. "
              "@BotFather -> /setprivacy -> Disable, oppure rendilo admin del gruppo.")

    letture = (("nome", "getMyName", "name"),
               ("about", "getMyShortDescription", "short_description"),
               ("descrizione", "getMyDescription", "description"))
    for etichetta, metodo, campo in letture:
        status, dati, errore = chiama(api, metodo)
        valore = (dati.get("result") or {}).get(campo, "") if status == 200 else errore
        print(f"{etichetta}: {valore!r}")

    if not args.apply:
        print("\n(nessuna scrittura: aggiungi --apply per applicare i testi qui sopra)")
        return 0

    print("")
    for metodo, campo, valore in (("setMyName", "name", args.name),
                                  ("setMyShortDescription", "short_description", args.about),
                                  ("setMyDescription", "description", args.description)):
        status, dati, errore = chiama(api, metodo, **{campo: valore})
        esito = "ok" if status == 200 and dati.get("ok") else f"FALLITO [{status}]"
        print(f"{metodo}: {esito} {errore or dati.get('description', '')}")

    print("")
    for etichetta, metodo, campo in letture:
        status, dati, errore = chiama(api, metodo)
        valore = (dati.get("result") or {}).get(campo, "") if status == 200 else errore
        print(f"{etichetta} ora: {valore!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Migra la memoria legacy (gzip) dentro Hermes, senza inventare una mappatura.

Perché esiste: passare `MEMORY_BACKEND` da `legacy` a `hermes` NON porta con sé le
voci già scritte — il vecchio file resta lì e la memoria nuova nasce vuota. Qui si
leggono le voci vecchie e si mandano a Hermes **nella forma del contratto**
(`shared/memory_schema.py`, `hyperspace.memory.v1`), che è l'involucro che il
bridge conosce.

Tre scelte che valgono la pena di essere dette:

1. **Non cancella niente.** Il file legacy resta com'è: se la migrazione va male si
   riprova, e le voci originali non sono mai l'unica copia di qualcosa.
2. **È ripetibile.** L'id di una voce è stabile (id esplicito o hash del
   contenuto) e il bridge scarta i duplicati: rilanciarlo non raddoppia nulla.
3. **Dice cosa non entra.** L'envelope v1 ha campi suoi (schema, id, ts, type,
   content, source, retention...); una voce legacy che porta solo telemetria
   (`duration_ms`, `tokens_per_sec`) la perde — e viene contata e stampata qui,
   invece di sparire in silenzio.

    python scripts/memory_migrate.py --dry-run      # cosa entrerebbe, senza scrivere
    python scripts/memory_migrate.py                # migra (idempotente)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.hermes_memory import HermesMemoryClient  # noqa: E402
from shared.memory_schema import normalize_entry, validate_entry  # noqa: E402

PERCORSO_DEFAULT = ROOT / "data" / "runtime" / "memory" / "memory.json.gz"
LOTTO_DEFAULT = 25


def carica_voci(percorso) -> list:
    """Le voci dal file legacy: una lista, o sotto `entries`/`memory`."""
    with gzip.open(str(percorso), "rt", encoding="utf-8") as file:
        dati = json.load(file)
    if isinstance(dati, list):
        return dati
    if isinstance(dati, dict):
        for chiave in ("entries", "memory", "items"):
            if isinstance(dati.get(chiave), list):
                return dati[chiave]
    raise ValueError(f"formato non riconosciuto in {percorso}: "
                     f"attesa una lista o un oggetto con 'entries'")


def prepara_voci(voci) -> tuple:
    """Normalizza col contratto e dice cosa va perso. Ritorna (voci, problemi, persi).

    `campi_persi` conta le chiavi presenti nell'originale e assenti nell'involucro:
    è la parte che il contratto non prevede (telemetria, campi di una versione
    precedente), e va VISTA invece che subita.
    """
    preparate, problemi, campi_persi = [], [], {}
    for voce in voci or []:
        if not isinstance(voce, dict):
            problemi.append("voce non è un oggetto")
            continue
        normalizzata = normalize_entry(voce)
        for errore in validate_entry(normalizzata):
            problemi.append(f"{str(normalizzata.get('id', '?'))[:12]}: {errore}")
        for chiave in voce:
            if chiave not in normalizzata:
                campi_persi[chiave] = campi_persi.get(chiave, 0) + 1
        preparate.append(normalizzata)
    return preparate, problemi, campi_persi


def migra(client, voci, *, lotto: int = LOTTO_DEFAULT) -> dict:
    """Manda le voci a Hermes a blocchi. Ritorna il resoconto complessivo."""
    esito = {"stored": 0, "duplicates": 0, "failed": 0, "errors": []}
    passo = max(1, int(lotto))
    for inizio in range(0, len(voci), passo):
        risultato = client.import_entries(voci[inizio:inizio + passo])
        for chiave in ("stored", "duplicates", "failed"):
            esito[chiave] += int(risultato.get(chiave) or 0)
        esito["errors"].extend(risultato.get("errors") or [])
    return esito


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Migra la memoria legacy (gzip) dentro Hermes.")
    parser.add_argument("--file", default=str(PERCORSO_DEFAULT),
                        help="file gzip della memoria legacy")
    parser.add_argument("--dry-run", action="store_true",
                        help="mostra cosa entrerebbe, senza scrivere niente")
    parser.add_argument("--lotto", type=int, default=LOTTO_DEFAULT)
    parser.add_argument("--url", default=os.getenv("HERMES_MEMORY_URL", ""))
    parser.add_argument("--token-file", default=os.getenv("HERMES_MEMORY_TOKEN_FILE", ""))
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    percorso = Path(args.file)
    if not percorso.is_file():
        print(f"file di memoria non trovato: {percorso}")
        print("(default: <repo>/data/runtime/memory/memory.json.gz; in container: "
              "/app/memory/memory.json.gz)")
        return 1
    voci = carica_voci(percorso)
    preparate, problemi, persi = prepara_voci(voci)
    print(f"voci lette: {len(voci)} | preparate: {len(preparate)} | "
          f"problemi di contratto: {len(problemi)}")
    tipi = {}
    for voce in preparate:
        tipi[voce.get("type", "?")] = tipi.get(voce.get("type", "?"), 0) + 1
    print("tipi nell'envelope: " + json.dumps(tipi, ensure_ascii=False, sort_keys=True))
    if persi:
        print("campi NON previsti dall'envelope (restano nel file legacy):")
        for chiave, quante in sorted(persi.items(), key=lambda x: -x[1]):
            print(f"  - {chiave}: in {quante} voci")
    for problema in problemi[:10]:
        print(f"  [contratto] {problema}")

    if args.dry_run:
        print("\n(dry-run: non ho scritto niente)")
        return 0

    token = ""
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"non riesco a leggere il token in {args.token_file}: {e}")
            return 1
    client = HermesMemoryClient(base_url=args.url or None, token=token or None)
    print(f"\nHermes: {client.base_url} | token: {'si' if client.token else 'NO'}")
    if not client.token:
        print("senza token il bridge risponde 401: usa --token-file o "
              "HERMES_MEMORY_TOKEN_FILE")
        return 1
    esito = migra(client, preparate, lotto=args.lotto)
    print(f"importate: {esito['stored']} | duplicati: {esito['duplicates']} | "
          f"fallite: {esito['failed']}")
    for errore in esito["errors"][:5]:
        print(f"  [errore] {errore}")
    return 0 if esito["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cruscotto settimanale di crescita: KPI dai dati già persistiti.

Legge data/control-plane/{diario,instagram_vips}.json e stampa i KPI di
docs/growth-strategy.md: volume per serie, stato di pubblicazione, bande del
pubblico e ritenzione. Nessuna rete, nessun modello.

Uso:

    python3 scripts/growth_report.py            # console
    python3 scripts/growth_report.py --json     # JSON per automazioni
    python3 scripts/growth_report.py --days 14  # finestra diversa
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.growth_metrics import compute_growth  # noqa: E402

DEFAULT_DATA = ROOT / "data" / "control-plane"


def _load(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Cruscotto settimanale di crescita.")
    ap.add_argument("--days", type=int, default=7, help="finestra in giorni (default 7)")
    ap.add_argument("--json", action="store_true", help="stampa il report come JSON")
    args = ap.parse_args()

    diario = _load(DEFAULT_DATA / "diario.json").get("voci") or []
    vips = _load(DEFAULT_DATA / "instagram_vips.json").get("people") or {}
    report = compute_growth(diario, vips, days=args.days,
                            now=datetime.now(timezone.utc))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    serie = report["series"]
    v = report["vips"]
    print(f"Crescita — ultimi {report['window_days']} giorni\n")
    print("Serie (totali | recenti):")
    for nome in ("sogno", "poesia", "dialogo", "post"):
        s = serie[nome]
        print(f"  {nome:8s} {s['total']:3d} | {s['recent']:3d}")
    print(f"\nPubblicazione Instagram: {report['published']}")
    print(f"\nPubblico: {v['total_contacts']} contatti, {v['total_messages']} messaggi")
    print(f"  bande: {v['levels']}")
    print(f"  attivi negli ultimi {report['window_days']} giorni: {v['active_last_n_days']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

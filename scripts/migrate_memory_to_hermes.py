#!/usr/bin/env python3
"""Import the legacy compressed HyperSpace memory into Hermes idempotently."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.hermes_memory import HermesMemoryClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="legacy memory.json.gz or memory.jsonl snapshot")
    parser.add_argument("--url")
    parser.add_argument("--token")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.source.suffix.lower() == ".jsonl":
        entries = [json.loads(line) for line in args.source.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    else:
        with gzip.open(args.source, "rt", encoding="utf-8") as stream:
            entries = json.load(stream)
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise SystemExit("legacy snapshot must contain a JSON list of objects")
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "entries": len(entries)}, indent=2))
        return
    result = HermesMemoryClient(args.url, args.token, timeout=60).import_entries(entries)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

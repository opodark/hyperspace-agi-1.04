# SPDX-License-Identifier: Apache-2.0
"""Inserisce l'header SPDX nei sorgenti tracciati che non ce l'hanno.

Idempotente: salta i file che hanno gia' la riga. Se il file comincia con uno
shebang, la riga va **dopo** lo shebang — metterla sopra romperebbe l'eseguibile.

Questo script APPLICA la convenzione; `tests/test_licensing.py` la IMPONE. La
differenza conta: uno script che nessuno lancia non mantiene niente.

Uso:

    python3 scripts/add_license_headers.py            # mostra cosa farebbe
    python3 scripts/add_license_headers.py --apply
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPDX = "SPDX-License-Identifier: Apache-2.0"

# Il prefisso di commento giusto per estensione: usare `#` in un .js non lo
# romperebbe (sarebbe codice), quindi va scelto, non improvvisato.
PREFISSI = {".py": "#", ".js": "//", ".mjs": "//", ".sh": "#", ".ps1": "#"}


def tracciati():
    """I file di git, non quelli su disco: cosi' .venv e gli ignorati restano fuori."""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [Path(p) for p in out.stdout.splitlines() if p.endswith(tuple(PREFISSI))]


def aggiorna(percorso: Path, applica: bool) -> bool:
    """True se il file va intestato (e, con `applica`, se e' stato intestato)."""
    righe = (ROOT / percorso).read_text(encoding="utf-8").splitlines(keepends=True)
    if any(SPDX in riga for riga in righe[:5]):
        return False
    inizio = 1 if righe and righe[0].startswith("#!") else 0
    righe.insert(inizio, f"{PREFISSI[percorso.suffix]} {SPDX}\n")
    if applica:
        (ROOT / percorso).write_text("".join(righe), encoding="utf-8")
    return True


def main() -> int:
    applica = "--apply" in sys.argv
    cambiati = [p for p in tracciati() if aggiorna(p, applica)]

    for percorso in cambiati[:20]:
        print(f"  {'intestato' if applica else 'da intestare'}: {percorso}")
    if len(cambiati) > 20:
        print(f"  ... e altri {len(cambiati) - 20}")

    verbo = "aggiornati" if applica else "da aggiornare"
    print(f"\n{len(cambiati)} file {verbo}")
    if cambiati and not applica:
        print("Rilancia con --apply per applicare.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

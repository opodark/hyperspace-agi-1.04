# SPDX-License-Identifier: Apache-2.0
"""Verifica che la licenza sia APPLICATA, non solo dichiarata.

Un file LICENSE e' una promessa; l'header SPDX e' la promessa applicata file per
file. Questo test esiste perche' la convenzione non si perda: un sorgente nuovo
senza header fallisce qui invece di passare inosservato per anni.
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPDX = "SPDX-License-Identifier: Apache-2.0"
ESTENSIONI = (".py", ".js", ".mjs", ".sh", ".ps1")

# Le licenze che il progetto NON usa. Serve a intercettare il caso peggiore: un
# file copiato da altrove che porta con se' un'altra licenza, in silenzio.
ALTRE_LICENZE = ("SPDX-License-Identifier: MIT",
                 "SPDX-License-Identifier: GPL",
                 "SPDX-License-Identifier: AGPL",
                 "SPDX-License-Identifier: BSD",
                 "SPDX-License-Identifier: MPL")


def sorgenti():
    """I sorgenti tracciati da git (non i file su disco: .venv resta fuori)."""
    esito = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if esito.returncode != 0:
        raise unittest.SkipTest("git non disponibile: verifica saltata")
    return [p for p in esito.stdout.splitlines() if p.endswith(ESTENSIONI)]


class TestLicenzaDelProgetto(unittest.TestCase):
    def test_license_e_apache_2_0(self):
        testo = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", testo)
        self.assertIn("Version 2.0, January 2004", testo)
        self.assertNotIn("Permission is hereby granted, free of charge", testo,
                         "il testo MIT non deve piu' essere presente")

    def test_notice_nomina_i_due_detentori(self):
        """Il LICENSE non basta: i detentori vanno nominati, entrambi."""
        testo = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("opodark", testo)
        self.assertIn("cips", testo)
        self.assertIn("Copyright 2026", testo)

    def test_third_party_notices_esiste_e_copre_i_servizi(self):
        testo = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
        # I nomi come compaiono nel requirements e nella tabella: il nome del
        # PROGETTO (Flask) e quello del PACCHETTO (fastapi) non coincidono sempre,
        # e il test deve cercare quello che il file contiene davvero.
        for atteso in ("control-plane/requirements.txt", "node/requirements.txt",
                       "sandbox/requirements.txt", "Flask", "fastapi"):
            self.assertIn(atteso, testo)

    def test_la_decisione_e_documentata(self):
        testo = (ROOT / "docs" / "licensing.md").read_text(encoding="utf-8")
        self.assertIn("Apache", testo)
        self.assertIn("rimandato", testo.lower(), "lo split open-core va spiegato")


class TestHeaderNeiSorgenti(unittest.TestCase):
    def test_ogni_sorgente_ha_l_header(self):
        mancanti = []
        for percorso in sorgenti():
            righe = (ROOT / percorso).read_text(encoding="utf-8", errors="replace").splitlines()
            if not any(SPDX in riga for riga in righe[:5]):
                mancanti.append(percorso)
        self.assertEqual(mancanti, [],
                         f"senza header SPDX ({len(mancanti)}): rilancia "
                         f"`python3 scripts/add_license_headers.py --apply`")

    def test_nessun_sorgente_dichiara_un_altra_licenza(self):
        colpevoli = []
        for percorso in sorgenti():
            righe = (ROOT / percorso).read_text(encoding="utf-8", errors="replace").splitlines()[:5]
            for riga in righe:
                if any(altra in riga for altra in ALTRE_LICENZE):
                    colpevoli.append(f"{percorso}: {riga.strip()}")
        self.assertEqual(colpevoli, [], f"licenza diversa da Apache-2.0: {colpevoli}")

    def test_lo_shebang_resta_la_prima_riga(self):
        """L'header va DOPO lo shebang: sopra lo renderebbe inerte."""
        rotti = []
        for percorso in sorgenti():
            righe = (ROOT / percorso).read_text(encoding="utf-8", errors="replace").splitlines()
            if len(righe) < 2:
                continue
            if righe[0].startswith("#!") is False and righe[0].strip().startswith("#!"):
                rotti.append(percorso)
            if righe[0].lstrip().startswith("#!") and righe[0] != righe[0].lstrip():
                rotti.append(percorso)
        self.assertEqual(rotti, [], f"shebang non in prima colonna: {rotti}")


if __name__ == "__main__":
    unittest.main()

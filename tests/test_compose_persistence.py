# SPDX-License-Identifier: Apache-2.0
"""I servizi con stato devono montare il loro /app/data sull'host.

Nasce da un guasto osservato: al control-plane mancava il volume per /app/data,
quindi ogni `docker compose up -d --build` azzerava il database E rigenerava
l'identita' ECDSA del CP. Il `peer_id` e' cambiato due volte in una sessione:
225378a217ffe488... -> 7fdd15a5491e3640... Un peer accoppiato — cioe' in
allowlist per PUBKEY — non avrebbe piu' riconosciuto questo CP, e i task e i log
sarebbero spariti senza un errore da nessuna parte.

Il test legge i compose come testo (come ComposeIsolationTests in
tests/test_code_sandbox.py): PyYAML non e' fra le dipendenze della suite, e la
regex qui basta a proteggere la riga che conta.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]

# servizio -> file, per il nome che ha in ciascun compose
SERVIZI_CON_DATI = {
    "docker-compose.yml":          ("control-plane", "node-1"),
    "docker-compose.windows.yml":  ("hyperspace-core", "hyperspace-node-1"),
}


def _blocco(testo, servizio):
    match = re.search(rf"(?ms)^  {re.escape(servizio)}:\n.*?(?=^  \S|\Z)", testo)
    return match.group(0) if match else ""


class ComposePersistenceTests(unittest.TestCase):
    def test_chi_ha_stato_monta_app_data(self):
        for filename, servizi in SERVIZI_CON_DATI.items():
            testo = (ROOT / filename).read_text(encoding="utf-8")
            for servizio in servizi:
                with self.subTest(compose=filename, servizio=servizio):
                    blocco = _blocco(testo, servizio)
                    self.assertTrue(blocco, f"{servizio} non trovato in {filename}")
                    self.assertIn("/app/data", blocco,
                                  f"{servizio} in {filename} non persiste /app/data: "
                                  f"database e identita' spariscono a ogni recreate")

    def test_il_mount_non_e_un_volume_anonimo(self):
        """Un volume nominato sopravvive, ma non e' ispezionabile ne'
        cancellabile dall'utente: per dati che l'operatore deve poter guardare e
        salvare si monta una directory della repo (data/ e' in .gitignore)."""
        for filename, servizi in SERVIZI_CON_DATI.items():
            testo = (ROOT / filename).read_text(encoding="utf-8")
            for servizio in servizi:
                with self.subTest(compose=filename, servizio=servizio):
                    blocco = _blocco(testo, servizio)
                    mount = [r for r in re.findall(r"^\s+- (\S+):/app/data", blocco, re.M)]
                    self.assertTrue(mount, f"{servizio}: nessun mount per /app/data")
                    for sorgente in mount:
                        self.assertTrue(sorgente.startswith(("./data/", "${")),
                                        f"{servizio}: sorgente inattesa per /app/data: {sorgente}")


if __name__ == "__main__":
    unittest.main()

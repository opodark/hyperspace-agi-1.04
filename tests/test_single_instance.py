# SPDX-License-Identifier: Apache-2.0
"""Il lucchetto di processo: due driver non devono poter girare insieme.

Il caso non è teorico (2026-09-22): su Windows lo stesso launcher è partito due
volte e i due driver si sono divisi i messaggi di Telegram — **senza un errore nei
log**, perché due poller non litigano: ognuno prende una metà degli update. Da
fuori si vede solo una che risponde a metà.

I test girano su processi veri: è lì che il lucchetto deve funzionare (dentro un
solo processo sarebbe solo una variabile).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.single_instance import AlreadyRunning, SingleInstance  # noqa: E402


def prova_in_un_altro_processo(lock: Path) -> subprocess.CompletedProcess:
    """Avvia un figlio che PROVA a prendere il lucchetto e dice com'è andata."""
    codice = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from shared.single_instance import AlreadyRunning, SingleInstance\n"
        "try:\n"
        f"    SingleInstance({str(lock)!r}, label='prova').acquire()\n"
        "except AlreadyRunning:\n"
        "    print('occupato', flush=True)\n"
        "else:\n"
        "    print('entrato', flush=True)\n"
    )
    return subprocess.run([sys.executable, "-c", codice], capture_output=True,
                          text=True, timeout=60)


class SingleInstanceTests(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.lock = Path(self._dir.name) / "prova.lock"

    def tearDown(self):
        self._dir.cleanup()

    def test_un_altro_processo_non_entra_mentre_il_lucchetto_e_preso(self):
        lucchetto = SingleInstance(self.lock, label="prova").acquire()
        try:
            esito = prova_in_un_altro_processo(self.lock)
            self.assertIn("occupato", esito.stdout)
            self.assertNotIn("entrato", esito.stdout)
        finally:
            lucchetto.release()

    def test_quando_il_processo_muore_il_lucchetto_si_libera(self):
        """Niente lucchetti fantasma: se il processo sparisce, sparisce il lucchetto."""
        esito = prova_in_un_altro_processo(self.lock)
        self.assertIn("entrato", esito.stdout)
        # Il figlio è già uscito senza rilasciare: qui si deve poter entrare.
        SingleInstance(self.lock, label="prova").acquire().release()

    def test_il_rilascio_esplicito_libera_subito(self):
        lucchetto = SingleInstance(self.lock, label="prova")
        lucchetto.acquire()
        lucchetto.release()
        self.assertIn("entrato", prova_in_un_altro_processo(self.lock).stdout)

    def test_rilasciare_due_volte_non_e_un_errore(self):
        lucchetto = SingleInstance(self.lock, label="prova").acquire()
        lucchetto.release()
        lucchetto.release()

    def test_nello_stesso_processo_il_secondo_non_entra(self):
        primo = SingleInstance(self.lock, label="prova").acquire()
        try:
            with self.assertRaises(AlreadyRunning):
                SingleInstance(self.lock, label="prova").acquire()
        finally:
            primo.release()

    def test_il_context_manager_rilascia(self):
        with SingleInstance(self.lock, label="prova"):
            self.assertIn("occupato", prova_in_un_altro_processo(self.lock).stdout)
        self.assertIn("entrato", prova_in_un_altro_processo(self.lock).stdout)

    def test_la_cartella_manca_e_viene_creata(self):
        dentro = Path(self._dir.name) / "sotto" / "cartella" / "prova.lock"
        SingleInstance(dentro, label="prova").acquire().release()
        self.assertTrue(dentro.exists())

    def test_un_altro_processo_puo_leggere_il_pid(self):
        """Il file resta leggibile da chiunque mentre è preso.

        Su Windows la parte bloccata di un file non è leggibile dagli altri
        processi, e il controllo è sulla lunghezza *richiesta* dalla lettura:
        bloccando un byte vicino all'inizio, chi apriva il lucchetto per curiosità
        riceveva "un altro processo ha bloccato una parte del file" — un errore che
        sembra un guasto mentre è tutto regolare. Questo test è nato da quel caso.
        """
        lucchetto = SingleInstance(self.lock, label="prova").acquire()
        try:
            codice = f"import sys; sys.stdout.write(open({str(self.lock)!r}).read())"
            esito = subprocess.run([sys.executable, "-c", codice], capture_output=True,
                                   text=True, timeout=60)
            self.assertEqual(esito.returncode, 0, esito.stderr)
            self.assertEqual(esito.stdout.strip(), str(os.getpid()))
        finally:
            lucchetto.release()

    def test_il_messaggio_dice_chi_e_il_lucchetto(self):
        lucchetto = SingleInstance(self.lock, label="driver Telegram").acquire()
        try:
            with self.assertRaises(AlreadyRunning) as errore:
                SingleInstance(self.lock, label="driver Telegram").acquire()
            self.assertIn("driver Telegram", str(errore.exception))
            self.assertIn(str(self.lock), str(errore.exception))
        finally:
            lucchetto.release()


if __name__ == "__main__":
    unittest.main()

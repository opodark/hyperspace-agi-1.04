# SPDX-License-Identifier: Apache-2.0
"""Il token di Hermes: un segreto solo, in tre posti diversi.

Il rischio vero non e' generarlo: e' che i tre file divergano in silenzio. Un lato
risponderebbe 401 e la memoria del Mac smetterebbe di scrivere senza dire perche'.
Questi test difendono il "tutti uguali" e il fatto che un token non finisca mai
stampato dai controlli.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hermes_token  # noqa: E402


class TokenTests(unittest.TestCase):
    def test_e_di_sessantaquattro_caratteri_esadecimali(self):
        token = hermes_token.genera_token()
        self.assertEqual(len(token), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in token))

    def test_due_token_non_sono_mai_uguali(self):
        self.assertNotEqual(hermes_token.genera_token(), hermes_token.genera_token())

    def test_e_piu_lungo_del_minimo_che_il_bridge_accetta(self):
        self.assertGreater(len(hermes_token.genera_token()),
                           hermes_token.MIN_TOKEN_LENGTH)


class PercorsiTests(unittest.TestCase):
    def test_i_due_percorsi_di_default(self):
        elenco = hermes_token.percorsi(Path("C:/repo"))
        self.assertEqual([str(p).replace("\\", "/") for p in elenco],
                         ["C:/repo/data/hermes-memory.token",
                          "C:/repo/data/runtime/data/hermes-memory.token"])

    def test_hs_data_dir_sostituisce_il_default(self):
        elenco = hermes_token.percorsi(Path("C:/repo"), "C:/repo/data/runtime/data")
        self.assertEqual(len(elenco), 2)
        self.assertIn("runtime", str(elenco[1]))

    def test_un_percorso_extra_si_aggiunge(self):
        elenco = hermes_token.percorsi(Path("C:/repo"), "", "C:/HyperSpace/data")
        self.assertEqual(len(elenco), 3)
        self.assertIn("HyperSpace", str(elenco[2]))
        self.assertTrue(str(elenco[2]).endswith("hermes-memory.token"))

    def test_un_percorso_extra_che_e_gia_il_file_non_si_raddoppia(self):
        elenco = hermes_token.percorsi(Path("C:/repo"), "",
                                       "C:/altrove/mio.token")
        self.assertTrue(str(elenco[2]).endswith("mio.token"))
        self.assertNotIn("mio.token\\hermes-memory.token", str(elenco[2]))

    def test_i_doppioni_non_si_ripetono(self):
        """Su Windows maiuscole e separatori fanno facilmente lo stesso percorso."""
        elenco = hermes_token.percorsi(Path("C:/repo"), "C:/Repo/Data/Runtime/Data")
        chiavi = {str(p).lower().replace("/", "\\") for p in elenco}
        self.assertEqual(len(chiavi), len(elenco))


class ImprontaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, nome, contenuto):
        percorso = self.cartella / nome
        percorso.write_text(contenuto, encoding="utf-8")
        return percorso

    def test_un_file_assente_non_ha_impronta(self):
        self.assertEqual(hermes_token.impronta(self.cartella / "nonesiste"), "")

    def test_lo_stesso_token_da_la_stessa_impronta(self):
        token = hermes_token.genera_token()
        a = self._file("a.token", token + "\n")
        b = self._file("b.token", token)          # senza newline finale
        self.assertEqual(hermes_token.impronta(a), hermes_token.impronta(b))
        self.assertEqual(len(hermes_token.impronta(a)), 12)

    def test_token_diversi_danno_impronte_diverse(self):
        a = self._file("a.token", hermes_token.genera_token())
        b = self._file("b.token", hermes_token.genera_token())
        self.assertNotEqual(hermes_token.impronta(a), hermes_token.impronta(b))

    def test_l_impronta_non_contiene_il_token(self):
        token = hermes_token.genera_token()
        percorso = self._file("a.token", token)
        self.assertNotIn(token, hermes_token.impronta(percorso))


class AllineamentoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _righe(self, *percorsi):
        return hermes_token.stato([Path(p) for p in percorsi])

    def _file(self, nome, contenuto):
        percorso = self.cartella / nome
        percorso.write_text(contenuto, encoding="utf-8")
        return str(percorso)

    def test_tutti_uguali_e_allineato(self):
        token = hermes_token.genera_token()
        righe = self._righe(self._file("a", token), self._file("b", token))
        self.assertTrue(hermes_token.allineati(righe))

    def test_uno_diverso_non_e_allineato(self):
        righe = self._righe(self._file("a", hermes_token.genera_token()),
                            self._file("b", hermes_token.genera_token()))
        self.assertFalse(hermes_token.allineati(righe))

    def test_un_file_mancante_non_rompe_l_allineamento(self):
        """Il launcher del bridge guarda un file che il CP non ha: se il CP e' a
        posto, il verdetto non deve dire '401' per un file che non serve."""
        token = hermes_token.genera_token()
        righe = self._righe(self._file("a", token), str(self.cartella / "assente"))
        self.assertTrue(hermes_token.allineati(righe))

    def test_un_file_vuoto_non_conta_come_allineato(self):
        righe = self._righe(self._file("a", ""), self._file("b", ""))
        self.assertTrue(hermes_token.allineati(righe))   # nessun token presente
        righe = self._righe(self._file("a", hermes_token.genera_token()),
                            self._file("b", ""))
        self.assertTrue(hermes_token.allineati(righe))


class ScritturaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scrive_in_tutti_i_percorsi_creando_le_cartelle(self):
        token = hermes_token.genera_token()
        elenco = [self.cartella / "data" / "hermes-memory.token",
                  self.cartella / "data" / "runtime" / "data" / "hermes-memory.token"]
        scritti = hermes_token.scrittura(elenco, token)
        self.assertEqual(len(scritti), 2)
        for percorso in elenco:
            self.assertEqual(percorso.read_text(encoding="utf-8").strip(), token)

    def test_dopo_la_scrittura_i_file_sono_allineati(self):
        token = hermes_token.genera_token()
        elenco = [self.cartella / "a.token", self.cartella / "b.token"]
        hermes_token.scrittura(elenco, token)
        self.assertTrue(hermes_token.allineati(hermes_token.stato(elenco)))


if __name__ == "__main__":
    unittest.main()

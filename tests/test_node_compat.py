# SPDX-License-Identifier: Apache-2.0
"""Test della compatibilita' di protocollo fra control-plane e nodi.

Il caso che conta non e' "la versione combacia": e' che un nodo che NON dichiara
la versione non venga buttato fuori dalla mesh in modalita' normale, e che
l'avviso compaia una volta sola.
"""
import unittest

from shared.node_compat import (
    MISSING, MODE_ADVISORY, MODE_STRICT, NEWER, OK, OLDER, UNKNOWN, PROTOCOL_VERSION,
    ProtocolWatch, check, is_usable, mode_from_env,
)


class TestCheck(unittest.TestCase):
    def test_versione_allineata(self):
        stato, _ = check({"protocol_version": PROTOCOL_VERSION})
        self.assertEqual(stato, OK)

    def test_nodo_piu_avanti(self):
        stato, motivo = check({"protocol_version": PROTOCOL_VERSION + 1})
        self.assertEqual(stato, NEWER)
        self.assertIn("avanti", motivo)

    def test_nodo_piu_indietro_ma_compatibile(self):
        stato, _ = check({"protocol_version": 1}, supported=2, minimum=1)
        self.assertEqual(stato, OLDER)

    def test_nodo_sotto_il_minimo(self):
        stato, motivo = check({"protocol_version": 1}, supported=3, minimum=3)
        self.assertEqual(stato, OLDER)
        self.assertIn("incompatibile", motivo)

    def test_versione_assente_e_il_caso_dell_immagine_vecchia(self):
        stato, motivo = check({})
        self.assertEqual(stato, MISSING)
        self.assertIn("piu' vecchia del repository", motivo)

    def test_stringa_vuota_vale_come_assente(self):
        self.assertEqual(check({"protocol_version": ""})[0], MISSING)

    def test_versione_non_numerica_non_solleva(self):
        stato, motivo = check({"protocol_version": "una"})
        self.assertEqual(stato, UNKNOWN)
        self.assertIn("non numerica", motivo)

    def test_payload_inatteso_non_solleva(self):
        self.assertEqual(check(None)[0], MISSING)
        self.assertEqual(check("non-un-dizionario")[0], UNKNOWN)


class TestUsabilita(unittest.TestCase):
    def test_advisory_lascia_passare_tutto_tranne_l_ignoto(self):
        for stato in (OK, OLDER, NEWER, MISSING):
            with self.subTest(stato=stato):
                self.assertTrue(is_usable(stato, MODE_ADVISORY))
        self.assertFalse(is_usable(UNKNOWN, MODE_ADVISORY))

    def test_strict_passa_solo_il_protocollo_esatto(self):
        self.assertTrue(is_usable(OK, MODE_STRICT))
        for stato in (OLDER, NEWER, MISSING, UNKNOWN):
            with self.subTest(stato=stato):
                self.assertFalse(is_usable(stato, MODE_STRICT))

    def test_modalita_da_env(self):
        self.assertEqual(mode_from_env({}), MODE_ADVISORY)
        self.assertEqual(mode_from_env({"NODE_PROTOCOL_MODE": "strict"}), MODE_STRICT)
        self.assertEqual(mode_from_env({"NODE_PROTOCOL_MODE": "STRICT"}), MODE_STRICT)
        self.assertEqual(mode_from_env({"NODE_PROTOCOL_MODE": "boh"}), MODE_ADVISORY)


class TestProtocolWatch(unittest.TestCase):
    def test_avvisa_una_volta_sola(self):
        w = ProtocolWatch()
        nodo = {"node_id": "n1"}
        self.assertTrue(w.observe(nodo)[2])
        self.assertFalse(w.observe(nodo)[2], "il secondo avviso va taciuto")

    def test_avvisa_di_nuovo_se_cambia_lo_stato(self):
        w = ProtocolWatch()
        self.assertTrue(w.observe({"node_id": "n1", "protocol_version": 1})[2])
        self.assertTrue(w.observe({"node_id": "n1"})[2], "stato diverso = nuovo avviso")

    def test_nodi_diversi_hanno_avvisi_indipendenti(self):
        w = ProtocolWatch()
        self.assertTrue(w.observe({"node_id": "n1"})[2])
        self.assertTrue(w.observe({"node_id": "n2"})[2])

    def test_advisory_non_esclude_nessuno(self):
        w = ProtocolWatch()
        nodi = [{"node_id": "vecchio"}, {"node_id": "ok", "protocol_version": PROTOCOL_VERSION}]
        righe = []
        ammessi = w.report(nodi, log=righe.append)
        self.assertEqual(len(ammessi), 2)
        self.assertEqual(len(righe), 1, "solo il nodo senza versione va segnalato")

    def test_strict_esclude_ma_non_cancella_il_nodo_dagli_altri(self):
        w = ProtocolWatch(mode=MODE_STRICT)
        nodi = [{"node_id": "vecchio"}, {"node_id": "ok", "protocol_version": PROTOCOL_VERSION}]
        ammessi = w.report(nodi, log=lambda *_: None)
        self.assertEqual([n["node_id"] for n in ammessi], ["ok"])

    def test_report_su_lista_vuota(self):
        self.assertEqual(ProtocolWatch().report([], log=lambda *_: None), [])
        self.assertEqual(ProtocolWatch().report(None, log=lambda *_: None), [])

    def test_describe_non_espone_nulla_di_sensibile(self):
        self.assertEqual(ProtocolWatch().describe()["mode"], MODE_ADVISORY)


if __name__ == "__main__":
    unittest.main()

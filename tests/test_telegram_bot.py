# SPDX-License-Identifier: Apache-2.0
"""Il driver Telegram: due bot nello stesso gruppo non devono rispondersi a vicenda.

Il driver e' uno script con un ciclo infinito, quindi qui si copre la sola parte
decidibile — filtro dei messaggi, riconoscimento della chiamata per nome,
fail-closed senza token — caricando il modulo con l'ambiente finto. La rete e il
ciclo getUpdates restano fuori, come per gli altri moduli puri del repo.
"""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "telegram_bot.py"
TOKEN_BOT = "123456:token-finto"
TOKEN_CANALE = "d" * 64
NOSTRO = "Aurora_2001Bot"


def carica_driver(**env):
    """Importa il driver con l'ambiente indicato (senza token esce all'import)."""
    ambiente = {"TELEGRAM_BOT_TOKEN": TOKEN_BOT, "CHANNEL_TOKEN": TOKEN_CANALE}
    ambiente.update(env)
    spec = importlib.util.spec_from_file_location("telegram_bot_sotto_test", DRIVER)
    modulo = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, ambiente, clear=False):
        spec.loader.exec_module(modulo)
    return modulo


class SenzaTokenTests(unittest.TestCase):
    """Senza i due token il driver non deve partire in silenzio."""

    def test_senza_token_esce_con_il_nome_del_token_mancante(self):
        for mancante in ("TELEGRAM_BOT_TOKEN", "CHANNEL_TOKEN"):
            with self.subTest(mancante=mancante):
                ambiente = {"TELEGRAM_BOT_TOKEN": TOKEN_BOT, "CHANNEL_TOKEN": TOKEN_CANALE}
                ambiente[mancante] = ""   # solo questo manca: l'altro resta valido
                esito = subprocess.run([sys.executable, str(DRIVER)],
                                       capture_output=True, text=True,
                                       env={**os.environ, **ambiente})
                self.assertNotEqual(esito.returncode, 0)
                self.assertIn(mancante, esito.stdout + esito.stderr)


class MessaggiDiBotTests(unittest.TestCase):
    """Il ciclo A pubblica -> B legge -> B risponde -> A legge non deve esistere."""

    @classmethod
    def setUpClass(cls):
        cls.driver = carica_driver()

    def test_i_messaggi_di_un_altro_bot_si_riconoscono(self):
        self.assertTrue(self.driver.da_bot({"from": {"is_bot": True,
                                                    "username": "pimpachatbot"}}))
        self.assertFalse(self.driver.da_bot({"from": {"is_bot": False,
                                                     "username": "alberto"}}))

    def test_un_messaggio_senza_mittente_non_e_di_un_bot(self):
        self.assertFalse(self.driver.da_bot({}))


class ChiamataPerNomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = carica_driver()

    def test_la_menzione_iniziale_sparisce(self):
        # Serve al comando dell'operatore: il CP confronta il testo dall'inizio.
        self.assertEqual(
            self.driver.testo_senza_menzione("@Aurora_2001Bot !presentati", NOSTRO),
            "!presentati")

    def test_la_menzione_si_riconosce_senza_distinzione_di_maiuscole(self):
        self.assertEqual(
            self.driver.testo_senza_menzione("@aurora_2001bot ciao", NOSTRO),
            "ciao")

    def test_una_menzione_non_iniziale_resta_nel_testo(self):
        self.assertEqual(
            self.driver.testo_senza_menzione("guarda @Aurora_2001Bot", NOSTRO),
            "guarda @Aurora_2001Bot")

    def test_una_menzione_da_sola_resta_una_chiamata(self):
        self.assertEqual(self.driver.testo_senza_menzione("@Aurora_2001Bot", NOSTRO),
                         "@Aurora_2001Bot")

    def test_senza_username_conosciuto_il_testo_resta_intatto(self):
        self.assertEqual(self.driver.testo_senza_menzione("@x ciao", ""), "@x ciao")

    def test_e_rivolto_a_noi_con_menzione_o_con_una_risposta(self):
        self.assertTrue(self.driver.rivolta_a_noi({"text": f"@{NOSTRO} ci sei?"},
                                                  NOSTRO, 42))
        self.assertTrue(self.driver.rivolta_a_noi(
            {"text": "eccolo", "reply_to_message": {"from": {"id": 42}}}, NOSTRO, 42))

    def test_non_e_rivolto_a_noi_una_chiamata_all_altro_bot(self):
        self.assertFalse(self.driver.rivolta_a_noi(
            {"text": "@pimpachatbot ci sei?", "reply_to_message": {"from": {"id": 7}}},
            NOSTRO, 42))

    def test_senza_nome_o_senza_id_non_si_riconosce_nulla(self):
        self.assertFalse(self.driver.rivolta_a_noi({"text": "@x ciao"}, "", 42))
        self.assertFalse(self.driver.rivolta_a_noi(
            {"text": "ciao", "reply_to_message": {"from": {"id": 42}}}, NOSTRO, 0))


class ModalitaMentionTests(unittest.TestCase):
    """Il comportamento di default NON cambia: la mention è opt-in."""

    def test_di_default_non_si_chiede_la_mention(self):
        self.assertFalse(carica_driver().REQUIRE_MENTION)

    def test_con_la_variabile_attiva_si_chiede_la_mention(self):
        self.assertTrue(carica_driver(TELEGRAM_REQUIRE_MENTION="1").REQUIRE_MENTION)


if __name__ == "__main__":
    unittest.main()

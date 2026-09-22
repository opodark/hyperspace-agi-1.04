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

    def test_il_nome_e_la_prima_parola_del_nome_del_bot(self):
        # Il nome vero di Aurora e' "Aurora · IA di HyperSpace, voce della mesh".
        me = {"first_name": "Aurora · IA di HyperSpace, voce della mesh"}
        self.assertEqual(self.driver.nome_chiamata(me), "aurora")

    def test_un_nome_troppo_corto_non_si_usa(self):
        # "ai" comparirebbe in mezza conversazione.
        self.assertEqual(self.driver.nome_chiamata({"first_name": "Ai"}), "")
        self.assertEqual(self.driver.nome_chiamata({}), "")

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

    def test_il_nome_all_inizio_e_una_chiamata(self):
        """In "UltraMind" le chiamate erano "@aurora presentati" e "aurora?":
        il nome NON e' una menzione Telegram, e' testo."""
        for testo in ("@aurora presentati", "aurora, ci sei?", "Aurora?"):
            with self.subTest(testo=testo):
                self.assertTrue(self.driver.rivolta_a_noi({"text": testo}, NOSTRO, 42,
                                                          "aurora"))

    def test_il_nome_a_meta_frase_non_e_una_chiamata(self):
        for testo in ("la mia aurora boreale", "auroraboreale e' un fenomeno"):
            with self.subTest(testo=testo):
                self.assertFalse(self.driver.rivolta_a_noi({"text": testo}, NOSTRO, 42,
                                                           "aurora"))

    def test_si_toglie_anche_il_nome_iniziale(self):
        self.assertEqual(
            self.driver.testo_senza_menzione("@aurora presentati", NOSTRO, "aurora"),
            "presentati")
        self.assertEqual(
            self.driver.testo_senza_menzione("Aurora presentati", NOSTRO, "aurora"),
            "presentati")


class ComandoPresentazioneTests(unittest.TestCase):
    """Tre "presentati" sono rimasti senza risposta in UltraMind: il CP conosce
    solo `!presentati`, quindi la traduzione va fatta dove il nome e' stato
    riconosciuto."""

    @classmethod
    def setUpClass(cls):
        cls.driver = carica_driver()

    def test_presentati_diventa_il_comando_del_control_plane(self):
        for detto in ("presentati", "Presentati!", "presentati.", "intro",
                      "!presentati", "!intro"):
            with self.subTest(detto=detto):
                self.assertEqual(self.driver.normalizza_comando(detto), "!presentati")

    def test_una_frase_che_parla_di_presentazioni_non_diventa_un_comando(self):
        for detto in ("presentati tutti gli altri", "vi presento Mario", "ciao"):
            with self.subTest(detto=detto):
                self.assertEqual(self.driver.normalizza_comando(detto), detto)


class ModalitaMentionTests(unittest.TestCase):
    """Il comportamento di default NON cambia: la mention è opt-in."""

    def test_di_default_non_si_chiede_la_mention(self):
        self.assertFalse(carica_driver().REQUIRE_MENTION)

    def test_con_la_variabile_attiva_si_chiede_la_mention(self):
        self.assertTrue(carica_driver(TELEGRAM_REQUIRE_MENTION="1").REQUIRE_MENTION)


class ConsegnaImmaginiTests(unittest.TestCase):
    """La consegna: il CP mette in outbox, il driver manda il FILE.

    Il control-plane non ha l'immagine e non sa parlare con Telegram: la unisce il
    driver, che ha entrambi. Queste sono le regole di quella unione.
    """

    @classmethod
    def setUpClass(cls):
        cls.driver = carica_driver()

    def test_solo_le_consegne_complete_passano(self):
        risposta = {"messages": [
            {"id": "a1", "file": "C:/out/x.png", "destinazione": "123",
             "prompt": "un gatto"},
            {"id": "a2", "file": "", "destinazione": "123"},          # senza file
            {"id": "a3", "file": "C:/out/y.png", "destinazione": ""},  # senza chat
            {"id": "", "file": "C:/out/z.png", "destinazione": "1"},   # senza id
            "spazzatura",
        ]}
        consegne = self.driver.immagini_da_consegnare(risposta)
        self.assertEqual([c["id"] for c in consegne], ["a1"])
        self.assertEqual(consegne[0]["chat"], "123")

    def test_un_outbox_vuoto_non_e_un_errore(self):
        self.assertEqual(self.driver.immagini_da_consegnare({}), [])
        self.assertEqual(self.driver.immagini_da_consegnare({"messages": []}), [])
        self.assertEqual(self.driver.immagini_da_consegnare(None), [])


class UnSoloDriverTests(unittest.TestCase):
    """Due driver sullo stesso bot si rubano i messaggi: il secondo non deve partire.

    Non è teoria: su Windows lo stesso launcher è partito due volte e i due driver
    si sono divisi gli update di Telegram — nessun errore, solo una che rispondeva
    a metà. Il lucchetto lo rende impossibile comunque venga lanciato.
    """

    def test_il_secondo_driver_esce_invece_di_girare(self):
        import tempfile
        with tempfile.TemporaryDirectory() as cartella:
            driver = carica_driver(TELEGRAM_LOCK_FILE=str(Path(cartella) / "unico.lock"))
            lucchetto = driver.un_solo_driver()   # il primo prende il lucchetto
            try:
                with self.assertRaises(SystemExit) as uscita:
                    driver.un_solo_driver()       # il secondo deve arrendersi
                self.assertIn("secondo driver", str(uscita.exception))
            finally:
                # Su Windows un file bloccato non si cancella: senza rilascio il
                # teardown della cartella temporanea fallirebbe.
                lucchetto.release()

    def test_il_lucchetto_predefinito_sta_nei_dati_del_repo(self):
        driver = carica_driver()
        self.assertTrue(Path(driver.LOCK_FILE).name == "telegram-driver.lock")
        self.assertEqual(Path(driver.LOCK_FILE).parent, ROOT / "data")


class ConversazioneTests(unittest.TestCase):
    """Chi ha avviato una conversazione non ripete il nome a ogni frase.

    Verificato dai log: quattro messaggi ingeriti e ZERO tentativi di risposta,
    perché il nome non c'era. La finestra risolve quello senza trasformare il bot
    in un ascoltatore permanente di una stanza che non la sta cercando.
    """

    @classmethod
    def setUpClass(cls):
        cls.driver = carica_driver()

    def test_senza_una_nostra_risposta_non_c_e_conversazione(self):
        self.assertFalse(self.driver.in_conversazione({}, 1000.0))
        self.assertFalse(self.driver.in_conversazione({"ultima_risposta_ts": 0.0}, 1000.0))

    def test_dentro_la_finestra_la_conversazione_e_aperta(self):
        entry = {"ultima_risposta_ts": 1000.0}
        self.assertTrue(self.driver.in_conversazione(entry, 1060.0, finestra=900))

    def test_passata_la_finestra_torna_a_servire_il_nome(self):
        entry = {"ultima_risposta_ts": 1000.0}
        self.assertFalse(self.driver.in_conversazione(entry, 1901.0, finestra=900))

    def test_una_finestra_a_zero_non_apre_niente(self):
        entry = {"ultima_risposta_ts": 1000.0}
        self.assertFalse(self.driver.in_conversazione(entry, 1001.0, finestra=0))


class SegnaleScritturaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = carica_driver()

    def _finto_tg(self, errore=False):
        chiamate = []

        def finto(metodo, **params):
            if errore:
                raise self.driver.requests.RequestException("niente rete")
            chiamate.append((metodo, params))
            return {}

        self.driver.tg = finto
        return chiamate

    def test_il_segnale_non_si_ripete_a_ogni_giro(self):
        chiamate = self._finto_tg()
        stato = {}
        self.assertTrue(self.driver.segnala_scrittura(1, stato, 100.0))
        self.assertFalse(self.driver.segnala_scrittura(1, stato, 101.0))
        self.assertEqual(len(chiamate), 1)
        self.assertEqual(chiamate[0][0], "sendChatAction")
        self.assertEqual(chiamate[0][1]["action"], "typing")

    def test_dopo_l_intervallo_si_manda_di_nuovo(self):
        chiamate = self._finto_tg()
        stato = {}
        self.driver.segnala_scrittura(1, stato, 100.0)
        self.assertTrue(self.driver.segnala_scrittura(1, stato, 105.0))
        self.assertEqual(len(chiamate), 2)

    def test_un_errore_di_rete_non_impedisce_la_risposta(self):
        self._finto_tg(errore=True)
        self.assertFalse(self.driver.segnala_scrittura(1, {}, 100.0))


if __name__ == "__main__":
    unittest.main()

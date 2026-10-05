"""Confini delle route Instagram: i punti in cui il dominio parla con l'esterno,
e dove un errore non si vede finche' Meta non rimbalza la chiamata.

Sono test sul blueprint montato in un'app Flask minimale, con i tre store veri
di `shared/` puntati su un tmpdir. Meglio i veri che dei finto: il comportamento
che conta in queste route e' proprio la validazione e la persistenza, e una
classe finta la sostituirebbe proprio li' dove serve.

Perche' questi test e non uno "importa main ed esercita la route": `main.py`
all'import scrive l'identita' del nodo, apre il database e crea l'outbox. Un
test che lo importa modifica lo stato della macchina che lo esegue, e l'ordine
di esecuzione degli altri test dipenderebbe da quello.

La copertura del confine che conta di piu' — la firma HMAC e il dispatch del
payload — arriva con il webhook, in `test_instagram_webhook.py`.
"""
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from cp import http as cp_http
from cp import instagram as cp_instagram

ADMIN = "a" * 40
AMMINISTRA = {"X-Hyperspace-Network-Token": ADMIN}


class MontaggioInstagramMixin:
    """App col blueprint montato e i tre store su un tmpdir."""

    def monta(self, **extra):
        self.directory = tempfile.TemporaryDirectory()
        radice = Path(self.directory.name)
        self.app = Flask(__name__)
        cp_instagram.monta(
            self.app,
            vip_file=radice / "vips.json",
            memory_file=radice / "memoria.json",
            outbox_file=radice / "risposte.json",
            **extra,
        )
        self.app.config["TESTING"] = True
        return self.app.test_client()

    def spegni(self):
        self.directory.cleanup()


class TokenAmministratoreMixin(MontaggioInstagramMixin, unittest.TestCase):
    def setUp(self):
        self.aggiusta_token(ADMIN)
        self.client = self.monta()

    def aggiusta_token(self, valore):
        """`_network_admin_error` legge NETWORK_ADMIN_TOKEN al momento della
        chiamata, ma dalla costante di modulo: vanno rimosse entrambe le prove
        (il valore vecchio e l'eventuale override dato dall'ambiente)."""
        self._token_precedente = cp_http.NETWORK_ADMIN_TOKEN
        cp_http.NETWORK_ADMIN_TOKEN = valore
        self.addCleanup(lambda: setattr(cp_http, "NETWORK_ADMIN_TOKEN",
                                        self._token_precedente))

    def intestazione(self):
        return AMMINISTRA


class GuardAmministrazioneTests(TokenAmministratoreMixin):
    """Le route admin rispondono solo a chi porta il token, e quando il token non
    e' configurato dicono 'non e' attivo' invece di far finta di nulla."""

    def test_token_valido_il_riscontro_non_e_401(self):
        self.assertEqual(self.client.get("/instagram/vips", headers=AMMINISTRA).status_code, 200)

    def test_senza_token_il_riscontro_e_401(self):
        for percorso in ("/instagram/vips", "/instagram/webhook/events"):
            with self.subTest(percorso=percorso):
                risposta = self.client.get(percorso)
                self.assertEqual(risposta.status_code, 401)
                self.assertFalse(risposta.get_json()["ok"])

    def test_token_sbagliato_il_riscontro_e_401(self):
        risposta = self.client.get("/instagram/vips",
                                   headers={"X-Hyperspace-Network-Token": "b" * 40})
        self.assertEqual(risposta.status_code, 401)

    def test_token_non_configurato_il_riscontro_e_503(self):
        """Senza NETWORK_ADMIN_TOKEN le azioni di rete sono disabilitate, e il
        client deve poter distinguere 'non sei autorizzato' da 'non e' attivo'."""
        self.aggiusta_token("")
        risposta = self.client.get("/instagram/vips")
        self.assertEqual(risposta.status_code, 503)
        self.assertFalse(risposta.get_json()["configured"])


class VipRouteTests(TokenAmministratoreMixin):
    def test_elenco_all_inizio_e_vuoto(self):
        risposta = self.client.get("/instagram/vips", headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 200)
        corpo = risposta.get_json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["vips"], [])
        self.assertEqual(corpo["tracked"], 0)
        self.assertIn("memory", corpo)

    def test_scoped_id_malformato_e_400(self):
        """La validazione e' isdigit(), quindi '12a', '-3' e '1.5' si fermano
        prima di arrivare allo store: senza questo il file dei VIP si
        riempirebbe di chiavi non numeriche."""
        for valore in ("non-un-numero", "-3", "1.5", "", "   "):
            with self.subTest(scoped_id=valore):
                risposta = self.client.post("/instagram/vips/creator",
                                            json={"scoped_id": valore},
                                            headers=AMMINISTRA)
                self.assertEqual(risposta.status_code, 400)
                self.assertIn("scoped_id", risposta.get_json()["error"])

    def test_creatore_promuove_il_contatto(self):
        risposta = self.client.post("/instagram/vips/creator",
                                    json={"scoped_id": "55501", "username": "musa_di_prova"},
                                    headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 200)
        self.assertEqual(risposta.get_json()["vip"]["level"], "creatore")
        elenco = self.client.get("/instagram/vips", headers=AMMINISTRA).get_json()
        self.assertEqual(len(elenco["vips"]), 1)

    def test_username_mancante_va_pure_bene(self):
        """set_creator accetta anche lo scoped_id da solo: il nome si puo'
        scoprire dopo, e rifiutare qui bloccherebbe la promozione."""
        risposta = self.client.post("/instagram/vips/creator", json={"scoped_id": "55502"},
                                    headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 200)

    def test_scoped_id_viene_ripulito_dagli_spazi(self):
        risposta = self.client.post("/instagram/vips/creator", json={"scoped_id": " 55503 "},
                                    headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 200)


class MemoriaRouteTests(TokenAmministratoreMixin):
    def test_azzeramento_di_un_contatto_inesistente_e_404(self):
        risposta = self.client.post("/instagram/memory/clear", json={"scoped_id": "404404"},
                                    headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 404)
        self.assertIn("non trovato", risposta.get_json()["error"])

    def test_scoped_id_malformato_e_400(self):
        risposta = self.client.post("/instagram/memory/clear", json={"scoped_id": "abc"},
                                    headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 400)

    def test_corpo_vuoto_e_400_e_non_500(self):
        """Senza corpo JSON `data` diventa {}, lo scoped_id manca, e deve essere
        un 400 pulito e non un'eccezione."""
        risposta = self.client.post("/instagram/memory/clear", json={}, headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 400)

    def test_scoped_id_viene_ripulito_dagli_spazi(self):
        """Lo store cerca '404404' e lo trova vuoto: deve dire 404, non 400.
        Se il ripulimento cambiasse, la validazione isdigit() leggerebbe ' 404404 '
        come non numerico e risponderebbe 400."""
        risposta = self.client.post("/instagram/memory/clear", json={"scoped_id": " 404404 "},
                                    headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 404)


class OutboxStatusTests(MontaggioInstagramMixin, unittest.TestCase):
    """Lo stato delle risposte e' l'unica route di lettura aperta: non espone il
    corpo dei messaggi, quindi non chiede il token. Conviene che resti cosi' e che
    il test lo fissi, perche' e' una scelta di privacy e non un dimenticanza."""

    def setUp(self):
        self.client = self.monta()

    def test_stato_iniziale_ha_la_forma_attesa(self):
        risposta = self.client.get("/instagram/replies/status")
        self.assertEqual(risposta.status_code, 200)
        corpo = risposta.get_json()
        self.assertTrue(corpo["ok"])
        for chiave in ("queued", "sending", "sent", "retry", "generating", "needs_review"):
            self.assertIn(chiave, corpo["counts"])
        self.assertEqual(corpo["items"], [])

    def test_nessun_corpo_di_messaggio_nello_stato(self):
        corpo = self.client.get("/instagram/replies/status").get_json()
        for voce in corpo["items"]:
            for chiave in ("text", "body", "message"):
                self.assertNotIn(chiave, voce)
            if voce.get("contact"):
                self.assertTrue(voce["contact"].startswith("DM-"))


class MediaPrivatoTests(MontaggioInstagramMixin, unittest.TestCase):
    """Il token dei media privati viaggia nel path, non in un header."""

    def setUp(self):
        self.client = self.monta()

    def test_token_non_configurato_e_403(self):
        """`expected` vuoto significa media privata disattivata: senza token
        configurato la route non deve servire nulla."""
        self.assertEqual(self.client.get("/instagram/media/qualcosa/una.png").status_code, 403)

    def test_token_sbagliato_e_403(self):
        risposta = self.client.get("/instagram/media/token-sbagliato/una.png")
        self.assertEqual(risposta.status_code, 403)

    def test_percorso_published_non_scappa_dalla_cartella(self):
        """`send_from_directory(os.path.basename(nome))` e' la difesa contro il
        path traversal. Serve un token configurato per arrivare al punto in cui
        quella difesa viene esercitata."""
        with self.patch_media_token("t" * 20):
            risposta = self.client.get(
                "/instagram/media/ttttttttttttttttttttt/published/../../etc/passwd")
            self.assertNotEqual(risposta.status_code, 200)
            self.assertNotIn(b"root:", risposta.data)

    def patch_media_token(self, valore):
        import os
        from unittest import mock
        return mock.patch.dict(os.environ, {"INSTAGRAM_MEDIA_TOKEN": valore})


class WebhookEventiTests(TokenAmministratoreMixin):
    def test_lista_eventi_iniziale_e_vuota(self):
        risposta = self.client.get("/instagram/webhook/events", headers=AMMINISTRA)
        self.assertEqual(risposta.status_code, 200)
        self.assertEqual(risposta.get_json()["events"], [])


class StatoNonCongelatoTests(unittest.TestCase):
    """I tre store sono creati dentro `monta()`, non all'import.

    Un `from cp.instagram import instagram_vips` congela il valore al momento
    dell'import, che e' `None`: il risultato e' un 500 sui messaggi veri, con un
    `AttributeError: 'NoneType' object has no attribute 'record'` dentro
    `_dispatch_instagram_messages`. E' successo, ed e' il tipo di difetto che i
    test sui confini non vedono — perche' il confine risponde 500 e nessuno
    guarda il corpo della risposta. Qui si verifica direttamente la regola.
    """

    def test_un_riferimento_preso_prima_di_monta_diventa_vecchio(self):
        """Il meccanismo del difetto, in forma deterministica.

        `from cp.instagram import instagram_vips` produce un riferimento a un
        valore che `monta()` non aggiorna: dopo il montaggio quel nome vale
        ancora la store precedente, e se quel valore era None a import time
        l'errore in `_dispatch_instagram_messages` è un 500 sui messaggi veri.
        Qui si prende il riferimento prima del montaggio e si verifica che non
        segua quello nuovo — che è il motivo per cui main.py riceve gli store
        dalla funzione invece di importarli per nome.
        """
        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            prima = cp_instagram.instagram_vips
            app = Flask(__name__)
            dopo, _memoria, _outbox = cp_instagram.monta(
                app, vip_file=radice / "v.json",
                memory_file=radice / "m.json", outbox_file=radice / "o.json")
            self.assertIsNot(prima, dopo,
                             "monta() ha creato uno store nuovo: un riferimento "
                             "preso prima non lo puo' vedere")
            self.assertIs(dopo, cp_instagram.instagram_vips)

    def test_gli_store_escono_da_monta(self):
        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            app = Flask(__name__)
            vips, memoria, outbox = cp_instagram.monta(
                app,
                vip_file=radice / "v.json",
                memory_file=radice / "m.json",
                outbox_file=radice / "o.json",
            )
            self.assertIsNotNone(vips)
            self.assertIsNotNone(memoria)
            self.assertIsNotNone(outbox)
            self.assertIs(vips, cp_instagram.instagram_vips)
            self.assertIs(outbox, cp_instagram.instagram_reply_outbox)

    def test_le_route_vedono_lo_stato_di_questo_montaggio(self):
        """Due montaggi con tmpdir diversi non devono condividere lo stato: e'
        cio' che permette ai test di essere indipendenti tra loro."""
        with tempfile.TemporaryDirectory() as che_a, \
                tempfile.TemporaryDirectory() as che_b:
            def montaggio(radice):
                app = Flask(__name__)
                cp_instagram.monta(app, vip_file=Path(radice) / "v.json",
                                   memory_file=Path(radice) / "m.json",
                                   outbox_file=Path(radice) / "o.json")
                app.config["TESTING"] = True
                return app.test_client()

            montaggio(che_a).post("/instagram/vips/creator", json={"scoped_id": "111"},
                                  headers=AMMINISTRA)
            self.assertEqual(
                len(montaggio(che_b).get("/instagram/vips", headers=AMMINISTRA)
                    .get_json()["vips"]),
                0, "lo stato del montaggio precedente e' rimasto")


if __name__ == "__main__":
    unittest.main()

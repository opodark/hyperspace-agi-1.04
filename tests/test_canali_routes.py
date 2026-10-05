# SPDX-License-Identifier: Apache-2.0
"""I confini delle otto route dei canali che vivono in cp/canali.py.

Queste rotte hanno due interlocutori con due contratti diversi, e qui si
fissano entrambi.

Il **driver** le chiama a ogni giro — `channel_ingest` a ogni secondo, per ogni
messaggio — e conta sui codici di risposta. `401` e `503` non sono
intercambiabili: `401` vuol dire "non sei autorizzato", `503` "non c'è nessun
canale configurato", e un driver che li confonde smette di parlare con la
persona invece di segnalare un problema di configurazione.

L'**operatore** le guarda per rispondere a "perché il bot non ha risposto", e lì
la cosa che conta è che **non escano segreti**: i nomi dei canali sì, i token
mai. `/channels` e `/channel/status` sono le due rotte che l'operatore ha a
schermo e nessuna delle due può filtrare un `clients` per intero.

C'è anche una terza cosa, meno ovvia: `/channel/outbox` e `/channel/commands`
rispondono `200` con una lista vuota quando non c'è niente, non `204` e non
`404`. Il driver deve poter distinguere "nessuna immagine da consegnare" da
"rotta rotta", e un 404 lo farebbe smettere di chiedere.

La baseline in `tests/canali_baseline.py` fotografa le stesse risposte dal server
vero, che è l'unico modo per un confronto prima/dopo: `main.py` non è importabile
in un test senza effetti collaterali.
"""
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from cp import canali as cp_canali  # noqa: E402
from shared.channel import ChannelGuard, ChannelPolicy, ChannelRuntime  # noqa: E402

TOKEN = "t" * 40
CANALE = {"X-Hyperspace-Channel-Token": TOKEN}


class _Queue:
    """La coda vera non serve: serve sapere cosa le viene chiesto.

    `_per_canale` e non `da_consegnare` perche' quello e' anche il nome del metodo
    che la coda espone, e un attributo che lo copre farebbe fallire la chiamata
    con un `TypeError: 'dict' object is not callable` — che sembrerebbe un difetto
    del dominio e invece e' una mia variabile male chiamata.
    """

    def __init__(self):
        self._per_canale = {"prova": []}
        self.consegnati = []

    def da_consegnare(self, canale):
        return list(self._per_canale.get(canale, []))

    def consegnato(self, job_id):
        self.consegnati.append(job_id)
        return False


class MontaggioCanaliMixin:
    def monta(self, *, policy=None, queue=None):
        self.app = Flask(__name__)
        self.queue = queue if queue is not None else _Queue()
        # Il ricordo dei messaggi: qui conta solo che arrivi la chiamata.
        self.ricordi = []
        cp_canali.channel_policy = policy or ChannelPolicy(
            {"prova": TOKEN})
        cp_canali.channel_guard = ChannelGuard()
        cp_canali.channel_runtime = ChannelRuntime()
        self.precedenti = (cp_canali.channel_policy, cp_canali.channel_guard,
                           cp_canali.channel_runtime)
        cp_canali.monta(
            self.app,
            image_queue=self.queue,
            context_messages=lambda: {"turns": [], "summary": ""},
            context_chars=lambda: 0,
            num_ctx=lambda: 8192,
            channel_remember=lambda *a, **k: self.ricordi.append((a, k)),
            node_list=lambda: [{"id": "nodo-1", "url": "http://nodo-1"}],
        )
        self.app.config["TESTING"] = True
        self.addCleanup(self.dimentica)
        return self.app.test_client()

    def dimentica(self):
        (cp_canali.channel_policy, cp_canali.channel_guard,
         cp_canali.channel_runtime) = self.precedenti
        cp_canali.smonta()


class GuardTests(MontaggioCanaliMixin, unittest.TestCase):
    """Le tre risposte del guard, che un driver o un operatore devono poter
    distinguere. Non sono una sfumatura della stessa cosa."""

    # Sei, non otto: `/channels` e `/channel/status` sono APERTE per scelta — sono
    # le due che l'operatore guarda e non contengono segreti (lo verifica
    # SegretoTests). Metterle qui sarebbe stato inventare una protezione che non
    # c'è, e il test sarebbe passato comunque contro il codice vero.
    PROTETTE = [("/channel/outbox", "GET"), ("/channel/commands", "GET"),
                ("/channel/ingest", "POST"), ("/channel/result", "POST"),
                ("/channel/outbox/ack", "POST"), ("/channel/state", "POST")]
    APERTE = ["/channels", "/channel/status"]

    def test_ogni_route_richiede_il_token(self):
        self.monta()
        for percorso, metodo in self.PROTETTE:
            with self.subTest(percorso=percorso):
                if metodo == "GET":
                    risposta = self.app.test_client().get(percorso)
                else:
                    risposta = self.app.test_client().post(percorso, json={})
                self.assertEqual(risposta.status_code, 401,
                                 f"{percorso} ha risposto senza chiedere il token")

    def test_token_sbagliato_e_401(self):
        self.monta()
        risposta = self.app.test_client().get(
            "/channel/outbox", headers={"X-Hyperspace-Channel-Token": "z" * 40})
        self.assertEqual(risposta.status_code, 401)

    def test_le_due_route_operatore_restano_aperte(self):
        """`/channels` e `/channel/status` non chiedono il token, e devono
        continuare a non chiederlo: sono la vista dell'operatore, e il motivo per
        cui sono aperte e' che non mostrano segreti. Se un giorno diventassero
        guardate, il test qui si romperebbe e la domanda da farsi sarebbe "ci
        abbiamo messo dentro qualcosa che non si deve vedere?"."""
        self.monta()
        for percorso in self.APERTE:
            with self.subTest(percorso=percorso):
                self.assertEqual(self.app.test_client().get(percorso).status_code, 200)

    def test_canali_disattivati_e_503(self):
        self.monta(policy=ChannelPolicy({"prova": TOKEN}, enabled=False))
        risposta = self.app.test_client().get("/channel/outbox", headers=CANALE)
        self.assertEqual(risposta.status_code, 503)
        self.assertIn("CHANNEL_ENABLED", risposta.get_json()["error"])

    def test_nessun_canale_configurato_e_503_e_non_401(self):
        """503 e non 401: 'non c'è nessuno autorizzato' e 'non sei autorizzato'
        sono fatti diversi, e un'installazione senza CHANNEL_CLIENTS deve
        poterlo vedere senza che sembri un tentativo di accesso."""
        self.monta(policy=ChannelPolicy({}))
        risposta = self.app.test_client().get("/channel/outbox", headers=CANALE)
        self.assertEqual(risposta.status_code, 503)
        self.assertIn("CHANNEL_CLIENTS", risposta.get_json()["error"])


class NienteDaConsegnareTests(MontaggioCanaliMixin, unittest.TestCase):
    """Le risposte "non c'è niente": 200 con lista vuota, non 404 e non 204."""

    def setUp(self):
        self.client = self.monta()

    def test_outbox_vuota_e_200_con_lista_vuota(self):
        risposta = self.client.get("/channel/outbox", headers=CANALE)
        self.assertEqual(risposta.status_code, 200)
        corpo = risposta.get_json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["messages"], [])

    def test_commands_vuota_e_200_con_lista_vuota(self):
        risposta = self.client.get("/channel/commands", headers=CANALE)
        self.assertEqual(risposta.status_code, 200)
        corpo = risposta.get_json()
        self.assertEqual(corpo["commands"], [])
        self.assertIn("auto", corpo["available"])

    def test_outbox_ripete_il_canale_chiamante(self):
        """Il driver accoda su più canali: la risposta dice da quale canale
        vengono le immagini, altrimenti non sa a chi consegnarle."""
        corpo = self.client.get("/channel/outbox", headers=CANALE).get_json()
        self.assertEqual(corpo["channel"], "prova")

    def test_ack_di_un_id_inesistente_e_404(self):
        """Qui 404 è giusto: qui il driver ha dichiarato qualcosa che non esiste,
        e vuole saperlo per non riprovare all'infinito."""
        risposta = self.client.post("/channel/outbox/ack", json={"id": "non-esiste"},
                                    headers=CANALE)
        self.assertEqual(risposta.status_code, 404)


class SegretoTests(MontaggioCanaliMixin, unittest.TestCase):
    """Le due rotte che l'operatore ha a schermo non devono mostrare i token."""

    def setUp(self):
        self.client = self.monta()

    def _stringhe(self, risposta):
        grezzo = risposta.get_data(as_text=True)
        return [s for s in grezzo.split('"') if len(s) >= 8 and "token" in s.lower()]

    def test_channels_non_esponde_il_token(self):
        risposta = self.client.get("/channels", headers=CANALE)
        corpo = risposta.get_json()
        for voce in corpo["channels"]:
            self.assertNotIn("token", voce,
                             "la scheda Social mostra 'configurato sì/no', non il token")
            self.assertIn("configured", voce)
        self.assertNotIn(TOKEN, risposta.get_data(as_text=True))

    def test_channel_status_non_esponde_il_token(self):
        risposta = self.client.get("/channel/status", headers=CANALE)
        self.assertNotIn(TOKEN, risposta.get_data(as_text=True))

    def test_il_nome_del_canale_esce_invece_del_token(self):
        """Il nome serve all'operatore per capire quale canale tace: quello deve
        esserci. E' la differenza tra 'non ci sono segreti' e 'non c'è niente'."""
        corpo = self.client.get("/channels", headers=CANALE).get_json()
        chiavi = [v["key"] for v in corpo["channels"]]
        self.assertTrue(chiavi, "l'elenco delle piattaforme note deve esserci")


class IngestTests(MontaggioCanaliMixin, unittest.TestCase):
    def setUp(self):
        self.client = self.monta()

    def test_events_mancanti_e_400(self):
        """400 e non 500: un driver che sbaglia il payload deve poter fermarsi da
        solo, e il corpo deve dirgli cosa manca."""
        for payload in ({}, {"events": []}, {"events": "non-una-lista"},
                        {"events": 42}):
            with self.subTest(payload=payload):
                risposta = self.client.post("/channel/ingest", json=payload,
                                            headers=CANALE)
                self.assertEqual(risposta.status_code, 400)
                self.assertIn("events", risposta.get_json()["error"])

    def test_un_tip_va_in_memoria(self):
        """Il cablaggio della memoria per i tip: era `_channel_remember(...)` e ora
        arriva dal contesto del dominio. Il test guarda l'attributo, quindi vale
        per come il cablaggio è scritto e non per come era scritto."""
        risposta = self.client.post("/channel/ingest", json={"events": [
            {"kind": "tip", "author": "qualcuno", "amount": 500, "key": "k1"}]},
            headers=CANALE)
        self.assertEqual(risposta.status_code, 200)
        self.assertTrue(self.ricordi, "il tip non è arrivato in memoria")
        self.assertIn("tip", self.ricordi[0][0][1])

    def test_la_moderazione_riuscita_finisce_in_memoria(self):
        risposta = self.client.post("/channel/result",
                                    json={"kind": "moderate", "ok": True,
                                          "target": "utente-1"}, headers=CANALE)
        self.assertEqual(risposta.status_code, 200)
        self.assertTrue(self.ricordi)

    def test_un_evento_troppo_lungo_viene_ritagliato(self):
        """L'autore e il testo hanno un tetto: senza, un batch enorme entra tutto
        in memoria e nei log."""
        risposta = self.client.post("/channel/ingest", json={"events": [
            {"kind": "message", "author": "a" * 400, "text": "ciao"}]},
            headers=CANALE)
        self.assertEqual(risposta.status_code, 200)
        self.assertLessEqual(len(risposta.get_json()["results"][0]["author"]), 64)


class ConfigurazioneRicaricataTests(unittest.TestCase):
    """Le tre liste e il modello sono posseduti da cp/canali.py.

    Prima stavano in main.py e venivano riassegnati con `global`. Se il modulo
    che li possiede non li ricarica, chi legge da lì legge il valore vecchio: è il
    "salvato ma inerte" che la tab Setup esiste per evitare, trovato il
    2026-09-22 proprio cambiando CHANNEL_MODEL.
    """

    def setUp(self):
        self.precedente = cp_canali.CHANNEL_MODEL
        self.addCleanup(lambda: cp_canali.imposta_modello(self.precedente))

    def test_imposta_modello_sostituisce_il_valore(self):
        cp_canali.imposta_modello("  llama-nuovo  ")
        self.assertEqual(cp_canali.CHANNEL_MODEL, "llama-nuovo",
                         "il valore deve essere ripulito: uno spazio residuo "
                         "diventa un nome modello che non esiste")

    def test_imposta_modello_accetta_il_vuoto(self):
        """Vuoto = modello di default del control-plane: è un modo valido per
        togliere l'override, non un errore."""
        cp_canali.imposta_modello("")
        self.assertEqual(cp_canali.CHANNEL_MODEL, "")

    def test_ricarica_ritorna_sei_valori_e_rilegge_il_modello(self):
        with_temp = {**os.environ, "CHANNEL_MODEL": "da-env"}
        with mock.patch.dict(os.environ, with_temp, clear=True):
            valori = cp_canali.ricarica()
        self.assertEqual(len(valori), 6)
        self.assertEqual(cp_canali.CHANNEL_MODEL, "da-env")

    def test_le_tre_liste_sono_insiemi_di_nomi_minuscoli(self):
        with_temp = {**os.environ, "CHANNEL_OPERATOR": "Anna, marY ",
                     "CHANNEL_VIP": "Bob", "CHANNEL_CERCHIA": ""}
        with mock.patch.dict(os.environ, with_temp, clear=True):
            cp_canali.ricarica()
        self.assertEqual(cp_canali.CHANNEL_OPERATOR, {"anna", "mary"})
        self.assertEqual(cp_canali.CHANNEL_VIP, {"bob"})
        self.assertEqual(cp_canali.CHANNEL_CERCHIA, set())


class NonMontatoTests(unittest.TestCase):
    def test_una_route_senza_monta_da_un_errore_chiaro(self):
        app = Flask(__name__)
        with app.test_request_context("/channel/outbox"):
            with self.assertRaises(RuntimeError) as presa:
                cp_canali._serve("image_queue")
        self.assertIn("montato", str(presa.exception))


if __name__ == "__main__":
    unittest.main()

class DocstringDelleRouteTests(unittest.TestCase):
    """Ogni route di `cp/canali.py` deve spiegare cosa risponde e perché.

    Non è pignoleria di stile: in `main.py` 72 route su 110 non avevano un
    docstring, e la prima cosa che fa un'estrazione è coprirle di commenti. Se
    il docstring finisce *dopo* la prima istruzione non è più un docstring ma una
    stringa buttata via: `help()` mostra `None`, e il pezzo che spiega perché il
    driver conta sui codici sparisce dalla documentazione. È successo a quattro
    rotte durante questo refactor, e nessun test se n'era accorto.
    """
    ROUTI = ("channels_overview", "channel_status", "channel_commands",
             "channel_state", "channel_ingest", "channel_result",
             "channel_outbox", "channel_outbox_ack")

    def test_ogni_route_ha_il_docstring(self):
        import ast
        import inspect
        from tests import cp_source
        funzioni = cp_source.funzioni()
        for nome in self.ROUTI:
            with self.subTest(route=nome):
                nodo = funzioni[nome]
                # il docstring in un AST e' la prima istruzione se e' una stringa
                primo = nodo.body[0]
                e_stringa = (isinstance(primo, ast.Expr)
                             and isinstance(primo.value, ast.Constant)
                             and isinstance(primo.value.value, str))
                self.assertTrue(e_stringa,
                                f"{nome} non ha il docstring come prima "
                                "istruzione: help() direbbe None")
                testo = inspect.getdoc(getattr(cp_canali, nome, None))
                self.assertTrue(testo and len(testo.strip()) > 20,
                                f"{nome} ha un docstring troppo corto per "
                                "spiegare il contratto")

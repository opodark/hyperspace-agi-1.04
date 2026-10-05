# SPDX-License-Identifier: Apache-2.0
"""I confini della coda immagini: il contratto con il ponte ComfyUI.

Queste sei route hanno un interlocutore che non è il browser e non è un altro
test: è il ponte che gira sull'altra macchina, chiede il prossimo job a
`/image/jobs` e riporta l'esito a `/image/result`. Non chiama una funzione Python
del nostro processo, e se sbagliaamo una forma di risposta nessun test di unità lo
vede: il ponte risponde che non ha capito e smette di ritirare job.

Sono quindi tre le cose che qui si fissano, e sono tutte cose che una
riscrittura può rompere senza rompere nient'altro:

- **il 204 a coda vuota** è la risposta normale di un ponte in attesa. Diventare
  un 500 significa che il ponte considera il control-plane rotto e si ferma;
  diventare un 200 con `{"ok": true}` e niente job significa che il ponte entra in
  un ciclo di richieste a vuoto.
- **il 401 senza token** e il 503 a canali non configurati sono due risposte
  diverse per due fatti diversi, e i client li contano.
- **`_percorso_disegno_servibile` rifiuta i percorsi che escono dal volume.** Non
  per principio ma per conseguenza: quell'URL finisce anche su Instagram, e un
  percorso che esce dal volume delle immagini diventerebbe un link pubblico a
  qualcosa che non è un'immagine.

La baseline in `tests/immagini_baseline.py` fotografa le stesse risposte dal
server vero: serve perché `main.py` non è importabile in un test senza effetti
collaterali, e quindi è l'unico modo di avere un confronto prima/dopo.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from cp import canali as cp_canali  # noqa: E402
from cp import immagini as cp_immagini  # noqa: E402
from shared.image_jobs import ImmagineQueue  # noqa: E402

TOKEN = "t" * 40
CANALE = {"X-Hyperspace-Channel-Token": TOKEN}


class _Gate:
    """Il gate vero non serve: qui conta sapere cosa gli viene chiesto."""

    def __init__(self):
        self.rilasci = []
        self.continue_queue = False

    def release_image(self, job_id):
        self.rilasci.append(job_id)

    def reserve_image(self, job_id, timeout=120):
        return True

    def continue_image_queue(self):
        return self.continue_queue

    def status(self):
        """`/image/status` chiede al gate il suo stato, e il finto deve saperlo
        rispondere: senza, la rotta cade su un AttributeError e il test
        misurerebbe il finto invece della coda."""
        return {"active_chats": 0, "image_job": "", "lease_remaining_s": 0}


class _Connettori:
    def __init__(self):
        self.chiamate = []

    def execute(self, tool, args):
        self.chiamate.append((tool, args))
        return {"ok": True}


class MontaggioCodaMixin:
    def monta(self, *, queue=None, diario=None, connettori=None, gate=None):
        self.directory = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.queue = queue if queue is not None else ImmagineQueue()
        self.gate = gate if gate is not None else _Gate()
        self.connettori = connettori if connettori is not None else _Connettori()
        self.diario = diario if diario is not None else SimpleNamespace(
            get=lambda *a: None, save=lambda *a: None, aggiorna_file=lambda *a: False,
            aggiorna_instagram=lambda *a: None)
        cp_immagini.monta(self.app, image_queue=self.queue, image_memory_gate=self.gate,
                          connector_manager=self.connettori, diario=self.diario)
        self.app.config["TESTING"] = True
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(cp_immagini.smonta)
        return self.app.test_client()

    def autorizza(self, policy=None):
        """Un canale che accetta il token di test.

        `cp.canali.channel_policy` viene letto al momento della richiesta, ma è una
        costante di modulo: vanno rimosse entrambe le prove.
        """
        policy = policy or SimpleNamespace(
            enabled=True, configured=True,
            clients={"prova": TOKEN},
            authenticate=lambda t: "prova" if t == TOKEN else None)
        self.precedente = cp_canali.channel_policy
        cp_canali.channel_policy = policy
        self.addCleanup(lambda: setattr(cp_canali, "channel_policy", self.precedente))
        return policy


class CodaVuotaTests(MontaggioCodaMixin, unittest.TestCase):
    """Il ponte in attesa è il caso normale, non un caso d'errore."""

    def setUp(self):
        self.client = self.monta()
        self.autorizza()

    def test_il_prossimo_job_a_coda_vuota_e_204(self):
        risposta = self.client.get("/image/jobs", headers=CANALE)
        self.assertEqual(risposta.status_code, 204)
        self.assertEqual(risposta.data, b"")

    def test_204_non_deve_avere_un_corpo_json(self):
        """Un corpo su un 204 confonde i client che lo leggono senza guardare il
        codice: è il modo più semplice di far fallire il ponte senza errori.

        Flask non solleva su un corpo vuoto, restituisce None — quindi qui si
        verifica la forma (`data` vuoto, nessun JSON), non come reagisce
        `get_json` a un corpo non valido.
        """
        risposta = self.client.get("/image/jobs", headers=CANALE)
        self.assertEqual(risposta.status_code, 204)
        self.assertEqual(risposta.data, b"")
        self.assertIsNone(risposta.get_json())

    def test_filtro_per_famiglia_su_coda_vuota_e_ancora_204(self):
        risposta = self.client.get("/image/jobs?famiglia=sdxl-turbo", headers=CANALE)
        self.assertEqual(risposta.status_code, 204)


class AutorizzazioneTests(MontaggioCodaMixin, unittest.TestCase):
    def test_senza_token_e_401(self):
        self.client = self.monta()
        self.autorizza()
        risposta = self.client.get("/image/jobs")
        self.assertEqual(risposta.status_code, 401)

    def test_token_sbagliato_e_401(self):
        self.client = self.monta()
        self.autorizza()
        risposta = self.client.get("/image/jobs",
                                   headers={"X-Hyperspace-Channel-Token": "z" * 40})
        self.assertEqual(risposta.status_code, 401)

    def test_canali_disattivati_e_503(self):
        self.client = self.monta()
        self.autorizza(SimpleNamespace(enabled=False, configured=True,
                                       authenticate=lambda t: "prova"))
        self.assertEqual(self.client.get("/image/jobs", headers=CANALE).status_code, 503)

    def test_nessun_canale_configurato_e_503(self):
        """503 e non 401: 'non sono autorizzato' e 'non c'è nessuno autorizzato'
        sono fatti diversi, e un installazione senza CHANNEL_CLIENTS deve poterlo
        vedere senza che sembri un attacco."""
        self.client = self.monta()
        self.autorizza(SimpleNamespace(enabled=True, configured=False,
                                       authenticate=lambda t: None))
        risposta = self.client.get("/image/jobs", headers=CANALE)
        self.assertEqual(risposta.status_code, 503)
        self.assertIn("CHANNEL_CLIENTS", risposta.get_json()["error"])


class CicloDelPonteTests(MontaggioCodaMixin, unittest.TestCase):
    """Il giro completo: accodare, ritirare, chiudere."""

    def setUp(self):
        self.client = self.monta()
        self.autorizza()

    def test_accodare_ritirare_e_chiudere(self):
        accodato = self.client.post("/image/generate",
                                    json={"prompt": "un faro di notte", "canale": "prova"},
                                    headers=CANALE)
        self.assertEqual(accodato.status_code, 201)
        job_id = accodato.get_json()["job"]["id"]
        self.assertTrue(job_id)

        preso = self.client.get("/image/jobs", headers=CANALE)
        self.assertEqual(preso.status_code, 200)
        self.assertEqual(preso.get_json()["job"]["id"], job_id)

        esito = self.client.post("/image/result",
                                json={"id": job_id, "ok": True,
                                      "file": "bridge_00001_.jpg"}, headers=CANALE)
        self.assertEqual(esito.status_code, 200)
        self.assertTrue(esito.get_json()["job"]["esito"]["file"].endswith(".jpg"))

    def test_un_job_inesistente_e_404(self):
        risposta = self.client.get("/image/job/non-esiste", headers=CANALE)
        self.assertEqual(risposta.status_code, 404)

    def test_esito_di_un_job_inesistente_e_404(self):
        risposta = self.client.post("/image/result",
                                    json={"id": "non-esiste", "ok": True,
                                          "file": "x.jpg"}, headers=CANALE)
        self.assertEqual(risposta.status_code, 404)

    def test_prompt_vuoto_e_400_con_motivo(self):
        """Un job senza prompt non è un errore da log silenzioso: il 400 dice
        perché, e senza quel motivo il ponte riproverebbe lo stesso payload."""
        risposta = self.client.post("/image/generate", json={}, headers=CANALE)
        self.assertEqual(risposta.status_code, 400)
        self.assertIn("prompt", risposta.get_json()["error"])

    def test_defer_rilascia_il_gate_e_rimette_in_coda(self):
        accodato = self.client.post("/image/generate",
                                    json={"prompt": "prova", "canale": "prova"},
                                    headers=CANALE)
        job_id = accodato.get_json()["job"]["id"]
        risposta = self.client.post("/image/defer", json={"id": job_id},
                                    headers=CANALE)
        self.assertEqual(risposta.status_code, 200)
        self.assertEqual(self.gate.rilasci, [job_id])

    def test_lo_stato_ha_la_forma_attesa(self):
        corpo = self.client.get("/image/status", headers=CANALE).get_json()
        for chiave in ("in_coda", "in_esecuzione", "da_consegnare", "max_jobs",
                       "per_stato", "memory_gate"):
            self.assertIn(chiave, corpo)


class PercorsoServibileTests(MontaggioCodaMixin, unittest.TestCase):
    """`_percorso_disegno_servibile` decide se un file diventa un URL pubblico.

    L'URL che ne esce finisce anche su Instagram, quindi un percorso che esce dal
    volume delle immagini non deve diventare pubblicabile: non per principio, ma
    perché quel link diventerebbe un modo per pubblicare qualcos'altro.
    """

    def setUp(self):
        self.client = self.monta()
        self.autorizza()
        self.volumi = Path(self.directory.name) / "immagini"
        self.volumi.mkdir()
        (self.volumi / "buona.jpg").write_bytes(b"non-una-immagine")
        (self.volumi / "HyperSpace").mkdir()
        (self.volumi / "HyperSpace" / "bridge.jpg").write_bytes(b"x")
        self.vecchio = cp_immagini.DIARIO_IMMAGINI_DIR
        cp_immagini.DIARIO_IMMAGINI_DIR = str(self.volumi)
        self.addCleanup(lambda: setattr(cp_immagini, "DIARIO_IMMAGINI_DIR", self.vecchio))

    def test_un_file_dentro_il_volume_e_servibile(self):
        self.assertEqual(cp_immagini._percorso_disegno_servibile("buona.jpg"), "buona.jpg")

    def test_un_file_in_una_sottocartella_e_servibile(self):
        self.assertEqual(cp_immagini._percorso_disegno_servibile("HyperSpace/bridge.jpg"),
                         "HyperSpace/bridge.jpg")

    def test_una_sbarra_iniziale_non_cambia_niente(self):
        self.assertEqual(cp_immagini._percorso_disegno_servibile("/buona.jpg"), "buona.jpg")

    def test_un_percorso_che_esce_dal_volume_non_e_servibile(self):
        for percorso in ("../fuori.jpg", "../../etc/passwd", "HyperSpace/../../fuori.jpg"):
            with self.subTest(percorso=percorso):
                self.assertEqual(cp_immagini._percorso_disegno_servibile(percorso), "")

    def test_un_file_inesistente_non_e_servibile(self):
        self.assertEqual(cp_immagini._percorso_disegno_servibile("non-esiste.jpg"), "")

    def test_il_percorso_vuoto_non_e_servibile(self):
        for percorso in ("", "   ", None, "/"):
            with self.subTest(percorso=percorso):
                self.assertEqual(cp_immagini._percorso_disegno_servibile(percorso), "")

    def test_un_simbolo_nel_volume_non_e_servibile_come_file(self):
        """Una sottocartella passata come file: `isfile` lo distingue, e senza quel
        controllo l'URL diventerebbe una directory listata."""
        self.assertEqual(cp_immagini._percorso_disegno_servibile("HyperSpace"), "")


class NonMontatoTests(unittest.TestCase):
    """Usare il dominio senza aver montato deve dirlo, non restituire None."""

    def test_la_rotta_chiede_il_contesto_e_senza_monta_da_un_errore_chiaro(self):
        app = Flask(__name__)
        with app.test_request_context("/image/jobs"):
            with self.assertRaises(RuntimeError) as presa:
                cp_immagini._serve("image_queue", "image_memory_gate")
        self.assertIn("montato", str(presa.exception))


if __name__ == "__main__":
    unittest.main()
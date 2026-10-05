# SPDX-License-Identifier: Apache-2.0
"""Il collegamento di `cp/inferenza.py`, e le tre funzioni che lo rendono vivo.

`cp/inferenza.py` riceve cinque cose dal boot e non le crea da solo: la chiave con
cui firma verso i nodi, il gate che conta le chiamate locali, l'id e la chiave
pubblica del nodo, e il controllo di persona sulla risposta.

Sono tutte stabili dopo l'avvio — nessuna viene riassegnata — quindi iniettarle
una volta sola è sicuro. Il pericolo è il contrario di quello della memoria: lì
`memory_sync` cambia e un binding dimenticato diventava "salvato ma inerte"; qui
niente cambia, e il pericolo è che il collegamento non venga fatto affatto, o che
venga fatto con il valore di un'altra istanza.

Non si importa `main.py`: all'import scrive su `/app`, che in test non esiste, ed è
il motivo per cui quasi tutti i test di questo repository leggono il sorgente con
`cp_source` invece di eseguire il monolite.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from cp import inferenza as cp_inferenza  # noqa: E402
from tests import cp_source  # noqa: E402


class _Gate:
    """Il gate vero non serve: qui conta sapere che viene chiamato, e quando."""

    def __init__(self, entra=True):
        self.entra = entra
        calls = []
        self.chiamate = calls

    def enter_chat(self, timeout=120):
        self.chiamate.append("enter")
        return self.entra

    def leave_chat(self):
        self.chiamate.append("leave")


class CollegamentoTests(unittest.TestCase):
    def setUp(self):
        cp_inferenza.collega("chiave-di-prova", _Gate(), "node-1", "pubkey-1",
                             lambda risposta: None)
        self.addCleanup(cp_inferenza.collega, None, None, None, None, None)

    def test_le_cinque_dependenze_sono_incollate(self):
        self.assertEqual(cp_inferenza._chiave_privata, "chiave-di-prova")
        self.assertIsNotNone(cp_inferenza._gate_memoria)
        self.assertEqual(cp_inferenza._node_id, "node-1")
        self.assertEqual(cp_inferenza._pubkey, "pubkey-1")
        self.assertTrue(callable(cp_inferenza._audit))

    def test_collega_accetta_il_richiamo_per_il_pulizia(self):
        """`collega(None, None, ...)` è il modo di staccare: senza, il teardown di
        un test lascerebbe il modulo collegato a uno di un altro, e il test
        successivo passerebbe o fallirebbe a seconda dell'ordine."""
        cp_inferenza.collega(None, None, None, None, None)
        with self.assertRaises(RuntimeError):
            cp_inferenza._serve()


class PostLocaleTests(unittest.TestCase):
    """`_local_model_post` è il post che tiene conto delle chiamate di fondo.

    I loop in background — sogni, post, sketch — chiamano l'inferenza senza che un
    cliente aspetti una risposta. Senza il gate, saturerebbero la GPU mentre una
    chat è in corso, e il sintomo sarebbe "il bot è lento", non "il loop è
    disattivato".
    """

    def setUp(self):
        self.gate = _Gate(entra=True)
        cp_inferenza.collega("chiave", self.gate, "node", "pub", lambda r: None)
        self.addCleanup(cp_inferenza.collega, None, None, None, None, None)
        self.risposte = []

        def finto(url, **kwargs):
            self.risposte.append(url)
            return mock.Mock(status_code=200, raise_for_status=lambda: None)

        self.finto = finto

    def test_il_gate_registra_entrata_e_uscita(self):
        with mock.patch.object(cp_inferenza, "requests") as req:
            req.post.side_effect = self.finto
            cp_inferenza._local_model_post("http://ollama:11434/api/chat", json={})
        self.assertEqual(self.gate.chiamate, ["enter", "leave"])

    def test_se_il_gate_rifiuta_il_post_non_esce_e_lo_dice(self):
        """Occupato: la funzione SOLLEVA, non restituisce `None`.

        Non è una scelta di gusto. Un ritorno silenzioso lascerebbe il chiamante
        con un risultato vuoto da trattare come una risposta, e la catena dei
        fallback passerebbe al tentativo dopo credendo che il modello avesse
        risposto "niente". Eccezione vuol dire "non ho potuto provare", e il
        chiamante la distingue da un modello che ha risposto male.
        """
        gate = _Gate(entra=False)
        cp_inferenza.collega("chiave", gate, "node", "pub", lambda r: None)
        with mock.patch.object(cp_inferenza, "requests") as req:
            req.post.side_effect = self.finto
            with self.assertRaises(RuntimeError) as presa:
                cp_inferenza._local_model_post("http://x/api/chat", json={})
        self.assertIn("immagine", str(presa.exception),
                      "il messaggio deve dire PERCHE' non si e' potuto procedere")
        self.assertEqual(self.risposte, [], "il post non doveva uscire")
        self.assertEqual(gate.chiamate, ["enter"],
                         "senza entrare non si esce, e non si deve nemmeno "
                         "lasciare la chat: sarebbe un permesso mai preso")


class NodeBusyErrorTests(unittest.TestCase):
    """È un segnale del protocollo, non un errore generico.

    `503 node_busy_timeout` da un nodo vuol dire "prova il prossimo candidato", e
    non "la richiesta è fallita". Confonderli fa fallire una richiesta che avrebbe
    avuto successo al tentativo dopo.
    """

    def test_e_un_eccezione_e_si_cattura_per_tipo(self):
        with self.assertRaises(cp_inferenza.NodeBusyError):
            raise cp_inferenza.NodeBusyError("occupato")

    def test_main_py_e_cp_condividono_la_stessa_classe(self):
        """Se le due copie fossero diverse, il `except NodeBusyError` di
        `_run_tool_loop` non catturerebbe mai quello sollevato qui dentro, e la
        catena di fallback diventerebbe un `except` che non scatta."""
        sorgente = cp_source.SORGENTE()
        self.assertIn("from cp.inferenza import", sorgente)
        # una sola classe in tutto il control-plane
        import ast
        albero = ast.parse(sorgente)
        classi = [n.name for n in ast.walk(albero)
                  if isinstance(n, ast.ClassDef) and n.name == "NodeBusyError"]
        self.assertEqual(len(classi), 1, f"NodeBusyError definita {len(classi)} volte")


if __name__ == "__main__":
    unittest.main()
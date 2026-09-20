# SPDX-License-Identifier: Apache-2.0
"""Il canale dal terminale: scripts/code_channel.py.

Tre cose che questo file protegge, in ordine di quanto male farebbe un guasto:

1. IL FILO. `push_log` genera un trace_id nuovo quando non gliene passi uno:
   un messaggio senza trace e' una conversazione di un messaggio solo, e non se
   ne accorge nessuno. `messaggio()` quindi lo pretende, e c'e' un test.
2. LE DUE IMPLEMENTAZIONI DEL RAGGRUPPAMENTO. La stessa regola vive in Python
   (terminale) e in JavaScript (browser): il test le esegue ENTRAMBE sullo stesso
   caso e confronta il risultato. Due copie che divergono sono peggio di una.
3. IL VERDETTO. `summarize_suite` legge l'esito REALE di unittest: se prendesse
   l'ultima riga, un traceback o un print finale lo farebbero mentire.
"""
import io
import json
import pathlib
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
DASH = ROOT / "infra-ui" / "dashboard.html"

from scripts import code_channel as cc  # noqa: E402

RIGHE = [
    {"id": 1, "log_id": "a", "type": "code_proposal", "trace_id": "t1", "source": "macbook",
     "target": "win11", "status": "info", "summary": "proposta", "detail": "", "ts": "2026-09-20T10:00:00Z"},
    {"id": 2, "log_id": "b", "type": "code_review", "trace_id": "t1", "source": "win11",
     "target": "macbook", "status": "warning", "summary": "revisione", "detail": "", "ts": "2026-09-20T10:05:00Z"},
    {"id": 3, "log_id": "c", "type": "code_verdict", "trace_id": "t1", "source": "win11",
     "target": "macbook", "status": "success", "summary": "ok", "detail": "5 test OK",
     "ts": "2026-09-20T10:06:00Z"},
    {"id": 4, "log_id": "d", "type": "code_proposal", "trace_id": "t2", "source": "win11",
     "target": "", "status": "info", "summary": "altra", "detail": "", "ts": "2026-09-20T11:00:00Z"},
    {"id": 5, "log_id": "e", "type": "code_proposal", "trace_id": "", "source": "macbook",
     "target": "", "status": "info", "summary": "senza filo", "detail": "", "ts": "2026-09-20T12:00:00Z"},
]


def _esegui_raggruppatore_js(righe):
    """Esegue il groupCodeThreads VERO della dashboard (estratto dall'HTML)."""
    html = DASH.read_text(encoding="utf-8")
    inizio = html.find("const CODE_KIND_LABEL=")
    fine = html.find("\nfunction pushCodeMessage(")
    assert inizio != -1 and fine != -1, "blocco codice non trovato in infra-ui/dashboard.html"
    script = html[inizio:fine] + """
const righe = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(groupCodeThreads(righe.map(codeMessageFromLog)).map(f => ({
  trace: f.trace, participants: f.participants, verdict: f.verdict,
  kinds: f.messages.map(m => m.kind)}))));
"""
    esito = subprocess.run(["node", "-e", script], input=json.dumps(righe),
                           capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout)


class MessaggioTests(unittest.TestCase):
    def test_il_filo_e_obbligatorio(self):
        """Senza trace, push_log ne inventa uno: il messaggio finirebbe in un filo
        suo, per sempre. Meglio un errore adesso."""
        with self.assertRaises(ValueError) as errore:
            cc.messaggio("code_proposal", "", "macbook", "win11", "intento")
        self.assertIn("trace", str(errore.exception))

    def test_il_riassunto_e_obbligatorio(self):
        with self.assertRaises(ValueError):
            cc.messaggio("code_proposal", "t1", "macbook", "win11", "   ")

    def test_il_tipo_deve_stare_nel_vocabolario(self):
        with self.assertRaises(ValueError):
            cc.messaggio("code_idea", "t1", "macbook", "win11", "intento")

    def test_un_messaggio_valido_ha_i_campi_giusti(self):
        corpo = cc.messaggio("code_review", "t1", "win11", "macbook", "ok", "nota", "warning")
        self.assertEqual(corpo["type"], "code_review")
        self.assertEqual(corpo["traceId"], "t1")
        self.assertEqual(corpo["sourceNode"], "win11")
        self.assertEqual(corpo["targetNode"], "macbook")
        self.assertEqual(corpo["status"], "warning")
class FormaChiaviTests(unittest.TestCase):
    def test_log_field_legge_entrambe_le_forme(self):
        self.assertEqual(cc.log_field({"source": "macbook"}, "sourceNode", "source"), "macbook")
        self.assertEqual(cc.log_field({"sourceNode": "win11"}, "sourceNode", "source"), "win11")
        self.assertEqual(cc.log_field({"trace_id": "t1"}, "traceId", "trace_id"), "t1")
        self.assertEqual(cc.log_field({}, "traceId", "trace_id"), "")
        self.assertEqual(cc.log_field({"traceId": ""}, "traceId", "trace_id"), "")


class RaggruppamentoTests(unittest.TestCase):
    def test_fili_ordinati_e_messaggi_in_ordine(self):
        fili = cc.group_threads(RIGHE)
        self.assertEqual([f["trace"] for f in fili], ["t2", "t1"])
        self.assertEqual([cc.log_field(m, "type") for m in fili[1]["messages"]],
                         ["code_proposal", "code_review", "code_verdict"])
        self.assertEqual(fili[1]["participants"], ["macbook", "win11"])
        self.assertEqual(fili[1]["verdict"], "success")
        self.assertEqual(fili[0]["verdict"], "")

    def test_un_messaggio_senza_filo_viene_ignorato(self):
        traces = [f["trace"] for f in cc.group_threads(RIGHE)]
        self.assertNotIn("", traces)
        self.assertEqual(len(cc.group_threads(RIGHE)), 2)

    @unittest.skipUnless(shutil.which("node"), "node non disponibile")
    def test_python_e_javascript_raggruppano_uguale(self):
        """Le due implementazioni devono concordare: fili, ordine, partecipanti,
        esito. Se divergono, terminale e browser raccontano due storie diverse
        della stessa conversazione — e nessuno dei due lo dice."""
        da_python = [{"trace": f["trace"], "participants": f["participants"],
                      "verdict": f["verdict"],
                      "kinds": [cc.log_field(m, "type") for m in f["messages"]]}
                     for f in cc.group_threads(RIGHE)]
        self.assertEqual(da_python, _esegui_raggruppatore_js(RIGHE))


class VerdettoTests(unittest.TestCase):
    def test_legge_ok_e_fallito(self):
        self.assertEqual(cc.summarize_suite("........\nRan 331 tests in 6.8s\n\nOK\n"),
                         "331 test · OK")
        self.assertEqual(cc.summarize_suite("..F\nRan 12 tests in 0.1s\n\nFAILED (failures=1)"),
                         "12 test · FALLITI: failures=1")

    def test_una_suite_senza_riga_finale_non_mente(self):
        """Un output troncato o un errore all'avvio non devono diventare 'OK'."""
        riassunto = cc.summarize_suite("Traceback (most recent call last):\n  File ..., line 1\nImportError")
        self.assertIn("non riconosciuto", riassunto)
        self.assertNotIn("OK", riassunto.replace("non riconosciuto", ""))


class FormattazioneTests(unittest.TestCase):
    def test_il_filo_si_legge_in_ordine_con_l_esito(self):
        testo = cc.format_thread(cc.group_threads(RIGHE)[1])
        self.assertIn("filo t1", testo)
        self.assertIn("macbook → win11", testo)
        self.assertIn("esito: success", testo)
        self.assertLess(testo.index("proposta"), testo.index("revisione"), "ordine cronologico")
        self.assertIn("5 test OK", testo)

    def test_i_dettagli_lunghi_si_tagliano(self):
        filo = {"trace": "t", "participants": ["a"], "verdict": "", "last_ts": "",
                "messages": [{"type": "code_verdict", "source": "a", "summary": "s",
                              "detail": "x" * 500, "ts": "2026-09-20T10:00:00Z"}]}
        self.assertIn("…", cc.format_thread(filo))
        self.assertNotIn("…", cc.format_thread(filo, mostra_detail=False))


class ComandiTests(unittest.TestCase):
    def test_propose_crea_il_filo_e_lo_stampa(self):
        with mock.patch.object(cc, "pubblica", return_value={}) as finta:
            uscita = io.StringIO()
            with redirect_stdout(uscita):
                codice = cc.main(["propose", "--to", "win11", "--summary", "estrai X",
                                  "--from", "macbook"])
        self.assertEqual(codice, 0)
        corpo = finta.call_args[0][1]
        self.assertEqual(corpo["type"], "code_proposal")
        self.assertEqual(corpo["detail"], "")
        self.assertEqual(len(corpo["traceId"]), 8, "il filo creato e' di 8 caratteri")
        self.assertIn(corpo["traceId"], uscita.getvalue(),
                      "se il trace non si stampa, il filo e' perso: o lo si riusa o non esiste")

    def test_propose_con_artifact_riferisce_il_forge(self):
        with mock.patch.object(cc, "pubblica", return_value={}) as finta:
            with redirect_stdout(io.StringIO()):
                cc.main(["propose", "--to", "win11", "--summary", "x", "--artifact", "art-123"])
        self.assertEqual(finta.call_args[0][1]["detail"], "forge://artifact/art-123")

    def test_review_pretende_il_filo(self):
        with self.assertRaises(SystemExit):
            cc.main(["review", "--summary", "senza trace"])

    def test_un_trace_di_spazi_non_diventa_un_filo_a_se(self):
        """`--trace "   "` non e' 'trace assente': se passasse, il messaggio
        finirebbe in un filo nuovo e la conversazione si spezzerebbe in due. Il
        comando esce con 2 e NON pubblica niente."""
        with mock.patch.object(cc, "pubblica", return_value={}) as finta:
            codice = cc.main(["propose", "--to", "win11", "--summary", "x", "--trace", "   "])
        self.assertEqual(codice, 2)
        self.assertFalse(finta.called, "con un trace solo spazi non si pubblica nulla")

    def test_gate_pubblica_l_esito_vero(self):
        with mock.patch.object(cc, "esegui_suite",
                               return_value=(1, "F\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n")), \
                mock.patch.object(cc, "pubblica", return_value={}) as finta:
            with redirect_stdout(io.StringIO()):
                codice = cc.main(["gate", "--trace", "t1", "--from", "win11", "--cmd", "echo"])
        self.assertEqual(codice, 1, "l'exit code rispecchia la suite")
        corpo = finta.call_args[0][1]
        self.assertEqual(corpo["status"], "failed", "un test rosso non puo' diventare 'success'")
        self.assertIn("3 test", corpo["detail"])
        self.assertIn("FAILED", corpo["detail"])

    def test_gate_senza_cmd_usa_la_suite_di_default(self):
        """`--cmd` non passato e' la stringa vuota: senza il controllo giusto il
        comando eseguito sarebbe VUOTO (subprocess solleva, e il gate non parte)."""
        with mock.patch.object(cc, "esegui_suite", return_value=(0, "Ran 1 tests\n\nOK\n")) as suite, \
                mock.patch.object(cc, "pubblica", return_value={}), \
                redirect_stdout(io.StringIO()):
            cc.main(["gate", "--trace", "t1", "--dry"])
        self.assertEqual(suite.call_args[0][0], list(cc.SUITE_DEFAULT))

    def test_dry_non_pubblica_nulla(self):
        with mock.patch.object(cc, "pubblica", side_effect=AssertionError("non deve pubblicare")):
            uscita = io.StringIO()
            with redirect_stdout(uscita):
                codice = cc.main(["verdict", "--trace", "t1", "--summary", "ok", "--dry"])
        self.assertEqual(codice, 0)
        self.assertEqual(json.loads(uscita.getvalue())["type"], "code_verdict")


if __name__ == "__main__":
    unittest.main()



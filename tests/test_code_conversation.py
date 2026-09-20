# SPDX-License-Identifier: Apache-2.0
"""Conversazione fra agenti che scrivono codice: il filo e i suoi agganci.

Cosa protegge questo file, in ordine di quanto male farebbe un guasto silenzioso:

1. il RAGGRUPPAMENTO per filo (`trace`), cioe' la logica che trasforma messaggi
   sparsi in una conversazione. Vive dentro infra-ui/dashboard.html perche' la
   dashboard e' un file singolo, quindi il test lo ESEGUE estraendolo da li' con
   node — non ne testa una copia, che sarebbe un test su se stesso;
2. gli AGGANCI: un tipo di log che non entra in `LOG_TYPES` viene riscritto a
   "system" (push_log), e un tipo che non entra nei poll del bridge non compare
   mai in dashboard. Sono due modi di perdere la conversazione senza un errore da
   nessuna parte, quindi si controlla che i tre tipi siano in tutti e tre i posti:
   control-plane/main.py, infra-ui/server.py, e i due dashboard.

Se node non c'e', il test 1 si salta invece di fallire: la suite Python non deve
dipendere da node per tutto il resto.
"""
import ast
import __future__
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
CP_MAIN = (ROOT / "control-plane" / "main.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "infra-ui" / "server.py").read_text(encoding="utf-8")
DASH = (ROOT / "infra-ui" / "dashboard.html").read_text(encoding="utf-8")
CP_DASH = (ROOT / "control-plane" / "dashboard.html").read_text(encoding="utf-8")

TIPI = ("code_proposal", "code_review", "code_verdict")


def _estrai_blocco_code() -> str:
    """Il blocco con la mappa etichette, la normalizzazione dei log e il raggruppatore."""
    inizio = DASH.find("const CODE_KIND_LABEL=")
    fine = DASH.find("\nfunction pushCodeMessage(")
    assert inizio != -1 and fine != -1 and inizio < fine, (
        "il blocco codice non e' piu' dove il test lo cerca in infra-ui/dashboard.html: "
        "aggiorna l'estrazione (non cancellare il test)")
    return DASH[inizio:fine]


def _esegui_node(espressione: str, argomento) -> object:
    """Esegue il blocco codice vero (estratto dall'HTML) con node e restituisce il JSON."""
    script = _estrai_blocco_code() + f"""
const casi = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify({espressione}));
"""
    esito = subprocess.run(["node", "-e", script], input=json.dumps(argomento),
                           capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout)


def _messaggio(trace, kind, ts, sender="macbook", status="info", label=""):
    return {"id": f"{trace}-{kind}-{ts}", "trace": trace, "kind": kind, "from": sender,
            "to": "win11", "status": status, "label": label or f"{kind} {ts}", "ts": ts}


class RaggruppamentoTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node non disponibile: suite Python senza JS")
    def test_i_messaggi_dello_stesso_filo_diventano_una_conversazione(self):
        messaggi = [
            _messaggio("a1b2c3d4", "code_proposal", "2026-09-20T10:00:00Z", "macbook"),
            _messaggio("a1b2c3d4", "code_review", "2026-09-20T10:05:00Z", "win11"),
            _messaggio("a1b2c3d4", "code_verdict", "2026-09-20T10:06:00Z", "win11", "success"),
            _messaggio("ffee0011", "code_proposal", "2026-09-20T11:00:00Z", "win11"),
            {"id": "senza-filo", "kind": "code_proposal", "trace": "", "ts": "2026-09-20T12:00:00Z"},
        ]
        fili = _esegui_node("groupCodeThreads(casi)", messaggi)

        self.assertEqual([f["trace"] for f in fili], ["ffee0011", "a1b2c3d4"],
                         "i fili piu' recenti vengono prima")
        self.assertEqual(len(fili[1]["messages"]), 3, "un messaggio senza trace non e' una conversazione")
        self.assertEqual([m["kind"] for m in fili[1]["messages"]],
                         ["code_proposal", "code_review", "code_verdict"],
                         "i messaggi si ordinano per ts, non per arrivo")
        self.assertEqual(fili[1]["participants"], ["macbook", "win11"])
        self.assertEqual(fili[1]["verdict"], "success")
        self.assertEqual(fili[0]["verdict"], "", "un filo senza verdetto resta aperto")

    @unittest.skipUnless(shutil.which("node"), "node non disponibile: suite Python senza JS")
    def test_le_due_forme_di_chiavi_danno_lo_stesso_messaggio(self):
        """`/logs` (DB) e `/logs/add` (push_log) espongono gli stessi dati con nomi
        diversi. Senza normalizzazione il filo sparisce — nessun errore, solo una
        conversazione che non c'e'. E' successo davvero, quindi si prova."""
        riga_db = {"id": 1302, "log_id": "f7a4bac4", "type": "code_proposal", "trace_id": "e2e00001",
                   "source": "macbook", "target": "win11", "status": "info",
                   "summary": "proposta", "detail": "forge://x", "ts": "2026-09-20T10:12:37Z"}
        riga_push = {"id": "f7a4bac4", "type": "code_proposal", "traceId": "e2e00001",
                     "sourceNode": "macbook", "targetNode": "win11", "status": "info",
                     "summary": "proposta", "detail": "forge://x", "ts": "2026-09-20T10:12:37Z"}
        da_db, da_push = _esegui_node("[codeMessageFromLog(casi[0]),codeMessageFromLog(casi[1])]",
                                      [riga_db, riga_push])
        self.assertEqual(da_db, da_push)
        self.assertEqual(da_db["trace"], "e2e00001")
        self.assertEqual(da_db["from"], "macbook")
        self.assertEqual(da_db["to"], "win11")
        self.assertEqual(da_db["id"], "f7a4bac4", "l'id stabile e' log_id, non l'intero di riga")


class AgganciTests(unittest.TestCase):
    def test_i_tre_tipi_sono_in_LOG_TYPES(self):
        blocco = re.search(r"LOG_TYPES = \{(.*?)\}", CP_MAIN, re.S)
        self.assertIsNotNone(blocco, "LOG_TYPES non trovato")
        for tipo in TIPI:
            with self.subTest(tipo=tipo):
                self.assertIn(f'"{tipo}"', blocco.group(1),
                              "un tipo fuori da LOG_TYPES viene riscritto a 'system': "
                              "la conversazione sparirebbe senza errori")

    def test_il_bridge_polla_i_tre_tipi_e_li_manda_come_un_evento(self):
        self.assertIn('_CODE_LOG_TYPES = ("code_proposal", "code_review", "code_verdict")', BRIDGE)
        self.assertIn("for _tipo in _CODE_LOG_TYPES", BRIDGE,
                      "senza il giro di poll i messaggi non arrivano mai in dashboard")
        self.assertIn('_broadcast("code_message"', BRIDGE)

    def test_la_dashboard_infra_ui_ha_tab_pane_e_rendering(self):
        for atteso in ('data-tab="code"', 'id="pane-code"', "if(t==='code')renderCodeThreads()",
                       "es.addEventListener('code_message'", "fetchCodeThreads()"):
            with self.subTest(atteso=atteso):
                self.assertIn(atteso, DASH)

    def test_il_dashboard_del_cp_ha_la_tab_e_i_badge(self):
        self.assertIn("setTab('code',this)", CP_DASH)
        self.assertIn("if(curType==='code')", CP_DASH, "la tab non unirebbe i tre tipi")
        for tipo in TIPI:
            with self.subTest(tipo=tipo):
                self.assertIn(f".tb-{tipo}", CP_DASH)


class BridgeFieldTests(unittest.TestCase):
    def test_log_field_accetta_entrambe_le_forme(self):
        """L'helper del bridge si prova eseguendolo davvero (estratto con ast),
        sulla riga del DB e su quella di /logs/add."""
        tree = ast.parse(BRIDGE)
        fn = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_log_field"), None)
        self.assertIsNotNone(fn, "_log_field non c'e' piu' in infra-ui/server.py")
        scope: dict = {}
        # Il modulo da cui la funzione e' estratta ha `from __future__ import
        # annotations`: senza lo stesso flag l'annotazione `dict[str, Any]`
        # verrebbe valutata subito e su 3.11/3.12 (le versioni dei container)
        # servirebbe importare `Any` nel namespace. Su 3.14 non serve — le
        # annotazioni sono pigre — ed e' per questo che in locale il test
        # passava e in CI no. Cosi' si prova la funzione com'e' davvero
        # compilata in produzione, su qualunque versione.
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "bridge", "exec",
                     flags=__future__.annotations.compiler_flag, dont_inherit=True), scope)
        campo = scope["_log_field"]

        riga_db = {"source": "macbook", "trace_id": "abc", "log_id": "f7a4"}
        riga_push = {"sourceNode": "win11", "traceId": "def", "id": "uuid"}
        self.assertEqual(campo(riga_db, "sourceNode", "source"), "macbook")
        self.assertEqual(campo(riga_push, "sourceNode", "source"), "win11")
        self.assertEqual(campo(riga_db, "traceId", "trace_id"), "abc")
        self.assertEqual(campo(riga_push, "traceId", "trace_id"), "def")
        self.assertEqual(campo({}, "sourceNode", "source"), "")
        self.assertEqual(campo({"sourceNode": ""}, "sourceNode", "source"), "",
                         "una chiave presente ma vuota non deve bloccare il fallback")

    def test_il_bridge_usa_l_helper_per_la_conversazione(self):
        for atteso in ('_log_field(log, "traceId", "trace_id")',
                       '_log_field(log, "sourceNode", "source")',
                       '_log_field(log, "targetNode", "target")'):
            with self.subTest(atteso=atteso):
                self.assertIn(atteso, BRIDGE)


if __name__ == "__main__":
    unittest.main()

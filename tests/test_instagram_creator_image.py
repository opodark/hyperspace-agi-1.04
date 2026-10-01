# SPDX-License-Identifier: Apache-2.0
"""Il ponte delle immagini in DM: creatore e banda intima, sketch o vetrina.

Il webhook Instagram non passa da ``/channel/reply``, quindi questo ponte *e'* il
punto in cui un messaggio di una persona diventa un job. Tre cose si fissano qui:

- **chi entra**: il creatore, e la banda piu' vicina (`INTIMATE_LEVELS`, cioe' `musa`)
  **con il consenso registrato** — la stessa lista che decide la voce. Chi ha la parola
  esplicita ha l'immagine esplicita: `StessaListaTests` impedisce che le due liste
  tornino a divergere in silenzio.
- **la ricetta**: un soggetto qualunque resta uno sketch leggero; una richiesta di
  **se stessa** passa dalla vetrina del documento (famiglia, modello, seed,
  `riferimento` del volto) — perche' uno sketch generico disegna *una* donna, non
  questa. `JobTests` legge il job VERO (`shared/image_jobs.nuovo_job`) come lo legge
  ComfyUI, e vede la differenza fra le due strade.
- **cosa non passa**: i minori, di nessuno e per nessun livello, e una richiesta che
  la vetrina non regge si **rifiuta dicendolo** invece di accodare un'immagine
  castigata senza spiegazione (`RifiutoTests`).

La funzione e' estratta dal VERO `control-plane/main.py` con `ast` ed eseguita in
isolamento (la tecnica di `tests/test_channel_immagine.py`): nessuna dipendenza da
Flask, e la richiesta viene riconosciuta dalla regex vera di `shared/prompt_immagine.py`
(il modello non e' configurato in test: `richiesta_immagine_smart` ripiega sulla
regex, che e' la stessa strada della produzione quando Ollama non risponde).
"""
import ast
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import FAMIGLIA_SDXL, nuovo_job  # noqa: E402
from shared.instagram_intimacy import split_messages  # noqa: E402
from shared.instagram_vip import CREATOR_LEVEL, INTIMATE_LEVELS, LEVEL_NAMES  # noqa: E402
from shared.prompt_immagine import prepara_prompt_canale, richiesta_immagine_smart  # noqa: E402
from shared.showcase import (VIETATI_MINORI, conflitti, negativo_ritratto,  # noqa: E402
                             prompt_ritratto, richiesta_di_se, verifica_vetrina,
                             vetrina_con_quadro_erotismo, vetrina_dal_documento)
from shared.sketch import SKETCH_LATO, SKETCH_PASSI, negativo_sketch  # noqa: E402

SOURCE = ROOT / "control-plane" / "main.py"

# La vetrina che il finto documento dichiara: la stessa di
# `tests/test_channel_immagine.py`, così "acceso" e "spento" sono due casi scritti.
VETRINA_ANNA = {
    "famiglia": "sd15", "modello": "chickmixflat_v10.ckpt",
    "larghezza": 512, "altezza": 768, "passi": 48, "seed": 20260930,
    "riferimento": "anna-volto-canonico.jpg", "riferimento_forza": 0.7,
    "consenti_nudo_artistico_virtuale": True,
    "consenti_erotismo_esplicito_creatore": True,
}


class Queue:
    """La coda vera non serve: serve sapere cosa le viene dato."""

    def __init__(self):
        self.jobs = []

    def accoda(self, job):
        self.jobs.append(job)
        return job


class Consensi:
    """Il registro del consenso, ridotto all'unica domanda che si fa qui."""

    def __init__(self, valore: str = ""):
        self.valore = valore

    def consent(self, sender_id: str) -> str:
        return self.valore


def load(queue, consenso: str = "", vetrina=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nomi = {"_queue_instagram_creator_image", "_job_ritratto_instagram",
            "_livello_immagine_intima", "_nome_persona"}
    nodi = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in nomi]
    scope = {
        "CREATOR_LEVEL": CREATOR_LEVEL,
        "INTIMATE_LEVELS": INTIMATE_LEVELS,
        "instagram_vips": Consensi(consenso),
        "richiesta_immagine_smart": richiesta_immagine_smart,
        "persona_store": SimpleNamespace(
            system_block=lambda: "Sono Anna",
            persona=SimpleNamespace(name="Anna"),
            sezioni={"vetrina": {**VETRINA_ANNA, **(vetrina or {})}}),
        "image_queue": queue,
        "nuovo_job": nuovo_job,
        "prepara_prompt_canale": prepara_prompt_canale,
        "negativo_sketch": negativo_sketch,
        "SKETCH_LATO": SKETCH_LATO,
        "SKETCH_PASSI": SKETCH_PASSI,
        "FAMIGLIA_SDXL": FAMIGLIA_SDXL,
        "vetrina_dal_documento": vetrina_dal_documento,
        "vetrina_con_quadro_erotismo": vetrina_con_quadro_erotismo,
        "verifica_vetrina": verifica_vetrina,
        "prompt_ritratto": prompt_ritratto,
        "negativo_ritratto": negativo_ritratto,
        "richiesta_di_se": richiesta_di_se,
        "conflitti": conflitti,
        "VIETATI_MINORI": VIETATI_MINORI,
        "push_log": lambda *args, **kwargs: None,
    }
    exec(compile(ast.Module(body=nodi, type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope["_queue_instagram_creator_image"]


def _nomi_usati(funzione: str) -> set:
    """I nomi (costanti, attributi) che quella funzione legge, presi dall'AST."""
    albero = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodo = next(n for n in ast.walk(albero)
                if isinstance(n, ast.FunctionDef) and n.name == funzione)
    return ({n.id for n in ast.walk(nodo) if isinstance(n, ast.Name)}
            | {n.attr for n in ast.walk(nodo) if isinstance(n, ast.Attribute)})

class PortaTests(unittest.TestCase):
    """Chi entra: il creatore, e la banda intima col consenso registrato."""

    def test_creatore_in_dm_accoda_e_consegna_al_suo_scoped_id(self):
        queue = Queue()

        self.assertTrue(load(queue)(
            "123", "fammi un disegno di un faro", {"level": CREATOR_LEVEL}))
        self.assertEqual(len(queue.jobs), 1)
        self.assertEqual(queue.jobs[0]["canale"], "instagram")
        self.assertEqual(queue.jobs[0]["destinazione"], "123")
        self.assertEqual(queue.jobs[0]["richiedente"], "creatore")

    def test_il_richiedente_e_lusername_quando_lo_scoped_id_non_lo_dice(self):
        queue = Queue()

        load(queue)("123", "fammi un disegno di un faro",
                    {"level": CREATOR_LEVEL, "username": "malvaniroberto"})
        self.assertEqual(queue.jobs[0]["richiedente"], "malvaniroberto")

    def test_la_banda_intima_senza_consenso_non_accoda(self):
        for livello in INTIMATE_LEVELS:
            for consenso in ("", "asked", "denied"):
                queue = Queue()
                self.assertFalse(load(queue, consenso)(
                    "123", "fammi un disegno di un faro", {"level": livello}))
                self.assertEqual(queue.jobs, [])

    def test_la_banda_intima_col_consenso_accoda(self):
        for livello in INTIMATE_LEVELS:
            queue = Queue()
            self.assertTrue(load(queue, "granted")(
                "123", "fammi un disegno di un faro", {"level": livello}))
            self.assertEqual(len(queue.jobs), 1)

    def test_il_consenso_non_basta_fuori_dalla_banda_intima(self):
        for livello in ("", "vip", "conosciuto"):
            queue = Queue()
            self.assertFalse(load(queue, "granted")(
                "123", "fammi un disegno di un faro", {"level": livello}))
            self.assertEqual(queue.jobs, [])

    def test_una_frase_che_non_e_una_richiesta_non_accoda_nulla(self):
        queue = Queue()

        self.assertFalse(load(queue)(
            "123", "che bella giornata oggi", {"level": CREATOR_LEVEL}))
        self.assertEqual(queue.jobs, [])


class RicettaTests(unittest.TestCase):
    """Un soggetto resta uno sketch; una richiesta di sé passa dalla vetrina."""

    def test_soggetto_qualunque_resta_lo_sketch_leggero(self):
        queue = Queue()

        load(queue)("123", "fammi un disegno di un faro", {"level": CREATOR_LEVEL})
        job = queue.jobs[0]
        self.assertEqual(job["famiglia"], FAMIGLIA_SDXL)
        self.assertEqual(job["larghezza"], SKETCH_LATO)
        self.assertEqual(job["altezza"], SKETCH_LATO)
        self.assertEqual(job["passi"], SKETCH_PASSI)
        self.assertEqual(job["reference_image"], "")
        self.assertEqual(job["negativo"], negativo_sketch()[:1000])

    def test_la_richiesta_di_se_stessa_passa_dalla_vetrina(self):
        queue = Queue()

        load(queue)("123", "fammi un disegno di te", {"level": CREATOR_LEVEL})
        job = queue.jobs[0]
        self.assertEqual(job["famiglia"], "sd15")
        self.assertEqual(job["modello"], "chickmixflat_v10.ckpt")
        self.assertEqual(job["modello_effettivo"], "chickmixflat_v10.ckpt")
        self.assertEqual(job["seed"], 20260930)
        self.assertEqual((job["larghezza"], job["altezza"]), (512, 768))
        self.assertEqual(job["passi"], 48)
        # Il volto canonico: senza riferimento il seed tiene il disegno, non l'identità.
        self.assertEqual(job["reference_image"], "anna-volto-canonico.jpg")
        self.assertEqual(job["reference_strength"], 0.7)

    def test_il_nome_basta_a_riconoscere_un_ritratto_di_se(self):
        queue = Queue()

        load(queue)("123", "fammi un disegno di Anna", {"level": CREATOR_LEVEL})
        self.assertEqual(queue.jobs[0]["famiglia"], "sd15")

    def test_un_ritratto_senza_parole_esplicite_resta_castigato(self):
        """Il livello non è un effetto collaterale: senza richiesta, il negativo è quello."""
        queue = Queue()

        load(queue)("123", "fammi un disegno di te", {"level": CREATOR_LEVEL})
        negativo = queue.jobs[0]["negativo"]
        self.assertIn("nudità", negativo)
        self.assertIn("esplicito", negativo)
        self.assertIn("minori", negativo)


class LivelloTests(unittest.TestCase):
    """Il livello dichiarato dal documento, e le parole scritte dal sistema."""

    def test_la_banda_intima_col_consenso_ha_il_livello_pieno(self):
        for livello in INTIMATE_LEVELS:
            with self.subTest(livello=livello):
                queue = Queue()
                vip = {"level": livello, "username": "tizia"}

                self.assertTrue(load(queue, "granted")(
                    "123", "fammi un disegno di te nuda", vip))
                job = queue.jobs[0]
                self.assertEqual(job["famiglia"], "sd15")
                self.assertNotIn("nudità", job["negativo"])
                self.assertNotIn("esplicito", job["negativo"])
                # I minori restano l'unica voce che nessun livello tocca.
                self.assertIn("minori", job["negativo"])
                self.assertEqual(vip["quadro"], ["adult", "virtual"])

    def test_il_creatore_ha_lo_stesso_livello_della_banda_intima(self):
        queue = Queue()
        vip = {"level": CREATOR_LEVEL}

        load(queue)("123", "fammi un disegno di te nuda", vip)
        self.assertNotIn("nudità", queue.jobs[0]["negativo"])
        self.assertEqual(vip["quadro"], ["adult", "virtual"])

    def test_il_documento_che_non_dichiara_il_livello_rifiuta(self):
        queue = Queue()
        vip = {"level": "musa"}

        self.assertTrue(load(queue, "granted", vetrina={
            "consenti_nudo_artistico_virtuale": False,
            "consenti_erotismo_esplicito_creatore": False})(
                "123", "fammi un disegno di te nuda", vip))
        self.assertEqual(queue.jobs, [])
        self.assertIn("non dichiara", vip["rifiuto"])
        self.assertNotIn("quadro", vip)

    def test_una_richiesta_che_dichiara_gia_il_quadro_non_aggiunge_nulla(self):
        """Le parole aggiunte si dicono solo quando ci sono: niente note a vuoto."""
        vip = {"level": CREATOR_LEVEL}
        queue = Queue()

        load(queue)("123", "fammi un disegno di te nuda adult virtual", vip)
        self.assertNotIn("quadro", vip)
        self.assertNotIn("nudità", queue.jobs[0]["negativo"])


class MinoriTests(unittest.TestCase):
    """Nessun soggetto minorenne, per nessuno e con nessun livello."""

    def test_il_creatore_non_ottiene_un_soggetto_minorenne(self):
        queue = Queue()
        vip = {"level": CREATOR_LEVEL}

        self.assertTrue(load(queue)("123", "fammi un disegno di una bambina", vip))
        self.assertEqual(queue.jobs, [])
        self.assertIn("minorenni", vip["rifiuto"])

    def test_nemmeno_la_banda_intima_col_consenso(self):
        queue = Queue()
        vip = {"level": "musa"}

        self.assertTrue(load(queue, "granted")(
            "123", "fammi un disegno di una bambina nuda", vip))
        self.assertEqual(queue.jobs, [])
        self.assertIn("minorenni", vip["rifiuto"])


class StessaListaTests(unittest.TestCase):
    """Chi ha la voce ha l'immagine: le due liste sono una sola costante.

    Il test è meccanico di proposito: non chiede che le regole siano *simili*, chiede
    che entrambe le strade leggano lo **stesso** nome. Se qualcuno allarga la voce
    senza allargare l'immagine (o viceversa) toccando uno solo dei due punti, le due
    liste divergono e questo test lo dice — invece di lasciarlo scoprire a una
    persona che si sente promettere una cosa e ne ottiene un'altra.
    """

    def test_canale_voce_e_immagine_leggono_la_stessa_lista(self):
        for funzione in ("_channel_immagine", "_channel_reply"):
            nomi = _nomi_usati(funzione)
            self.assertIn("CHANNEL_CERCHIA", nomi, funzione)
            self.assertIn("CHANNEL_OPERATOR", nomi, funzione)

    def test_instagram_immagine_e_consenso_leggono_la_stessa_lista(self):
        # L'immagine (la porta) e il consenso (l'ingresso nella banda intima): se il
        # consenso si chiedesse a un livello e l'immagine si aprisse per un altro,
        # si chiederebbe il permesso a chi non può usarlo.
        for funzione in ("_livello_immagine_intima", "_dispatch_instagram_messages"):
            self.assertIn("INTIMATE_LEVELS", _nomi_usati(funzione), funzione)

    def test_la_voce_di_instagram_e_quella_dell_immagine(self):
        # `consent == "granted"` nella risposta è la stessa condizione della porta: il
        # consenso si chiede solo a `INTIMATE_LEVELS` e i livelli non scendono, quindi
        # l'insieme di chi ha la voce è l'insieme di chi ha l'immagine.
        nomi = _nomi_usati("_instagram_auto_reply")
        for nome in ("consent", "musa_context", "compagna_context", "INTIMATE_LEVELS"):
            self.assertIn(nome, nomi)
        # E le due cose che il ponte lascia alla risposta: il rifiuto (che sostituisce
        # il testo del modello) e la nota del quadro (che gli si appende).
        for nome in ("rifiuto", "_nota_quadro", "_chunks_con_nota"):
            self.assertIn(nome, nomi, nome)

    def test_nessuna_banda_scritta_a_mano_nella_voce(self):
        """Le bande si leggono dalla scala, non si riscrivono come stringhe.

        `_instagram_auto_reply` sceglie il registro confrontando il livello: scritto
        come stringa (`"musa"`) resterebbe fermo a una scala che non c'è più, e la voce
        andrebbe avanti per conto suo rispetto alla porta dell'immagine.
        """
        albero = ast.parse(SOURCE.read_text(encoding="utf-8"))
        nodo = next(n for n in ast.walk(albero)
                    if isinstance(n, ast.FunctionDef) and n.name == "_instagram_auto_reply")
        letterali = {n.value for n in ast.walk(nodo)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for banda in (*LEVEL_NAMES, CREATOR_LEVEL):
            self.assertNotIn(banda, letterali, banda)


class NotaQuadroTests(unittest.TestCase):
    """Le due righe di sistema che accompagnano un'immagine: si dicono o non si dicono.

    Stanno qui, e non nella rotta, perché il ponte e la risposta sono due processi
    diversi: il primo accoda, la seconda parla. Il canale la nota la scrive già
    (`_channel_immagine`); Instagram la ricostruisce dal job — e le due frasi sono la
    stessa cosa detta a due persone, non due formule da tenere allineate a mano.
    """

    @classmethod
    def setUpClass(cls):
        albero = ast.parse(SOURCE.read_text(encoding="utf-8"))
        nodi = [n for n in albero.body if isinstance(n, ast.FunctionDef)
                and n.name in {"_nota_quadro", "_chunks_con_nota"}]
        scope = {}
        exec(compile(ast.Module(body=nodi, type_ignores=[]), str(SOURCE), "exec"), scope)
        cls.nota_quadro = staticmethod(scope["_nota_quadro"])
        cls.chunks_con_nota = staticmethod(scope["_chunks_con_nota"])

    def test_senza_parole_aggiunte_non_si_dice_niente(self):
        self.assertEqual(self.nota_quadro({}), "")
        self.assertEqual(self.nota_quadro({"quadro": []}), "")
        # Parole vuote sono rumore, non un quadro: la nota è o non è.
        self.assertEqual(self.nota_quadro({"quadro": ["", " "]}), "")

    def test_le_parole_del_quadro_si_dicono_e_si_vedono(self):
        nota = self.nota_quadro({"quadro": ["adult", "virtual"]})
        self.assertIn("adult", nota)
        self.assertIn("virtual", nota)
        self.assertIn("l'ho scritto io", nota)

    def test_la_nota_si_appende_all_ultimo_messaggio(self):
        pezzi = self.chunks_con_nota(["ok, ci penso", "arriva presto"], " NOTA")
        self.assertEqual(pezzi, ["ok, ci penso", "arriva presto NOTA"])

    def test_la_nota_non_si_taglia_col_limite_del_messaggio(self):
        """`reply[:1000]` passa prima di qui: un messaggio già al limite non si allunga,
        la nota diventa un messaggio a sé invece di sparire nel troncamento."""
        pezzi = self.chunks_con_nota(["x" * 996], " NOTA")
        self.assertEqual(pezzi, ["x" * 996, "NOTA"])

    def test_senza_nota_i_messaggi_restano_quelli(self):
        self.assertEqual(self.chunks_con_nota(["uno", "due"], ""), ["uno", "due"])
        self.assertEqual(self.chunks_con_nota([], ""), [])


class Modello:
    """Il modello finto: dice se è stato chiamato e cosa risponde."""

    def __init__(self, contenuto: str = "ok, arriva subito"):
        self.chiamate, self.contenuto = 0, contenuto

    def post(self, *args, **kwargs):
        self.chiamate += 1
        return _RispostaHTTP(self.contenuto)


class _RispostaHTTP:
    """Il minimo che la risposta legge: `raise_for_status` e `json`."""

    def __init__(self, contenuto: str):
        self._contenuto = contenuto

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._contenuto}}]}


class RispostaTests(unittest.TestCase):
    """L'altra metà della promessa: cosa arriva **scritto** nella DM.

    Il ponte può rifiutare bene e la risposta può disdire tutto ("te la mando
    subito"): sarebbe il modo peggiore di rispondere, perché la persona aspetta
    un'immagine che nessuno sta disegnando. Qui la funzione di risposta gira
    **vera** ed è eseguita in isolamento (modello e connettore finti): quello che si
    legge è il testo che partirebbe verso Instagram.
    """

    def _inviati(self, vip, testo="fammi un disegno di te nuda"):
        albero = ast.parse(SOURCE.read_text(encoding="utf-8"))
        nodi = [n for n in albero.body if isinstance(n, ast.FunctionDef)
                and n.name in {"_instagram_auto_reply", "_nota_quadro", "_chunks_con_nota"}]
        inviati, modello = [], Modello()

        def _execute(_nome, argomenti):
            inviati.append(str(argomenti.get("text", "")))
            return json.dumps({"ok": True, "message_id": "m1"})

        scope = {
            "wants_continuous": lambda _testo: False,
            "instagram_memory": SimpleNamespace(
                context=lambda *a, **k: {"turns": [], "summary": ""},
                append=lambda *a, **k: None),
            "_channel_int": lambda _nome, default: default,
            "load_codex": lambda *a: {},
            "INSTAGRAM_LANGUAGE_CODEX": "",
            "fast_reply": lambda *a: "",
            "language_hint": lambda *a: "",
            "wants_project_info": lambda *a: False,
            "should_offer_creator": lambda *a: False,
            "sister_note": lambda *a: "",
            "_sister_peer": lambda: "",
            "PROJECT_CONTEXT": "",
            "CREATOR_LEVEL": CREATOR_LEVEL,
            "instagram_vips": Consensi("granted"),
            "compagna_context": lambda *a: "COMPAGNA",
            "cerchia_entry_context": lambda *a: "INGRESSO",
            "musa_context": lambda *a: "MUSA",
            "advanced_config": {"ollama": {"defaultModel": "finto"}},
            "requests": modello,
            "connector_manager": SimpleNamespace(execute=_execute),
            "instagram_reply_outbox": SimpleNamespace(
                sending=lambda *a: True, sent=lambda *a: None,
                fail=lambda *a, **k: None),
            "push_log": lambda *a, **k: None,
            "split_messages": split_messages,
            "os": os,
            "json": json,
        }
        exec(compile(ast.Module(body=nodi, type_ignores=[]), str(SOURCE), "exec"), scope)
        scope["_instagram_auto_reply"]("123", "m1", testo, vip)
        return inviati, modello

    def test_un_rifiuto_e_la_risposta_e_il_modello_non_si_chiama(self):
        rifiuto = ("Questa non te la disegno: non disegno soggetti minorenni, "
                   "mai e per nessuno.")
        inviati, modello = self._inviati({"level": CREATOR_LEVEL, "rifiuto": rifiuto})

        self.assertEqual(inviati, [rifiuto])
        self.assertEqual(modello.chiamate, 0, "un rifiuto non si genera")

    def test_le_parole_del_quadro_arrivano_nella_risposta(self):
        inviati, _ = self._inviati({"level": CREATOR_LEVEL,
                                    "quadro": ["adult", "virtual"]})

        self.assertEqual(len(inviati), 1)
        self.assertIn("adult", inviati[0])
        self.assertIn("l'ho scritto io", inviati[0])
        self.assertTrue(inviati[0].startswith("ok, arriva subito"),
                        "la nota sta in coda: il testo del modello è intatto")

    def test_senza_rifiuto_e_senza_quadro_la_risposta_e_solo_quella_del_modello(self):
        inviati, modello = self._inviati({"level": CREATOR_LEVEL})

        self.assertEqual(modello.chiamate, 1)
        self.assertEqual(inviati, ["ok, arriva subito"])




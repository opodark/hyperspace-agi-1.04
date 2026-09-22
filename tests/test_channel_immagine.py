# SPDX-License-Identifier: Apache-2.0
"""`!immagine <idea>`: l'idea arriva al diffusion senza riscritture, e il freno e' la GPU.

Perche' questi test sembrano "solo" una funzione di canale e non lo sono:

- **La catena HyperSpace non ha nessun filtro di contenuto** (vedi
  `docs/comfyui.md`): il control-plane accoda il prompt com'e', il ponte non
  giudica l'immagine, i pesi sono la variante `-UC` senza safety checker. L'unico
  punto in cui un filtro potrebbe nascere senza che nessuno se ne accorga e'
  **qui**, dove l'idea di una stanza diventa un job: il test fissa che il testo
  arrivi VERBATIM fino al nodo di condizionatura — se qualcuno aggiungesse una
  riscrittura, una bonifica o un "prompt sicuro", questo test lo direbbe invece di
  lasciarlo scoprire da un'immagine sbagliata.
- **Il freno vero non e' morale ma di risorsa**: una scheda sola, 313-700 s per
  immagine, coda da 8 job. Da li' le due regole che questi test difendono: la coda
  piena si rifiuta con una frase (non in silenzio), e l'operatore
  (`CHANNEL_OPERATOR`) puo' limitare chi chiede. Quando la variabile **non** e'
  configurata la guardia non si applica: e' il comportamento dichiarato in
  `docs/comfyui.md` ("una scelta, non un caso"), quindi va fissato qui — chi lo
  cambia lo cambia sapendo.

La funzione e' estratta dal VERO `control-plane/main.py` con `ast` ed eseguita in
isolamento (la tecnica di `tests/test_assistant_text.py`): nessuna dipendenza da
Flask. `nuovo_job` e `workflow` sono quelli VERI di `shared/image_jobs.py`,
perche' sono quegli oggetti a finire nel grafo che ComfyUI esegue.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import nuovo_job, richiesta_immagine, workflow  # noqa: E402

SOURCE = ROOT / "control-plane" / "main.py"
COSTANTI = {"COMANDI_IMMAGINE"}


class CodaFinta:
    """La coda vera non serve: serve sapere COSA le viene dato."""

    def __init__(self, errore: str = ""):
        self.job = []
        self.errore = errore

    def accoda(self, job):
        if self.errore:
            raise RuntimeError(self.errore)
        self.job.append(job)
        return job


def _load(operator=(), coda=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodi = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_channel_immagine"]
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in COSTANTI
                                            for t in n.targets):
            nodi.append(n)
    registrati = []
    scope = {
        "CHANNEL_OPERATOR": set(operator),
        "image_queue": coda if coda is not None else CodaFinta(),
        "nuovo_job": nuovo_job,
        "richiesta_immagine": richiesta_immagine,
        "push_log": lambda *a, **k: registrati.append((a, k)),
    }
    exec(compile(ast.Module(body=nodi, type_ignores=[]), str(SOURCE), "exec"), scope)
    scope["_log"] = registrati
    return scope


def _contesto(testo: str, autore: str = "tizio") -> list:
    return [{"author": autore, "text": testo}]


class ComandoTests(unittest.TestCase):
    def test_le_forme_del_comando_accettate(self):
        """Le varianti sono dichiarate in `COMANDI_IMMAGINE`, non inventate a mano."""
        for comando in ("!immagine", "!IMMAGINE", "!immagine:", "!foto", "!image",
                        "!imagine"):
            with self.subTest(comando=comando):
                scope = _load()
                scope["_channel_immagine"](_contesto(f"{comando} una torre al tramonto"),
                                           channel="telegram", destinazione="1")
                self.assertEqual(len(scope["image_queue"].job), 1)

    def test_una_frase_che_contiene_la_parola_non_e_un_comando(self):
        for testo in ("mi piace immaginare le torri", "ecco la foto di ieri",
                      "!immagina un cavallo"):
            with self.subTest(testo=testo):
                scope = _load()
                self.assertIsNone(scope["_channel_immagine"](
                    _contesto(testo), channel="telegram", destinazione="1"))
                self.assertEqual(scope["image_queue"].job, [])

    def test_l_idea_arriva_verbatim_al_diffusion(self):
        """La promessa onesta: qui non si riscrive, si accoda.

        L'unica trasformazione ammessa e' la normalizzazione degli spazi bianchi
        (una nuova riga dentro una condizionatura di CLIP non e' una richiesta, e'
        un a capo). Niente tagli di parole, niente "prompt sicuro".
        """
        idea = ("un faro nella tempesta, lunga esposizione, dettagli visivi concreti; "
                "colori saturi e un soggetto scomodo per un filtro morale")
        scope = _load()
        scope["_channel_immagine"](_contesto(f"!immagine {idea}"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(scope["image_queue"].job[0]["prompt"], idea)

    def test_il_prompt_intero_finisce_nel_nodo_che_condiziona_clip(self):
        """Fine della catena: quello che si legge nel grafo e' quello che si e' scritto."""
        idea = "una donna in armatura consumata, sguardo diretto, luce laterale dura"
        scope = _load()
        scope["_channel_immagine"](_contesto(f"!immagine {idea}"),
                                   channel="telegram", destinazione="1")
        job = scope["image_queue"].job[0]
        grafo = workflow(job)
        self.assertEqual(grafo["452"]["inputs"]["prompt"], idea)
        self.assertEqual(grafo["452"]["inputs"]["negative_prompt"], job["negativo"])

    def test_le_spazi_bianchi_si_normalizzano_e_il_resto_resta(self):
        scope = _load()
        scope["_channel_immagine"](_contesto("!immagine un   lupo\nbianco"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(scope["image_queue"].job[0]["prompt"], "un lupo bianco")

    def test_un_comando_senza_idea_chiede_cosa_disegnare(self):
        scope = _load()
        risposta = scope["_channel_immagine"](_contesto("!immagine"), channel="telegram",
                                              destinazione="1")
        self.assertIn("Dimmi cosa disegnare", risposta)
        self.assertEqual(scope["image_queue"].job, [])


    def test_la_destinazione_del_canale_finisce_nel_job(self):
        """Senza destinazione l'immagine non ha dove andare: la legge il driver."""
        scope = _load()
        risposta = scope["_channel_immagine"](_contesto("!immagine un gatto"),
                                              channel="telegram", destinazione="")
        self.assertEqual(scope["image_queue"].job[0]["destinazione"], "")
        self.assertIn("non so dove mandartela", risposta)

    def test_la_coda_piena_non_e_un_silenzio(self):
        scope = _load(coda=CodaFinta(errore="coda piena (8 job)"))
        risposta = scope["_channel_immagine"](_contesto("!immagine un gatto"),
                                              channel="telegram", destinazione="1")
        self.assertIn("Non posso adesso", risposta)
        self.assertIn("coda piena", risposta)

    def test_il_job_accodato_finisce_nei_log(self):
        """'perche' non ha disegnato?' deve essere una riga, non un mistero."""
        scope = _load()
        scope["_channel_immagine"](_contesto("!immagine un gatto", autore="Alberto"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(len(scope["_log"]), 1)
        (tipo, sommario), extra = scope["_log"][0]
        self.assertEqual(tipo, "channel")
        self.assertIn("richiesta immagine", sommario)
        self.assertEqual(extra.get("source"), "channel:telegram")


class OperatoreTests(unittest.TestCase):
    """Il freno di risorsa: chi puo' occupare la scheda per un quarto d'ora."""

    def test_con_l_operatore_configurato_chi_non_lo_e_non_accoda(self):
        scope = _load(operator={"alberto"})
        risposta = scope["_channel_immagine"](
            _contesto("!immagine un gatto", autore="tizio"),
            channel="telegram", destinazione="1")
        self.assertIn("chi mi ha costruita", risposta)
        self.assertEqual(scope["image_queue"].job, [])

    def test_l_operatore_si_riconosce_anche_scritto_in_maiuscolo(self):
        scope = _load(operator={"alberto"})
        scope["_channel_immagine"](_contesto("!immagine un gatto", autore="Alberto"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(len(scope["image_queue"].job), 1)

    def test_senza_operatore_il_comando_resta_aperto(self):
        """Il default documentato: la variabile assente NON chiude il comando.

        Non e' una svista da correggere in silenzio — e' la scelta dichiarata in
        `docs/comfyui.md` ("aperto a chiunque sia in chat — una scelta, non un
        caso"). Se un giorno si vuole il contrario si cambiano il codice E il
        documento, insieme.
        """
        scope = _load(operator=())
        scope["_channel_immagine"](_contesto("!immagine un gatto", autore="chiunque"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(len(scope["image_queue"].job), 1)


class RichiestaAParoleTests(unittest.TestCase):
    """Il riconoscitore: regole dichiarate, e silenzio quando non è una richiesta.

    Ogni frase qui sotto è una frase che qualcuno scriverebbe davvero. I negativi
    contano quanto i positivi: un falso positivo non è un fastidio, è un quarto
    d'ora di scheda occupata per un'immagine che nessuno ha chiesto.
    """

    def test_le_forme_comuni_accodano_con_l_idea_giusta(self):
        for frase, idea in (
                ("aurora, mandami una foto di te esplicita", "di te esplicita"),
                ("fammi un disegno di un faro nella tempesta",
                 "di un faro nella tempesta"),
                ("genera un'immagine di una donna in armatura",
                 "di una donna in armatura"),
                ("potresti mandarmi un ritratto con la luce del tramonto",
                 "con la luce del tramonto"),
                ("mi mandi una foto di un lupo bianco?", "di un lupo bianco?"),
                ("disegnami una scena con la neve", "con la neve"),
                ("mi serve un paesaggio al tramonto", "al tramonto"),
                ("aurora voglio una foto notturna della città",
                 "notturna della città")):
            with self.subTest(frase=frase):
                self.assertEqual(richiesta_immagine(frase)["idea"], idea)

    def test_la_regola_che_riconosce_la_frase_ha_un_nome(self):
        """Nei log si legge `via=...`: "perché ha disegnato?" deve restare una frase."""
        for frase, regola in (("mandami una foto", "mandare"),
                              ("puoi mandarmi una foto", "potere-infinito"),
                              ("voglio una foto", "volere")):
            with self.subTest(frase=frase):
                self.assertEqual(richiesta_immagine(frase)["regola"], regola)

    def test_le_preposizioni_dell_idea_restano(self):
        """`di te` e `con un cappello` sono contenuto: toglierli cambia la richiesta."""
        self.assertEqual(richiesta_immagine("mandami una foto di te")["idea"], "di te")
        self.assertEqual(richiesta_immagine("fammi un disegno con un cappello")["idea"],
                         "con un cappello")

    def test_una_richiesta_senza_idea_non_e_un_assenso_vuoto(self):
        esito = richiesta_immagine("mandami una foto")
        self.assertIsNotNone(esito)
        self.assertEqual(esito["idea"], "")

    def test_le_frasi_che_non_sono_richieste_restano_mute(self):
        for frase in ("hai mai fatto una foto di te?",
                      "guardate la foto che ho mandato ieri",
                      "mandami il link della foto",
                      "mandami il numero e poi la foto",
                      "mandami il file",
                      "puoi mandarmi il file",
                      "mi serve il link",
                      "che bella foto!",
                      "mi piace immaginare le torri",
                      "foto", "", None):
            with self.subTest(frase=frase):
                self.assertIsNone(richiesta_immagine(frase))


class RichiestaAParoleCanaleTests(unittest.TestCase):
    """Il collegamento: chi può chiedere a parole, e cosa risponde la stanza."""

    def test_l_operatore_ottiene_il_job_con_l_idea_verbatim(self):
        scope = _load(operator={"alberto"})
        risposta = scope["_channel_immagine"](
            _contesto("mandami una foto di un faro nella tempesta", autore="Alberto"),
            channel="telegram", destinazione="1")
        self.assertEqual(scope["image_queue"].job[0]["prompt"],
                         "di un faro nella tempesta")
        self.assertIn("appena è pronta", risposta)

    def test_chi_non_e_operatore_non_ottiene_niente_e_non_sente_un_no(self):
        """A parole la strada è chiusa in silenzio: la stanza risponde con le sue parole."""
        scope = _load(operator={"alberto"})
        self.assertIsNone(scope["_channel_immagine"](
            _contesto("mandami una foto di te", autore="tizio"),
            channel="telegram", destinazione="1"))
        self.assertEqual(scope["image_queue"].job, [])

    def test_senza_operatore_configurato_la_strada_a_parole_e_chiusa(self):
        """Fail-closed, al contrario del comando: una frase male letta costa 12 minuti."""
        scope = _load(operator=())
        self.assertIsNone(scope["_channel_immagine"](
            _contesto("mandami una foto di te", autore="chiunque"),
            channel="telegram", destinazione="1"))
        self.assertEqual(scope["image_queue"].job, [])

    def test_la_risposta_non_dice_che_la_foto_e_gia_mandata(self):
        """L'immagine la consegna il driver: prima di allora non è vera."""
        scope = _load(operator={"alberto"})
        risposta = scope["_channel_immagine"](
            _contesto("mandami una foto di un gatto", autore="alberto"),
            channel="telegram", destinazione="1")
        minuscolo = risposta.lower()
        for bugia in ("eccola", "ecco la foto", "te l'ho mandata", "inviata", "ecco qui"):
            with self.subTest(bugia=bugia):
                self.assertNotIn(bugia, minuscolo)
        self.assertIn("appena è pronta", risposta)

    def test_il_log_dice_da_quale_regola_e_arrivata(self):
        scope = _load(operator={"alberto"})
        scope["_channel_immagine"](
            _contesto("puoi mandarmi una foto di neve", autore="alberto"),
            channel="telegram", destinazione="1")
        dettaglio = scope["_log"][0][1].get("detail", "")
        self.assertIn("via=potere-infinito", dettaglio)

    def test_il_comando_resta_scritto_come_prima(self):
        """La strada nuova non deve cambiare quella vecchia, nemmeno nei log."""
        scope = _load(operator={"alberto"})
        scope["_channel_immagine"](_contesto("!immagine un faro", autore="alberto"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(scope["image_queue"].job[0]["prompt"], "un faro")
        self.assertIn("via=comando", scope["_log"][0][1].get("detail", ""))


if __name__ == "__main__":
    unittest.main()


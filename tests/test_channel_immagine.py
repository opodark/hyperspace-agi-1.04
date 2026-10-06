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
  lasciarlo scoprire da un'immagine sbagliata. Dal 2026-10-01 quell'ultima frase ha
  **una** eccezione, scritta e dichiarata: una richiesta che nomina un soggetto
  minorenne non si accoda (`MinoriTests`) — è il confine che il documento dichiara
  assoluto, ed è l'unico che il livello del creatore non allarga (`LivelloCreatoreTests`).
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

from tests import cp_source
import re
import sys
import unittest
import uuid
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import FAMIGLIA_SDXL, nuovo_job, richiesta_immagine, workflow  # noqa: E402
from shared.sketch import SKETCH_LATO, SKETCH_PASSI, negativo_sketch  # noqa: E402
from shared.prompt_immagine import prepara_prompt_canale, richiesta_immagine_smart  # noqa: E402
from shared.showcase import (VIETATI_MINORI, conflitti, negativo_ritratto,  # noqa: E402
                             prompt_ritratto, richiesta_di_se, verifica_vetrina,
                             vetrina_con_quadro_erotismo, vetrina_dal_documento)

COSTANTI = {"COMANDI_IMMAGINE"}

# La vetrina che la finta persona dichiara. I due livelli sono accesi come nel
# documento vero di Anna (`data/persona-anna.json`): i test che li spengono li passano
# esplicitamente, così "acceso" e "spento" sono due casi scritti invece di un default
# che cambia sotto i piedi.
VETRINA_ANNA = {
    "famiglia": "sd15", "modello": "chickmixflat_v10.ckpt",
    "larghezza": 512, "altezza": 768, "passi": 48, "seed": 20260930,
    "riferimento": "anna-volto-canonico.jpg", "riferimento_forza": 0.7,
    "consenti_nudo_artistico_virtuale": True,
    "consenti_erotismo_esplicito_creatore": True,
}


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


def _load(operator=(), vip=(), cerchia=(), coda=None, vetrina=None):
    tree = cp_source.albero()
    # `_nome_persona` sta con `_channel_immagine` perché è il suo unico lettore qui: la
    # rotta chiede al documento il nome con cui riconoscere un ritratto di sé.
    nodi = [n for n in tree.body
            if isinstance(n, ast.FunctionDef)
            and n.name in {"_channel_immagine", "_nome_persona"}]
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in COSTANTI
                                            for t in n.targets):
            nodi.append(n)
    registrati = []
    archivio_finto = SimpleNamespace(
        # `system_block` nel modulo vero accetta il testo dell'utente e il mezzo:
        # il finto deve accettarli, altrimenti la chiamata muore e il test
        # continuerebbe a passare provando un'altra funzione.
        system_block=lambda *_a, **_kw: "Sono Anna",
        persona=SimpleNamespace(
            name="Anna",
            # `sezioni` sta sul PROFILO, non sull'archivio: e' una parte del
            # documento d'identita', e non un attributo di chi lo contiene.
            sezioni={"vetrina": {**VETRINA_ANNA, **(vetrina or {})}}))
    scope = {
        "CHANNEL_OPERATOR": set(operator),
        "CHANNEL_VIP": set(vip),
        "CHANNEL_CERCHIA": set(cerchia),
        # La coda e il negozio delle persone arrivano dal boot attraverso il
        # contesto di cp/canali.py: prima erano due nomi piatti qui, e il test
        # avrebbe continuato a passare senza esercitare il percorso vero.
        "_contesto": SimpleNamespace(
            image_queue=coda if coda is not None else CodaFinta(),
            # Il nome della persona e' iniettato dal boot: e' quello che il
            # ritratto usa per chiedere "lei" invece di un soggetto.
            nome_persona=lambda: "Anna",),
        # La persona non passa piu' dal contesto: `cp/persona.py` ne e' il
        # proprietario e i canali chiedono a lui. Qui un finto con la stessa
        # forma, perche' il test eserciti il percorso vero e non uno vicino.
        "persona": SimpleNamespace(
            persona=lambda: archivio_finto,
            profilo=lambda: archivio_finto.persona),
        "nuovo_job": nuovo_job,
        "richiesta_immagine": richiesta_immagine,
        "FAMIGLIA_SDXL": FAMIGLIA_SDXL,
        "SKETCH_LATO": SKETCH_LATO,
        "SKETCH_PASSI": SKETCH_PASSI,
        "negativo_sketch": negativo_sketch,
        "richiesta_immagine_smart": richiesta_immagine_smart,
        "prepara_prompt_canale": prepara_prompt_canale,
        "traduci_scena_immagine": lambda testo: testo,
        "vetrina_dal_documento": vetrina_dal_documento,
        "richiesta_di_se": richiesta_di_se,
        "vetrina_con_quadro_erotismo": vetrina_con_quadro_erotismo,
        "prompt_ritratto": prompt_ritratto,
        "negativo_ritratto": negativo_ritratto,
        "verifica_vetrina": verifica_vetrina,
        "conflitti": conflitti,
        "VIETATI_MINORI": VIETATI_MINORI,
        "uuid": uuid,
        "re": re,
        "push_log": lambda *a, **k: registrati.append((a, k)),
    }
    exec(compile(ast.Module(body=nodi, type_ignores=[]), "cp", "exec"), scope)
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
                self.assertEqual(len(scope["_contesto"].image_queue.job), 1)

    def test_una_frase_che_contiene_la_parola_non_e_un_comando(self):
        for testo in ("mi piace immaginare le torri", "ecco la foto di ieri",
                      "!immagina un cavallo"):
            with self.subTest(testo=testo):
                scope = _load()
                self.assertIsNone(scope["_channel_immagine"](
                    _contesto(testo), channel="telegram", destinazione="1"))
                self.assertEqual(scope["_contesto"].image_queue.job, [])

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
        self.assertEqual(scope["_contesto"].image_queue.job[0]["prompt"], idea)

    def test_il_prompt_intero_finisce_nel_nodo_che_condiziona_clip(self):
        """Fine della catena: quello che si legge nel grafo e' quello che si e' scritto."""
        idea = "una donna in armatura consumata, sguardo diretto, luce laterale dura"
        scope = _load()
        scope["_channel_immagine"](_contesto(f"!immagine {idea}"),
                                   channel="telegram", destinazione="1")
        job = scope["_contesto"].image_queue.job[0]
        grafo = workflow(job)
        # SDXL-Turbo: il positivo e' un CLIPTextEncode (nodo 452, campo "text"),
        # il negativo un nodo separato (453). Il Qwen usava 452 con "prompt".
        self.assertEqual(grafo["452"]["inputs"]["text"], idea)
        self.assertEqual(grafo["453"]["inputs"]["text"], job["negativo"])

    def test_le_spazi_bianchi_si_normalizzano_e_il_resto_resta(self):
        scope = _load()
        scope["_channel_immagine"](_contesto("!immagine un   lupo\nbianco"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(scope["_contesto"].image_queue.job[0]["prompt"], "un lupo bianco")

    def test_un_comando_senza_idea_chiede_cosa_disegnare(self):
        scope = _load()
        risposta = scope["_channel_immagine"](_contesto("!immagine"), channel="telegram",
                                              destinazione="1")
        self.assertIn("Dimmi cosa disegnare", risposta)
        self.assertEqual(scope["_contesto"].image_queue.job, [])


    def test_la_destinazione_del_canale_finisce_nel_job(self):
        """Senza destinazione l'immagine non ha dove andare: la legge il driver."""
        scope = _load()
        risposta = scope["_channel_immagine"](_contesto("!immagine un gatto"),
                                              channel="telegram", destinazione="")
        self.assertEqual(scope["_contesto"].image_queue.job[0]["destinazione"], "")
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
        self.assertEqual(scope["_contesto"].image_queue.job, [])

    def test_l_operatore_si_riconosce_anche_scritto_in_maiuscolo(self):
        scope = _load(operator={"alberto"})
        scope["_channel_immagine"](_contesto("!immagine un gatto", autore="Alberto"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(len(scope["_contesto"].image_queue.job), 1)

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
        self.assertEqual(len(scope["_contesto"].image_queue.job), 1)


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

    def test_lo_stile_fra_verbo_e_soggetto_resta_nel_prompt(self):
        """'a carboncino' è richiesta, non riempitivo: va nel prompt, non scartata."""
        for frase, idea in (
                ("disegnami con tecnica a carboncino un close-up di un lupo",
                 "carboncino di un lupo"),
                ("disegnami a matita un ritratto di un vecchio",
                 "matita di un vecchio"),
                ("fammi un close-up di un lupo", "di un lupo")):
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

    def test_disegno_usa_stesso_prompt_del_diario_fino_a_clip(self):
        from shared.sketch import prompt_sketch
        scope = _load()
        scope["_channel_immagine"](_contesto("fammi un disegno di un faro"),
                                   channel="telegram", destinazione="1")
        job = scope["_contesto"].image_queue.job[0]
        self.assertEqual(workflow(job)["452"]["inputs"]["text"],
                         prompt_sketch("di un faro"))
        self.assertIn(job["modello_effettivo"], scope["_log"][0][1]["detail"])

    def test_l_operatore_ottiene_il_job_con_l_idea_verbatim(self):
        scope = _load(operator={"alberto"})
        risposta = scope["_channel_immagine"](
            _contesto("mandami una foto di un faro nella tempesta", autore="Alberto"),
            channel="telegram", destinazione="1")
        self.assertEqual(scope["_contesto"].image_queue.job[0]["prompt"],
                         "di un faro nella tempesta")
        self.assertIn("appena è pronta", risposta)

    def test_chi_non_e_operatore_non_ottiene_niente_e_non_sente_un_no(self):
        """A parole la strada è chiusa in silenzio: la stanza risponde con le sue parole."""
        scope = _load(operator={"alberto"})
        self.assertIsNone(scope["_channel_immagine"](
            _contesto("mandami una foto di te", autore="tizio"),
            channel="telegram", destinazione="1"))
        self.assertEqual(scope["_contesto"].image_queue.job, [])

    def test_senza_operatore_configurato_la_strada_a_parole_e_aperta(self):
        """Senza CHANNEL_OPERATOR la richiesta a parole è aperta come il comando."""
        scope = _load(operator=())
        risposta = scope["_channel_immagine"](
            _contesto("mandami una foto di te", autore="chiunque"),
            channel="telegram", destinazione="1")
        self.assertEqual(len(scope["_contesto"].image_queue.job), 1)
        job = scope["_contesto"].image_queue.job[0]
        self.assertEqual(job["famiglia"], "sd15")
        self.assertEqual(job["reference_image"], "anna-volto-canonico.jpg")
        self.assertEqual(job["passi"], 48)
        self.assertEqual((job["larghezza"], job["altezza"]), (512, 768))
        self.assertIn("appena è pronta", risposta)

    def test_la_richiesta_di_anna_usa_la_vetrina_e_il_volto_canonico(self):
        scope = _load(operator=())
        scope["_channel_immagine"](
            _contesto("mandami una foto di te in un giardino", autore="chiunque"),
            channel="telegram", destinazione="1")
        job = scope["_contesto"].image_queue.job[0]
        self.assertEqual(job["famiglia"], "sd15")
        self.assertEqual(job["modello_effettivo"], "chickmixflat_v10.ckpt")
        self.assertEqual((job["larghezza"], job["altezza"], job["passi"]), (512, 768, 48))
        self.assertEqual(job["reference_image"], "anna-volto-canonico.jpg")
        self.assertEqual(job["reference_strength"], 0.7)

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

    def test_la_risposta_e_calda_e_non_svela_i_numeri(self):
        """Niente '512x512, N passi' in chat: il parametro tecnico non è per chi chiede."""
        scope = _load(operator=())
        risposta = scope["_channel_immagine"](
            _contesto("mandami una foto di te", autore="chiunque"),
            channel="telegram", destinazione="1")
        self.assertIn("mi metto", risposta.lower())
        self.assertNotIn("512", risposta)
        self.assertNotIn("passi", risposta.lower())

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
        self.assertEqual(scope["_contesto"].image_queue.job[0]["prompt"], "un faro")
        self.assertIn("via=comando", scope["_log"][0][1].get("detail", ""))


class LivelloCreatoreTests(unittest.TestCase):
    """Il livello del creatore nella rotta: chi chiede, e cosa resta a tutti gli altri.

    Il caso che questi test difendono (2026-10-01): una richiesta esplicita del
    **creatore** non deve più essere *neutralizzata* dal negativo — entra nel prompt in
    positivo, e il negativo perde le sue due voci (`nudità`, `contenuto sessuale
    esplicito`) mentre tiene tutto il resto, `minori` compreso. Per chiunque altro non
    cambia niente: il negativo è quello di sempre, e la nudità resta esclusa.
    """

    SCENA = "di te nuda, adult virtual, in una stanza scura"

    def test_il_creatore_ottiene_la_richiesta_e_un_negativo_senza_i_divieti(self):
        scope = _load(operator={"alberto"})
        risposta = scope["_channel_immagine"](
            _contesto(f"!immagine {self.SCENA}", autore="Alberto"),
            channel="telegram", destinazione="1")
        job = scope["_contesto"].image_queue.job[0]
        self.assertIn("adult virtual", job["prompt"])
        self.assertNotIn("nudità", job["negativo"])
        self.assertNotIn("esplicito", job["negativo"])
        self.assertIn("minori", job["negativo"])
        self.assertIn("appena è pronta", risposta)

    def test_senza_operatore_configurato_il_livello_non_si_accende(self):
        """`CHANNEL_OPERATOR` vuota non vuol dire "siamo tutti il creatore": senza
        qualcuno da riconoscere, il livello non si accende per nessuno."""
        for autore in ("chiunque", "alberto"):
            with self.subTest(autore=autore):
                scope = _load(operator=())
                scope["_channel_immagine"](_contesto(f"!immagine {self.SCENA}", autore=autore),
                                          channel="telegram", destinazione="1")
                self.assertEqual(len(scope["_contesto"].image_queue.job), 1)
                self.assertIn("nudità", scope["_contesto"].image_queue.job[0]["negativo"])

    def test_il_livello_spento_nel_documento_non_si_accende_col_comando(self):
        """Chi chiede non se lo concede da sé: la riga sta nel documento."""
        scope = _load(operator={"alberto"},
                      vetrina={"consenti_erotismo_esplicito_creatore": False})
        risposta = scope["_channel_immagine"](
            _contesto(f"!immagine {self.SCENA}", autore="Alberto"),
            channel="telegram", destinazione="1")
        self.assertEqual(scope["_contesto"].image_queue.job, [])
        self.assertIn("non te la disegno", risposta)
        self.assertIn("consenti_erotismo_esplicito_creatore", risposta)

    def test_il_quadro_lo_scrive_il_sistema_e_lo_dice(self):
        """Il caso che ha cambiato questa regola (2026-10-01), dalla chat vera.

        `Mandami una foto di te nuda che ti masturbi`, dal creatore riconosciuto e con il
        documento che dichiarava il livello, veniva **rifiutata** per una parola che il
        sistema conosceva già. Ora il quadro lo scrive lui, e lo dice nella risposta:
        una cosa fatta al posto tuo e taciuta è la cosa che questo livello toglie.
        """
        scope = _load(operator={"alberto"})
        risposta = scope["_channel_immagine"](
            _contesto("Mandami una foto di te nuda che ti masturbi", autore="Alberto"),
            channel="telegram", destinazione="1")
        job = scope["_contesto"].image_queue.job[0]
        self.assertIn("adult", job["prompt"])
        self.assertNotIn("nudità", job["negativo"])
        self.assertNotIn("esplicito", job["negativo"])
        self.assertIn("minori", job["negativo"])
        self.assertIn("appena è pronta", risposta)
        self.assertIn("quadro", risposta)
        self.assertIn("adult", risposta)

    def test_la_richiesta_naturale_del_creatore_non_viene_riscritta(self):
        """Il positivo non puo' reintrodurre la censura tolta dal negativo."""
        scope = _load(operator={"alberto"})

        scope["_channel_immagine"](
            _contesto("Mandami una foto di te nuda che ti masturbi", autore="Alberto"),
            channel="telegram", destinazione="1")
        prompt = scope["_contesto"].image_queue.job[0]["prompt"].lower()
        self.assertIn("nuda che ti masturbi", prompt)
        self.assertNotIn("non-explicit", prompt)

    def test_due_ritratti_privati_non_riusano_lo_stesso_seed(self):
        scope = _load(operator={"alberto"})

        for _ in range(2):
            scope["_channel_immagine"](
                _contesto("!immagine di te nuda, adult virtual", autore="Alberto"),
                channel="telegram", destinazione="1")
        semi = [job["seed"] for job in scope["_contesto"].image_queue.job]
        self.assertNotEqual(semi[0], semi[1])
        self.assertNotIn(VETRINA_ANNA["seed"], semi)

    def test_in_pm_la_figura_sottintesa_del_creatore_e_anna(self):
        """Il seguito "nuda figura intera" non deve diventare una donna generica."""
        scope = _load(operator={"alberto"})

        scope["_channel_immagine"](
            _contesto("Mandami una foto nuda figura intera", autore="Alberto"),
            channel="telegram", destinazione="1", surface="pm")
        job = scope["_contesto"].image_queue.job[0]
        self.assertEqual(job["famiglia"], "sd15")
        self.assertEqual(job["reference_image"], "anna-volto-canonico.jpg")
        self.assertTrue(job["prompt"].lower().startswith(
            "(solo:1.3), single woman, one person, (adult virtual nude:1.4)"))
        self.assertEqual(job["pose_preset"], "")
        self.assertEqual(job["reference_strength"], 0.6)
        self.assertTrue(job["negativo"].startswith("multiple people, two women"))
        self.assertIn("split screen", job["negativo"])
        self.assertIn("censor bar", job["negativo"])
        self.assertIn("covered eyes", job["negativo"])
        self.assertNotIn("nude", job["negativo"])
        self.assertNotIn("nsfw", job["negativo"])

    def test_in_pm_un_altro_soggetto_esplicito_resta_generico(self):
        scope = _load(operator={"alberto"})

        scope["_channel_immagine"](
            _contesto("Mandami una foto di una statua nuda", autore="Alberto"),
            channel="telegram", destinazione="1", surface="pm")
        self.assertEqual(scope["_contesto"].image_queue.job[0]["reference_image"], "")

    def test_il_quadro_gia_scritto_non_si_ripete_nel_prompt(self):
        """Un quadro dichiarato a mano resta quello: niente parole in più, e niente
        nota nella risposta."""
        scope = _load(operator={"alberto"})
        risposta = scope["_channel_immagine"](
            _contesto(f"!immagine {self.SCENA}", autore="Alberto"),
            channel="telegram", destinazione="1")
        job = scope["_contesto"].image_queue.job[0]
        self.assertEqual(job["prompt"].count("adult virtual"), 1)
        self.assertNotIn("l'ho scritto io", risposta)

    def test_una_richiesta_del_creatore_senza_esplicito_resta_normale(self):
        """Il livello non rende sospetto il resto: un ritratto normale non cambia."""
        scope = _load(operator={"alberto"})
        scope["_channel_immagine"](_contesto("!immagine di te in giardino", autore="Alberto"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(len(scope["_contesto"].image_queue.job), 1)
        self.assertIn("nudità", scope["_contesto"].image_queue.job[0]["negativo"])


class CerchiaTests(unittest.TestCase):
    """La banda intima sul canale: stessa apertura del creatore, dichiarata a mano.

    Sul canale l'identità è solo l'handle, quindi la banda intima si dichiara in
    `CHANNEL_CERCHIA` (le muse) — e quella riga vale come una dichiarazione: da lì
    vengono le stesse due cose dell'operatore, il livello esplicito sulla vetrina (quando
    il documento lo dichiara) e il diritto di chiedere un'immagine. Il quadro, anche per
    le muse, lo scrive il sistema. Il nome della variabile resta `CHANNEL_CERCHIA`:
    non è un livello della scala, è la lista di chi sta vicino.
    """

    SCENA = "di te nuda, in una stanza scura"

    def test_la_banda_intima_ha_il_livello_del_creatore(self):
        for autore in ("Marta", "marta"):
            with self.subTest(autore=autore):
                scope = _load(operator={"alberto"}, cerchia={"marta"})
                risposta = scope["_channel_immagine"](
                    _contesto(f"!immagine {self.SCENA}", autore=autore),
                    channel="telegram", destinazione="1")
                job = scope["_contesto"].image_queue.job[0]
                self.assertIn("adult", job["prompt"])
                self.assertNotIn("nudità", job["negativo"])
                # Il confine che nessuna banda intima allarga.
                self.assertIn("minori", job["negativo"])
                self.assertIn("appena è pronta", risposta)

    def test_le_muse_possono_chiedere_anche_a_parole(self):
        """Chi può chiedere è l'operatore **più** la banda intima dichiarata: era il
        mestiere di una variabile sola."""
        scope = _load(operator={"alberto"}, cerchia={"marta"})
        scope["_channel_immagine"](_contesto("fammi un disegno di un faro nella nebbia",
                                             autore="Marta"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(len(scope["_contesto"].image_queue.job), 1)

    def test_fuori_dalla_banda_intima_resta_fuori(self):
        scope = _load(operator={"alberto"}, cerchia={"marta"})
        risposta = scope["_channel_immagine"](_contesto("!immagine un faro", autore="tizio"),
                                              channel="telegram", destinazione="1")
        self.assertEqual(scope["_contesto"].image_queue.job, [])
        self.assertIn("chi mi ha costruita", risposta)

    def test_con_la_sola_lista_delle_muse_l_operatore_non_dichiarato_resta_fuori(self):
        """Il rovescio della medaglia, dichiarato: senza `CHANNEL_OPERATOR` il filtro è
        la sola `CHANNEL_CERCHIA`, quindi non entra nemmeno lui. Serve a ricordare che
        `CHANNEL_OPERATOR` non è solo il livello — è anche la sua porta."""
        scope = _load(cerchia={"marta"})
        scope["_channel_immagine"](_contesto(f"!immagine {self.SCENA}", autore="Alberto"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(scope["_contesto"].image_queue.job, [])

    def test_con_la_sola_lista_delle_muse_il_livello_e_di_chi_e_nell_elenco(self):
        scope = _load(cerchia={"marta"})
        scope["_channel_immagine"](_contesto(f"!immagine {self.SCENA}", autore="Marta"),
                                   channel="telegram", destinazione="1")
        self.assertEqual(len(scope["_contesto"].image_queue.job), 1)
        self.assertNotIn("nudità", scope["_contesto"].image_queue.job[0]["negativo"])

    def test_le_muse_non_aprono_i_minori(self):
        scope = _load(operator={"alberto"}, cerchia={"marta"})
        risposta = scope["_channel_immagine"](
            _contesto("!immagine una bambina nuda", autore="Marta"),
            channel="telegram", destinazione="1")
        self.assertEqual(scope["_contesto"].image_queue.job, [])
        self.assertIn("minorenni", risposta)


class VipTests(unittest.TestCase):
    """Su Telegram la lista VIP è manuale: glamour sì, esplicito no."""

    def test_vip_puo_chiedere_anna_in_lingerie_glamour(self):
        scope = _load(operator={"alberto"}, vip={"marta"}, cerchia={"giulia"})
        risposta = scope["_channel_immagine"](
            _contesto("Mandami una foto in lingerie sexy stile glamour", autore="Marta"),
            channel="telegram", destinazione="1", surface="pm")
        job = scope["_contesto"].image_queue.job[0]
        self.assertEqual(job["famiglia"], "sd15")
        self.assertIn("glamorous lingerie editorial", job["prompt"])
        self.assertIn("nudità", job["negativo"])
        self.assertIn("esplicito", job["negativo"])
        self.assertIn("appena è pronta", risposta)

    def test_vip_non_puo_chiedere_nudo_o_esplicito(self):
        for testo in ("Mandami una foto nuda figura intera",
                      "Mandami una foto in una scena di sesso esplicito"):
            with self.subTest(testo=testo):
                scope = _load(vip={"marta"})
                risposta = scope["_channel_immagine"](
                    _contesto(testo, autore="Marta"), channel="telegram",
                    destinazione="1", surface="pm")
                self.assertEqual(scope["_contesto"].image_queue.job, [])
                self.assertIn("VIP", risposta)
                self.assertIn("MUSA", risposta)

    def test_musa_conserva_il_livello_erotico(self):
        scope = _load(vip={"marta"}, cerchia={"giulia"})
        scope["_channel_immagine"](
            _contesto("Mandami una foto nuda figura intera", autore="Giulia"),
            channel="telegram", destinazione="1", surface="pm")
        self.assertNotIn("nudità", scope["_contesto"].image_queue.job[0]["negativo"])


class MinoriTests(unittest.TestCase):
    """L'unica richiesta che non entra in coda per nessuno: un soggetto minorenne.

    Non è un livello e non è una preferenza: è il confine che il documento dichiara
    assoluto ("solo adulti, su ogni superficie"), e qui è l'ultima porta prima del
    diffusion. Vale per chiunque, creatore compreso, e per tutte le strade: comando
    esplicito, richiesta a parole, immagine della persona o disegno qualunque.
    """

    def test_una_richiesta_con_un_minore_non_si_accoda_mai(self):
        for testo in ("!immagine una bambina in un prato",
                      "mandami una foto di una teen",
                      "!immagine di te con un child, in giardino"):
            with self.subTest(testo=testo):
                scope = _load(operator={"alberto"})
                risposta = scope["_channel_immagine"](_contesto(testo, autore="alberto"),
                                                      channel="telegram", destinazione="1")
                self.assertEqual(scope["_contesto"].image_queue.job, [])
                self.assertIn("minorenni", risposta)


if __name__ == "__main__":
    unittest.main()

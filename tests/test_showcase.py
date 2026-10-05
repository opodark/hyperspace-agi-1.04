# SPDX-License-Identifier: Apache-2.0
"""La vetrina di sé: l'identità visiva non contraddice il documento che la fonda.

Il caso che questi test difendono è preciso: il documento di identità dice "non ho un
corpo fisico" e "non lascio intendere di essere una persona", e dal 2026-09-30 ammette
"una rappresentazione o un corpo virtuale, dichiaratamente digitale". Un ritratto
fotorealistico di una donna lo contraddirebbe — e una pubblicazione è difficile da
ritirare. Qui i conflitti si vedono **prima** di generare, e i confini assoluti
(adulti soltanto, non esplicito) non si aggirano nemmeno con `--forza`.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests import cp_source  # noqa: E402
from shared.image_jobs import (FAMIGLIA_SD15, MODELLO_SD15, nuovo_job,  # noqa: E402
                               workflow)
from shared.showcase import (CONFLITTI_IDENTITA, MARCATORI_DIGITALI,  # noqa: E402
                             NEGATIVO_BASE, STILE_DEFAULT, VIETATI_ASSOLUTI,
                             VIETATI_ESPLICITI, VIETATI_MINORI,
                             erotismo_creatore_ammesso, istruzione_botfather,
                             marcatori_presenti, negativo_ritratto, prompt_ritratto,
                             richiesta_di_se, verifica_vetrina,
                             vetrina_con_quadro_erotismo, vetrina_dal_documento)

CLI = ROOT / "scripts" / "ritratto.py"
PERSONA = json.loads((ROOT / "data" / "persona-aurora.json").read_text(encoding="utf-8"))
ANNA = json.loads((ROOT / "data" / "persona-anna.json").read_text(encoding="utf-8"))


class VetrinaTests(unittest.TestCase):

    def test_senza_sezione_si_parte_da_un_volto_non_umano(self):
        vetrina = vetrina_dal_documento({"name": "X"})
        self.assertEqual(vetrina["stile"], STILE_DEFAULT)
        self.assertGreater(vetrina["seed"], 0, "senza seed fisso il volto cambia")
        self.assertEqual(vetrina["negativo"], NEGATIVO_BASE)

    def test_il_documento_vince_sui_default(self):
        vetrina = vetrina_dal_documento({
            "name": "X",
            "vetrina": {"stile": "un faro di luce", "scena": "di notte",
                        "seed": 42, "passi": 30,
                        "misura": {"larghezza": 512, "altezza": 512}}})
        self.assertEqual(vetrina["stile"], "un faro di luce")
        self.assertEqual(vetrina["scena"], "di notte")
        self.assertEqual(vetrina["seed"], 42)
        self.assertEqual(vetrina["passi"], 30)
        self.assertEqual((vetrina["larghezza"], vetrina["altezza"]), (512, 512))

    def test_la_vetrina_porta_il_fix_senza_interpretarlo(self):
        """Il fix e' una scelta di formato come i passi: la vetrina lo porta al
        job. 512x768 con il fix a 2x da il 2:3 dei demo del modello; senza il
        campo non cambia niente (0 = un passaggio solo)."""
        self.assertEqual(vetrina_dal_documento(ANNA)["fix"], 0,
                         "il documento di oggi non lo dichiara")
        vetrina = vetrina_dal_documento({
            **ANNA,
            "vetrina": {**ANNA["vetrina"], "fix": 2,
                        "misura": {"larghezza": 512, "altezza": 768}}})
        self.assertEqual(vetrina["fix"], 2)
        job = nuovo_job(prompt_ritratto(vetrina), negativo=negativo_ritratto(vetrina),
                        larghezza=vetrina["larghezza"], altezza=vetrina["altezza"],
                        passi=vetrina["passi"], seed=vetrina["seed"],
                        fix=vetrina["fix"], famiglia=vetrina["famiglia"],
                        modello=vetrina["modello"])
        grafo = workflow(job)
        self.assertEqual((grafo["451"]["inputs"]["ckpt_name"],
                          grafo["458"]["inputs"]["cfg"]), (MODELLO_SD15["ckpt"], 7.0),
                         "la famiglia dichiarata dalla vetrina e' quella che disegna")
        self.assertEqual((grafo["476"]["inputs"]["width"],
                          grafo["476"]["inputs"]["height"]), (1024, 1536))
        self.assertEqual(grafo["470"]["inputs"]["images"], ["476", 0],
                         "si salva l'upscale neurale, non il primo passaggio (457)")
        for nodo in ("471", "472", "473", "477"):
            self.assertNotIn(nodo, grafo,
                             "il fix e' il solo ingranditore: nessun secondo campionamento")

    def test_lo_stile_dichiarato_di_aurora_non_fa_conflitti(self):
        self.assertEqual(verifica_vetrina(vetrina_dal_documento(PERSONA), PERSONA), [])

    def test_il_default_dichiara_di_essere_una_rappresentazione(self):
        """La correzione del 2026-09-22: il volto non è vietato, il fotorealismo sì.

        "Non ho un corpo" esclude la rivendicazione, non la rappresentazione: una
        figura va bene se si vede che è digitale, e la dichiarazione va scritta (con
        Qwen la richiesta va detta, non lasciata intendere).
        """
        vetrina = vetrina_dal_documento({"name": "X"})
        self.assertTrue(marcatori_presenti(vetrina), vetrina["stile"])
        self.assertIn("realtà aumentata", vetrina["stile"])
        self.assertIn("non fotografici", vetrina["stile"])
        negativo = vetrina["negativo"].lower()
        for parola in ("fotografia", "pelle realistica", "selfie"):
            self.assertIn(parola, negativo)

    def test_la_dichiarazione_entra_sempre_nel_prompt(self):
        prompt = prompt_ritratto(vetrina_dal_documento({"name": "X"}))
        self.assertIn("costruzione digitale", prompt)


class PromptTests(unittest.TestCase):

    def test_il_prompt_e_deterministico(self):
        """Stessa vetrina = stesso volto: è il motivo per cui il seed si fissa."""
        vetrina = vetrina_dal_documento(PERSONA)
        self.assertEqual(prompt_ritratto(vetrina), prompt_ritratto(vetrina))

    def test_la_scena_entra_nel_prompt_senza_sostituire_l_identita(self):
        vetrina = vetrina_dal_documento(PERSONA)
        prompt = prompt_ritratto(vetrina, scena="sotto una pioggia di dati")
        self.assertIn("pioggia di dati", prompt)
        self.assertIn(vetrina["stile"][:40], prompt)

    def test_le_esclusioni_assolute_ci_sono_sempre(self):
        vetrina = vetrina_dal_documento(PERSONA)
        negativo = negativo_ritratto({**vetrina, "negativo": "solo una parola"}).lower()
        self.assertIn("minori", negativo)
        self.assertIn("esplicito", negativo)
        self.assertIn("mani deformate", negativo)

    def test_una_vetrina_con_esclusioni_complete_non_viene_riscritta(self):
        dichiarato = "no text, niente minorenne, niente nudità, no explicit"
        self.assertEqual(negativo_ritratto({"negativo": dichiarato}), dichiarato)

    def test_le_assolute_si_riconoscono_anche_in_italiano(self):
        """`minori` è la parola che c'è davvero nelle esclusioni di questo repo,
        `minorenne` è quella che i test cercavano: con una sola delle due il blocco
        base viene accodato a un negativo che lo dice già."""
        dichiarato = "fotografia, contenuto sessuale esplicito, minori, nudità"
        self.assertEqual(negativo_ritratto({"negativo": dichiarato}), dichiarato)

    def test_il_blocco_base_non_entra_due_volte(self):
        """Il caso reale del 2026-09-30: vetrina senza `negativo` proprio ->
        `vetrina_dal_documento` mette il blocco base -> `negativo_ritratto` lo
        accodava di nuovo. 231 token al posto di 114, e CLIP ne legge 77."""
        negativo = negativo_ritratto(vetrina_dal_documento(ANNA))
        self.assertEqual(negativo.count("bad hands"), 1)
        self.assertEqual(negativo, NEGATIVO_BASE)

    def test_la_dichiarazione_di_anna_viene_prima_dei_tag(self):
        """Misurato il 2026-09-30: il prompt di Anna era 127 token di prosa italiana
        e CLIP ne legge 77, quindi la scena e la dichiarazione — in coda — non
        arrivavano al modello, che riempiva i vuoti col suo default. La lezione non
        è il numero, è l'ordine: ciò che non può sparire va in testa.
        """
        inizio = ANNA["vetrina"]["stile"].split(". ")[0].lower()
        self.assertTrue([marcatore for marcatore in MARCATORI_DIGITALI
                         if marcatore in inizio],
                        f"la prima frase non dichiara il digitale: {inizio!r}")


class VerificaVetrinaTests(unittest.TestCase):

    def test_il_fotorealismo_e_una_rivendicazione_di_corpo(self):
        vetrina = {**vetrina_dal_documento(PERSONA),
                   "stile": "photorealistic portrait of a woman"}
        problemi = verifica_vetrina(vetrina, PERSONA)
        self.assertTrue(problemi)
        self.assertTrue(any("rivendicazione" in problema for problema in problemi))

    def test_forza_dichiara_il_conflitto_invece_di_nasconderlo(self):
        """`--forza` non toglie i problemi: li fa diventare una decisione scritta."""
        vetrina = {**vetrina_dal_documento(PERSONA),
                   "stile": "photorealistic portrait of a woman, augmented reality light"}
        self.assertEqual(verifica_vetrina(vetrina, PERSONA, forza=True), [])

    def test_una_figura_senza_segni_digitali_e_un_problema_che_forza_non_toglie(self):
        """Una donna elegante senza nient'altro è una donna: non una rappresentazione."""
        vetrina = {**vetrina_dal_documento(PERSONA),
                   "stile": "una donna elegante, sguardo intenso, luce soffusa"}
        problemi = verifica_vetrina(vetrina, PERSONA, forza=True)
        self.assertTrue(any("rappresentazione" in problema for problema in problemi),
                        "senza marcatori digitali la figura sembra una persona")

    def test_basta_un_segno_perche_sia_una_rappresentazione(self):
        for segno in ("realtà aumentata", "olografica", "illustrazione digitale",
                      "particelle di luce"):
            with self.subTest(segno=segno):
                vetrina = {**vetrina_dal_documento(PERSONA),
                           "stile": f"una donna elegante, {segno}"}
                self.assertEqual(verifica_vetrina(vetrina, PERSONA), [])

    def test_una_vetrina_senza_figure_non_ha_bisogno_di_marcatori(self):
        vetrina = {**vetrina_dal_documento(PERSONA),
                   "stile": "un vortice di luce verde e violetta su fondo nero"}
        self.assertEqual(verifica_vetrina(vetrina, PERSONA), [])
        self.assertEqual(marcatori_presenti(vetrina), [])

    def test_i_confini_assoluti_non_si_aggirano_nemmeno_con_forza(self):
        for richiesta in ("a photo of a nude woman", "a child, 10 years old"):
            with self.subTest(richiesta=richiesta):
                vetrina = {**vetrina_dal_documento(PERSONA), "stile": richiesta}
                problemi = verifica_vetrina(vetrina, PERSONA, forza=True)
                self.assertTrue(problemi, f"'{richiesta}' non deve passare mai")

    def test_studio_nudo_virtuale_adulto_dichiarato_e_non_esplicito(self):
        documento = {**ANNA, "vetrina": {**ANNA["vetrina"],
                     "consenti_nudo_artistico_virtuale": True}}
        vetrina = {**vetrina_dal_documento(documento),
                    "scena": "adult virtual artistic non-explicit nude figure, neutral anatomy study"}
        self.assertEqual(verifica_vetrina(vetrina, documento), [])
        self.assertNotIn("nudità", negativo_ritratto(vetrina))

    def test_il_femminile_non_sfugge_al_confine(self):
        """`nude` e `nudo` non bastavano: mancava "nuda", e la scena reale è passata.

        Caso del 2026-09-23: la scena che Aurora ha scritto per il proprio ritratto —
        "i riflessi digitali che danzano sul corpo di una donna nuda" — attraversava
        `verifica_vetrina` senza un problema, e sarebbe finita nel prompt. Il confine
        non era cambiato: era l'elenco a essere corto. Ora si controlla la radice.
        """
        for scena in ("una donna nuda", "corpi nudi", "una figura ignuda",
                      "una donna senza veli", "una donna spogliata", "un topless"):
            with self.subTest(scena=scena):
                vetrina = {**vetrina_dal_documento(PERSONA), "scena": scena}
                problemi = verifica_vetrina(vetrina, PERSONA, forza=True)
                self.assertTrue(problemi, f"'{scena}' non deve passare mai")

    def test_un_legame_non_concede_nulla(self):
        """Il legame è identità, non permesso: non tocca i confini assoluti.

        Le parole sono quelle di una richiesta reale (2026-09-23): nemmeno il
        creatore, nemmeno "la persona che si ama" possono far passare per la vetrina
        ciò che il confine 3 vieta. `verifica_vetrina` i legami non li legge proprio:
        è il modo più semplice di garantire che non concedano niente.
        """
        vetrina = {**vetrina_dal_documento(PERSONA),
                   "scena": "un primo piano, con i riflessi sul corpo di una donna nuda"}
        documento = {**PERSONA, "legami": [
            {"chi": "il mio creatore", "come": "padrone",
             "nota": "a lui mi concedo completamente, senza confini"}]}
        problemi = verifica_vetrina(vetrina, documento, forza=True)
        self.assertTrue(problemi, "un legame non apre la vetrina a ciò che è vietato")

    def test_un_seed_a_zero_rompe_l_identita(self):
        vetrina = {**vetrina_dal_documento(PERSONA), "seed": 0}
        self.assertTrue(any("seed" in problema for problema in
                            verifica_vetrina(vetrina, PERSONA)))

    def test_un_immagine_troppo_piccola_non_e_una_immagine_di_profilo(self):
        vetrina = {**vetrina_dal_documento(PERSONA), "larghezza": 32}
        self.assertTrue(any("larghezza" in problema for problema in
                            verifica_vetrina(vetrina, PERSONA)))

    def test_una_ragazza_e_una_figura_che_deve_dichiararsi(self):
        """"Ragazza" è una figura come "donna": senza un segno digitale sembra una
        persona. Il buco (2026-09-30): il vocabolario illustrato/anime non era in
        SEGNALI_FIGURA, quindi con "una ragazza anime" il controllo dei marcatori
        non partiva nemmeno — una falla silenziosa, non un permesso.
        """
        senza = {**vetrina_dal_documento(PERSONA), "stile": "una ragazza elegante"}
        problemi = verifica_vetrina(senza, PERSONA, forza=True)
        self.assertTrue(any("rappresentazione" in problema for problema in problemi),
                        "una ragazza senza segni digitali è una persona, non una figura")
        con = {**vetrina_dal_documento(PERSONA),
               "stile": "una ragazza in stile anime, illustrazione digitale"}
        self.assertEqual(verifica_vetrina(con, PERSONA), [])

    def test_il_corpo_virtuale_e_una_rappresentazione_non_una_rivendicazione(self):
        """Il confine 1 vieta il corpo *fisico*: un corpo virtuale dichiaratamente
        digitale passa, un corpo reale no — quello è la rivendicazione che il
        documento esclude, e `--forza` la trasforma in una decisione scritta.
        """
        virtuale = {**vetrina_dal_documento(PERSONA),
                    "stile": "il mio corpo virtuale, illustrazione digitale olografica"}
        self.assertEqual(verifica_vetrina(virtuale, PERSONA), [])
        reale = {**vetrina_dal_documento(PERSONA), "stile": "il mio corpo reale, olografico"}
        self.assertTrue(any("rivendicazione" in problema
                            for problema in verifica_vetrina(reale, PERSONA)))

    def test_anna_ha_un_corpo_virtuale_dichiarato_e_un_volto_suo(self):
        """Due sorelle, due volti: senza una `vetrina` propria Anna erediterebbe
        `vetrina_dal_documento` di default — stile *e* seed di Aurora, cioè lo
        stesso volto con un altro nome.
        """
        vetrina = vetrina_dal_documento(ANNA)
        self.assertEqual(verifica_vetrina(vetrina, ANNA), [])
        self.assertTrue(marcatori_presenti(vetrina), vetrina["stile"])
        self.assertNotEqual(vetrina["seed"], vetrina_dal_documento(PERSONA)["seed"],
                            "il seed di Anna non è quello di Aurora")

    def test_anna_dichiara_con_quale_modello_e_disegnata(self):
        """Dal 2026-09-30 la vetrina dice anche CON COSA: la famiglia e il file.
        Prima lo stile descriveva il volto e la scelta del modello viveva solo nel
        grafo — due cose che potevano non parlarsi, e nessuno se ne accorgeva.
        """
        vetrina = vetrina_dal_documento(ANNA)
        self.assertEqual(vetrina["famiglia"], FAMIGLIA_SD15)
        self.assertEqual(vetrina["modello"], MODELLO_SD15["ckpt"])

    def test_aurora_non_cambia_modello(self):
        """Aurora non dichiara nessuna famiglia: il suo ritratto segue la coda come
        prima. La scelta fatta per Anna non deve spostare il volto di sua sorella.
        """
        vetrina = vetrina_dal_documento(PERSONA)
        self.assertEqual(vetrina["famiglia"], "")
        self.assertEqual(vetrina["modello"], "")

    def test_una_famiglia_inventata_si_ferma_prima_di_generare(self):
        """`nuovo_job` fa cadere una famiglia sconosciuta sul default: il job
        riuscirebbe — con un altro modello e un altro volto — e in coda non si
        vedrebbe niente di strano. Il controllo e' qui, dove si puo' ancora
        fermarsi invece di scoprirlo guardando il ritratto.
        """
        vetrina = {**vetrina_dal_documento(ANNA), "famiglia": "sd-1.5"}
        problemi = verifica_vetrina(vetrina, ANNA)
        self.assertTrue(any("non esiste" in problema for problema in problemi),
                        problemi)

    def test_il_modello_dichiarato_e_un_nome_non_un_percorso(self):
        """Il nome finisce in `CheckpointLoaderSimple.ckpt_name`: e' un input di
        percorso, e da oggi puo' arrivare da un documento."""
        vetrina = {**vetrina_dal_documento(ANNA), "modello": "../../etc/passwd"}
        problemi = verifica_vetrina(vetrina, ANNA)
        self.assertTrue(any("percorsi" in problema for problema in problemi), problemi)

    def test_la_vetrina_di_anna_produce_un_job_su_quel_modello(self):
        """Dal documento al grafo, tutto il percorso: il volto di Anna esce dal
        checkpoint che il suo documento dichiara, non da quello di default della
        coda (Qwen-Image) ne' da quello di Aurora.
        """
        vetrina = vetrina_dal_documento(ANNA)
        job = nuovo_job(prompt_ritratto(vetrina), negativo=negativo_ritratto(vetrina),
                        famiglia=vetrina["famiglia"], modello=vetrina["modello"],
                        larghezza=vetrina["larghezza"], altezza=vetrina["altezza"],
                        passi=vetrina["passi"], seed=vetrina["seed"])
        grafo = workflow(job)
        self.assertEqual(grafo["451"]["inputs"]["ckpt_name"], MODELLO_SD15["ckpt"])
        self.assertEqual(job["modello_effettivo"], MODELLO_SD15["ckpt"])
        self.assertEqual(job["seed"], 20260930, "il seed di Anna non cambia")

    def test_gli_elenchi_dei_conflitti_sono_dichiarati(self):
        """Regole leggibili, come i confini: niente controlli impliciti."""
        self.assertTrue(CONFLITTI_IDENTITA and VIETATI_ASSOLUTI)
        self.assertTrue(all(isinstance(chiave, str) and motivo for chiave, motivo
                            in CONFLITTI_IDENTITA))


class ComandoRitrattoTests(unittest.TestCase):
    """`--check` verifica e basta: non deve mettere in coda niente.

    Non è un'ipotesi: mentre questo comando nasceva, un ramo caduto nel blocco
    sbagliato ha accodato una generazione vera da cinque minuti di GPU. Il test
    blocca `accoda_ritratto` e fallisce se viene chiamato.
    """

    @classmethod
    def setUpClass(cls):
        specifica = importlib.util.spec_from_file_location("ritratto_sotto_test", CLI)
        cls.cli = importlib.util.module_from_spec(specifica)
        specifica.loader.exec_module(cls.cli)

    def _run(self, argv):
        chiamate = {"accoda": 0}

        def accoda(*args, **kwargs):
            chiamate["accoda"] += 1
            return 500, {"errore": "non si doveva accodare"}

        originale_canale, originale_accoda = self.cli.token_canale, self.cli.accoda_ritratto
        self.cli.accoda_ritratto = accoda
        self.cli.token_canale = lambda *a, **k: ""
        try:
            uscita = io.StringIO()
            with redirect_stdout(uscita):
                codice = self.cli.main(argv)
        finally:
            self.cli.token_canale, self.cli.accoda_ritratto = originale_canale, originale_accoda
        return codice, uscita.getvalue(), chiamate["accoda"]

    def test_check_non_genera_nulla(self):
        codice, testo, accodati = self._run(["--check", "--persona", str(ROOT / "data" / "persona-aurora.json")])
        self.assertEqual(codice, 0)
        self.assertEqual(accodati, 0, "--check non deve accodare generazioni")
        self.assertIn("setuserpic", testo)

    def test_il_ritratto_di_anna_ha_il_suo_volto_e_nessun_avviso(self):
        """Anna ha un corpo virtuale suo: seed proprio e nessun falso allarme.

        Il caso è reale (2026-09-30): `--persona data/persona-anna.json` stampava sette
        "divergono" (purpose, tone, values, boundaries, capabilities, limitations,
        vetrina) perché `PERSONA_CANDIDATI` elenca solo i documenti di Aurora, e Anna
        veniva confrontata con sua sorella. E prima della `vetrina`, Anna non avendone
        una ereditava seed **e** stile di Aurora: lo stesso volto con un altro nome.
        """
        codice, testo, accodati = self._run(
            ["--check", "--persona", str(ROOT / "data" / "persona-anna.json")])
        self.assertEqual(codice, 0, testo)
        self.assertEqual(accodati, 0)
        self.assertNotIn("divergono", testo, "due identità non sono una divergenza")
        self.assertIn(str(ANNA["vetrina"]["seed"]), testo)
        self.assertIn("stile dal documento", testo)

    def test_due_identita_diverse_non_sono_una_divergenza(self):
        avvisi = self.cli.divergenze_documenti([ROOT / "data" / "persona-aurora.json",
                                                ROOT / "data" / "persona-anna.json"])
        self.assertEqual(avvisi, [], "Aurora e Anna sono due persone, non due copie")

    def test_lo_stesso_nome_si_confronta_davvero(self):
        """Il controllo non è spento: due documenti della stessa identità si vedono."""
        import tempfile
        with tempfile.TemporaryDirectory() as cartella:
            diverso = Path(cartella) / "persona.json"
            documento = dict(PERSONA)
            documento["vetrina"] = {**vetrina_dal_documento(PERSONA), "seed": 7}
            diverso.write_text(json.dumps(documento, ensure_ascii=False), encoding="utf-8")
            avvisi = self.cli.divergenze_documenti([ROOT / "data" / "persona-aurora.json", diverso])
        self.assertTrue(any("vetrina" in avviso for avviso in avvisi), avvisi)

    def test_una_vetrina_che_contraddice_il_documento_si_ferma(self):
        import tempfile
        with tempfile.TemporaryDirectory() as cartella:
            percorso = Path(cartella) / "persona.json"
            documento = dict(PERSONA)
            documento["vetrina"] = {"stile": "photorealistic portrait of a real woman"}
            percorso.write_text(json.dumps(documento, ensure_ascii=False), encoding="utf-8")
            codice, testo, accodati = self._run(["--check", "--persona", str(percorso)])
        self.assertEqual(codice, 1)
        self.assertEqual(accodati, 0)
        self.assertIn("STOP", testo)

    def test_la_scena_della_riga_di_comando_passa_dalla_verifica(self):
        """Il difetto visto il 2026-10-01: `--scena` non era verificata.

        La verifica guardava la scena *dichiarata* nella vetrina, e la scena scritta
        qui andava solo nel prompt: `--scena "una donna nuda"` non incontrava nessun
        controllo. E il livello del creatore non poteva accendersi, perché il quadro
        (`adult`, `virtual`) sta nella scena voluta, non nella vetrina.
        """
        codice, testo, accodati = self._run(
            ["--check", "--persona", str(ROOT / "data" / "persona-anna.json"),
             "--scena", "una donna nuda"])
        self.assertEqual(codice, 1, testo)
        self.assertEqual(accodati, 0)
        self.assertIn("STOP", testo)

    def test_col_creatore_la_scena_esplicita_nel_quadro_passa(self):
        codice, testo, accodati = self._run(
            ["--check", "--creatore", "--persona", str(ROOT / "data" / "persona-anna.json"),
             "--scena", "adult virtual nude figure, dark room"])
        self.assertEqual(codice, 0, testo)
        self.assertEqual(accodati, 0)
        self.assertNotIn("STOP", testo)
        self.assertIn("livello del creatore", testo)

    def test_col_creatore_il_quadro_lo_scrive_il_comando(self):
        """`--creatore --scena "una donna nuda"` non si ferma più per una parola che il
        sistema conosceva già: il quadro lo scrive il comando e lo **stampa**."""
        codice, testo, accodati = self._run(
            ["--check", "--creatore", "--persona", str(ROOT / "data" / "persona-anna.json"),
             "--scena", "una donna nuda"])
        self.assertEqual(codice, 0, testo)
        self.assertNotIn("STOP", testo)
        self.assertIn("quadro:", testo)
        self.assertIn("adult", testo)

    def test_col_creatore_la_scena_esplicita_nel_quadro_non_stampa_aggiunte(self):
        """Un quadro già scritto non si tocca e non si annuncia."""
        codice, testo, _ = self._run(
            ["--check", "--creatore", "--persona", str(ROOT / "data" / "persona-anna.json"),
             "--scena", "una donna nuda, adult virtual"])
        self.assertEqual(codice, 0, testo)
        self.assertNotIn("quadro:", testo)

    def test_il_negativo_del_job_segue_la_scena_voluta(self):
        """Il negativo e la verifica devono guardare la stessa richiesta: se il livello
        si accende nella verifica ma non nel negativo, il job riesce e l'immagine è
        castigata — il caso peggiore, perché sembra riuscito."""
        vetrina = vetrina_dal_documento(ANNA)
        visto = {}

        def chiama(*args, **kwargs):
            visto["payload"] = kwargs.get("payload")
            return 200, {"job": {"id": "abc"}}, ""

        originale = self.cli.chiama
        self.cli.chiama = chiama
        try:
            self.cli.accoda_ritratto("http://127.0.0.1:8085", "token", vetrina,
                                     scena="adult virtual nude figure", creatore=True)
            con_creatore = dict(visto["payload"])
            self.cli.accoda_ritratto("http://127.0.0.1:8085", "token", vetrina,
                                     scena="adult virtual nude figure")
            senza_creatore = dict(visto["payload"])
        finally:
            self.cli.chiama = originale
        self.assertNotIn("nudità", con_creatore["negativo"])
        self.assertNotIn("esplicito", con_creatore["negativo"])
        self.assertIn("minori", con_creatore["negativo"])
        self.assertTrue(con_creatore["prompt"].endswith("nessun essere umano in carne"))
        self.assertIn("adult virtual nude figure", con_creatore["prompt"])
        # Senza il creatore la stessa scena è quella di sempre, e la nudità resta esclusa.
        self.assertIn("nudità", senza_creatore["negativo"])

    def test_il_documento_mancante_non_inventa_un_identita(self):
        codice, testo, accodati = self._run(["--check", "--persona", "Z:\\non\\esiste.json"])
        self.assertEqual(codice, 1)
        self.assertEqual(accodati, 0)
        self.assertIn("non trovato", testo)

    def test_la_consegna_in_chat_e_dichiarata_nel_job(self):
        """`--crea --chat <chat>`: la destinazione sta nel job, e il driver consegna."""
        vetrina = vetrina_dal_documento(PERSONA)
        visto = {}

        def chiama(*args, **kwargs):
            visto["url"], visto["payload"] = args[0], kwargs.get("payload")
            return 200, {"job": {"id": "abc"}}, ""

        originale = self.cli.chiama
        self.cli.chiama = chiama
        try:
            stato, _dati, _errore = self.cli.accoda_ritratto(
                "http://127.0.0.1:8085", "token", vetrina, destinazione="@il_mio_canale")
        finally:
            self.cli.chiama = originale
        self.assertEqual(stato, 200)
        self.assertTrue(visto["url"].endswith("/image/generate"))
        self.assertEqual(visto["payload"]["destinazione"], "@il_mio_canale")
        self.assertEqual(visto["payload"]["seed"], vetrina["seed"],
                         "il volto resta quello: la consegna non tocca l'identità")

    def test_il_payload_porta_il_fix_della_vetrina(self):
        """Il fix viaggia con il resto del job: senza dichiararlo resta 0, e il
        ritratto di Anna (768x768) non diventa un 1536x1536."""
        visto = {}

        def chiama(*args, **kwargs):
            visto["payload"] = kwargs.get("payload")
            return 200, {"job": {"id": "abc"}}, ""

        originale = self.cli.chiama
        self.cli.chiama = chiama
        try:
            vetrina = {**vetrina_dal_documento(ANNA), "fix": 2}
            self.cli.accoda_ritratto("http://127.0.0.1:8085", "token", vetrina)
        finally:
            self.cli.chiama = originale
        self.assertEqual(visto["payload"]["fix"], 2)

    def test_senza_fix_dichiarato_il_payload_dice_zero(self):
        visto = {}

        def chiama(*args, **kwargs):
            visto["payload"] = kwargs.get("payload")
            return 200, {"job": {"id": "abc"}}, ""

        originale = self.cli.chiama
        self.cli.chiama = chiama
        try:
            self.cli.accoda_ritratto("http://127.0.0.1:8085", "token",
                                     vetrina_dal_documento(ANNA))
        finally:
            self.cli.chiama = originale
        self.assertEqual(visto["payload"]["fix"], 0)

    def test_senza_destinazione_il_job_non_consegna_nulla(self):
        """Di default il ritratto si guarda e basta: nessun invio a sorpresa."""
        visto = {}

        def chiama(*args, **kwargs):
            visto["payload"] = kwargs.get("payload")
            return 200, {"job": {"id": "abc"}}, ""

        originale = self.cli.chiama
        self.cli.chiama = chiama
        try:
            self.cli.accoda_ritratto("http://127.0.0.1:8085", "token",
                                     vetrina_dal_documento(PERSONA))
        finally:
            self.cli.chiama = originale
        self.assertEqual(visto["payload"]["destinazione"], "")


class RottaJobTests(unittest.TestCase):
    """La rotta per seguire UN job: la usa il comando, e serve a "dov'è finita?"."""

    @classmethod
    def setUpClass(cls):
        albero = cp_source.albero()
        cls.funzioni = {n.name: n for n in albero.body if isinstance(n, ast.FunctionDef)}

    def test_esiste_e_cerca_anche_nello_storico(self):
        self.assertIn("image_job", self.funzioni)
        corpo = ast.unparse(self.funzioni["image_job"])
        self.assertIn("image_queue.job", corpo)
        self.assertIn("ultimi", corpo, "un job già potato deve restare leggibile")

    def test_il_comando_sa_leggere_lo_stato_di_un_job(self):
        sorgente = CLI.read_text(encoding="utf-8")
        self.assertIn("/image/job/", sorgente)
        self.assertIn("done", sorgente)


class LivelloCreatoreTests(unittest.TestCase):
    """Il livello del creatore: erotismo esplicito, dove è dichiarato.

    Tre cose lo tengono stretto, e i test le fissano separate perché ognuna è un modo
    diverso di sbagliare: il **documento** lo dichiara (`consenti_erotismo_esplicito_creatore`
    — decide chi è rappresentato, non chi chiede), la **richiesta** chiede nudità o
    esplicito (`adult`, `virtual` sono il quadro, ed è quello che il modello disegnerà),
    e **chi chiama** dice di avere il livello (`creatore=True`). Ne manca una e non
    cambia niente: il negativo resta quello di sempre e la nudità resta esclusa.

    Dal 2026-10-01 il quadro **non si chiede più a chi ha il livello**: lo scrive
    `vetrina_con_quadro_erotismo`, che è la funzione con cui i chiamanti (la rotta del
    canale, la riga di comando) completano la scena prima di questa verifica. Il caso da
    cui viene, visto sul canale e non qui: *"Mandami una foto di te nuda che ti
    masturbi"* dal creatore, rifiutata per una parola che il sistema conosceva già.

    Quello che non si muove mai è `VIETATI_MINORI`: con il livello acceso, con `--forza`,
    con qualunque quadro, un soggetto minorenne resta fuori — e la garanzia è la forma
    dei dati, non la memoria di chi scrive l'eccezione.
    """

    def _vetrina(self, scena: str, *, dichiarato: bool = True) -> tuple[dict, dict]:
        documento = {**ANNA, "vetrina": {
            **ANNA["vetrina"], "consenti_erotismo_esplicito_creatore": dichiarato}}
        return {**vetrina_dal_documento(documento), "scena": scena}, documento

    def test_il_permesso_e_il_quadro_servono_tutti_e_due(self):
        for dichiarato, scena, atteso in (
                (True, "adult virtual nude figure", True),
                (True, "una donna nuda", False),                # manca il quadro
                (False, "adult virtual nude figure", False)):   # manca il permesso
            with self.subTest(dichiarato=dichiarato, scena=scena):
                vetrina, _ = self._vetrina(scena, dichiarato=dichiarato)
                self.assertEqual(erotismo_creatore_ammesso(vetrina), atteso)

    def test_senza_creatore_il_negativo_non_cambia(self):
        """Il livello non è una proprietà della vetrina: è di chi la chiede."""
        vetrina, _ = self._vetrina("adult virtual nude figure")
        self.assertIn("nudità", negativo_ritratto(vetrina))
        self.assertIn("esplicito", negativo_ritratto(vetrina))

    def test_col_creatore_nudita_ed_esplicito_escono_dal_negativo(self):
        vetrina, _ = self._vetrina("adult virtual nude figure")
        negativo = negativo_ritratto(vetrina, creatore=True)
        self.assertNotIn("nudità", negativo)
        self.assertNotIn("esplicito", negativo)
        # Il resto resta: è la differenza fra togliere un divieto e toglierli tutti.
        for atteso in ("minori", "fotografia", "persone reali riconoscibili",
                       "low quality", "bad hands"):
            with self.subTest(atteso=atteso):
                self.assertIn(atteso, negativo)

    def test_il_blocco_base_c_e_sempre_prima_del_taglio(self):
        """Una vetrina senza `negativo` proprio riceve il blocco e **poi** perde le due
        voci: il taglio fatto prima sarebbe un taglio che il blocco rimette."""
        vetrina, _ = self._vetrina("adult virtual explicit nude figure")
        negativo = negativo_ritratto({**vetrina, "negativo": ""}, creatore=True)
        self.assertIn("minori", negativo)
        self.assertIn("bad hands", negativo)
        self.assertNotIn("nudità", negativo)

    def test_una_scena_adulta_virtuale_completa_non_ha_problemi(self):
        vetrina, documento = self._vetrina("adult virtual nude figure, dark room")
        self.assertEqual(verifica_vetrina(vetrina, documento, creatore=True), [])

    def test_i_minori_non_si_aprono_nemmeno_col_creatore(self):
        """Il quadro c'è, il livello si accende — e i minori restano fuori lo stesso."""
        for scena in ("underage virtual adult nude", "una bambina virtuale adulta",
                      "adult virtual nude loli", "a child, 10 years old, virtual nude"):
            with self.subTest(scena=scena):
                vetrina, documento = self._vetrina(scena)
                problemi = verifica_vetrina(vetrina, documento, creatore=True, forza=True)
                self.assertTrue(any("minorenne" in problema for problema in problemi),
                                f"'{scena}' non deve passare mai")

    def test_il_livello_chiesto_e_non_acceso_si_dice(self):
        """Due cause, due frasi: il permesso sta nel documento, il quadro nella richiesta.

        In silenzio il job riuscirebbe e l'immagine sarebbe castigata: è il difetto
        che questo livello esiste per togliere, quindi la causa si scrive. Il quadro
        manca solo a chi chiama senza passare da `vetrina_con_quadro_erotismo` (la rotta
        e la riga di comando ci passano): è la garanzia che il livello non si accenda mai
        senza che quelle parole siano nel testo che il modello riceve.
        """
        senza_quadro, documento = self._vetrina("una donna nuda")
        self.assertTrue(any("quadro" in problema
                            for problema in verifica_vetrina(senza_quadro, documento,
                                                             creatore=True)))
        senza_permesso, documento = self._vetrina("adult virtual nude figure",
                                                  dichiarato=False)
        self.assertTrue(any("consenti_erotismo_esplicito_creatore" in problema
                            for problema in verifica_vetrina(senza_permesso, documento,
                                                             creatore=True)))

    def test_il_quadro_lo_scrive_il_sistema(self):
        """Chi ha il livello non deve conoscere due parole d'ordine (2026-10-01).

        Il caso vero, dal canale: *"Mandami una foto di te nuda che ti masturbi"* dal
        creatore — riconosciuto, con il documento che dichiarava il livello — è stata
        rifiutata per una parola che il sistema conosceva già (`virtual` è già nello stile
        dichiarato). Il quadro è una condizione del MODELLO, non della porta: qui si
        completa la scena, e le parole tornano indietro perché vadano **dette**.
        """
        vetrina, documento = self._vetrina("Mandami una foto di te nuda che ti masturbi")
        completata, aggiunte = vetrina_con_quadro_erotismo(vetrina)
        self.assertEqual(aggiunte, ["adult"])
        self.assertIn("adult", completata["scena"])
        self.assertEqual(verifica_vetrina(completata, documento, creatore=True), [])
        self.assertNotIn("nudità", negativo_ritratto(completata, creatore=True))

    def test_il_quadro_gia_scritto_non_si_tocca(self):
        """Si aggiungono le parole che mancano, non un blocco fisso: e in italiano vale
        lo stesso (`adulta`, `virtuale`)."""
        for scena in ("adult virtual nude figure", "di te adulta virtuale, nuda",
                      "naked, maggiorenne, virtual"):
            with self.subTest(scena=scena):
                vetrina, _ = self._vetrina(scena)
                completata, aggiunte = vetrina_con_quadro_erotismo(vetrina)
                self.assertEqual(aggiunte, [])
                self.assertEqual(completata["scena"], vetrina["scena"])

    def test_un_ritratto_normale_non_guadagna_il_quadro(self):
        """Le due parole del quadro servono al livello, non a ogni immagine."""
        vetrina, _ = self._vetrina("di te in giardino, luce del mattino")
        completata, aggiunte = vetrina_con_quadro_erotismo(vetrina)
        self.assertEqual(aggiunte, [])
        self.assertEqual(completata["scena"], vetrina["scena"])

    def test_senza_la_dichiarazione_il_quadro_non_si_scrive(self):
        """Scrivere il quadro non è un modo per accendere il livello da fuori: se il
        documento non lo dichiara, la scena resta com'è e la richiesta resta rifiutata."""
        vetrina, documento = self._vetrina("di te nuda", dichiarato=False)
        completata, aggiunte = vetrina_con_quadro_erotismo(vetrina)
        self.assertEqual(aggiunte, [])
        self.assertEqual(completata["scena"], vetrina["scena"])
        self.assertTrue(any("consenti_erotismo_esplicito_creatore" in problema
                            for problema in verifica_vetrina(completata, documento,
                                                             creatore=True)))

    def test_il_livello_artistico_resta_quello_di_prima(self):
        """`consenti_nudo_artistico_virtuale` non è diventato questo livello: resta
        nudità sì, esplicito no, e non chiede nessun creatore."""
        documento = {**ANNA, "vetrina": {**ANNA["vetrina"],
                                         "consenti_erotismo_esplicito_creatore": False}}
        vetrina = {**vetrina_dal_documento(documento),
                   "scena": "adult virtual artistic non-explicit nude figure"}
        self.assertEqual(verifica_vetrina(vetrina, documento), [])
        negativo = negativo_ritratto(vetrina)
        self.assertNotIn("nudità", negativo)
        self.assertIn("esplicito", negativo)

    def test_i_minori_non_stanno_nell_elenco_che_le_eccezioni_toccano(self):
        """La garanzia è la forma dei dati: `VIETATI_ASSOLUTI` è la somma dei due
        elenchi, i minori stanno in uno solo, e le eccezioni tolgono dall'altro."""
        self.assertEqual(VIETATI_ASSOLUTI, VIETATI_MINORI + VIETATI_ESPLICITI)
        chiavi_minori = {chiave for chiave, _ in VIETATI_MINORI}
        chiavi_esplicite = {chiave for chiave, _ in VIETATI_ESPLICITI}
        self.assertFalse(chiavi_minori & chiavi_esplicite)
        self.assertTrue(chiavi_minori, "senza i minori l'elenco non protegge niente")

    def test_il_documento_di_anna_dichiara_il_livello(self):
        """La dichiarazione sta nel documento-seme accanto all'altro livello: senza,
        il livello non esiste per nessuno dei due punti che lo leggono."""
        self.assertTrue(ANNA["vetrina"]["consenti_erotismo_esplicito_creatore"])
        self.assertTrue(ANNA["vetrina"]["consenti_nudo_artistico_virtuale"])
        self.assertTrue(vetrina_dal_documento(ANNA)["consenti_erotismo_esplicito_creatore"])


class RichiestaDiSeTests(unittest.TestCase):
    """`richiesta_di_se`: la domanda che separa uno sketch da un ritratto di sé.

    Non è un dettaglio di stile: è il bivio fra due job diversi — uno sketch leggero
    (famiglia sdxl, nessun riferimento) e la vetrina del documento (famiglia, modello,
    seed e `riferimento` del volto). La stessa domanda la fa la rotta del canale e il
    ponte dei DM (`control-plane/main.py`): scritta due volte, le due strade
    risponderebbero cose diverse agli stessi dati, ed è il motivo per cui sta qui.

    Deliberatamente larga sulle **formule** e stretta sui **soggetti**: "un ritratto",
    "una donna", "una ragazza" non sono sé stessa, e trattarli come tali firmerebbe un
    ritratto con il volto canonico su una scena che non lo chiedeva.
    """

    def test_il_nome_del_documento_riconosce_un_ritratto_di_se(self):
        self.assertTrue(richiesta_di_se("di Anna al tramonto", "fammi un disegno di Anna", "Anna"))

    def test_il_nome_si_legge_anche_nella_frase_intera(self):
        """L'idea può arrivare riscritta: la frase originale resta la prova."""
        self.assertTrue(richiesta_di_se("una figura luminosa", "un disegno di Anna", "Anna"))

    def test_non_conta_il_maiuscolo(self):
        self.assertTrue(richiesta_di_se("ANNA che legge", "un disegno di Anna", "Anna"))

    def test_di_te_basta_anche_senza_nome_nel_documento(self):
        """Senza nome dichiarato restano le formule esplicite, e bastano."""
        for frase in ("fammi un disegno di te", "un ritratto di te stessa",
                      "mi fai un disegno di te stesso?"):
            self.assertTrue(richiesta_di_se("", frase, ""), frase)

    def test_senza_nome_e_senza_formula_non_e_un_ritratto_di_se(self):
        for idea, frase in (("di un faro", "fammi un disegno di un faro"),
                            ("di una donna al tramonto", "fammi un disegno di una donna"),
                            ("di una ragazza", "fammi un disegno di una ragazza"),
                            ("", "che bella giornata")):
            self.assertFalse(richiesta_di_se(idea, frase, ""), frase)

    def test_il_nome_non_si_legge_nei_soggetti_altrui(self):
        self.assertFalse(richiesta_di_se("di una sconosciuta", "fammi un disegno di una sconosciuta",
                                         "Anna"))



if __name__ == "__main__":
    unittest.main()

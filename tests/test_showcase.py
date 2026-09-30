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

from shared.image_jobs import (FAMIGLIA_SD15, MODELLO_SD15, nuovo_job,  # noqa: E402
                               workflow)
from shared.showcase import (CONFLITTI_IDENTITA, MARCATORI_DIGITALI,  # noqa: E402
                             NEGATIVO_BASE, STILE_DEFAULT, VIETATI_ASSOLUTI,
                             istruzione_botfather, marcatori_presenti,
                             negativo_ritratto, prompt_ritratto,
                             verifica_vetrina, vetrina_dal_documento)

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
        self.assertEqual(negativo.count(NEGATIVO_BASE), 1)
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
        albero = ast.parse((ROOT / "control-plane" / "main.py").read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()


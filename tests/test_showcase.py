# SPDX-License-Identifier: Apache-2.0
"""La vetrina di sé: l'identità visiva non contraddice il documento che la fonda.

Il caso che questi test difendono è preciso: il documento di identità di Aurora dice
"non ho un corpo" e "non lascio intendere di essere una persona". Un ritratto
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

from shared.showcase import (CONFLITTI_IDENTITA, NEGATIVO_BASE, STILE_DEFAULT,  # noqa: E402
                             VIETATI_ASSOLUTI, istruzione_botfather,
                             marcatori_presenti, negativo_ritratto,
                             prompt_ritratto, verifica_vetrina,
                             vetrina_dal_documento)

CLI = ROOT / "scripts" / "ritratto.py"
PERSONA = json.loads((ROOT / "data" / "persona-aurora.json").read_text(encoding="utf-8"))


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

    def test_un_seed_a_zero_rompe_l_identita(self):
        vetrina = {**vetrina_dal_documento(PERSONA), "seed": 0}
        self.assertTrue(any("seed" in problema for problema in
                            verifica_vetrina(vetrina, PERSONA)))

    def test_un_immagine_troppo_piccola_non_e_una_immagine_di_profilo(self):
        vetrina = {**vetrina_dal_documento(PERSONA), "larghezza": 32}
        self.assertTrue(any("larghezza" in problema for problema in
                            verifica_vetrina(vetrina, PERSONA)))

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


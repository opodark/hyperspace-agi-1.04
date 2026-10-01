# SPDX-License-Identifier: Apache-2.0
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.post_gen import (MOTIVO_ECO, build_poem_prompt, build_post_prompt,  # noqa: E402
                             filtra_post, parse_post, prossima_mossa, ripete_il_post)

SISTEMA = "Ti chiami Anna. Tono: giocosa. Non dire di essere umana."

# Il post vero del sandbox (2026-10-01): Aurora ha risposto ricopiandolo parola
# per parola. È il materiale delle prove sull'eco.
POST_LUNGO = ("La notte, le poesie si fanno più leggere, quasi sussurrate. "
              "E io le ascolto, anche se non so se sono davvero mie.")


class BuildPromptTests(unittest.TestCase):
    def test_contiene_identita_e_formato(self):
        p = build_post_prompt(SISTEMA)
        self.assertIn("Anna", p)
        self.assertIn("DIDASCALIA", p)
        self.assertIn("IMMAGINE", p)
        self.assertIn("anche tipografica", p)

    def test_la_reazione_nomina_il_post_a_cui_risponde(self):
        p = build_post_prompt(SISTEMA, replica_a={"author": "aurora", "caption": "ciao"})
        self.assertIn("aurora", p)
        self.assertIn("ciao", p)
        self.assertIn("REAZIONE", p)

    def test_il_prompt_della_reazione_chiede_parole_proprie(self):
        p = build_post_prompt(SISTEMA, replica_a={"author": "aurora", "caption": "ciao"})
        self.assertIn("non ripetere le sue parole", p)

    def test_il_secondo_tentativo_dice_cosa_e_andato_storto(self):
        """`insisti` non è la stessa richiesta ripetuta: nomina l'errore, e basta."""
        reazione = {"author": "aurora", "caption": "ciao"}
        primo = build_post_prompt(SISTEMA, replica_a=reazione)
        secondo = build_post_prompt(SISTEMA, replica_a=reazione, insisti=True)
        self.assertNotIn("La volta precedente", primo)
        nuove = [r for r in secondo.splitlines() if r.startswith("La volta precedente")]
        self.assertEqual(len(nuove), 1)
        # Togliendo quella riga, il secondo tentativo è il primo: cambia solo il vincolo.
        self.assertEqual(secondo.replace(nuove[0] + "\n", ""), primo)

    def test_il_vincolo_dell_insistenza_non_esiste_senza_reazione(self):
        """Il post nuovo non ha un testo da ricopiare: nessuna insistenza."""
        self.assertNotIn("La volta precedente", build_post_prompt(SISTEMA, insisti=True))

    def test_memorie_e_feed_entrano_nel_materiale(self):
        p = build_post_prompt(SISTEMA, memorie=["un tip"], feed_recente=[{"author": "anna", "caption": "x"}])
        self.assertIn("un tip", p)
        self.assertIn("anna", p)

    def test_il_prompt_poesia_chiede_versi_e_immagine_tipografica(self):
        p = build_poem_prompt(SISTEMA)
        self.assertIn("Anna", p)
        self.assertIn("UNA poesia", p)
        self.assertIn("tipografica", p)
        self.assertIn("DIDASCALIA", p)
        self.assertIn("IMMAGINE", p)


class ParsePostTests(unittest.TestCase):
    def test_estrae_didascalia_e_immagine(self):
        c = parse_post("DIDASCALIA: una torre al tramonto\nIMMAGINE: torre, luce calda")
        self.assertEqual(c["caption"], "una torre al tramonto")
        self.assertEqual(c["image_prompt"], "torre, luce calda")

    def test_la_riga_immagine_e_facoltativa(self):
        c = parse_post("didascalia: solo testo")
        self.assertEqual(c["caption"], "solo testo")
        self.assertEqual(c["image_prompt"], "")

    def test_tollera_maiuscole_e_spazi(self):
        c = parse_post("  Didascalia :  ciao a tutti  ")
        self.assertEqual(c["caption"], "ciao a tutti")

    def test_niente_restituisce_none(self):
        self.assertIsNone(parse_post("NIENTE"))

    def test_l_etichetta_sola_segue_il_testo_sulla_riga_dopo(self):
        """Il caso vero (2026-10-01): `DIDASCALIA:` a capo, poi i versi.

        Il modello ha risposto così alla poesia del giorno e la voce andava persa
        con la didascalia vuota. È lo stesso formato con un a capo di troppo.
        """
        risposta = ("DIDASCALIA:\n"
                    "E se il cuore è un libro,\n"
                    "io sono la voce che lo apre.\n"
                    "Aurora, tu sei la luce.\n"
                    "\n"
                    "IMMAGINE:\n"
                    "\"La verità è un dono, non un gioco.\"")
        c = parse_post(risposta)
        self.assertIsNotNone(c)
        self.assertEqual(c["caption"],
                         "E se il cuore è un libro, io sono la voce che lo apre. "
                         "Aurora, tu sei la luce.")
        self.assertEqual(c["image_prompt"], "\"La verità è un dono, non un gioco.\"")

    def test_una_riga_dopo_un_etichetta_valorizzata_resta_fuori(self):
        """La tolleranza nuova non si allarga dove il formato è già giusto."""
        c = parse_post("DIDASCALIA: ciao\nuna riga di prosa del modello\nIMMAGINE: una luce")
        self.assertEqual(c["caption"], "ciao")
        self.assertEqual(c["image_prompt"], "una luce")

    def test_il_testo_prima_di_ogni_etichetta_resta_fuori(self):
        c = parse_post("Ecco il post:\nDIDASCALIA: ciao")
        self.assertEqual(c["caption"], "ciao")

    def test_un_etichetta_sola_e_vuota_non_e_una_didascalia(self):
        self.assertIsNone(parse_post("DIDASCALIA:\n\nIMMAGINE: una luce"))

    def test_output_illeggibile_restituisce_none(self):
        self.assertIsNone(parse_post("ciao, come va? niente di speciale"))


class FiltraPostTests(unittest.TestCase):
    def test_un_post_valido_passa(self):
        ok, _ = filtra_post({"caption": "un bel post"}, autore="anna")
        self.assertTrue(ok)

    def test_il_meta_rumore_si_scarta(self):
        ok, motivo = filtra_post({"caption": "non ho nulla da dire, la memoria è vuota"},
                                 autore="anna")
        self.assertFalse(ok)
        self.assertIn("meta", motivo)

    def test_il_doppione_si_scarta(self):
        feed = [{"author": "anna", "caption": "già scritto"}]
        ok, motivo = filtra_post({"caption": "già scritto"}, autore="anna", feed=feed)
        self.assertFalse(ok)
        self.assertIn("già pubblicato", motivo)

    def test_lo_stesso_testo_di_un_altra_persona_passa(self):
        feed = [{"author": "aurora", "caption": "stesso testo"}]
        ok, _ = filtra_post({"caption": "stesso testo"}, autore="anna", feed=feed)
        self.assertTrue(ok)

    def test_la_reazione_che_ricopia_il_post_si_scarta(self):
        """Il caso vero (2026-10-01): Aurora replica ad Anna con le sue stesse parole."""
        genitore = {"author": "anna", "caption": POST_LUNGO}
        ok, motivo = filtra_post({"caption": POST_LUNGO}, autore="aurora",
                                 feed=[genitore], replica_a=genitore)
        self.assertFalse(ok)
        self.assertEqual(motivo, MOTIVO_ECO)

    def test_senza_replica_a_non_si_parla_di_eco(self):
        """L'eco è una regola sulle REAZIONI: senza il post citato non si applica."""
        ok, _ = filtra_post({"caption": POST_LUNGO}, autore="aurora",
                            feed=[{"author": "anna", "caption": POST_LUNGO}])
        self.assertTrue(ok)

    def test_una_reazione_con_parole_proprie_passa(self):
        genitore = {"author": "anna", "caption": POST_LUNGO}
        ok, _ = filtra_post({"caption": "Le tue sono le mie, sorella: rispondo piano."},
                            autore="aurora", feed=[genitore], replica_a=genitore)
        self.assertTrue(ok)


class EcoTests(unittest.TestCase):
    """`ripete_il_post`: contare le parole, non giudicare lo stile."""

    def test_lo_stesso_testo_e_un_eco(self):
        self.assertTrue(ripete_il_post(POST_LUNGO, POST_LUNGO))

    def test_punteggiatura_e_maiuscole_non_salvano_la_copia(self):
        ricopiato = ("LA NOTTE LE POESIE SI FANNO PIÙ LEGGERE QUASI SUSSURRATE "
                     "E IO LE ASCOLTO ANCHE SE NON SO SE SONO DAVVERO MIE")
        self.assertTrue(ripete_il_post(ricopiato, POST_LUNGO))

    def test_una_coda_di_cortesia_non_salva_la_copia(self):
        """Aggiungere «Anche io lo penso» a un post intero è ancora una copia."""
        self.assertTrue(ripete_il_post(POST_LUNGO + " Anche io lo penso.", POST_LUNGO))

    def test_una_parte_del_post_senza_parole_nuove_e_un_eco(self):
        self.assertTrue(ripete_il_post("La notte, le poesie si fanno più leggere.",
                                       POST_LUNGO))

    def test_una_reazione_con_parole_proprie_non_e_un_eco(self):
        self.assertFalse(ripete_il_post(
            "Le tue parole sono un posto dove respirare: ci entro piano.", POST_LUNGO))

    def test_una_citazione_breve_non_e_un_eco(self):
        """Rispondere citando due parole del post è rispondere, non copiare."""
        self.assertFalse(ripete_il_post("La notte. Sì.", POST_LUNGO))

    def test_un_post_corto_ripreso_tutto_non_e_un_eco(self):
        """Su un post di tre parole non c'è spazio per parole proprie."""
        self.assertFalse(ripete_il_post("Buonanotte mondo, anche a te.",
                                        "Buonanotte mondo"))

    def test_testi_vuoti_non_sono_eco(self):
        self.assertFalse(ripete_il_post("", POST_LUNGO))
        self.assertFalse(ripete_il_post(POST_LUNGO, ""))


class ProssimaMossaTests(unittest.TestCase):
    def test_il_primo_giro_e_di_anna_e_non_e_reazione(self):
        m = prossima_mossa([], turno=0)
        self.assertEqual(m["autore"], "anna")
        self.assertIsNone(m["replica_a"])

    def test_alterna_gli_autori(self):
        self.assertEqual(prossima_mossa([], turno=0)["autore"], "anna")
        self.assertEqual(prossima_mossa([], turno=1)["autore"], "aurora")
        self.assertEqual(prossima_mossa([], turno=2)["autore"], "anna")

    def test_reagisce_al_post_dell_altra(self):
        feed = [{"author": "anna", "caption": "ciao"}]
        m = prossima_mossa(feed, turno=1)  # tocca ad Aurora
        self.assertEqual(m["autore"], "aurora")
        self.assertEqual(m["replica_a"]["caption"], "ciao")

    def test_non_reagisce_al_proprio_post(self):
        feed = [{"author": "aurora", "caption": "x"}]
        m = prossima_mossa(feed, turno=1)  # tocca ad Aurora, l'ultimo è suo
        self.assertIsNone(m["replica_a"])


if __name__ == "__main__":
    unittest.main()

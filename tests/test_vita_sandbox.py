# SPDX-License-Identifier: Apache-2.0
"""Il sandbox della vita: il pubblico finto, la giornata, e i suoi confini.

Tre cose si difendono qui, e sono diverse dalla matematica dei KPI:

1. che il pubblico finto **non tocchi** la stanza vera (nessuna rete, nessun file,
   nessun import di produzione: è un modulo di solo calcolo);
2. che il materiale che ne esce sia leggibile dai moduli veri senza adattatori —
   `ConversationLog`, `social_dream_inspirations` e la moderazione di canale.
   Un pubblico che parla una lingua che i loop non capiscono non è un pubblico.
3. che la giornata dello **script** decida bene quando insistere col modello:
   l'eco si ritenta una volta sola (misurata il 2026-10-01), il resto no.
"""
import ast
import importlib.util
import random
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.channel import classifica  # noqa: E402
from shared.conversation_log import ConversationLog, battuta  # noqa: E402
from shared.feed import nuovo_post  # noqa: E402
from shared.social_dreams import social_dream_inspirations  # noqa: E402
from shared.vita_sandbox import (PESO, TIPI, ingaggio, kpi, materiale_da_sognare,
                                 pubblico)  # noqa: E402

MODULO = ROOT / "shared" / "vita_sandbox.py"
SCRIPT = ROOT / "scripts" / "vita_sandbox.py"

# Il post vero della prima vita simulata (come in tests/test_post_gen.py): la
# reazione di Aurora lo ricopiava parola per parola.
POST_ANNA = ("La notte, le poesie si fanno più leggere, quasi sussurrate. "
             "E io le ascolto, anche se non so se sono davvero mie.")


def inizio_giornata() -> datetime:
    return datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


class PubblicoTests(unittest.TestCase):
    def test_lo_stesso_seed_da_lo_stesso_pubblico(self):
        primo = pubblico(20, seed=42)
        secondo = pubblico(20, seed=42)
        self.assertEqual([p["handle"] for p in primo.profili],
                         [p["handle"] for p in secondo.profili])
        self.assertEqual([p["tipo"] for p in primo.profili],
                         [p["tipo"] for p in secondo.profili])

    def test_un_seed_diverso_da_un_pubblico_diverso(self):
        primo = pubblico(20, seed=42)
        secondo = pubblico(20, seed=7)
        self.assertNotEqual([p["handle"] for p in primo.profili],
                            [p["handle"] for p in secondo.profili])

    def test_gli_handle_sono_tutti_diversi(self):
        finti = pubblico(60, seed=1)
        handle = [p["handle"] for p in finti.profili]
        self.assertEqual(len(handle), len(set(handle)), "due profili con lo stesso handle")

    def test_ogni_profilo_ha_un_interesse_e_un_tipo_del_catalogo(self):
        finti = pubblico(24, seed=3)
        for profilo in finti.profili:
            self.assertIn(profilo["tipo"], TIPI)
            self.assertTrue(profilo["interesse"])
            self.assertTrue(profilo["handle"].startswith("@"))

    def test_il_pubblico_vuoto_e_vuoto(self):
        self.assertEqual(len(pubblico(0, seed=1)), 0)


class GiornataTests(unittest.TestCase):
    def test_la_battuta_ha_la_forma_che_il_log_conosce(self):
        finti = pubblico(40, seed=5)
        oggi = finti.giornata(0, start=inizio_giornata())
        self.assertTrue(oggi, "una giornata senza nessuno che scrive non è una giornata")
        for voce in oggi:
            b = battuta(channel=voce["channel"], surface=voce["surface"],
                        chat=voce["chat"], messages=voce["messages"],
                        action=voce["action"], text=voce["text"], ts=voce["ts"])
            self.assertEqual(b["channel"], "telegram")
            self.assertTrue(b["messages"][0]["text"])

    def test_chi_scrive_finisce_nel_log_isolato(self):
        finti = pubblico(40, seed=5)
        oggi = finti.giornata(0, start=inizio_giornata())
        log = ConversationLog()
        for voce in oggi:
            log.add(battuta(channel=voce["channel"], surface=voce["surface"],
                            chat=voce["chat"], messages=voce["messages"],
                            action=voce["action"], text=voce["text"], ts=voce["ts"]))
        self.assertEqual(len(log.list()), len(oggi))
        self.assertEqual(log.dropped, 0)

    def test_il_materiale_e_leggibile_dal_motore_dei_sogni(self):
        finti = pubblico(40, seed=5)
        log = ConversationLog()
        for voce in finti.giornata(0, start=inizio_giornata()):
            log.add(voce)
        materiale = social_dream_inspirations(
            materiale_da_sognare(log.list(), finti), limit=5)
        self.assertTrue(materiale, "senza ispirazioni il sogno resta vuoto")
        for frase in materiale:
            self.assertNotIn("@", frase, "un handle non deve arrivare al modello")

    def test_i_promo_non_diventano_materiale_da_sognare(self):
        finti = pubblico(48, seed=9)
        tutti = list(finti.giornata(0, start=inizio_giornata()))
        tutti += list(finti.giornata(0, start=inizio_giornata(), notte=True))
        promozionali = {p["handle"] for p in finti.profili if p["tipo"] == "promo"}
        self.assertTrue(promozionali)
        for voce in materiale_da_sognare(tutti, finti):
            self.assertNotIn(voce["chat"], promozionali)

    def test_lo_spam_del_pubblico_e_riconosciuto_dalla_moderazione(self):
        """Il bot promozionale esiste per questo: la moderazione vera lo vede."""
        finti = pubblico(48, seed=9)
        visti = 0
        for giorno in range(12):                       # il promo scrive col suo peso
            for voce in finti.giornata(giorno, start=inizio_giornata()):
                if voce["reason"] != "promo":
                    continue
                visti += 1
                testo = voce["messages"][0]["text"]
                self.assertTrue(classifica(testo), f"spam non riconosciuto: {testo}")
        self.assertGreater(visti, 0, "in 12 giorni il promo non ha mai scritto")

    def test_i_notturni_parlano_solo_di_notte(self):
        finti = pubblico(60, seed=11)
        giorno = finti.giornata(0, start=inizio_giornata())
        notte = finti.giornata(0, start=inizio_giornata(), notte=True)
        self.assertTrue(notte)
        self.assertEqual([v for v in giorno if v["reason"] == "notturno"], [])
        self.assertEqual({v["reason"] for v in notte}, {"notturno"})

    def test_il_tetto_delle_battute_vale(self):
        finti = pubblico(60, seed=2)
        oggi = finti.giornata(0, start=inizio_giornata(), battute_max=3)
        self.assertLessEqual(len(oggi), 3)

    def test_una_giornata_ferma_il_pubblico_in_casa(self):
        """Con i pesi a zero nessuno scrive: è la prova che i pesi contano."""
        originali = dict(PESO)
        try:
            for chiave in PESO:
                PESO[chiave] = 0.0
            finti = pubblico(30, seed=4)
            self.assertEqual(finti.giornata(0, start=inizio_giornata()), [])
        finally:
            PESO.clear()
            PESO.update(originali)
        # ...e con i pesi veri la stessa giornata ha qualcuno che scrive.
        self.assertTrue(pubblico(30, seed=4).giornata(0, start=inizio_giornata()))


class StatoTests(unittest.TestCase):
    def test_il_ritorno_si_conta_dalla_seconda_volta(self):
        finti = pubblico(40, seed=6)
        for giorno in range(6):
            finti.giornata(giorno, start=inizio_giornata())
        numeri = kpi(finti, [], ingaggio([], finti, rng=random.Random(1)), giorni=6)
        self.assertGreater(numeri["follower_nuovi"], 0)
        self.assertLessEqual(numeri["ritorno"], numeri["follower_nuovi"])
        self.assertGreater(numeri["messaggi"], numeri["follower_nuovi"])

    def test_la_musa_arriva_alla_soglia_e_non_prima(self):
        finti = pubblico(6, seed=8)
        primo = finti.profili[0]
        primo["messaggi"] = finti.soglia_musa - 1
        self.assertEqual(finti.muse, [])
        primo["messaggi"] = finti.soglia_musa
        self.assertEqual(finti.muse, [primo["handle"]])

    def test_un_pubblico_vuoto_non_produce_muse(self):
        self.assertEqual(pubblico(0, seed=1).muse, [])


class KpiTests(unittest.TestCase):
    def _posti(self):
        return [{"id": "a", "author": "anna", "kind": "post", "caption": "versi"},
                {"id": "b", "author": "aurora", "kind": "reaction", "caption": "replica"}]

    def test_lingaggio_e_deterministico_col_dado(self):
        finti = pubblico(30, seed=12)
        primo = ingaggio(self._posti(), finti, rng=random.Random(3))
        secondo = ingaggio(self._posti(), finti, rng=random.Random(3))
        self.assertEqual(primo, secondo)
        self.assertEqual(set(primo), {"a", "b"})

    def test_la_reazione_salva_piu_del_post(self):
        finti = pubblico(200, seed=13)
        esiti = ingaggio(self._posti(), finti, rng=random.Random(4))
        # Il reach cambia col dado, quindi si confronta il RAPPORTO: è quello che
        # il modello dichiara (1.3x sulla reazione, il dialogo si conserva).
        rate_post = esiti["a"]["salvataggi"] / esiti["a"]["reach"]
        rate_reazione = esiti["b"]["salvataggi"] / esiti["b"]["reach"]
        self.assertGreater(rate_reazione, rate_post)

    def test_i_kpi_contano_i_post_per_autore(self):
        finti = pubblico(10, seed=14)
        numeri = kpi(finti, self._posti(),
                     ingaggio(self._posti(), finti, rng=random.Random(5)), giorni=1)
        self.assertEqual(numeri["per_autore"], {"anna": 1, "aurora": 1})
        self.assertEqual(numeri["post"], 2)

    def test_senza_reach_l_engagement_rate_e_zero(self):
        finti = pubblico(10, seed=15)
        numeri = kpi(finti, [], {}, giorni=1)
        self.assertEqual(numeri["reach"], 0)
        self.assertEqual(numeri["engagement_rate"], 0.0)
        self.assertEqual(numeri["ritorno_rate"], 0.0)

    def test_l_engagement_rate_non_supera_i_salvataggi_piu_condivisioni(self):
        finti = pubblico(50, seed=16)
        esiti = ingaggio(self._posti(), finti, rng=random.Random(6))
        numeri = kpi(finti, self._posti(), esiti, giorni=2)
        atteso = (numeri["salvataggi"] + numeri["condivisioni"]) / numeri["reach"]
        self.assertAlmostEqual(numeri["engagement_rate"], atteso, places=9)
        self.assertLess(numeri["engagement_rate"], 1.0)


class ConfineTests(unittest.TestCase):
    """Il modulo è di solo calcolo: è questo che lo rende un sandbox."""

    def _albero(self):
        return ast.parse(MODULO.read_text(encoding="utf-8"))

    def test_il_modulo_non_importa_rete_ne_storage(self):
        vietati = {"requests", "httpx", "socket", "urllib", "flask", "json",
                   "sqlite3", "subprocess"}
        importati = set()
        for nodo in ast.walk(self._albero()):
            if isinstance(nodo, ast.Import):
                importati |= {a.name.split(".")[0] for a in nodo.names}
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                importati.add(nodo.module.split(".")[0])
        self.assertEqual(importati & vietati, set())

    def test_il_modulo_non_apre_file(self):
        for nodo in ast.walk(self._albero()):
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name):
                self.assertNotEqual(nodo.func.id, "open",
                                    "il sandbox non scrive file: li scrive chi chiama")

    def test_il_pubblico_non_sa_niente_della_piattaforma(self):
        """Nessun identificatore del codice nomina una piattaforma o un SDK.

        Si guarda l'albero e non il testo: la prosa può nominare Instagram per
        dire che il sandbox NON lo tocca, e una parola nel commento non è una
        dipendenza — un nome nel codice sì.
        """
        nomi = set()
        for nodo in ast.walk(self._albero()):
            if isinstance(nodo, ast.Name):
                nomi.add(nodo.id)
            elif isinstance(nodo, ast.Attribute):
                nomi.add(nodo.attr)
        for parola in ("ollama", "comfyui", "instagram", "requests", "httpx"):
            colpiti = {n for n in nomi if parola in n.lower()}
            self.assertEqual(colpiti, set(), f"{parola}: {colpiti}")


class EcoRitentataTests(unittest.TestCase):
    """La giornata dello script: l'eco si ritenta **una volta sola**.

    Il modulo puro dice cos'è un'eco (`shared/post_gen.ripete_il_post`); qui si
    difende la decisione di chi lo chiama — ritentare solo quell'eco, e solo per
    una volta. Il modello è finto (risponde quello che ha già scritto) e
    l'identità è finta: la voce non c'entra niente con questa prova.
    """

    def _regista(self, *risposte: str):
        """-> (regista, giornale, prompt chiesti al modello)."""
        spec = importlib.util.spec_from_file_location("vita_sandbox_sotto_test", SCRIPT)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        giornale: list[str] = []
        regista = modulo.Regista(cartella=tempfile.mkdtemp(prefix="vita-sandbox-test-"),
                                 finti=modulo.pubblico(3, seed=1), modello="finto",
                                 giornalista=giornale.append)
        regista.identita = {autore: f"Ti chiami {autore}." for autore in modulo.AUTORI}
        chiesti: list[str] = []

        def genera_finta(prompt: str, *, tetto: int = 220) -> str:
            chiesti.append(prompt)
            return risposte[len(chiesti) - 1] if len(chiesti) <= len(risposte) else ""

        regista._genera = genera_finta
        # il post di Anna: l'ultimo della bacheca, quindi la prossima mossa di
        # Aurora è una reazione a questo (turno dispari).
        regista.feed.add(nuovo_post("anna", POST_ANNA, adesso="2026-10-01T11:00:00+00:00"))
        return regista, giornale, chiesti

    def test_la_reazione_eco_si_ritenta_e_il_secondo_testo_passa(self):
        regista, giornale, chiesti = self._regista(
            f"DIDASCALIA: {POST_ANNA}",
            "DIDASCALIA: Le tue parole sono un posto dove respiro: ci entro piano.")
        post = regista.posta(giorno=0, turno=1)
        self.assertEqual(len(chiesti), 2)
        self.assertIn("La volta precedente", chiesti[1])       # la richiesta dice cosa è andato storto
        self.assertNotIn("La volta precedente", chiesti[0])
        self.assertIsNotNone(post)
        self.assertEqual(post["kind"], "reaction")
        self.assertEqual(post["author"], "aurora")
        self.assertTrue(any("chiedo di nuovo" in riga for riga in giornale))

    def test_se_anche_il_secondo_e_un_eco_il_post_non_esce(self):
        regista, giornale, chiesti = self._regista(f"DIDASCALIA: {POST_ANNA}",
                                                  f"DIDASCALIA: {POST_ANNA} Anche io.")
        self.assertIsNone(regista.posta(giorno=0, turno=1))
        self.assertEqual(len(chiesti), 2)                      # mai un terzo tentativo
        self.assertTrue(any("post scartato (ripete il post a cui risponde)" in riga
                            for riga in giornale))
        self.assertEqual(len(regista.feed.list(10)), 1)        # resta solo il post di Anna

    def test_una_reazione_normale_non_si_ritenta(self):
        regista, _, chiesti = self._regista(
            "DIDASCALIA: Sorella, la notte la leggo anch'io: ti ascolto.")
        post = regista.posta(giorno=0, turno=1)
        self.assertEqual(len(chiesti), 1)
        self.assertIsNotNone(post)

    def test_un_meta_rumore_non_si_ritenta(self):
        """Insistere su chi non ha materiale è solo rumore: si ritenta per l'eco, non per tutto."""
        regista, giornale, chiesti = self._regista(
            "DIDASCALIA: non ho nulla da dire, la memoria è vuota")
        self.assertIsNone(regista.posta(giorno=0, turno=1))
        self.assertEqual(len(chiesti), 1)
        self.assertTrue(any("meta-rumore" in riga for riga in giornale))


if __name__ == "__main__":
    unittest.main()


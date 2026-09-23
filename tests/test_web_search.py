# SPDX-License-Identifier: Apache-2.0
"""La ricerca web deve chiedere i risultati nella lingua della query.

Il difetto vero (2026-09-23): il tool passava `language=it-IT` fisso, e una query
inglese tornava **fuori tema** — "best russian nude wallpaper sites 2024" dava
"I Beati Paoli (romanzo) - Wikipedia", "Unbridled Market dark web marketplace" dava
"Sovranità e sicurezza alimentare". SearXNG risponde 200 con dei risultati, quindi
il tool li passava al modello come se fossero la risposta: è così che una chat ha
"trovato" una foto su un marketplace del dark web che non esiste.

E vale anche al contrario (`it-IT` è giusto per l'italiano: con `en-US` la stessa
domanda su Formula 1 restituisce "Chi (letter) - Wikipedia"). Quindi la lingua è una
**decisione per query**, e questi test la fissano.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "control-plane" / "main.py"

from shared import web_search  # noqa: E402


class LinguaTests(unittest.TestCase):

    def test_una_frase_italiana_chiede_italiano(self):
        for query in ("chi ha vinto il campionato mondiale di Formula 1 nel 2025",
                      "previsioni meteo Roma domani",
                      "come si cucina la carbonara",
                      "cercami le notizie di oggi su Roma",
                      "quanto costa un biglietto per il Colosseo"):
            with self.subTest(query=query):
                self.assertEqual(web_search.lingua(query), web_search.ITALIANO)

    def test_le_accentate_bastano(self):
        for query in ("perché il cielo è blu", "città più belle d'Italia"):
            with self.subTest(query=query):
                self.assertEqual(web_search.lingua(query), web_search.ITALIANO)

    def test_una_frase_inglese_chiede_inglese(self):
        """Sono le query vere che tornavano fuori tema, e le misure del modulo."""
        for query in ("best russian nude wallpaper sites 2024",
                      "Unbridled Market dark web marketplace",
                      "latest news about AlphaBay marketplace",
                      "how to install cuda on windows 11"):
            with self.subTest(query=query):
                self.assertEqual(web_search.lingua(query), web_search.INGLESE)

    def test_una_query_corta_non_decide_al_posto_dell_istanza(self):
        """`dark web` non ha marcatori italiani ma non è inglese: si lascia il
        default dell'istanza (che è `it-IT`), invece di tirare a indovinare.
        `meteo roma` invece NON è ambigua: è italiana, e lo dice il rilevatore."""
        for query in ("dark web", "cuda", "roma"):
            with self.subTest(query=query):
                self.assertEqual(web_search.lingua(query), "")
        self.assertEqual(web_search.lingua("meteo roma"), web_search.ITALIANO)

    def test_una_query_vuota_torna_il_default(self):
        for query in ("", "   ", None):
            with self.subTest(query=query):
                self.assertEqual(web_search.lingua(query), web_search.ITALIANO)

    def test_il_default_si_puo_cambiare(self):
        self.assertEqual(web_search.lingua("", default=web_search.INGLESE),
                         web_search.INGLESE)

    def test_la_soglia_e_quella_dichiarata(self):
        parole = ["alpha", "beta", "gamma", "delta"]
        corta = " ".join(parole[:web_search.MIN_PAROLE_SENTENZA - 1])
        frase = " ".join(parole[:web_search.MIN_PAROLE_SENTENZA])
        self.assertEqual(web_search.lingua(corta), "")
        self.assertEqual(web_search.lingua(frase), web_search.INGLESE)


class PertinenzaTests(unittest.TestCase):
    """La spazzatura di un engine bloccato non deve arrivare al modello.

    I casi sono quelli **misurati** il 2026-09-23 con i parametri del tool: le due
    query che sono finite in una confabulazione e i risultati veri che SearXNG ha
    dato per le stesse query quando gli engine rispondevano.
    """

    CASI = (
        ("chi ha vinto il campionato mondiale di Formula 1 nel 2025",
         ["Chi Magazine - Personaggi, coppie e tu",
          "Coroner's Court - WordReference Forums"],
         ["F1, la classifica Formula 1 Piloti e Costruttori 2025 - Sky Sport",
          "Lando Norris campione F1 2025, Verstappen vince Abu Dhabi"]),
        ("Unbridled Market dark web marketplace",
         ["WhatsApp Web",
          "La ricezione dell'ultimo Alessandro - UniCa Iris",
          "UNIVERSITA' DEGLI STUDI DI PADOVA - Padua Research Archive"],
         ["AlphaBay Marketplace Returns - DarkOwl",
          "Dark web marketplace takedown - Europol"]),
    )

    def test_la_spazzatura_non_passa(self):
        for query, spazzatura, _ in self.CASI:
            with self.subTest(query=query):
                self.assertFalse(web_search.pertinenti(spazzatura, query))

    def test_i_risultati_veri_passano(self):
        for query, _, veri in self.CASI:
            with self.subTest(query=query):
                self.assertTrue(web_search.pertinenti(veri, query))

    def test_una_query_senza_termini_significativi_lascia_passare(self):
        """Non c'è niente da verificare: non si boccia per partito preso."""
        self.assertTrue(web_search.pertinenti(["qualsiasi cosa"], "il la di"))
        self.assertTrue(web_search.pertinenti(["qualsiasi cosa"], ""))
        self.assertTrue(web_search.pertinenti([], ""))

    def test_i_termini_significativi(self):
        self.assertEqual(web_search.termini_significativi(
            "chi ha vinto il campionato mondiale di Formula 1 nel 2025"),
            ["vinto", "campionato", "mondiale", "formula", "2025"])
        self.assertEqual(web_search.termini_significativi("what is the best cuda"),
                         ["cuda"])
        # "web" è corto e generico: da solo avrebbe fatto passare "WhatsApp Web".
        self.assertEqual(web_search.termini_significativi("dark web"), ["dark"])

    def test_una_cifra_sola_non_e_un_termine(self):
        """Misurato: con "1" fra i termini, "Chi Magazine" passava il guardiano —
        una cifra sola compare in quasi qualsiasi pagina."""
        self.assertNotIn("1", web_search.termini_significativi("Formula 1 2025"))
        self.assertIn("2025", web_search.termini_significativi("Formula 1 2025"))
        self.assertFalse(web_search.pertinenti(
            ["Chi Magazine - Personaggi, coppie e tu", "Coroner's Court - dal 1° gennaio"],
            "chi ha vinto il campionato mondiale di Formula 1 nel 2025"))

    def test_il_caso_misto_si_pulisce(self):
        """Misurato: due annunci di noleggio auto dentro i risultati di Formula 1."""
        voci = ["- F1, la classifica Formula 1 Piloti e Costruttori 2025 - Sky Sport",
                "- Noleggio Auto Low Cost, Confronta i Prezzi - Rentalcars.com",
                "- iNoleggio.it - Auto a Noleggio - Confronta e Risparmia",
                "- Lando Norris campione F1 2025, Verstappen vince Abu Dhabi"]
        tenute = web_search.filtra(voci, "chi ha vinto il campionato mondiale di Formula 1 nel 2025")
        self.assertEqual(len(tenute), 2)
        self.assertTrue(all("Noleggio" not in voce for voce in tenute))

    def test_senza_termini_significativi_non_si_toglie_niente(self):
        voci = ["- una cosa", "- un'altra"]
        self.assertEqual(web_search.filtra(voci, "il la di"), voci)

    def test_il_titolo_conta_piu_della_descrizione(self):
        """Misurato: per "meteo Roma" passava anche un articolo sul clima che di
        Roma parlava solo nell'indirizzo — e il modello ha citato quello."""
        voci = [{"title": "Meteo Roma - Previsioni Oggi, Prossimi 15 Giorni",
                 "content": "temperature in calo", "url": "https://www.ilmeteo.it"},
                {"title": "Il sole brucia la citta", "content": "allarme caldo",
                 "url": "https://roma.corriere.it/articolo"}]
        tenute = web_search.filtra(
            voci, "meteo Roma",
            testo=lambda v: f"{v['title']} {v['content']} {v.get('url', '')}",
            titolo=lambda v: v["title"])
        self.assertEqual([v["title"] for v in tenute],
                         ["Meteo Roma - Previsioni Oggi, Prossimi 15 Giorni"])

    def test_se_nessun_titolo_lo_dice_si_tengono_tutte_le_pertinenti(self):
        """Il titolo è un segnale forte, non un requisito: non si svuota un
        insieme che perlomeno parla della ricerca."""
        voci = [{"title": "Previsioni 15 giorni", "content": "roma, lazio"},
                {"title": "Allerta meteo", "content": "roma e provincia"}]
        tenute = web_search.filtra(voci, "meteo roma",
                                   testo=lambda v: f"{v['title']} {v['content']}",
                                   titolo=lambda v: v["title"])
        self.assertEqual(len(tenute), 2)

    def test_gli_accenti_non_decidono(self):
        """La pagina può scrivere "citta" dove la query dice "città"."""
        self.assertTrue(web_search.pertinenti(["Le citta piu belle d'Italia"],
                                              "città più belle"))
        self.assertTrue(web_search.pertinenti(["Perche il cielo e blu"],
                                              "perché il cielo è blu"))


class CablaggioTests(unittest.TestCase):
    """Il tool deve USARE la decisione — e non avere più una lingua scritta dentro."""

    @classmethod
    def setUpClass(cls):
        cls.sorgente = SOURCE.read_text(encoding="utf-8")
        albero = ast.parse(cls.sorgente)
        cls.funzioni = {n.name: n for n in albero.body
                        if isinstance(n, ast.FunctionDef)}

    def test_il_tool_chiede_la_lingua_al_modulo(self):
        corpo = ast.unparse(self.funzioni["_tool_web_search"])
        self.assertIn("web_search.lingua(", corpo)

    def test_nessuna_lingua_fissa_nel_tool(self):
        """`it-IT` scritto a mano è esattamente il difetto: se qualcuno lo
        reintroduce, questo test lo dice."""
        corpo = ast.unparse(self.funzioni["_tool_web_search"])
        self.assertNotIn("it-IT", corpo)
        self.assertNotIn("en-US", corpo)

    def test_la_lingua_si_passa_solo_se_decisa(self):
        corpo = ast.unparse(self.funzioni["_tool_web_search"])
        # `ast.unparse` rende le stringhe con apici singoli.
        self.assertIn("params['language']", corpo)
        self.assertIn("if scelta_lingua", corpo)

    def test_l_esito_lo_dice_nel_log(self):
        corpo = ast.unparse(self.funzioni["_tool_web_search"])
        self.assertIn("lingua=", corpo)

    def test_il_guardiano_di_pertinenza_e_agganciato_a_tutti_i_rami(self):
        """SearXNG (risultati e infobox) e il ripiego DuckDuckGo: la spazzatura non
        passa da nessuno dei tre punti."""
        corpo = ast.unparse(self.funzioni["_tool_web_search"])
        self.assertEqual(corpo.count("web_search.filtra("), 3)
        self.assertIn("non pertinenti", corpo)

    def test_quando_non_c_e_niente_di_utile_lo_dice(self):
        """Niente risultati è un esito da dichiarare: il modello non deve riempirlo."""
        corpo = ast.unparse(self.funzioni["_tool_web_search"])
        self.assertIn("Nessun risultato utile", corpo)

    def test_il_modulo_e_importato(self):
        self.assertIn("from shared import web_search", self.sorgente)


if __name__ == "__main__":
    unittest.main()

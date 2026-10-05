# SPDX-License-Identifier: Apache-2.0
"""Lo stato del mesh: chi lo possiede, e chi lo deve leggere davvero.

`cp/mesh.py` ha spostato via il registro e il punteggio, ma non ha potuto portare
con se tutto cio' che ci stava attorno: a scrivere le cache delle metriche e' il
thread di raccolta, che resta in main.py, e a cambiare i pesi del routing e' la tab
Setup. Sono rimasti quindi dei fili che attraversano il confine, e i fili che
attraversano un confine sono la parte fragile di un'estrazione.

I quattro difetti che questi test coprono sono reali e sono gia' successi:

1. I pesi del routing vivevano in due copie — `main.py` e `cp/config.py`. La tab
   Setup aggiornava quella di `main.py`, il mesh leggeva l'altra: la dashboard
   diceva un peso e il punteggio ne usava un altro. Silenzioso, perche' nessuna
   delle due metteva eccezione.
2. `_node_aliases` viene riassegnato da `_load_aliases_from_db`, che ora sta nel
   modulo. Un `from cp.mesh import _node_aliases` tenerebbe il dizionario di quando
   e' stato importato: gli alias finirebbero sempre vuoti, senza che nulla fallisca.
3. Se le cache delle metriche fossero state copiate invece che passate per
   riferimento, il punteggio leggerebbe una cache sempre vuota e tutti i nodi
   avrebbero lo stesso score — di nuovo, senza eccezioni.
4. Il tetto della cache del punteggio, se non venisse ricalcolato quando cambia
   l'intervallo di raccolta, resterebbe valido piu' a lungo della metrica che
   contiene: un nodo che peggiora continuerebbe a vincere per qualche minuto.

Non si importa `main.py`: all'import scrive su `/app`, che in test non esiste.
"""
import ast
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from cp import config as cp_config  # noqa: E402
from cp import mesh as cp_mesh  # noqa: E402
from tests import cp_source  # noqa: E402

PESI = ("vram", "load", "tier", "uptime", "backend", "latency", "tput", "gpu",
        "recent_penalty", "recent_window")


def _monta(modello=None, metriche=None, alias=None, modello_cache=None):
    """Registra il blueprint su un'app finta, se il modulo e' montabile da solo."""
    from flask import Flask

    modello = modello if modello is not None else {}
    metriche = metriche if metriche is not None else {}
    alias = alias if alias is not None else {}
    modello_cache = modello_cache if modello_cache is not None else {"ts": 0.0, "data": None}
    app = Flask(__name__)
    cp_mesh._node_aliases = dict(alias)
    cp_mesh.monta(
        app,
        advanced_config={},
        recent_routing_lock=threading.Lock(),
        score_cache_lock=threading.Lock(),
        node_metrics_lock=threading.Lock(),
        aggregate_mesh_models=lambda **kw: modello,
        latest_metrics=lambda nid: metriche.get(nid),
        recent_ts=lambda nid: None,
        score_terms_breakdown=lambda breakdown: 0.0,
        local_node_id="local-di-prova",
        models_cache=modello_cache,
        node_metrics_cache=metriche,
    )
    return app


class IpesiDelRoutingTests(unittest.TestCase):
    def test_i_pesi_vivono_una_volta_sola(self):
        """`cp/config.py` e' l'unico proprietario: `main.py` non ne fa una copia."""
        self.assertEqual(set(cp_config._ROUTING_WEIGHTS), set(PESI))
        dove = cp_source.dove("_ROUTING_WEIGHTS")
        self.assertEqual(dove, "config.py",
                         f"i pesi devono stare in cp/config.py, non in {dove}")
        # e il modulo del mesh non deve Averne una propria
        sorgente = (ROOT / "control-plane/cp/mesh.py").read_text(encoding="utf-8")
        for riga in sorgente.splitlines():
            if riga.startswith("_ROUTING_WEIGHTS"):
                self.fail(f"cp/mesh.py ha una copia dei pesi: {riga!r}")

    def test_il_mesh_legge_i_pesi_dal_modulo_e_non_da_una_copia(self):
        """Il perche' di `cp.config._ROUTING_WEIGHTS`: se il mesh avesse un import
        per nome, cambiarlo a runtime non cambierebbe nulla, e nessuno se ne
        accorgerebbe perche' il valore giusto resterebbe valido e silenzioso."""
        corpo = ast.unparse(cp_source.funzioni()["_routing_scores"])
        self.assertIn("_config._ROUTING_WEIGHTS", corpo,
                      "il punteggio deve leggere i pesi dal modulo che li possiede")
        punteggio = ast.unparse(cp_source.funzioni()["_routing_scores"])
        self.assertNotIn(" from cp.config import (_ROUTING_WEIGHTS)", (
            ROOT / "control-plane/cp/mesh.py").read_text(encoding="utf-8"))
        self.assertIn("_config._ROUTING_WEIGHTS", punteggio)

    def test_un_peso_cambiato_a_runtime_il_mesh_lo_vede(self):
        """Il percorso completo della tab Setup: si scrive in cp.config, e il
        punteggio successivo usa il peso nuovo."""
        originale = cp_config._ROUTING_WEIGHTS["vram"]
        try:
            cp_config.ROUTING_WEIGHT_VRAM = 0.01
            cp_config._ROUTING_WEIGHTS["vram"] = 0.01
            _monta()
            # se il mesh avesse una copia dei pesi, da qui dentro leggerebbe
            # ancora il valore vecchio: e' l'unico posto in cui si vede
            sorgente = (ROOT / "control-plane/cp/mesh.py").read_text(encoding="utf-8")
            self.assertIn("_config._ROUTING_WEIGHTS", sorgente)
            self.assertEqual(cp_config._ROUTING_WEIGHTS["vram"], 0.01)
            main = (ROOT / "control-plane/main.py").read_text(encoding="utf-8")
            self.assertNotIn("globals()[key] = float(cv)", main,
                             "la tab Setup scrive ancora nei globals di main.py")
            self.assertIn("setattr(_config, key, float(cv))", main)
        finally:
            cp_config.ROUTING_WEIGHT_VRAM = originale
            cp_config._ROUTING_WEIGHTS["vram"] = originale

    def test_la_dashboard_legge_il_proprietario(self):
        """I getter di `_ENV_RUNTIME_GET` devono leggere da `cp.config`: con un
        import per nome mostrerebbero il valore di avvio anche dopo un cambio."""
        sorgente = (ROOT / "control-plane/main.py").read_text(encoding="utf-8")
        for chiave in ("ROUTING_WEIGHT_VRAM", "ROUTING_RECENT_PENALTY"):
            riga = next(l for l in sorgente.splitlines()
                        if f'"{chiave}": lambda' in l)
            self.assertIn("_config.", riga, f"{chiave} non legge da cp.config: {riga!r}")


class GliAliasTests(unittest.TestCase):
    def test_main_non_ha_una_copia_degli_alias(self):
        """`_node_aliases` viene riassegnato, quindi un import per nome in
        main.py tenerebbe il dizionario di quando e' stato importato."""
        corpo = (ROOT / "control-plane/main.py").read_text(encoding="utf-8")
        if "_node_aliases: dict" in corpo:
            self.fail("main.py definisce ancora _node_aliases: due dizionari, "
                      "di cui uno sempre vuoto")
        for riga in corpo.splitlines():
            if "_node_aliases" in riga and "mesh._node_aliases" not in riga \
                    and not riga.lstrip().startswith("#"):
                self.fail(f"lettura che non passa dal modulo: {riga.strip()!r}")

    def test_il_caricamento_ricambia_l_oggetto_degli_alias(self):
        """La trappola, vista dal basso: `_load_aliases_from_db` costruisce un
        dizionario nuovo e lo riassegna. Non aggiorna il precedente, lo sostituisce
        — quindi main.py non puo' tenersene una copia, nemmeno per import."""
        _monta(alias={"vecchio": "sbagliato"})
        prima = cp_mesh._node_aliases
        self.assertEqual(prima, {"vecchio": "sbagliato"})
        cp_mesh._load_aliases_from_db()
        dopo = cp_mesh._node_aliases
        self.assertIsNot(dopo, prima, "il dizionario non e' stato ricambiato: "
                        "e il binding importato in main.py resterebbe valido")
        self.assertEqual(dopo, {}, "un DB vuoto cancella gli alias: e' quello "
                         "che deve succedere, non 'tenere il vecchio'")

    def test_il_caricamento_alias_riscrive_il_globale(self):
        """Se `_load_aliases_from_db` riassegnasse un nome locale invece che il
        globale, il test sopra passerebbe comunque con lo stub del DB vuoto."""
        corpo = ast.unparse(cp_source.funzioni()["_load_aliases_from_db"])
        self.assertIn("global _node_aliases", corpo)


class LeCacheCondiviseTests(unittest.TestCase):
    def test_il_modello_riceve_gli_stessi_oggetti_e_non_delle_copie(self):
        metriche = {}
        modello = {}
        _monta(metriche=metriche, modello_cache=modello)
        contesto = cp_mesh._contesto
        self.assertIs(contesto.node_metrics_cache, metriche)
        self.assertIs(contesto.models_cache, modello)

    def test_una_cache_separata_farebbe_punteggi_sempre_vuoti(self):
        """Il difetto che il riferimento evita, detto come deve suonare: due
        dizionari distinti restano uno vuoto per sempre, e ogni nodo prende lo
        stesso score."""
        metriche = {}
        _monta(metriche=metriche)
        metriche["nodo-a"] = {"vram_free_mb": 1000}
        self.assertIn("nodo-a", cp_mesh._contesto.node_metrics_cache)
        # una copia, invece, non vedrebbe niente
        copia = dict(metriche)
        copia.clear()
        self.assertEqual(copia, {})

    def test_il_tetto_della_cache_si_riallinea(self):
        """Cambiando l'intervallo di raccolta il tetto va ricalcolato: se no, la
        cache sopravvive alla metrica che dovrebbe contenere."""
        con_intervallo_basso = max(5.0, 0.75 * 4)
        _monta()
        originale = cp_config.METRICS_POLL_INTERVAL_S
        try:
            cp_config.METRICS_POLL_INTERVAL_S = 4
            cp_mesh.riallinea_tetto_cache()
            self.assertEqual(cp_mesh._SCORE_CACHE_TTL, con_intervallo_basso)
            # il tetto non scende mai sotto i 5 secondi, per quanto bassa sia
            # la frequenza: sotto, la cache non farebbe che bruciare CPU
            cp_config.METRICS_POLL_INTERVAL_S = 2
            cp_mesh.riallinea_tetto_cache()
            self.assertEqual(cp_mesh._SCORE_CACHE_TTL, 5.0)
        finally:
            cp_config.METRICS_POLL_INTERVAL_S = originale
            cp_mesh.riallinea_tetto_cache()

    def test_azzzerare_il_punteggio_azzera_il_tetto_e_non_il_contenuto(self):
        _monta()
        cp_mesh._SCORE_CACHE["nodo-a"] = {"score": 0.9}
        cp_mesh._SCORE_CACHE_AT = 123.0
        cp_mesh._invalidate_fleet_scores()
        self.assertEqual(cp_mesh._SCORE_CACHE_AT, 0.0)
        self.assertIn("nodo-a", cp_mesh._SCORE_CACHE,
                      "invalidare non deve svuotare: solo farlo scadere")


class LaRottaMontataTests(unittest.TestCase):
    def test_il_modulo_registra_le_sue_route(self):
        app = _monta()
        regole = {str(r) for r in app.url_map.iter_rules()}
        for atteso in ("/mesh/nodes", "/mesh/topology", "/nodes/aliases",
                       "/config/routing-weights"):
            self.assertIn(atteso, regole)

    def test_senza_montaggio_il_modulo_lo_dice(self):
        """Chiamare il registro prima del boot deve essere un errore chiaro, non
        un `AttributeError` su un `None` a caso fra venti righe di punteggio."""
        cp_mesh.smonta()
        with self.assertRaises(RuntimeError) as catturato:
            cp_mesh._fleet_scores()
        self.assertIn("non e' montato", str(catturato.exception))
        _monta()


if __name__ == "__main__":
    unittest.main()
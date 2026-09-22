# SPDX-License-Identifier: Apache-2.0
"""Impostazioni della tab Setup: dichiarate, lette, e **applicate a caldo**.

Il difetto trovato il 2026-09-22 cambiando `CHANNEL_MODEL`: la chiave era nella
tabella della tab Setup (quindi scrivibile nel `.env`) ma nessuno la riapplicava
al processo — la stanza continuava a usare il modello vecchio fino al riavvio del
container. Il commento di `_apply_env_runtime` chiama quel caso *"salvato ma
inerte"* e dice che e' il difetto che la tab Setup esiste per non avere: qui
diventa un test invece di una buona intenzione.

Come si legge, senza importare Flask: `_ENV_META` e' un letterale di lista di
dizionari, quindi si estrae con `ast.literal_eval`. Una chiave dei canali e'
"applicata" se compare come stringa **dentro una funzione** di `main.py`
(riapplicata a caldo o riletta a chiamata) **oppure** in `shared/channel.py`, dove
vivono `ChannelPolicy.from_env()` e le sue costanti: `CHANNEL_ENABLED` e
`CHANNEL_CLIENTS` passano da li', ed e' `_reload_channel_config()` a
ricostruirla. Sono due origini legittime, non una scappatoia.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAIN = ROOT / "control-plane" / "main.py"
CANALI = ROOT / "shared" / "channel.py"
APPLICATORI = ("_apply_env_runtime", "_reload_channel_config")


def _albero(percorso: Path) -> ast.Module:
    return ast.parse(percorso.read_text(encoding="utf-8"))


def _meta_canali() -> list:
    """Le chiavi dichiarate nella sezione "Canali esterni" della tab Setup."""
    for nodo in _albero(MAIN).body:
        if isinstance(nodo, ast.Assign) and any(
                getattr(t, "id", "") == "_ENV_META" for t in nodo.targets):
            return [m["key"] for m in ast.literal_eval(nodo.value)
                    if m["section"] == "Canali esterni"]
    raise AssertionError("_ENV_META non trovata: la tabella della tab Setup e' cambiata")


def _stringhe_dentro_le_funzioni(percorso: Path) -> set:
    """Le stringhe che compaiono DENTRO una funzione: sono le chiavi lette a
    chiamata o riapplicate a caldo. Quello che sta solo a livello di modulo e'
    letto una volta all'import (ed e' li' che nasce il "salvato ma inerte")."""
    trovate = set()
    for nodo in ast.walk(_albero(percorso)):
        if isinstance(nodo, ast.FunctionDef):
            for dentro in ast.walk(nodo):
                if isinstance(dentro, ast.Constant) and isinstance(dentro.value, str):
                    trovate.add(dentro.value)
    return trovate


def _fonte(funzione: str) -> str:
    for nodo in _albero(MAIN).body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name == funzione:
            return ast.unparse(nodo)
    raise AssertionError(f"{funzione} non esiste piu' in control-plane/main.py")


class CanaliATest(unittest.TestCase):
    def test_il_modello_dei_canali_si_applica_a_caldo(self):
        """La regressione precisa: il salvataggio deve valere SUBITO.

        `_channel_model()` legge il globale `CHANNEL_MODEL` (non `os.environ` a
        ogni chiamata), quindi l'unico posto che puo' aggiornarlo e'
        `_apply_env_runtime`. Se questo test fallisce, cambiare il modello della
        stanza dalla tab Setup scrive il `.env` e non cambia niente fino al
        riavvio.
        """
        applicatori = " ".join(_fonte(f) for f in APPLICATORI)
        self.assertIn("CHANNEL_MODEL", applicatori)

    def test_ogni_chiave_dei_canali_ha_un_punto_che_la_legge_a_caldo(self):
        lette_a_chiamata = _stringhe_dentro_le_funzioni(MAIN)
        condiviso = CANALI.read_text(encoding="utf-8")
        for chiave in _meta_canali():
            with self.subTest(chiave=chiave):
                self.assertTrue(
                    chiave in lette_a_chiamata or chiave in condiviso,
                    f"{chiave} e' nella tab Setup ma nessuno la rilegge a caldo: "
                    "il salvataggio finirebbe nel .env e resterebbe inerte")

    def test_ogni_chiave_dichiarata_e_almeno_letta(self):
        """Una chiave scritta nella UI e letta da nessuno e' una promessa falsa."""
        tutto = MAIN.read_text(encoding="utf-8") + CANALI.read_text(encoding="utf-8")
        for chiave in _meta_canali():
            with self.subTest(chiave=chiave):
                self.assertGreaterEqual(tutto.count(f'"{chiave}"'), 1, chiave)


if __name__ == "__main__":
    unittest.main()

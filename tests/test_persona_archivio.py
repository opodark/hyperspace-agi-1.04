# SPDX-License-Identifier: Apache-2.0
"""L'archivio della persona ha un solo proprietario, e i canali lo vedono.

`persona_store` viene riassegnato dal boot quando il nome o il file della persona
cambiano dalla tab Setup. Un modulo che lo riceve per contesto ne tiene una copia,
e quella copia non si aggiorna: dopo un salvataggio, il control-plane e i canali
avrebbero due persone diverse, e quella giusta dipenderebbe da quale modulo ha
chiesto per primo.

Non è un errore che si vede subito. È un errore che si vede quando qualcuno cambia
nome alla persona e si chiede perché i canali non lo sappiano — e in quel momento
`cp/persona.py` non esisteva ancora, perché l'estrazione dei canali aveva passato
l'archivio per contesto senza chiedersi chi lo riassegnasse.

Non si importa `main.py`: all'import scrive su `/app`, che in test non esiste.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from cp import canali as cp_canali  # noqa: E402
from cp import persona as cp_persona  # noqa: E402


def _nomi_usati(percorso: Path) -> set:
    """I nomi che il file usa davvero, in codice.

    Via AST e non per ricerca di testo: un commento che scrive `persona_store` e'
    esattamente la documentazione del bug, e cercarlo a parole lo farebbe sembrare
    ancora presente.
    """
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    return {n.id for n in ast.walk(albero)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


class _Profilo:
    def __init__(self, nome: str):
        self.name = nome
        self.observations = []
        self.sezioni = {}


class _Archivio:
    def __init__(self, nome: str):
        self.persona = _Profilo(nome)
        self.osservazioni = []

    def system_block(self, *_a, **_kw):
        return f"IDENTITA: {self.persona.name}"


class ArchivioPersonaTests(unittest.TestCase):
    def setUp(self):
        self.vecchio = _Archivio("nome-vecchio")
        cp_persona.monta(self.vecchio)

    def tearDown(self):
        cp_persona.smonta()

    def test_il_modulo_espone_l_archivio_di_adesso(self):
        self.assertIs(cp_persona.persona(), self.vecchio)
        self.assertEqual(cp_persona.profilo().name, "nome-vecchio")

    def test_il_profilo_e_il_documento_non_l_archivio(self):
        """`persona.persona().persona` non si legge: due `persona` di fila che non
        hanno niente a che fare. Il profilo e' il documento, l'archivio e' la cosa
        che lo contiene e che sa salvarlo."""
        archivio = cp_persona.persona()
        self.assertIs(cp_persona.profilo(), archivio.persona)
        self.assertIsNot(cp_persona.profilo(), archivio)

    def test_ricaricare_sostituisce_l_archivio(self):
        nuovo = _Archivio("nome-nuovo")
        cp_persona._persona_store = nuovo          # quello che fa `ricarica()`
        self.assertEqual(cp_persona.profilo().name, "nome-nuovo")
        self.assertIs(cp_persona.persona(), nuovo)

    def test_i_canali_non_tenono_una_copia(self):
        """Il test che chiude il bug: i canali montati con l'archivio di PRIMA
        devono vedere quello di DOPO, perché leggono dal modulo e non da una
        copia catturata al montaggio."""
        from flask import Flask

        app = Flask(__name__)
        cp_canali.monta(app, nome_persona=lambda: "Aurora")
        # il boot ricarica la persona dalla tab Setup
        cp_persona._persona_store = _Archivio("nome-dopo-reload")
        # il punto in cui i canali leggevano la copia congelata
        self.assertEqual(cp_persona.profilo().name, "nome-dopo-reload")
        self.assertNotIn("persona_store",
                         _nomi_usati(ROOT / "control-plane/cp/canali.py"),
                         "i canali leggono ancora una copia dal contesto")


class IlModuloNelSitoGiustoTests(unittest.TestCase):
    def test_main_non_tiene_una_copia(self):
        """`main.py` non deve avere un `persona_store` proprio: ogni lettura passa
        dal modulo. Un binding qui si terrebbe l'archivio di quando e' stato preso."""
        self.assertNotIn("persona_store",
                         _nomi_usati(ROOT / "control-plane/main.py"),
                         "main.py usa ancora `persona_store` in codice")

    def test_i_moduli_che_leggono_la_persona_leggono_il_modulo(self):
        for nome in ("canali", "instagram"):
            sorgente = (ROOT / f"control-plane/cp/{nome}.py").read_text(encoding="utf-8")
            with self.subTest(modulo=nome):
                self.assertIn("from cp import persona", sorgente,
                              f"cp/{nome}.py non importa il modulo della persona")
                self.assertNotIn("persona_store",
                                 _nomi_usati(ROOT / f"control-plane/cp/{nome}.py"),
                                 f"cp/{nome}.py ha ancora una copia dell'archivio in codice")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
"""Dove vive un simbolo del control-plane, senza importare main.py.

I test NON importano `control-plane/main.py`: l'import avvia dieci thread daemon,
tocca il database e chiama Ollama (tutto il ramo `__main__`). Per questo lo
leggono come sorgente e lo analizzano con `ast`. Finche' il control-plane era un
file solo bastava puntargli sopra; ora che il codice si sposta in moduli, un
test che cerca una funzione deve sapere in QUALE file sta, senza indovinare.

Questo modulo e' quel posto. `MODULI()` e' l'elenco dei file che compongono il
control-plane, e le funzioni sotto danno la vista unificata: una sola `ast.parse`
per file, gli indici ricostruiti sopra.

Perche' un elenco esplicito e non un glob di `control-plane/*.py`: il glob
prenderebbe anche `app.py` (lo stub FastAPI) e `tasks_view.py`, e `app.py`
definisce `app` come fa `main.py`: nel merge l'ultimo dei due vincerebbe in
silenzio, stesso nome due significati. `MODULI()` e' anche l'unico posto da
modificare quando nasce un modulo nuovo, e va modificato insieme alla commit
che lo crea.

`routing.py` e' escluso per scelta: e' un'unita' a se' che `main.py` importa
(`import routing as _routing`), non un pezzo del monolite che stiamo dividendo.

Come si importa: `from tests import cp_source`, NON `import cp_source`.
`unittest discover -s tests` mette `tests/` in sys.path e il secondo forma
funziona, ma `python -m unittest tests.test_uno` no — e quello e' il modo in
cui si lancia un test singolo mentre si sviluppa. Il namespace package `tests`
(c'e' un solo file senza `__init__.py`) regge tutte e tre le maniere di invocare
la suite, compresa senza PYTHONPATH.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE = ROOT / "control-plane"

# Test che MODIFICANO i nodi estratti (togliendo i decorator prima di eseguire
# una route) e quindi non possono usare la vista in cache: vedi `albero()`.
MUTANO_L_ALBERO = ("test_image_generate_route.py", "test_instagram_dm_image.py",
                   "test_runtime_recovery.py")

_analizzati: dict = {}


def MODULI() -> list:
    """I file che compongono il control-plane, `main.py` per primo.

    L'ordine conta: i test che cercano un simbolo nel testo (conteggi, slicing)
    devono vedere `main.py` prima dei moduli che da lui si staccano.
    """
    moduli = [CONTROL_PLANE / "main.py"]
    moduli += sorted((CONTROL_PLANE / "cp").glob("*.py"))
    return moduli


def _modulo(percorso: Path, fresco: bool):
    """`(percorso, Module)`: analizza una volta sola, poi riusa.

    Con `fresco` si rianalizza invece di riusare i nodi: serve a chi li modifica.
    """
    if not fresco and percorso in _analizzati:
        return percorso, _analizzati[percorso]
    modulo = ast.parse(percorso.read_text(encoding="utf-8"))
    if not fresco:
        _analizzati[percorso] = modulo
    return percorso, modulo


def SORGENTE() -> str:
    """Il testo del control-plane, file concatenati nell'ordine di `MODULI()`."""
    return "\n".join(p.read_text(encoding="utf-8") for p in MODULI())


def albero(fresco: bool = False) -> ast.Module:
    """Il control-plane come un unico `ast.Module`.

    I NODI dei figli sono condivisi fra le chiamate (che cosa costa 196 ms
    `ast.parse` su un file da 9.700 righe): va bene per chi legge, non per chi
    cambia qualcosa. Passa `fresco=True` se intendi modificarli, altrimenti la
    modifica resta nel nodo condiviso e i test che vengono dopo leggono un
    albero sabotato. I tre file che lo fanno sono in `MUTANO_L_ALBERO`.
    """
    parti = [_modulo(p, fresco) for p in MODULI()]
    return ast.Module(body=[nodo for _, modulo in parti for nodo in modulo.body],
                      type_ignores=[])


def funzioni() -> dict:
    """`{nome: FunctionDef}` per tutti i moduli."""
    return {n.name: n for n in albero().body if isinstance(n, ast.FunctionDef)}


def classi() -> dict:
    """`{nome: ClassDef}` per tutti i moduli."""
    return {n.name: n for n in albero().body if isinstance(n, ast.ClassDef)}


def assegnazioni() -> dict:
    """`{nome: valore}` per le assegnazioni a livello di modulo."""
    return {t.id: nodo.value for nodo in albero().body
            if isinstance(nodo, ast.Assign) for t in nodo.targets
            if isinstance(t, ast.Name)}


def dove(nome: str, fresco: bool = False) -> str:
    """Il file che definisce `nome`, per i messaggi d'errore."""
    for percorso, modulo in (_modulo(p, fresco) for p in MODULI()):
        for nodo in modulo.body:
            if isinstance(nodo, (ast.FunctionDef, ast.ClassDef)) and nodo.name == nome:
                return percorso.name
            if isinstance(nodo, ast.Assign) and any(
                    getattr(t, "id", "") == nome for t in nodo.targets):
                return percorso.name
    return f"nessun modulo del control-plane ({', '.join(p.name for p in MODULI())})"


def nodo(nome: str, fresco: bool = False) -> ast.AST:
    """Il nodo di modulo (funzione, classe o assegnazione) che si chiama `nome`.

    Sostituisce il `next(n for n in tree.body if ...)` che i test scrivevano a
    mano: quello a `StopIteration` con un messaggio che non dice niente, questo
    sa anche in che file stava cercando.

    Con `fresco=True` il nodo e' tuo e puoi modificarlo — vedi `albero()`.
    """
    for percorso, modulo in (_modulo(p, fresco) for p in MODULI()):
        for candidato in modulo.body:
            if isinstance(candidato, (ast.FunctionDef, ast.ClassDef)):
                if candidato.name == nome:
                    return candidato
            elif isinstance(candidato, ast.Assign) and any(
                    getattr(t, "id", "") == nome for t in candidato.targets):
                return candidato
    raise AssertionError(
        f"{nome} non e' piu' nel control-plane: cercato in "
        f"{', '.join(p.name for p in MODULI())}. Se dove' sta adesso, il test "
        f"va spostato sul file giusto (o il simbolo e' stato cancellato).")

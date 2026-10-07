# SPDX-License-Identifier: Apache-2.0
"""Gli harness delle baseline: che ci siano, che siano eseguibili, e che lavorino.

Le fixture delle baseline stanno nel repo da sempre; gli script che le producevano
no, e sono finiti in una cartella temporanea che il sistema svuota fra una sessione
e l'altra. Sono tornati nel repo (`tests/harness/`), e questi test sono la rete che
li tiene appesi: senza, la prima estrazione dopo che la cartella si svuota ricomincia
a verificare il niente.

Il secondo controllo e' quello che ha gia' salvato una baseline: ogni rotta citata
da uno script di baseline deve esistere davvero. Una richiesta verso una URL che non
esiste risponde `404` con una pagina HTML, che e' stabile quanto un `200` — quindi
la baseline resta verde mentre non prova niente, e non si accorge che la rotta non
è mai stata provata. È successo: `/web/enqueue` invece di `/web/tasks`.

Non si avvia nessun server qui: sono controlli su file.
"""
import ast
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/harness"

# Le coppie: ogni baseline nel repo deve avere il suo harness, e viceversa.
COPPIE = {
    "mesh_baseline.py": "mesh.sh",
    "canali_baseline.py": "canali.sh",
    "instagram_baseline.py": "instagram.sh",
    "immagini_baseline.py": "immagini.sh",
    "chat_baseline.py": "chat.sh",
    "federazione_baseline.py": "federazione.sh",
    "bottles_baseline.py": "bottles.sh",
    "tool_baseline.py": "tool.sh",
}


def _rotte_dichiarate() -> set:
    """Le rotte che il control-plane registra davvero, da `main.py` e da `cp/*.py`.

    Non si importa `main.py` (all'import scrive su `/app`, che in test non esiste):
    si leggono i decoratori `@app.route` e `@_bp.route`, che sono l'unico posto dove
    una rotta viene dichiarata.
    """
    rotte = set()
    sorgenti = [ROOT / "control-plane/main.py"]
    sorgenti += sorted((ROOT / "control-plane/cp").glob("*.py"))
    for percorso in sorgenti:
        albero = ast.parse(percorso.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in nodo.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if dec.func.attr != "route" or not dec.args:
                    continue
                primo = dec.args[0]
                if isinstance(primo, ast.Constant) and isinstance(primo.value, str):
                    rotte.add(primo.value)
    return rotte


def _rotte_citate(baseline: str) -> list:
    """Le rotte che uno script di baseline chiama davvero, costruendone il path.

    Si seguono i chiamati a `_get`/`_post`/`_delete`/`_put` dentro espressioni
    costruite: una rotta puo' arrivare anche concatenando (`f"/web/node/{x}/status"`),
    e va contata come esistente solo se il prefisso e' una rotta reale.
    """
    percorso = ROOT / "tests" / baseline
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    prefissi = set()
    for nodo in ast.walk(albero):
        if not isinstance(nodo, ast.Call) or not isinstance(nodo.func, ast.Name):
            continue
        if nodo.func.id not in ("_get", "_post", "_delete", "_put"):
            continue
        if not nodo.args or not isinstance(nodo.args[0], ast.Constant):
            continue
        valore = nodo.args[0].value
        if isinstance(valore, str) and valore.startswith("/"):
            # la query string non fa parte del path: la rotta di Instagram e'
            # richiesta due volte, una con verify token giusto e una sbagliato
            prefissi.add(valore.split("?", 1)[0])
    return sorted(prefissi)


def _corrisponde_a_una_rotta(citata: str, rotte: set) -> bool:
    """Una rotta citata corrisponde a una dichiarata, segmento per segmento.

    Le baseline costruiscono alcune rotte concatenando (`f"/mesh/node/{x}/status"`),
    e quelle vanno confrontate con la dichiarazione a parametri
    (`/mesh/node/<node_id>/status`): sono la stessa rotta. Il confronto e' per
    segmento, e un segmento dichiarato fra `<...>` fa da jolly — altrimenti
    `/mesh/node/inesistente/status` sembrerebbe inesistente, che e' il contrario
    di quello che si vuole dire.
    """
    parti = citata.strip("/").split("/")
    for rotta in rotte:
        dichiarate = rotta.strip("/").split("/")
        if len(dichiarate) != len(parti):
            continue
        if all(d.startswith("<") or d == c for d, c in zip(dichiarate, parti)):
            return True
    return False


class GliHarnessTests(unittest.TestCase):
    def test_ogni_baseline_ha_un_harness(self):
        mancanti = [b for b, h in COPPIE.items() if not (HARNESS / h).is_file()]
        self.assertEqual(mancanti, [], f"baseline senza harness: {mancanti}")

    def test_non_ci_sono_harness_senza_baseline(self):
        """Un harness per una baseline che non esiste e' uno script che non
        verifica niente, e sembra copertura."""
        sul_disco = {p.name for p in HARNESS.glob("*.sh") if not p.name.startswith("_")}
        self.assertEqual(sul_disco, set(COPPIE.values()))

    def test_il_comune_e_il_suo_harness(self):
        for nome in ("_comune.sh", *(COPPIE.values())):
            percorso = HARNESS / nome
            with self.subTest(harness=nome):
                self.assertTrue(percorso.is_file(), f"{nome} manca")
                self.assertTrue(percorso.stat().st_mode & 0o111,
                                f"{nome} non e' eseguibile")

    def test_la_sintassi_e_valida(self):
        """`bash -n` senza eseguire nulla: uno script con un errore di sintassi
        fallisce solo quando qualcuno ha gia' perso mezz'ora."""
        for percorso in sorted(HARNESS.glob("*.sh")):
            with self.subTest(harness=percorso.name):
                esito = subprocess.run(["bash", "-n", str(percorso)],
                                       capture_output=True, text=True)
                self.assertEqual(esito.returncode, 0, esito.stderr)

    def test_il_shebang_e_l_header_come_si_deve(self):
        """L'header di licenza va DOPO lo shebang: sopra, lo shebang diventa
        inerte e lo script non parte piu'."""
        for percorso in sorted(HARNESS.glob("*.sh")):
            righe = percorso.read_text(encoding="utf-8").splitlines()
            with self.subTest(harness=percorso.name):
                self.assertTrue(righe[0].startswith("#!"),
                                f"{percorso.name}: lo shebang non e' in prima riga")
                self.assertTrue(any("SPDX-License-Identifier: Apache-2.0" in r
                                    for r in righe[:3]),
                                f"{percorso.name}: header di licenza assente")

    def test_il_confronto_e_un_opzione_di_tutti(self):
        """Ogni harness accetta `--confronta`: senza, non si puo' verificare
        un'estrazione senza prima riscrivere la fixture, che è il modo tipico di
        far svanire la verifica."""
        for nome in COPPIE.values():
            testo = (HARNESS / nome).read_text(encoding="utf-8")
            with self.subTest(harness=nome):
                self.assertIn('"$@"', testo, f"{nome} non passa gli argomenti")

    def test_il_token_di_amministrazione_e_lungo(self):
        """La trappola piu' economica: sotto la soglia, il server lo rifiuta,
        ogni route protetta risponde 401 e la baseline e' verde senza aver
        provato una riga di codice utile."""
        testo = (HARNESS / "_comune.sh").read_text(encoding="utf-8")
        genera = re.search(r"printf 'a%\.0s' \{1\.\.(\d+)\}", testo)
        self.assertIsNotNone(genera, "il token di amministrazione non e' generato")
        self.assertGreaterEqual(int(genera.group(1)), 32,
                                "token troppo corto: ogni route protetta risponderebbe 401")


class LeRotteCiteTests(unittest.TestCase):
    def test_ogni_rotta_citata_esiste(self):
        rotte = _rotte_dichiarate()
        citate = []
        for baseline in COPPIE:
            citate += [(baseline, r) for r in _rotte_citate(baseline)]
        sconosciute = [(b, r) for b, r in citate
                       if not _corrisponde_a_una_rotta(r, rotte)]
        self.assertEqual(
            sconosciute, [],
            "rotte citate dalle baseline che il control-plane non registra: "
            f"{sconosciute}. Una URL inesistente risponde 404 con HTML, che e' "
            "stabile quanto un 200: la baseline passerebbe senza provarle.")

    def test_la_baseline_del_mesh_conosce_il_nodo_web(self):
        """Le cinque rotte /web/* stanno in cp/webnode.py: se la baseline le
        coprisse solo in parte, l'estrazione di quel dominio passerebbe senza
        averla verificata."""
        rotte = _rotte_dichiarate()
        per_web = {r for r in rotte if r.startswith("/web/")}
        citate = set(_rotte_citate("mesh_baseline.py"))
        mancanti = per_web - citate
        self.assertEqual(mancanti, set(),
                         f"rotte /web/* non coperte dalla baseline: {sorted(mancanti)}")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
"""Sveglia un riflessione e verifica che il percorso completo arrivi fino al sogno.

Non e' una baseline di rotte: `sogni_baseline.py` copre il quarto del refactor,
cioe' che le rotte rispondono e le guardie tengono. Qui si prova l'altra meta' —
che il dominio pensi, che la risposta del modello venga riconosciuta come sogno, e
che la scena finisca nel diario.

Il percorso e' lungo e ognun pezzo puo' rompersi in silenzio:

    materiale di identita'  ->  prompt  ->  chiamata al modello  ->  `parse_dream`
        ->  filtro sui duplicati  ->  scena nel diario  ->  risposta della rotta

Se uno di questi smette di funzionare, le rotte continuano a rispondere 200 e non
si accorge niente. Percio' il caso che conta non e' il codice di risposta: e' che il
diario contenga una scena, e che la risposta dica che il riflessione ha prodotto
qualcosa.

Il finto (`tests/mock_openai.py`) risponde in forma di sogno — due righe `scena:` e
`disegno:` — perche' e' il formato che `parse_dream` riconosce. Una risposta
qualsiasi farebbe fallire il parsing e il dominio direbbe "nessun sogno", che e'
una risposta corretta: renderebbe questa verifica inutile senza dirlo.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")
TOKEN = os.environ.get("DREAM_REVIEW_TOKEN", "")

SOTTOPESI = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def _richiesta(metodo: str, path: str, payload=None, headers: dict | None = None) -> dict:
    dati = json.dumps(payload).encode("utf-8") if payload is not None else None
    richiesta = urllib.request.Request(BASE_URL + path, data=dati,
                                       headers={"Content-Type": "application/json",
                                                **(headers or {})},
                                       method=metodo)
    try:
        with urllib.request.urlopen(richiesta, timeout=120) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code, "body": errore.read().decode("utf-8", "replace")}


def _get(path: str) -> dict:
    return _richiesta("GET", path)


def _post(path: str, payload=None, headers: dict | None = None) -> dict:
    return _richiesta("POST", path, payload, headers)


def raccogli() -> dict:
    caso = {}

    # lo stato prima: dire che i sogni sono disattivati e' il modo piu' rapido per
    # accorgersi che l'harness non ha passato le variabili d'ambiente
    caso["prima"] = _get("/persona/dreams")

    # la sveglia. Il token protegge questa rotta, quindi senza si arriva al 503 e
    # non si verifica niente: e' un caso che vale la pena avere comunque, per
    # vedere che la guardia c'e'.
    caso["run_senza_token"] = _post("/persona/dream", {},
                                    {"Authorization": "Bearer sbagliato"})
    caso["run"] = _post("/persona/dream", {}, SOTTOPESI)

    # e il diario: qui si vede se il sogno e' passato dal modello e ha prodotto
    # qualcosa. E' la verifica vera, e per questo il confronto guarda il conteggio
    # e non il testo.
    caso["dopo"] = _get("/persona/dreams")

    return caso


_ID_CASUALE = re.compile(r"^(prop|personadream)-[0-9a-f]{6,}$")


def _e_id_casuale(valore: str) -> bool:
    """Gli id di sogno e proposta sono hash del contenuto: cambiano ogni volta."""
    return bool(_ID_CASUALE.match(valore))


def _azzera(nodo) -> None:
    """Via tutto il volatile, tieni il resto — e il resto è il punto.

    Il modello è finto e restituisce sempre lo stesso testo, quindi `text`,
    `scena` e `disegno` qui sono deterministici e si confrontano: sono il passaggio
    dal modello alla proposta, cioè esattamente ciò che questa baseline deve
    sorvegliare. Azzerarli la farebbe passare anche con un parser che sputa
    spazzatura, purché produca una proposta.

    Vengono azzerati gli orari (`last_run`, `created_at`, `*_ts`), i testi di
    errore (contengono host e porte della macchina) e gli id, che sono hash del
    contenuto e cambiano a ogni esecuzione. Se il percorso si rompe, i conteggi
    restano a zero e la baseline se ne accorge.
    """
    if isinstance(nodo, dict):
        for chiave, valore in nodo.items():
            if isinstance(valore, str) and (
                chiave.endswith("_ts")
                or "_at" in chiave
                or chiave in ("last_run", "next_run", "error", "detail", "node_id")
                or _e_id_casuale(valore) and chiave == "id"
            ):
                nodo[chiave] = "<variabile>"
            else:
                _azzera(valore)
    elif isinstance(nodo, list):
        for voce in nodo:
            _azzera(voce)


def _normalizza(testo: str) -> str:
    if not testo.strip():
        return "<vuoto>"
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera(dati)
    return json.dumps(dati, sort_keys=True)


def _norm(testo: str) -> str:
    return testo.replace("\n", "\\n")


def confronta(vecchio: dict, nuovo: dict) -> int:
    problemi = 0
    for chiave in sorted(set(vecchio) | set(nuovo)):
        prima = vecchio.get(chiave, {})
        dopo = nuovo.get(chiave, {})
        a = (prima.get("status"), _normalizza(prima.get("body", "")))
        b = (dopo.get("status"), _normalizza(dopo.get("body", "")))
        if a == b:
            print(f"  uguale        {chiave:<24} {a[0]} {_norm(a[1])[:80]!r}")
            continue
        problemi += 1
        print(f"  DIVERSO       {chiave}")
        print(f"      prima:  {a[0]} {_norm(a[1])[:200]!r}")
        print(f"      dopo:   {b[0]} {_norm(b[1])[:200]!r}")
    return problemi


def main() -> int:
    if "--prova" in sys.argv:
        # La prova che questo harness esiste per dare: il percorso completo produce
        # una proposta in attesa di revisione. Non un 200 — quello lo darebbe
        # anche con il modello spento.
        risultato = raccogli()
        risposta = risultato.get("run") or {}
        if risposta.get("status") != 200:
            print(f"  FALLITO: la rotta ha risposto {risposta.get('status')}")
            return 1
        try:
            corpo = json.loads(risposta.get("body", ""))
        except (json.JSONDecodeError, ValueError):
            print("  FALLITO: la risposta non e' JSON")
            return 1
        sogno = corpo.get("report") or {}
        proposte = sogno.get("proposals") or []
        stato = (corpo.get("dream") or {}).get("last_status")
        if not proposte:
            scartati = sogno.get("discarded") or []
            motivo = scartati[0].get("reason") if scartati else "nessuna proposta"
            print(f"  FALLITO: il sogno girato non ha prodotto proposte ({motivo})")
            return 1
        print(f"  OK: sogno prodotto, last_status={stato}, "
              f"{len(proposte)} {'proposta' if len(proposte)==1 else 'proposte'} in revisione")
        return 0
    if "--confronta" not in sys.argv:
        # Genera la fixture. Va lanciata con l'harness: da sola, questa baseline
        # troverebbe i sogni disattivati e produrrebbe quattro 503 — una fixture
        # che sembra pulita e non verifica niente.
        from pathlib import Path

        risultato = raccogli()
        destinazione = Path("tests/fixtures/sogni_genera.json")
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        destinazione.write_text(json.dumps(risultato, indent=2, sort_keys=True) + "\n", "utf-8")
        for chiave, valore in sorted(risultato.items()):
            print(f"  {valore['status']}  {chiave:<24} {_norm(_normalizza(valore['body']))[:1400]!r}")
        print(f"\n  scritto {destinazione}")
        return 0
    from pathlib import Path

    destinazione = Path("tests/fixtures/sogni_genera.json")
    if not destinazione.exists():
        print(f"  {destinazione} non esiste: genera prima la fixture")
        return 1
    vecchio = json.loads(destinazione.read_text("utf-8"))
    nuovo = raccogli()
    if "--diff" in sys.argv:
        # Il confronto normalizzato dice "diverso" ma non cosa. Il grezzo non
        # serve: sui corpi lunghi diverge sempre sull'orario, che e' proprio cio'
        # che `_azzera` rimuove, e nasconde il resto.
        for chiave in sorted(set(vecchio) | set(nuovo)):
            a = _normalizza(vecchio.get(chiave, {}).get("body", ""))
            b = _normalizza(nuovo.get(chiave, {}).get("body", ""))
            if a == b:
                continue
            # Il corpo e' una riga sola e lunghissima: un diff che la stampa
            # intera non serve a niente, serve sapere DOVE divergono.
            comune = 0
            for x, y in zip(a, b):
                if x != y:
                    break
                comune += 1
            print(f"  DIFF {chiave}  (al carattere {comune})")
            print(f"      atteso: ...{a[max(comune - 60, 0):comune + 90]}")
            print(f"      trovato: ...{b[max(comune - 60, 0):comune + 90]}")
    problemi = confronta(vecchio, nuovo)
    print(f"\n  {problemi} differenze su {len(set(vecchio))} richieste")
    return 1 if problemi else 0


if __name__ == "__main__":
    raise SystemExit(main())

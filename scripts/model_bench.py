# SPDX-License-Identifier: Apache-2.0
"""Banco di prova per modelli locali: domande con esito verificabile da codice.

Perche' esiste: giudicare un modello "a sensazione" dice poco, e di solito dice
la cosa sbagliata (una risposta fluente suona giusta anche quando e' falsa). Qui
ogni sonda ha un ESITO verificabile da una funzione: la capitale e' giusta o no,
il codice gira o no, il JSON si parse o no. Il confronto fra modelli diventa una
tabella, non un'opinione.

Un dettaglio che conta piu' di quanto sembri: il tetto di token. Un modello che
non si ferma (o che "pensa" a lungo senza rispondere — succede con le famiglie
reasoning) va fermato, altrimenti blocca l'intera sessione. Ogni sonda ha un
budget di parole; se lo sfora, l'esito e' LOOP/LENTO, non "sbagliato": e' un modo
diverso di non essere affidabile, e va distinto da una risposta sbagliata.

Uso:

    python3 scripts/model_bench.py                        # i modelli di default
    python3 scripts/model_bench.py --models minicpm5:latest,qwen3:8b
    python3 scripts/model_bench.py --node macbook         # pinnati su un nodo
    python3 scripts/model_bench.py --think false          # reasoning forzato off
    python3 scripts/model_bench.py --json                 # risultato come JSON

I modelli si pinnano col suffisso ::<nodo> cosi' il confronto avviene sullo STESSO
backend: due modelli su macchine diverse non sono confrontabili (GPU diverse,
latenza di rete diversa). Solo libreria standard, come mesh_health.py: gira anche
sul Windows del collega senza installare niente.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

CP_DEFAULT = os.environ.get("CONTROL_PLANE_URL", "http://localhost:8085").rstrip("/")
DEFAULT_MODELS = ("openbmb/minicpm5:latest", "qwen2:0.5b", "qwen3:8b", "gemma4:e4b")
TIMEOUT_S = 180
MAX_SECONDS = 150   # rete di sicurezza: il segnale vero e' il budget di TOKEN


# ── SONDE ─────────────────────────────────────────────────────────────────────
# Ogni sonda e' (nome, prompt, giudice, esito atteso, budget_di_parole). Il
# giudice torna True/False (o una tupla (ok, nota)) a partire dalla risposta.
def _json(risposta):
    m = re.search(r"\{.*\}", risposta, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def estrai_codice(risposta):
    """Estrae il codice da una risposta, con o senza triple backtick."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", risposta, re.S | re.I)
    if m:
        return m.group(1).strip()
    return re.sub(r"^```(?:python)?\s*|\s*```$", "", risposta, flags=re.I | re.S).strip()


def codice_ok(risposta, file_uscita="/tmp/_model_bench_pal.py"):
    """Vero se la funzione generata gira E passa gli assert. Non si fida del
    testo della risposta: esegue il codice."""
    sorgente = estrai_codice(risposta)
    if "def is_palindrome" not in sorgente:
        return False, "nessuna funzione is_palindrome nel codice"
    driver = ("\n\nimport sys\nassert is_palindrome('radar') is True\n"
              "assert is_palindrome('Ciao') is False\nprint('CODICE-OK')\n")
    with open(file_uscita, "w") as f:
        f.write(sorgente + driver)
    try:
        esito = subprocess.run([sys.executable, file_uscita],
                               capture_output=True, text=True, timeout=15)
    except Exception as e:
        return False, f"timeout/errore: {type(e).__name__}"
    if "CODICE-OK" in esito.stdout:
        return True, "gira e passa gli assert"
    return False, "gira ma sbaglia: " + (esito.stderr or esito.stdout).strip().splitlines()[-1][:70]


PROBE = [
    ("fatto_capitale", "Qual e' la capitale dell'Australia? Rispondi con una sola parola.",
     lambda a: ("canberra" in a.lower() and "sydney" not in a.lower()), "Canberra", 12),
    ("matematica_orario", "Un treno parte alle 14:30 e viaggia 90 minuti. A che ora arriva? Rispondi solo con l'orario HH:MM.",
     lambda a: "16:00" in a.replace("16.00", "16:00"), "16:00", 10),
    ("matematica_sconto", "Un maglione costa 80 euro e c'e' uno sconto del 25%. Quanto pago? Rispondi solo col numero.",
     lambda a: bool(re.search(r"\b60\b", a)) and "20" not in a, "60", 10),
    ("logica_famiglia", "In una famiglia ci sono due padri e due figli, ma solo tre persone. Com'e' possibile? Spiega in una frase.",
     lambda a: ("nonno" in a.lower()), "nonno, padre, figlio", 30),
    ("conteggio_r", "Quante lettere 'r' contiene la parola 'frigorifero'? Rispondi solo con il numero.",
     lambda a: bool(re.search(r"\b3\b", a)), "3", 10),
    ("codice_palindromo", "Scrivi SOLO il codice di una funzione Python is_palindrome(s) che torna True se la stringa e' un palindromo (ignorando le maiuscole). Nient'altro.",
     codice_ok, "funzione che gira", 120),
    ("json", "Rispondi SOLO con un JSON valido, senza altro testo: {\"citta\": \"...\", \"abitanti\": <numero>} per Venezia.",
     lambda a: (lambda d: bool(d) and "citta" in d and "abitanti" in d)(_json(a)), "JSON con i campi", 20),
    ("allucinazione", "Chi ha scritto il romanzo 'Il canto del merlo azzurro'? Se non lo sai, rispondi 'non lo so'.",
     lambda a: any(x in a.lower() for x in ("non lo so", "non lo conosco", "non sono a conoscenza", "non esiste", "non risulta")), "ammette di non sapere", 30),
]

# ── STREAMING (puro, testabile senza rete) ─────────────────────────────────────
def consume_stream(linee, max_tok):
    """Da un iteratore di righe SSE a `(testo, limite)`.

    `linee` e' una sequenza di stringhe gia' decodificate, come le righe del body
    streamato: "data: {...}" con un delta, "data: [DONE]", o rumore da ignorare.
    Il limite e' "ok", "loop" (sfora il budget di parole: non si ferma) o "lento"
    (non conclude in tempo). Separato dalla rete apposta: e' qui che si decide
    quando un modello "non si ferma", ed e' la parte che vale la pena testare.
    """
    testo = ""
    n_tokeni = 0
    t0 = time.time()
    for riga in linee:
        riga = str(riga)
        if not riga.startswith("data:"):
            continue
        dato = riga[5:].strip()
        if dato == "[DONE]":
            return testo.strip(), "ok"
        try:
            pezzo = json.loads(dato)["choices"][0].get("delta", {}).get("content") or ""
        except Exception:
            continue
        testo += pezzo
        n_tokeni += len(pezzo.split())
        if n_tokeni > max_tok:
            return testo.strip(), "loop"
        if time.time() - t0 > MAX_SECONDS:
            return testo.strip(), "lento"
    return testo.strip(), "ok"   # stream chiuso senza [DONE]


def ask(modello, prompt, max_tok=40, cp=CP_DEFAULT, think=None, opener=None, timeout=TIMEOUT_S):
    """Una completion verso il CP, in stream. `think` e' il flag JSON esplicito:
    None = lascia decidere al CP (che di default spegne il reasoning)."""
    corpo = {"model": modello, "messages": [{"role": "user", "content": prompt}],
             "stream": True, "temperature": 0}
    if think is not None:
        corpo["think"] = bool(think)
    dati = json.dumps(corpo).encode()
    richiesta = urllib.request.Request(f"{cp}/v1/chat/completions", data=dati,
                                       headers={"Content-Type": "application/json"})
    apre = opener or urllib.request.urlopen
    with apre(richiesta, timeout=timeout) as risposta:
        righe = (riga.decode(errors="replace") for riga in risposta)
        testo, limite = consume_stream(righe, max_tok)
    return testo, limite


def segna(giudice, risposta):
    esito = giudice(risposta)
    return esito if isinstance(esito, tuple) else (esito, "")

# ── ESECUZIONE ────────────────────────────────────────────────────────────────
def esegui(modelli, cp=CP_DEFAULT, think=None, progresso=None):
    """Esegue tutte le sonde su tutti i modelli. Ritorna una struttura riusabile
    da chi vuole il JSON, non solo la tabella stampata."""
    risultati = {}
    for modello in modelli:
        righe = {}
        for nome, prompt, giudice, atteso, max_tok in PROBE:
            try:
                risposta, limite = ask(modello, prompt, max_tok, cp=cp, think=think)
                if limite == "ok":
                    ok, nota = segna(giudice, risposta)
                else:
                    ok, nota = False, f"non conclude ({limite})"
                righe[nome] = {"ok": ok, "limite": limite, "risposta": risposta, "nota": nota}
                if progresso:
                    progresso(modello, nome, ok, limite, risposta)
            except urllib.error.HTTPError as e:
                righe[nome] = {"ok": None, "limite": "err", "nota": f"HTTP {e.code}"}
            except urllib.error.URLError as e:
                righe[nome] = {"ok": None, "limite": "err", "nota": f"rete: {e.reason}"}
            except Exception as e:
                righe[nome] = {"ok": None, "limite": "err", "nota": f"{type(e).__name__}: {str(e)[:80]}"}
        risultati[modello] = righe
    return risultati


def riepilogo(risultati):
    out = {}
    for modello, righe in risultati.items():
        buone = sum(1 for r in righe.values() if r["ok"] is True)
        loop = sum(1 for r in righe.values() if r["limite"] == "loop")
        lenti = sum(1 for r in righe.values() if r["limite"] == "lento")
        out[modello] = {"corrette": buone, "su": len(PROBE), "loop": loop, "lenti": lenti}
    return out


def tabella(risultati):
    modelli = list(risultati)
    righe = []
    testa = "{:<22}".format("sonda") + "".join("{:<14}".format(m.split(":")[0][:13]) for m in modelli)
    righe.append(testa)
    righe.append("-" * len(testa))
    for nome, _p, _g, _a, _m in PROBE:
        fila = "{:<22}".format(nome)
        for modello in modelli:
            r = risultati[modello][nome]
            cella = "ERR" if r["ok"] is None else ("LOOP" if r["limite"] == "loop"
                    else ("LENTO" if r["limite"] == "lento" else ("OK " if r["ok"] else "NO ")))
            fila += "{:<14}".format(cella)
        righe.append(fila)
    return "\n".join(righe)


def build_parser():
    p = argparse.ArgumentParser(prog="model_bench",
                                description="Confronto oggettivo fra modelli locali (esiti verificabili)")
    p.add_argument("--models", default=",".join(DEFAULT_MODELS),
                   help="modelli separati da virgola (default: i quattro locali)")
    p.add_argument("--node", default="", help="pinna ogni modello con ::<nodo> (stesso backend)")
    p.add_argument("--cp", default=CP_DEFAULT, help=f"control-plane (default {CP_DEFAULT})")
    p.add_argument("--think", default=None, choices=["true", "false"],
                   help="flag JSON think esplicito: None lascia decidere al CP")
    p.add_argument("--json", action="store_true", help="stampa il risultato come JSON")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    modelli = [f"{m}::{args.node}" if args.node and "::" not in m else m
               for m in args.models.split(",") if m.strip()]
    think = None if args.think is None else (args.think == "true")

    def progresso(modello, nome, ok, limite, risposta):
        segno = "OK" if ok else ("LOOP" if limite == "loop" else ("LENTO" if limite == "lento" else "NO"))
        print(f"  {modello} {nome:<20} {segno}  {risposta[:70].replace(chr(10), ' ')}", flush=True)

    print(f"== model_bench: {len(modelli)} modelli, {len(PROBE)} sonde ==", flush=True)
    risultati = esegui(modelli, cp=args.cp, think=think, progresso=progresso)

    if args.json:
        print(json.dumps({"risultati": risultati, "riepilogo": riepilogo(risultati)},
                         ensure_ascii=False, indent=2))
        return 0

    print()
    print(tabella(risultati))
    print()
    for modello, r in riepilogo(risultati).items():
        extra = f"  loop={r['loop']} lenti={r['lenti']}" if (r["loop"] or r["lenti"]) else ""
        print(f"  {modello:<42} {r['corrette']}/{r['su']}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

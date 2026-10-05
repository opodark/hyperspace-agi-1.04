# SPDX-License-Identifier: Apache-2.0
"""Registra le risposte reali di `/v1/chat/completions`, per confrontarle dopo
un'estrazione.

Quarta baseline del gruppo, e di gran lunga la più importante. Le altre tre
guardavano se una rotta rispondeva con il codice giusto; qui la risposta è sempre
`200`, e ciò che conta è la FORMA del corpo: un `_chunk_finale` sbagliato non
fallisce nessun test, restituisce un JSON valido e il nodo chiamante ci scrive
sopra una conversazione che non è mai uscita.

Quindi qui non si fotografa "funziona": si fotografa il testo esatto.

Per farlo serve un modello dietro. Il server vero non ne ha (OLLAMA non è in
avvio nella sessione di test), e senza modello la rotta prende solo i rami di
errore — che è comunque una baseline utile, ed è la parte `rami_di_errore` qui
sotto. Per il percorso felice si usa `MOCK_OLLAMA=1`, che fa rispondere il
backend locale con una risposta OpenAI finita, controllata da `MOCK_OLLAMA_REPLY`
e `MOCK_OLLAMA_TOOL`. Con quello si vedono i pezzi SSE, la modalita' think e il
passaggio dei tool al client.

    PYTHONPATH=. BASE_URL=http://127.0.0.1:8085 ./.venv/bin/python3 tests/chat_baseline.py
    ... --confronta

Le richieste che non hanno bisogno di un modello funzionano (OPTIONS, corpo
malformato, modello inesistente) girano sempre, e sono quelle che il confronto
controlla a ogni estrazione.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8085").rstrip("/")

# I campi che cambiano a ogni esecuzione. In una risposta SSE sono i timestamp
# di `created` e i tempi di `_respond_result`.
VOLATILI = ("created", "ts", "duration_ms", "durata_ms", "elapsed_ms", "at")


def _post(path: str, payload, headers: dict | None = None) -> dict:
    if isinstance(payload, (dict, list)):
        corpo = json.dumps(payload).encode("utf-8")
    else:
        corpo = payload
    richiesta = urllib.request.Request(
        BASE_URL + path, data=corpo,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(richiesta, timeout=120) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace"),
                    "content_type": risposta.headers.get("Content-Type", "")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code,
                "body": errore.read().decode("utf-8", "replace"),
                "content_type": errore.headers.get("Content-Type", "")}


def _options(path: str) -> dict:
    richiesta = urllib.request.Request(BASE_URL + path, method="OPTIONS")
    try:
        with urllib.request.urlopen(richiesta, timeout=20) as risposta:
            return {"status": risposta.status,
                    "body": risposta.read().decode("utf-8", "replace"),
                    "content_type": risposta.headers.get("Content-Type", "")}
    except urllib.error.HTTPError as errore:
        return {"status": errore.code,
                "body": errore.read().decode("utf-8", "replace"),
                "content_type": errore.headers.get("Content-Type", "")}


def raccogli() -> dict:
    modello = os.environ.get("MOCK_MODEL", "modello-di-prova")
    base = {"model": modello, "messages": [{"role": "user", "content": "ciao"}]}
    return {
        # I rami che non dipendono da un modello: girano sempre.
        "options": _options("/v1/chat/completions"),
        "corpo_malformato": _post("/v1/chat/completions", b"{non e' json"),
        "messages_assenti": _post("/v1/chat/completions", {"model": modello}),
        "modello_inesistente": _post("/v1/chat/completions",
                                     {**base, "model": "nessun-modello"}),
        "senza_stream": _post("/v1/chat/completions", {**base, "stream": False}),
        "con_stream": _post("/v1/chat/completions", {**base, "stream": True}),
        "think_richiesto": _post("/v1/chat/completions",
                                 {**base, "chat_template_kwargs": {"thinking": True}}),
        "think_non_richiesto": _post("/v1/chat/completions",
                                     {**base, "chat_template_kwargs": {"thinking": False}}),
        "con_tools": _post("/v1/chat/completions", {**base, "tools": [
            {"type": "function", "function": {"name": "tool_di_prova_client",
                                              "description": "un tool che il CP non conosce"}}]}),
        "tool_choice_none": _post("/v1/chat/completions", {
            **base, "tools": [{"type": "function", "function": {"name": "tool_di_prova_client"}}],
            "tool_choice": "none"}),
        "system_prompt": _post("/v1/chat/completions", {**base, "messages": [
            {"role": "system", "content": "sei un nodo della mesh"},
            {"role": "user", "content": "ciao"}]}),
        "max_tokens_zero": _post("/v1/chat/completions", {**base, "max_tokens": 0}),
        "temperature_estrema": _post("/v1/chat/completions", {**base, "temperature": 99}),
        "nodo_pinnato_inesistente": _post("/v1/chat/completions",
                                          {**base, "model": "⛁ nessun-nodo ⛁"}),
    }


def _azzera_orari(nodo) -> None:
    if isinstance(nodo, dict):
        for chiave, valore in nodo.items():
            if chiave in VOLATILI or chiave.endswith("_ts"):
                nodo[chiave] = "<orario>"
            else:
                _azzera_orari(valore)
    elif isinstance(nodo, list):
        for voce in nodo:
            _azzera_orari(voce)


def _normalizza_corpo(testo: str) -> str:
    """Un body SSE e' una successione di `data: {...}`: si normalizza ogni pezzo
    come JSON, cosi' l'ordine delle chiavi non conta e i campi volatili spariscono.
    Un body che non e' SSE resta grezzo."""
    righe = [r for r in testo.splitlines() if r.strip()]
    if righe and all(r.startswith("data:") for r in righe):
        pezzi = []
        for riga in righe:
            pezzo = riga[len("data:"):].strip()
            if pezzo == "[DONE]":
                pezzi.append("[DONE]")
                continue
            try:
                dati = json.loads(pezzo)
            except (json.JSONDecodeError, ValueError):
                pezzi.append(pezzo)
                continue
            _azzera_orari(dati)
            pezzi.append(json.dumps(dati, sort_keys=True))
        return "\n".join(pezzi)
    try:
        dati = json.loads(testo)
    except (json.JSONDecodeError, ValueError):
        return testo.strip()
    _azzera_orari(dati)
    return json.dumps(dati, sort_keys=True)


def _appiattisci(risposta: dict) -> str:
    corpo = _normalizza_corpo(risposta.get("body", ""))
    tipo = risposta.get("content_type", "").split(";")[0].strip()
    return f"{risposta['status']} {tipo} {corpo}"


def confronta(vecchio: dict, nuovo: dict) -> int:
    problemi = 0
    for chiave in sorted(set(vecchio) | set(nuovo)):
        a, b = vecchio.get(chiave), nuovo.get(chiave)
        if a is None or b is None:
            print(f"  SOLO IN UNO   {chiave}")
            problemi += 1
        elif _appiattisci(a) != _appiattisci(b):
            print(f"  DIVERSO       {chiave}")
            print(f"      prima: {_appiattisci(a)[:300]}")
            print(f"      dopo:  {_appiattisci(b)[:300]}")
            problemi += 1
        else:
            print(f"  uguale        {chiave}  ({_appiattisci(a)[:70]})")
    return problemi


def main() -> int:
    if "--confronta" in sys.argv:
        vecchio = json.loads(Path("tests/fixtures/chat_baseline.json").read_text("utf-8"))
        nuovo = raccogli()
        problemi = confronta(vecchio, nuovo)
        print(f"\n  {problemi} differenze su {len(set(vecchio) | set(nuovo))} richieste")
        return 1 if problemi else 0

    risultato = raccogli()
    destinazione = Path("tests/fixtures/chat_baseline.json")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(json.dumps(risultato, indent=2, sort_keys=True) + "\n", "utf-8")
    for chiave, valore in sorted(risultato.items()):
        corpo = _normalizza_corpo(valore["body"])
        print(f"  {valore['status']}  {chiave:<26} {corpo[:90]!r}")
    print(f"\n  scritto {destinazione}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
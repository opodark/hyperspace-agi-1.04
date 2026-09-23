#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Il ritratto di sé: genera l'immagine di profilo e la mette dove si può.

Perché esiste (2026-09-22): una personalità social ha bisogno di un volto che resti
lo stesso, e il volto è una dichiarazione. Il documento di identità dice "non ho un
corpo": quindi la vetrina visiva si ricava **dal documento** (`shared/showcase.py`),
e i conflitti si vedono prima di generare — non dopo aver pubblicato.

Due cose che l'API di Telegram non permette a un bot, verificate il 2026-09-22 sulla
documentazione (Bot API + MTProto):

  - **la foto del bot stesso**: non esiste un metodo per cambiarla (ci sono
    setMyName/Description/ShortDescription/Commands; per sé, niente). Resta
    @BotFather `/setuserpic`: qui si stampa l'istruzione esatta col file generato.
  - **le Storie**: `stories.sendStory` è un metodo MTProto da account utente (serve
    Premium) o da canale con abbastanza boost e il diritto `post_stories`; la Bot API
    non ha metodi per le storie. Quindi "storia" qui diventa un **post del suo canale
    con scadenza a 24 ore** — vedi docs/social.md.

Uso:
    python scripts/ritratto.py --check                 # cosa manca, senza generare
    python scripts/ritratto.py --crea                  # genera la candidata (5 min)
    python scripts/ritratto.py --crea --scena "..."/--seed N
    python scripts/ritratto.py --foto <file> --chat @canale   # foto del CANALE
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.showcase import (istruzione_botfather, negativo_ritratto,  # noqa: E402
                             prompt_ritratto, verifica_vetrina,
                             vetrina_dal_documento)

TOKEN_ENV_DEFAULT = ROOT / "data" / "telegram-bot.env"
# L'identità viva è quella del runtime: è il file montato nel control-plane
# (`data/runtime/data` → `/app/data`), quello che il CP legge e riscrive, e la
# stessa scelta che fa `scripts/start.ps1`. Il file nel repo resta il **seme**: se il
# runtime non c'è, si parte da lì. L'ordine inverso è ciò che il 2026-09-23 ha
# prodotto due documenti divergenti — la vetrina scritta da Aurora in uno, le sue
# osservazioni e i suoi sogni nell'altro (version 3 contro version 4).
PERSONA_CANDIDATI = (
    ROOT / "data" / "runtime" / "data" / "persona-aurora.json",
    ROOT / "data" / "runtime" / "data" / "persona.json",
    ROOT / "data" / "persona-aurora.json",
    ROOT / "data" / "persona.json",
)

# Le sezioni su cui due documenti NON possono divergere: quelle che decidono un
# volto o un confine. Osservazioni e versioni possono essere diverse — il runtime
# evolve — e non è un problema. Nemmeno i `legami` si confrontano: sono dati di una
# persona (un handle, delle parole dette a lei), e un documento-seme in un repo
# pubblico non può portarli: confrontarli produrrebbe un avviso che suona sempre.
SEZIONI_DI_IDENTITA = ("purpose", "tone", "values", "boundaries", "capabilities",
                       "limitations", "vetrina")


def leggi_env_file(path: Path) -> dict:
    """Legge un file KEY=VALUE ignorando commenti e righe vuote (come start-telegram.ps1)."""
    valori: dict = {}
    if not path.is_file():
        return valori
    for riga in path.read_text(encoding="utf-8", errors="replace").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, _, valore = riga.partition("=")
        valori[chiave.strip()] = valore.strip().strip('"').strip("'")
    return valori


def token_canale(nome: str = "") -> str:
    """Il token di canale dal .env (default: 'comfy', poi 'telegram').

    Serve per parlare al control-plane: la coda immagini è una rotta di canale.
    """
    clienti = leggi_env_file(ROOT / ".env").get("CHANNEL_CLIENTS", "")
    voci = {}
    for voce in clienti.split(";"):
        if "=" in voce:
            chiave, _, valore = voce.partition("=")
            voci[chiave.strip().lower()] = valore.strip()
    if nome:
        return voci.get(nome.strip().lower(), "")
    return voci.get("comfy") or voci.get("telegram") or ""


def bot_token(percorso_env: Path) -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            or leggi_env_file(percorso_env).get("TELEGRAM_BOT_TOKEN", "").strip())


def trova_persona(esplicito: str = "") -> Path | None:
    """Il documento di identità visto DALLA MACCHINA (non il percorso del container)."""
    if esplicito:
        candidato = Path(esplicito)
        return candidato if candidato.is_file() else None
    from_env = os.getenv("PERSONA_FILE", "").strip()
    if from_env and not from_env.startswith("/app"):
        candidato = Path(from_env)
        if candidato.is_file():
            return candidato
    for candidato in PERSONA_CANDIDATI:
        if candidato.is_file():
            return candidato
    return None


def chiama(json_url: str, token: str, *, metodo: str = "get", payload: dict | None = None,
           timeout: float = 30.0) -> tuple[int, dict]:
    """GET/POST JSON verso il control-plane. Non solleva: ritorna (0, errore)."""
    try:
        if metodo == "post":
            risposta = requests.post(json_url, json=payload or {},
                                     headers={"X-Hyperspace-Channel-Token": token},
                                     timeout=timeout)
        else:
            risposta = requests.get(json_url, headers={"X-Hyperspace-Channel-Token": token},
                                    timeout=timeout)
    except requests.RequestException as e:
        return 0, {"errore": str(e)[:160]}
    try:
        return risposta.status_code, risposta.json()
    except ValueError:
        return risposta.status_code, {"errore": risposta.text[:160]}


def telegram(api: str, metodo: str, **params) -> tuple[int, dict, str]:
    """Una chiamata alla Bot API. Non solleva per un 4xx: il motivo serve leggerlo."""
    try:
        risposta = requests.post(f"{api}/{metodo}", json=params, timeout=60)
    except requests.RequestException as e:
        return 0, {}, str(e)[:160]
    try:
        return risposta.status_code, risposta.json(), ""
    except ValueError:
        return risposta.status_code, {}, risposta.text[:160]


def divergenze_documenti(percorsi) -> list[str]:
    """Dove due documenti di identità non dicono la stessa cosa.

    Si confrontano solo le sezioni che decidono un volto o un confine: il runtime
    evolve (osservazioni, sogni, versioni) e pretendere file identici sarebbe un
    avviso che suona sempre. Il 2026-09-23 i due documenti erano a versioni diverse,
    uno con la vetrina e uno senza, e nessuno se ne sarebbe accorto fino a un
    ritratto con la faccia sbagliata: ecco perché il controllo esiste.
    """
    documenti = []
    for percorso in percorsi:
        try:
            testo = Path(percorso).read_text(encoding="utf-8")
            documenti.append((Path(percorso), json.loads(testo)))
        except (OSError, ValueError):
            continue
    if len(documenti) < 2:
        return []
    riferimento_percorso, riferimento = documenti[0]
    avvisi: list[str] = []
    for percorso, documento in documenti[1:]:
        for sezione in SEZIONI_DI_IDENTITA:
            primo = json.dumps(riferimento.get(sezione), sort_keys=True, ensure_ascii=False)
            secondo = json.dumps(documento.get(sezione), sort_keys=True, ensure_ascii=False)
            if primo != secondo:
                avvisi.append(f"{sezione}: {percorso} diverso da {riferimento_percorso}")
    return avvisi


def accoda_ritratto(base: str, token: str, vetrina: dict, *, scena: str = "",
                    seed: int | None = None, destinazione: str = "") -> tuple[int, dict]:
    """Mette in coda il ritratto. Con `destinazione` il driver lo consegna in chat.

    La consegna non passa dal control-plane: lui decide *dove* (una chat, un id),
    il file ce l'ha il driver — che è l'unico a poter parlare con Telegram.
    """
    payload = {
        "prompt": prompt_ritratto(vetrina, scena=scena),
        "negativo": negativo_ritratto(vetrina),
        "larghezza": int(vetrina["larghezza"]),
        "altezza": int(vetrina["altezza"]),
        "passi": int(vetrina["passi"]),
        "seed": int(vetrina["seed"] if seed is None else seed),
        "richiedente": "ritratto",
        "destinazione": destinazione.strip(),
    }
    return chiama(f"{base}/image/generate", token, metodo="post", payload=payload)


def aspetta_job(base: str, token: str, job_id: str, *, attesa_s: float = 480.0,
                ogni_s: float = 5.0, log=print) -> dict:
    """Aspetta che il job finisca. Ritorna il job (o l'ultimo stato visto)."""
    scadenza = time.monotonic() + max(0.0, attesa_s)
    ultimo: dict = {}
    while time.monotonic() < scadenza:
        stato, dati = chiama(f"{base}/image/job/{job_id}", token, timeout=20)
        if stato == 200:
            ultimo = dati.get("job") or {}
            if ultimo.get("stato") in ("done", "failed"):
                return ultimo
            log(f"  ... {ultimo.get('stato', '?')} ({int(scadenza - time.monotonic())}s di attesa rimasti)")
        elif stato == 404:
            log("  ... job non ancora visibile nella coda")
        else:
            log(f"  ... lettura dello stato fallita (HTTP {stato})")
        time.sleep(max(1.0, ogni_s))
    return ultimo


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # I testi hanno accenti e un trattino lungo: su console Windows la codifica
        # di default li troncherebbe proprio mentre si leggono.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Genera il ritratto di sé e lo mette dove l'API lo permette.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verifica e basta (default)")
    parser.add_argument("--crea", action="store_true", help="genera la candidata")
    parser.add_argument("--foto", default="", help="metti questa immagine come foto di una CHAT (canale)")
    parser.add_argument("--chat", default="",
                        help="id o @username della chat: con --foto è la chat amministrata, "
                             "con --crea è dove il driver consegna il ritratto")
    parser.add_argument("--scena", default="", help="scena del ritratto (default: quella dichiarata)")
    parser.add_argument("--seed", type=int, default=None, help="un altro seed = un'altra candidata")
    parser.add_argument("--forza", action="store_true",
                        help="accetta una vetrina che contraddice il documento (resta scritto)")
    parser.add_argument("--attesa", type=float, default=480.0,
                        help="secondi di attesa massima per la generazione (default 480)")
    parser.add_argument("--persona", default="", help="documento di identità (default: quello del repo)")
    parser.add_argument("--url", default=os.getenv("CHANNEL_URL", "http://127.0.0.1:8085"))
    parser.add_argument("--token-env", default=str(TOKEN_ENV_DEFAULT))
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")

    percorso = trova_persona(args.persona)
    if percorso is None:
        print("documento di identità non trovato: usa --persona <file>")
        return 1
    try:
        documento = json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"{percorso}: non leggibile ({e})")
        return 1

    vetrina = vetrina_dal_documento(documento)
    dichiarata = documento.get("vetrina") if isinstance(documento.get("vetrina"), dict) else {}
    stile_dichiarato = bool(str(dichiarata.get("stile") or "").strip())
    print(f"documento: {percorso}")
    print(f"identità:  {documento.get('name', '?')} ({documento.get('kind', '?')})")
    print(f"vetrina:   seed {vetrina['seed']} · {vetrina['larghezza']}x{vetrina['altezza']} · "
          f"{vetrina['passi']} passi · stile "
          + ("dal documento" if stile_dichiarato else "default del modulo (dichiaralo "
             "in `vetrina` per cambiarlo)"))
    altri_documenti = [p for p in PERSONA_CANDIDATI if p != percorso and p.is_file()]
    for avviso in divergenze_documenti([percorso] + altri_documenti):
        print(f"ATTENZIONE: due documenti di identità divergono — {avviso}")
    if args.forza:
        print("ATTENZIONE: --forza attivo — la vetrina può contraddire il documento, "
              "e la cosa resta scritta qui e nei log")
    problemi = verifica_vetrina(vetrina, documento, forza=args.forza)
    if problemi:
        print("STOP: la vetrina non va bene:")
        for problema in problemi:
            print(f"  - {problema}")
        return 1

    if args.foto:
        return _metti_foto(args, documento)

    token = token_canale()
    if args.check or not args.crea:
        print("")
        if not token:
            print("canale: nessun token in CHANNEL_CLIENTS (.env) — la coda immagini non risponde")
        else:
            stato, dati = chiama(f"{base}/image/status", token, timeout=15)
            if stato == 200:
                print(f"control-plane {base}: ok (in coda {dati.get('in_coda')}, "
                      f"in esecuzione {dati.get('in_esecuzione')})")
            elif stato == 401:
                print("control-plane: token rifiutato (401) — .env e control-plane non "
                      "hanno lo stesso CHANNEL_CLIENTS")
            elif stato == 503:
                print("control-plane: canali disattivati (503) — CHANNEL_ENABLED=false "
                      "o CHANNEL_CLIENTS vuoto")
            else:
                print(f"control-plane: non raggiungibile ({stato or dati.get('errore', '')})")
        print("")
        print(istruzione_botfather("<file generato>", nome=documento.get("name", "Aurora")))
        return 0
    if not token:
        print("nessun token di canale: la coda immagini non è raggiungibile")
        return 1
    print("")
    print("accodo il ritratto (identità stabile: stesso prompt, stesso seed)...")
    stato, dati = accoda_ritratto(base, token, vetrina, scena=args.scena, seed=args.seed,
                                  destinazione=args.chat)
    if stato != 201 or not dati.get("ok"):
        print(f"accodamento fallito (HTTP {stato}): {dati.get('error') or dati.get('errore')}")
        return 1
    job = dati["job"]
    if dati.get("scheda"):
        print(f"  {dati['scheda']}")
    print(f"job {job['id']}: {job['larghezza']}x{job['altezza']} passi={job['passi']} "
          f"seed={job['seed']}")
    if args.chat:
        print(f"consegna: il driver manderà il file in {args.chat} appena è pronto")
    print(f"il ponte lo esegue (misura tipica ~5 minuti): aspetto fino a {int(args.attesa)}s")
    finale = aspetta_job(base, token, job["id"], attesa_s=args.attesa)
    esito = finale.get("esito") or {}
    if finale.get("stato") == "done" and esito.get("file"):
        print("")
        print(f"fatto in {round((esito.get('durata_ms') or 0) / 1000, 1)}s: {esito['file']}")
        print("")
        print("Guarda la candidata: se ti piace, questa è la strada per metterla.")
        print(istruzione_botfather(esito["file"], nome=documento.get("name", "Aurora")))
        return 0
    print("")
    print(f"non concluso: stato={finale.get('stato') or 'sconosciuto'} {esito.get('errore', '')}")
    print("riprova con --attesa più alto, o guarda /image/status.")
    return 1


def _metti_foto(args, documento: dict) -> int:
    """setChatPhoto su una chat amministrata: è il canale della personalità."""
    token_bot = bot_token(Path(args.token_env))
    if not token_bot:
        print(f"TELEGRAM_BOT_TOKEN mancante ({args.token_env})")
        return 1
    if not args.chat:
        print("--foto richiede --chat (@canale o id): la foto del BOT non si cambia via API.")
        print(istruzione_botfather(args.foto, nome=documento.get("name", "Aurora")))
        return 1
    percorso_file = Path(args.foto)
    if not percorso_file.is_file():
        print(f"file non trovato: {percorso_file}")
        return 1
    api = f"https://api.telegram.org/bot{token_bot}"
    try:
        with open(percorso_file, "rb") as file:
            risposta = requests.post(f"{api}/setChatPhoto", data={"chat_id": args.chat},
                                     files={"photo": file}, timeout=120)
        dati = risposta.json()
    except (requests.RequestException, ValueError) as e:
        print(f"setChatPhoto non riuscito: {e}")
        return 1
    if dati.get("ok"):
        print(f"foto di {args.chat} aggiornata ✓ (il bot deve essere amministratore con "
              f"il diritto di cambiare le informazioni)")
        return 0
    print(f"setChatPhoto: {dati.get('description') or dati}")
    print("(se la chat è il bot stesso non è possibile: l'API non lo permette)")
    print(istruzione_botfather(str(percorso_file), nome=documento.get("name", "Aurora")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())




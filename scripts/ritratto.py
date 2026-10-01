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
    python scripts/ritratto.py --crea --scena "..." --seed N
    python scripts/ritratto.py --crea --riferimento ~/volto.png   # il VOLTO da tenere
    python scripts/ritratto.py --foto <file> --chat @canale   # foto del CANALE
    python scripts/ritratto.py --crea --creatore --scena "adult virtual nude figure"

`--creatore` (2026-10-01) è il **livello del creatore**: la nudità e l'esplicito di
una persona che ha dichiarato `consenti_erotismo_esplicito_creatore` nella propria
vetrina smettono di essere respinti dal negativo, e la scena li porta nel prompt in
positivo. Tre cose lo tengono stretto, e servono tutte: la dichiarazione nel
documento (chi è rappresentato decide della propria rappresentazione), il quadro
nella scena (`adult`, `virtual`: adulti e virtuale, cioè le due assolute che
sopravvivono), e il comando che si dichiara creatore — perché il documento non sa
chi ha scritto il comando. `VIETATI_MINORI` non si apre per nessun livello.
"""
from __future__ import annotations

import argparse
import hashlib
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
                             vetrina_con_quadro_erotismo, vetrina_dal_documento)
# Il caricamento del riferimento è l'upload di ComfyUI, e quell'upload è già scritto
# nel ponte (`--stage-riferimento`): qui si prende quella funzione invece di
# ricomporre il multipart. Due implementazioni del protocollo di upload sarebbero due
# cose da tenere allineate a ComfyUI, e a divergere sarebbe quella che si usa di rado.
from integrations.comfyui.comfy_bridge import COMFY_DEFAULT, carica_riferimento  # noqa: E402
from shared.image_jobs import RIFERIMENTO_FORZA_DEFAULT  # noqa: E402

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

    E si confrontano solo documenti della **stessa identità** (2026-09-30): Aurora e
    Anna sono due persone, e `PERSONA_CANDIDATI` elenca solo i documenti di Aurora —
    così `--persona data/persona-anna.json` stampava sette avvisi (uno per sezione)
    a ogni esecuzione. Un avviso che suona sempre è un avviso che non si legge: due
    identità diverse non sono una divergenza, sono due identità.
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
    identita = str(riferimento.get("name") or "")
    avvisi: list[str] = []
    for percorso, documento in documenti[1:]:
        if str(documento.get("name") or "") != identita:
            continue
        for sezione in SEZIONI_DI_IDENTITA:
            primo = json.dumps(riferimento.get(sezione), sort_keys=True, ensure_ascii=False)
            secondo = json.dumps(documento.get(sezione), sort_keys=True, ensure_ascii=False)
            if primo != secondo:
                avvisi.append(f"{sezione}: {percorso} diverso da {riferimento_percorso}")
    return avvisi


def accoda_ritratto(base: str, token: str, vetrina: dict, *, scena: str = "",
                    seed: int | None = None, destinazione: str = "",
                    creatore: bool = False) -> tuple[int, dict]:
    """Mette in coda il ritratto. Con `destinazione` il driver lo consegna in chat.

    La consegna non passa dal control-plane: lui decide *dove* (una chat, un id),
    il file ce l'ha il driver — che è l'unico a poter parlare con Telegram.

    Della vetrina il job porta anche il **volto** (`riferimento`) e la **posa**
    (`pose_preset`): due cose che il prompt non può fare. Il seed fisso tiene fermo
    il disegno, non l'identità — con la stessa richiesta e due scene diverse il
    modello disegna due volti diversi — e una posa scritta a parole è una speranza,
    non una geometria.

    `creatore` accende il livello del creatore nel negativo (`shared/showcase.py`):
    senza, il negativo è quello di sempre e la nudità resta esclusa. Non è un
    permesso locale — la vetrina deve dichiararlo e la scena deve dichiarare il
    quadro, altrimenti non cambia niente — ma è quel po' che la riga di comando deve
    dire di sé, perché il documento non sa chi ha scritto il comando.
    """
    # Il negativo si calcola sulla STESSA scena che va nel prompt: i livelli (artistico
    # e del creatore) si accendono sul quadro che la scena dichiara, quindi un negativo
    # calcolato sulla vetrina dichiarata sarebbe calcolato su un'altra richiesta. Il
    # difetto che questo evita, visto il 2026-10-01: `--scena "adult virtual nude
    # figure"` con `--creatore` non faceva niente — il quadro non arrivava mai al
    # negativo, che continuava a escludere la nudità, e il job riusciva.
    variante = {**vetrina, "scena": scena} if str(scena or "").strip() else vetrina
    # Il seed del volto resta un'ancora, ma una serie con scene diverse non deve
    # riciclare vestiti, palette e composizione. Stessa scena = seed ripetibile.
    seed_effettivo = (int(seed) if seed is not None else
                      (int(vetrina["seed"]) if not str(scena or "").strip() else
                       int(vetrina["seed"]) + int(hashlib.sha256(
                           str(scena).encode()).hexdigest()[:8], 16)))
    payload = {
        "prompt": prompt_ritratto(vetrina, scena=scena),
        "negativo": negativo_ritratto(variante, creatore=creatore),
        "larghezza": int(vetrina["larghezza"]),
        "altezza": int(vetrina["altezza"]),
        "passi": int(vetrina["passi"]),
        # Il secondo passaggio (0 = uno solo): lo dichiara la vetrina, perché la
        # misura del ritratto è una sua scelta — 512×768 + fix dà il 2:3 dei demo
        # del modello, 768×768 con il fix sarebbe 1536×1536 (fuori misura).
        "fix": int(vetrina.get("fix") or 0),
        "seed": seed_effettivo,
        "richiedente": "ritratto",
        "destinazione": destinazione.strip(),
        # Con chi è disegnato il volto: se il documento non lo dichiara resta vuoto,
        # e il job cade sulla famiglia di default della coda come è sempre stato.
        "famiglia": str(vetrina.get("famiglia") or ""),
        "modello": str(vetrina.get("modello") or ""),
        # Il volto e la posa: la vetrina li PORTA al job, non li interpreta. Il
        # riferimento lo rifiuta `verifica_vetrina` quando la famiglia non ha
        # l'IP-Adapter (e il grafo lo rifiuta a sua volta, in `workflow`), quindi qui
        # si manda e basta: una seconda difesa qui sarebbe una difesa da ricordarsi.
        "reference_image": str(vetrina.get("riferimento") or ""),
        "reference_strength": float(vetrina.get("riferimento_forza")
                                    or RIFERIMENTO_FORZA_DEFAULT),
        # La posa è della VARIANTE, non dell'identità: la serie ne sceglie una per
        # scena (`scripts/serie.py`), e dove il documento non ne dichiara nessuna
        # resta al testo decidere — come è sempre stato.
        "pose_preset": str(vetrina.get("pose_preset") or ""),
        "pose_strength": float(vetrina.get("pose_strength") or 1.0),
    }
    return chiama(f"{base}/image/generate", token, metodo="post", payload=payload)


def prepara_riferimento(percorso: str, comfy_url: str, nome: str = "") -> str:
    """Mette un volto di riferimento in ComfyUI/input e torna il nome per il job.

    Il nome che va nel job è quello che ComfyUI dichiara di aver salvato: la sua
    cartella input la decide l'installazione (`~/ComfyUI-Shared/input` su questa
    macchina, non `~/Documents/ComfyUI/input`), e indovinarla vuol dire scrivere un
    file che ComfyUI non leggerà mai — con il ritratto che fallisce su `LoadImage`.
    Vuoto = non caricato, e il motivo è già stampato.
    """
    nome_file, motivo = carica_riferimento(percorso, comfy_url=comfy_url, nome=nome)
    if not nome_file:
        print(f"riferimento non caricato: {motivo}")
        return ""
    print(f"riferimento in ComfyUI/input: {nome_file}")
    return nome_file


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
    # Il riferimento è il volto: senza, il seed fisso dà lo stesso disegno ma non la
    # stessa identità. Questo flag è come si mette il PRIMO volto (e come se ne
    # cambia uno): il file finisce in ComfyUI/input, e da lì in poi basta il nome —
    # che è quello che il documento dichiara in `vetrina.riferimento`.
    parser.add_argument("--riferimento", default="",
                        help="immagine del volto da usare come riferimento: viene messa "
                             "nella cartella input di ComfyUI e il job la usa "
                             "(serve una famiglia con IP-Adapter)")
    parser.add_argument("--riferimento-forza", type=float, default=None,
                        help="peso del riferimento sul volto (default: quello "
                             f"dichiarato, {RIFERIMENTO_FORZA_DEFAULT})")
    parser.add_argument("--comfy", default=os.getenv("COMFY_URL", COMFY_DEFAULT),
                        help="indirizzo di ComfyUI: serve solo a mettere il riferimento "
                             "nella sua cartella input")
    parser.add_argument("--forza", action="store_true",
                        help="accetta una vetrina che contraddice il documento (resta scritto)")
    parser.add_argument("--creatore", action="store_true",
                        help="livello del creatore: nudità ed esplicito fuori dal "
                             "negativo, se il documento lo dichiara e la scena "
                             "dichiara il quadro (adult, virtual)")
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
    # Con cosa è disegnato il volto. Vuoto = famiglia di default della coda: senza
    # questa riga, sapere che un ritratto è uscito da un modello invece che da un
    # altro vorrebbe dire leggere il job dopo.
    print("modello:   " + (vetrina.get("famiglia") or "(famiglia di default della coda)")
          + (f" · {vetrina['modello']}" if vetrina.get("modello") else ""))
    # Il riferimento chiesto a mano vince su quello dichiarato. Il file va messo in
    # ComfyUI/input una volta sola: dopo, basta il nome che ComfyUI gli ha dato, che è
    # quello che il documento dichiara in `vetrina.riferimento`.
    if args.riferimento:
        nome_riferimento = prepara_riferimento(args.riferimento, args.comfy)
        if not nome_riferimento:
            return 1
        vetrina["riferimento"] = nome_riferimento
    if args.riferimento_forza is not None:
        vetrina["riferimento_forza"] = float(args.riferimento_forza)
    # Il volto: il riferimento e con quanta forza. Senza questa riga, «perché questo
    # ritratto ha una faccia diversa da quello di ieri» si scoprirebbe solo leggendo
    # il job, e il job sparisce dopo dodici ore.
    if vetrina.get("riferimento"):
        print(f"riferimento: {vetrina['riferimento']} · forza {vetrina['riferimento_forza']}"
              + (" (dalla riga di comando, non dal documento)" if args.riferimento else ""))
    else:
        print("riferimento: nessuno — il volto lo decide il seed (stesso seed, stesso "
              "disegno; scene diverse possono cambiare il volto)")
    altri_documenti = [p for p in PERSONA_CANDIDATI if p != percorso and p.is_file()]
    for avviso in divergenze_documenti([percorso] + altri_documenti):
        print(f"ATTENZIONE: due documenti di identità divergono — {avviso}")
    if args.forza:
        print("ATTENZIONE: --forza attivo — la vetrina può contraddire il documento, "
              "e la cosa resta scritta qui e nei log")
    if args.creatore:
        print("creatore: livello del creatore — nudità ed esplicito escono dal negativo "
              "se il documento dichiara il livello e la scena chiede nudità o esplicito "
              "(il quadro `adult`, `virtual` lo scrive il sistema); i minori restano "
              "esclusi come per chiunque")
    # La scena scritta qui è la scena di QUESTA generazione, e la verifica deve vederla:
    # senza, `--scena "una donna nuda"` non passava da nessun controllo — la vetrina
    # dichiarata è un'altra — e il livello del creatore non si accendeva mai (il quadro
    # sta nella scena, non nella vetrina). È lo stesso difetto corretto nel negativo di
    # `accoda_ritratto`: verifica e negativo devono guardare la stessa richiesta.
    variante = {**vetrina, "scena": args.scena.strip()} if args.scena.strip() else vetrina
    # Il quadro lo scrive il sistema, come nella rotta del canale: `adult` e `virtual`
    # sono cose che il documento sa già (il livello è dichiarato, il corpo è
    # dichiaratamente virtuale), e chiederle a chi scrive il comando era attrito —
    # `--creatore --scena "di te nuda"` si fermava alla verifica per una parola che il
    # sistema conosceva già.
    aggiunte = []
    if args.creatore and args.scena.strip():
        variante, aggiunte = vetrina_con_quadro_erotismo(variante)
        if aggiunte:
            print("quadro:     " + ", ".join(aggiunte) + " — scritto qui: la scena non lo "
                  "dichiarava, e senza il quadro il livello non si accende")
    problemi = verifica_vetrina(variante, documento, forza=args.forza,
                                creatore=args.creatore)
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
    stato, dati = accoda_ritratto(base, token, vetrina,
                                  scena=variante["scena"] if args.scena.strip() else "",
                                  seed=args.seed,
                                  destinazione=args.chat, creatore=args.creatore)
    if stato != 201 or not dati.get("ok"):
        print(f"accodamento fallito (HTTP {stato}): {dati.get('error') or dati.get('errore')}")
        return 1
    job = dati["job"]
    if dati.get("scheda"):
        print(f"  {dati['scheda']}")
    print(f"job {job['id']}: {job['larghezza']}x{job['altezza']} passi={job['passi']} "
          f"seed={job['seed']}")
    if int(job.get("fix") or 0) > 1:
        # La misura del job è quella del PRIMO passaggio: il file esce al doppio,
        # e va detto prima di aspettare il fix (che sulla Mac costa minuti).
        fattore = int(job["fix"])
        print(f"  fix {fattore}×: il file esce a "
              f"{job['larghezza'] * fattore}x{job['altezza'] * fattore}")
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


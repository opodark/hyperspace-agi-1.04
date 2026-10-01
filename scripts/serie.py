#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""La serie: più varianti della stessa persona — pose, abiti, inquadrature, ambienti.

Perché esiste (2026-09-30): `ritratto.py` fa UNA immagine, la candidata di profilo.
Ma per scegliere una posa, un abito, un'inquadratura o un ambiente servono le
varianti, e farle a mano una per volta vuol dire perdere il filo — quale seed,
quale scena, quale file — o cambiare volto a ogni tentativo.

La serie invece è deterministica: **stile e seed restano quelli dichiarati nel
documento** e cambia solo la `scena`. Il volto resta lo stesso perché la richiesta
è la stessa, non perché il modello se lo ricordi.

Dal 2026-09-30 «la richiesta è la stessa» non basta più a dire il volto: il seed
tiene fermo il *disegno*, non l'identità, e due scene diverse disegnano due volti
diversi. Le due cose che il testo non può fare le porta il grafo:
 - il **riferimento** (`--riferimento`, o `vetrina.riferimento` nel documento): il
   volto che l'adattatore mostra al modello a ogni passo. È la ragione per cui una
   serie di dodici varianti è dodici volte la stessa persona;
 - la **posa** di ogni variante (il terzo pezzo di `SCENE`): il ControlNet openpose
   la impone, dove `standing` scritto nel prompt è una speranza. Si manda solo alle
   famiglie che hanno quel ControlNet — altrove il job fallirebbe, e un job fallito
   non è una variante da giudicare.

Ogni variante passa *prima di essere accodata* dallo stesso controllo del ritratto
(`shared/showcase.verifica_vetrina`): una scena che chiede una nudità, un soggetto
minorenne o una fotografia viene **rifiutata**, e la ragione resta nel log. Non è
una formalità — `VIETATI_ASSOLUTI` non si aggira nemmeno con `--forza` — ed è la
ragione per cui questa serie non sa produrre un certo tipo di immagine: non perché
manchi il codice, ma perché il documento di quella persona dice di no. Un soggetto
minorenne resta rifiutato sempre; una nudità no, se la persona ha dichiarato il
**livello del creatore** (`--creatore`, 2026-10-01) e la scena dichiara il quadro
(`adult`, `virtual`) — in quel caso la nudità esce dal negativo e la scena la porta
nel prompt. Vedi `scripts/ritratto.py`, che ha la stessa manopola.

Uso:
    python scripts/serie.py --prova                     # stampa le varianti, non accoda
    python scripts/serie.py --persona data/persona-anna.json --misura 512x768
    python scripts/serie.py --solo 1,7                  # solo alcune varianti
    python scripts/serie.py --riferimento ~/volto.png   # le dodici varianti, un volto
    SD15_TOKENIZER_DIR=... python scripts/serie.py --prova   # con i token contati
"""
from __future__ import annotations

import argparse
import html
import json
import os
import struct
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Gli helper del canale sono quelli di `ritratto.py` — token dal .env, chiamata
# JSON, attesa del job: un posto solo, così un cambio al protocollo non ne
# aggiorna uno e dimentica l'altro.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ritratto as cli  # noqa: E402
from shared.image_jobs import (FAMIGLIA_DEFAULT, RIFERIMENTO_FORZA_DEFAULT,  # noqa: E402
                               posa_supportata, riferimento_supportato)
from shared.showcase import (negativo_ritratto, prompt_ritratto,  # noqa: E402
                             verifica_vetrina, vetrina_con_quadro_erotismo,
                             vetrina_dal_documento)

# Le varianti: (nome, scena, posa).
SCENE: Tuple[Tuple[str, str, str], ...] = (
    # (nome, scena, posa). Il primo pezzo di ogni scena è l'identità (capelli,
    # sguardo): sta davanti perché è la parte che non deve cambiare, e la variazione
    # viene dopo. L'ordine è per costo del dettaglio: prima i volti (dove si giudica
    # un'identità), poi i piani medi (dove si giudica un abito), poi le figure intere
    # (dove si giudica una posa). Un abito per variante: così una scena che non piace
    # non porta con sé anche la posa che piaceva.
    #
    # Il terzo pezzo è la POSA di quella variante (`POSE_PRESET`, vuoto = nessuna):
    # è una cosa che il testo chiede e non ottiene — «standing» scritto nel prompt
    # dà una figura in piedi *circa* — mentre il ControlNet openpose la impone. Sta
    # variante per variante e non nella vetrina perché la posa è della SCENA, non
    # dell'identità: la stessa persona, seduta qui e in piedi là. Dove non c'è una
    # posa da imporre (i primi piani) resta vuoto, e il testo fa come ha sempre
    # fatto. `scripts/serie.py` la manda solo alle famiglie che hanno il ControlNet
    # (vedi `posa_supportata`): altrove farebbe fallire il job, e un rifiuto non è un
    # ritratto.
    ("01-volto", "close-up face, front view, calm gaze at viewer, "
                 "black hair with purple tips, dark background, glowing particles", ""),
    ("02-volto-sorriso", "close-up portrait, slight smile, head tilted, "
                         "black hair with purple tips, violet rim light, dark background", ""),
    ("03-busto-giacca", "upper body, black tailored jacket, arms crossed, "
                        "black hair with purple tips, warm light, blurred library shelves", ""),
    ("04-busto-felpa", "upper body, oversized grey hoodie, hands in pockets, "
                       "black hair with purple tips, cyan screen glow, dark room", ""),
    ("05-busto-vestito", "upper body, white dress, looking away, black hair with purple tips, "
                         "sunset window light, city bokeh", ""),
    ("06-seduta", "seated on an armchair, legs crossed, black knit sweater, "
                  "black hair with purple tips, dim lamp, scattered books", "seated"),
    ("07-erba-notte", "lying on night grass, dark clothes, "
                      "black hair with purple tips, fireflies, green glow", "lying"),
    ("08-figura-cappotto", "full body, standing, long black coat, snowy street at night, "
                           "black hair with purple tips, neon reflections", "standing"),
    ("09-figura-abito", "full body, standing, long violet evening dress, mirror hall, "
                        "black hair with purple tips, chandelier light", "standing"),
    ("10-figura-tailleur", "full body, walking, grey business suit, glass corridor, "
                           "black hair with purple tips, rain on glass", "walking"),
    ("11-figura-strada", "full body, hands in pockets, denim jacket and jeans, "
                         "wet street, black hair with purple tips, neon signs", "standing"),
    ("12-studio", "full body, standing, plain grey studio backdrop, simple black top, "
                  "black hair with purple tips, soft box light", "standing"),
)


def conta_token(percorso: str):
    """Il tokenizer di SD 1.5, se dichiarato (`SD15_TOKENIZER_DIR`). Senza, la serie
    funziona lo stesso: si perde solo la misura — cioè sapere se la scena entra nei
    77 token che CLIP legge davvero; oltre quelli il modello riempie i vuoti da sé."""
    if not percorso:
        return None
    try:
        from transformers import CLIPTokenizer
    except ImportError:
        return None
    try:
        return CLIPTokenizer.from_pretrained(percorso)
    except (OSError, ValueError):
        return None


def misura_file(percorso: Path) -> Optional[Tuple[int, int]]:
    """La misura VERA del file, letta dal suo interno (JPEG).

    È l'unica prova che conta: un file è 512×768 solo se il suo header lo dice. Il
    campo `larghezza` del job dice cosa è stato **chiesto**, e con il fix il file
    esce al doppio: tenere distinte le due cose è ciò che impedisce a un fix che
    non ingrandisce di passare per riuscito.
    """
    try:
        with open(percorso, "rb") as file:
            if file.read(2) != b"\xff\xd8":
                return None
            while True:
                byte = file.read(1)
                while byte and byte != b"\xff":
                    byte = file.read(1)
                marcatore = file.read(1)
                while marcatore == b"\xff":
                    marcatore = file.read(1)
                if not marcatore:
                    return None
                if marcatore[0] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    file.read(3)
                    altezza, larghezza = struct.unpack(">HH", file.read(4))
                    return larghezza, altezza
                file.seek(struct.unpack(">H", file.read(2))[0] - 2, 1)
    except (OSError, struct.error):
        return None


def scelte(solo: str) -> List[Tuple[str, str, str]]:
    """Le varianti da fare: tutte, o quelle nominate in `--solo` (numeri o nomi).

    Il numero si confronta con lo zero davanti (`7` → `07`): senza, `--solo 1`
    pescherebbe anche 10, 11 e 12 con una sottostringa.
    """
    volute = [voce.strip().lower() for voce in (solo or "").split(",") if voce.strip()]
    if not volute:
        return list(SCENE)
    scelte_fatte: List[Tuple[str, str, str]] = []
    for nome, scena, posa in SCENE:
        numero = nome.split("-")[0]
        for voluta in volute:
            if (voluta == nome.lower() or voluta.zfill(2) == numero
                    or (voluta.isdigit() and int(voluta) == int(numero))):
                scelte_fatte.append((nome, scena, posa))
                break
    return scelte_fatte


def posa_da_mandare(vetrina: dict, posa: str) -> str:
    """La posa che questa variante impone al grafo, o "" se la lascia al testo.

    Il ControlNet openpose è ciò che trasforma una posa in geometria; dove la
    famiglia non ce l'ha, mandare il preset non darebbe una posa diversa — farebbe
    FALLIRE il job (`workflow_checkpoint` solleva apposta: meglio un errore visibile
    che un ritratto sbagliato). Quindi la posa si manda solo dove serve davvero, e
    tutto il resto resta al testo, come è sempre stato.
    """
    if not posa:
        return ""
    famiglia = str(vetrina.get("famiglia") or FAMIGLIA_DEFAULT)
    return posa if posa_supportata(famiglia) else ""


def scrivi_indice(percorso: Path, esiti: List[dict], documento: dict, vetrina: dict) -> bool:
    """Una pagina con le immagini in fila: dodici file da aprire uno per uno non si
    giudicano, e un giudizio è esattamente ciò che una serie serve a produrre."""
    righe = []
    for esito in esiti:
        if not esito.get("file"):
            righe.append(f"<figure><figcaption>{html.escape(esito['nome'])} — "
                         f"{html.escape(esito.get('stato', ''))}</figcaption></figure>")
            continue
        sorgente = Path(esito["file"]).as_uri()
        misura = f"{esito['misura'][0]}×{esito['misura'][1]}" if esito.get("misura") else ""
        righe.append(
            f"<figure><img src=\"{sorgente}\" alt=\"{html.escape(esito['nome'])}\">"
            f"<figcaption><b>{html.escape(esito['nome'])}</b> · {misura} · "
            f"{esito.get('durata_s', '')}s<br><small>{html.escape(esito.get('scena', ''))}"
            f"</small></figcaption></figure>")
    # Il volto su cui si giudicano le immagini: un indice di dodici facce diverse ha
    # bisogno di dire da dove vengono, altrimenti il giudizio è su dodici persone.
    if vetrina.get("riferimento"):
        volto = (f"volto: {html.escape(str(vetrina.get('riferimento')))} · "
                 f"forza {vetrina.get('riferimento_forza')}")
    else:
        volto = ("volto: nessun riferimento — quello che il seed disegna, scena per scena")
    pagina = f"""<!doctype html>
<html lang="it"><meta charset="utf-8">
<title>{html.escape(str(documento.get('name', 'serie')))} — serie</title>
<style>
 body {{ font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 24px; background: #14161a; color: #e8e8ea; }}
 h1 {{ font-size: 18px; font-weight: 600; }}
 p.meta {{ color: #9aa0a6; }}
 .serie {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 18px; }}
 figure {{ margin: 0; }}
 img {{ width: 100%; height: auto; border-radius: 6px; display: block; background: #222; }}
 figcaption {{ padding-top: 6px; color: #c9ccd1; }}
 small {{ color: #8b9096; }}
</style>
<h1>{html.escape(str(documento.get('name', 'serie')))} — {len(esiti)} varianti</h1>
<p class="meta">stile e seed dal documento ({html.escape(str(vetrina.get('stile'))[:80])}…),
 seed {vetrina.get('seed')}, {vetrina.get('larghezza')}×{vetrina.get('altezza')} al primo
 passaggio{' + fix ' + str(vetrina.get('fix')) if int(vetrina.get('fix') or 0) > 1 else ''}<br>
 {volto}</p>
<div class="serie">
{chr(10).join(righe)}
</div>
</html>
"""
    try:
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(pagina, encoding="utf-8")
        return True
    except OSError as e:
        print(f"indice non scritto: {e}")
        return False


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # I testi hanno accenti e un trattino lungo; e una serie dura decine di
        # minuti: senza `line_buffering` un log su file resta vuoto fino alla
        # fine, e un log che non si legge mentre gira non è un log.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(
        description="Genera una serie di varianti della stessa persona (pose, abiti, ambienti).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prova", action="store_true", help="stampa le varianti e non accoda nulla")
    parser.add_argument("--creatore", action="store_true",
                        help="livello del creatore: nudità ed esplicito fuori dal "
                             "negativo, se il documento lo dichiara e la scena "
                             "dichiara il quadro (adult, virtual)")
    parser.add_argument("--persona", default="", help="documento di identità (default: quello del repo)")
    parser.add_argument("--solo", default="", help="solo alcune varianti: numeri o nomi, separati da virgola")
    parser.add_argument("--scena", default="", help="una scena sola, invece delle varianti di serie")
    parser.add_argument("--stile", default="",
                        help="prova uno stile candidato invece di quello dichiarato "
                             "(resta scritto: non è il documento)")
    parser.add_argument("--seed", type=int, default=None, help="un altro seed = un'altra persona")
    # Il volto, come in `ritratto.py`: il file si mette in ComfyUI/input una volta, e
    # il nome che ComfyUI gli dà è quello che ogni variante porta al job. È la ragione
    # per cui una serie non è dodici persone diverse.
    parser.add_argument("--riferimento", default="",
                        help="immagine del volto da usare come riferimento per TUTTE le "
                             "varianti (serve una famiglia con IP-Adapter)")
    parser.add_argument("--riferimento-forza", type=float, default=None,
                        help="peso del riferimento sul volto (default: quello "
                             f"dichiarato, {RIFERIMENTO_FORZA_DEFAULT})")
    parser.add_argument("--comfy", default=os.getenv("COMFY_URL", cli.COMFY_DEFAULT),
                        help="indirizzo di ComfyUI: serve solo a mettere il riferimento "
                             "nella sua cartella input")
    parser.add_argument("--misura", default="", help="LxA del PRIMO passaggio (default: quella dichiarata)")
    parser.add_argument("--fix", type=int, default=None, help="secondo passaggio 2× (default: quello dichiarato)")
    parser.add_argument("--passi", type=int, default=None, help="passi (default: quelli dichiarati)")
    parser.add_argument("--attesa", type=float, default=2400.0,
                        help="secondi di attesa massima per OGNI immagine "
                             "(default 2400: il tetto del ponte è 1800 s, e un client "
                             "che rinuncia prima non vede mai il risultato)")
    parser.add_argument("--url", default=os.getenv("CHANNEL_URL", "http://127.0.0.1:8085"))
    parser.add_argument("--output", default=os.getenv("COMFY_OUTPUT_DIR", ""),
                        help="cartella di output di ComfyUI: serve a leggere la misura dei file")
    parser.add_argument("--indice", default="", help="dove scrivere la pagina HTML con le immagini")
    parser.add_argument("--tokenizer", default=os.getenv("SD15_TOKENIZER_DIR", ""),
                        help="cartella del tokenizer di SD 1.5: serve solo a contare i token (77)")
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")

    percorso = cli.trova_persona(args.persona)
    if percorso is None:
        print("documento di identità non trovato: usa --persona <file>")
        return 1
    try:
        documento = json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"{percorso}: non leggibile ({e})")
        return 1

    vetrina = vetrina_dal_documento(documento)
    stile_dichiarato = str(vetrina.get("stile") or "")
    if args.stile:
        # Uno stile candidato si prova sulla STESSA scena: è l'unico confronto che
        # dice qualcosa. Resta scritto che non è quello del documento — e se lo
        # stile candidato toglie i segni digitali, il controllo lo rifiuta comunque.
        vetrina["stile"] = args.stile
    if args.misura:
        try:
            larghezza, altezza = args.misura.lower().replace("×", "x").split("x")
            vetrina["larghezza"], vetrina["altezza"] = int(larghezza), int(altezza)
        except ValueError:
            print("--misura vuole LxA (es. 512x768)")
            return 1
    if args.fix is not None:
        vetrina["fix"] = int(args.fix)
    if args.passi is not None:
        vetrina["passi"] = int(args.passi)
    # Il riferimento chiesto a mano vince su quello del documento, e vale per tutte e
    # dodici le varianti: una serie è il posto dove si giudica un volto, e giudicarlo
    # su dodici volti diversi non è un giudizio.
    if args.riferimento:
        nome_riferimento = cli.prepara_riferimento(args.riferimento, args.comfy)
        if not nome_riferimento:
            return 1
        vetrina["riferimento"] = nome_riferimento
    if args.riferimento_forza is not None:
        vetrina["riferimento_forza"] = float(args.riferimento_forza)
    famiglia_volto = str(vetrina.get("famiglia") or FAMIGLIA_DEFAULT)
    # Il riferimento è lo STESSO per tutte le varianti: se la famiglia non ha
    # l'adattatore (IP-Adapter + CLIP-ViT) il controllo per variante lo direbbe dodici
    # volte — dopo aver accodato nulla, ma dopo aver fatto credere per dodici schermate
    # che la serie stesse per partire. Qui si dice una volta e si esce.
    if vetrina.get("riferimento") and not riferimento_supportato(famiglia_volto):
        print(f"STOP: la famiglia «{famiglia_volto}» non ha l'adattatore del volto "
              "(IP-Adapter + CLIP-ViT): il riferimento non arriverebbe al modello. "
              "Togli --riferimento, o usa la famiglia che ce l'ha.")
        return 1

    tok = conta_token(args.tokenizer)
    print(f"documento: {percorso}")
    print(f"identità:  {documento.get('name', '?')} ({documento.get('kind', '?')})")
    print(f"vetrina:   seed {vetrina['seed']} · {vetrina['larghezza']}x{vetrina['altezza']} · "
          f"{vetrina['passi']} passi · fix {int(vetrina.get('fix') or 0)}")
    print("modello:   " + (str(vetrina.get("famiglia") or "")
                           or "(famiglia di default della coda)")
          + (f" · {vetrina['modello']}" if vetrina.get("modello") else ""))
    # Il volto su cui si giudicano le dodici varianti: senza questa riga, un indice di
    # dodici immagini con dodici facce non direbbe perché.
    if vetrina.get("riferimento"):
        print(f"riferimento: {vetrina['riferimento']} · forza {vetrina['riferimento_forza']}"
              + (" (dalla riga di comando, non dal documento)" if args.riferimento else ""))
    else:
        print("riferimento: nessuno — il volto lo decide il seed (stesso seed, stesso "
              "disegno; scene diverse possono cambiare il volto)")
    if args.stile and args.stile.strip() != stile_dichiarato.strip():
        print("ATTENZIONE: stile dalla riga di comando — NON è quello dichiarato nel "
              "documento: la prova si può fare, il risultato non è ancora la sua vetrina")
    if args.creatore:
        print("creatore:  livello del creatore — nudità ed esplicito escono dal negativo "
              "se il documento dichiara il livello e la scena chiede nudità o esplicito "
              "(il quadro `adult`, `virtual` lo scrive il sistema); i minori restano "
              "esclusi come per chiunque")

    varianti = [("scena-dichiarata", args.scena, "")] if args.scena else scelte(args.solo)
    if not varianti:
        print("nessuna variante da fare: --solo non ha trovato nulla")
        return 1
    # Il negativo della prima variante, non quello della vetrina: il livello del
    # creatore si accende sulla SCENA (che chiede nudità o esplicito), quindi il
    # conteggio dei token deve leggere lo stesso negativo che il job manderà — contare il
    # negativo della vetrina dichiarata direbbe un numero che nessuna variante userà. Il
    # quadro (`adult`, `virtual`) lo scrive il sistema, come nella rotta del canale.
    prima = {**vetrina, "scena": varianti[0][1]}
    if args.creatore and str(varianti[0][1]).strip():
        prima, _ = vetrina_con_quadro_erotismo(prima)
    negativo = negativo_ritratto(prima, creatore=args.creatore)
    print("negativo:  " + (f"{len(tok.encode(negativo))} token (CLIP ne legge 77: il resto "
                           "non arriva al modello)" if tok else "non contato (manca il tokenizer)"))

    token = ""
    if not args.prova:
        token = cli.token_canale()
        if not token:
            print("nessun token di canale in .env (CHANNEL_CLIENTS): la coda non è raggiungibile")
            return 1
        stato, dati = cli.chiama(f"{base}/image/status", token, timeout=15)
        if stato == 200:
            print(f"control-plane {base}: ok (in coda {dati.get('in_coda')}, "
                  f"in esecuzione {dati.get('in_esecuzione')})")
        else:
            print(f"control-plane {base}: non raggiungibile ({stato or dati.get('errore', '')})")
            return 1

    esiti: List[dict] = []
    for nome, scena, posa in varianti:
        # La posa è della variante, e va solo dove il ControlNet la sa imporre: dove
        # manca, `posa_da_mandare` torna vuoto e il testo fa come ha sempre fatto.
        variante = {**vetrina, "scena": scena, "pose_preset": posa_da_mandare(vetrina, posa)}
        # Il quadro lo scrive il sistema anche qui (vedi la rotta del canale: stesso
        # criterio, stesso helper): chi ha il livello non deve conoscere due parole
        # d'ordine, e le parole servono al MODELLO, che è quello che disegnerà.
        aggiunte = []
        if args.creatore and str(scena).strip():
            variante, aggiunte = vetrina_con_quadro_erotismo(variante)
        print("")
        print(f"[{nome}] {scena}")
        if aggiunte:
            print("  quadro: " + ", ".join(aggiunte) + " — scritto qui (la scena non lo "
                  "dichiarava): senza il quadro il livello non si accende")
        if posa:
            print("  posa: " + (posa if variante["pose_preset"] else
                                f"{posa} — lasciata al testo (la famiglia non ha il "
                                "ControlNet openpose: imporla farebbe fallire il job)"))
        problemi = verifica_vetrina(variante, documento, creatore=args.creatore)
        if problemi:
            print("  RIFIUTATA — il documento di questa persona non la permette:")
            for problema in problemi:
                print(f"    - {problema}")
            esiti.append({"nome": nome, "scena": scena, "stato": "rifiutata"})
            continue
        if tok:
            # Il tetto è 77 token e CLIP legge l'INIZIO del prompt: quello che conta
            # non è il totale (la dichiarazione in coda lo supera sempre, ed è la
            # ragione per cui la dichiarazione sta anche nella prima frase dello
            # stile) ma se `stile + scena` ci sta tutto.
            quanti = len(tok.encode(f"{vetrina.get('stile')}. {variante['scena']}"))
            print(f"  stile+scena {quanti} token su 77"
                  + ("" if quanti <= 77 else " ← NON ci sta tutto: la coda della scena "
                                             "non arriva al modello"))
        if args.prova:
            print(f"  {prompt_ritratto(variante)}")
            continue
        stato, dati = cli.accoda_ritratto(base, token, variante, seed=args.seed,
                                          creatore=args.creatore)
        if stato != 201 or not dati.get("ok"):
            motivo = str(dati.get("error") or dati.get("errore") or "")[:160]
            print(f"  accodamento fallito (HTTP {stato}): {motivo}")
            esiti.append({"nome": nome, "scena": scena, "stato": "non accodata", "motivo": motivo})
            continue
        job = dati["job"]
        fattore = int(job.get("fix") or 0)
        print(f"  job {job['id']}: {job['larghezza']}x{job['altezza']} passi={job['passi']} "
              f"seed={job['seed']}"
              + (f" · fix {fattore}× → {job['larghezza'] * fattore}x{job['altezza'] * fattore}"
                 if fattore > 1 else ""))
        finale = cli.aspetta_job(base, token, job["id"], attesa_s=args.attesa, ogni_s=10)
        esito = finale.get("esito") or {}
        voce = {"nome": nome, "scena": scena, "job": job["id"], "stato": finale.get("stato", "?")}
        if finale.get("stato") == "done" and esito.get("file"):
            file_completo = (Path(args.output) / esito["file"]) if args.output else Path(esito["file"])
            voce["file"] = str(file_completo)
            voce["misura"] = misura_file(file_completo) if file_completo.is_file() else None
            voce["durata_s"] = round((esito.get("durata_ms") or 0) / 1000)
            print(f"  fatto in {voce['durata_s']}s: {esito['file']}"
                  + (f" · {voce['misura'][0]}x{voce['misura'][1]}" if voce["misura"]
                     else " · misura non letta"))
        else:
            print(f"  non concluso: stato={voce['stato']} {esito.get('errore', '')}")
            voce["motivo"] = str(esito.get("errore") or "")
        esiti.append(voce)

    if not args.prova:
        print("")
        print("serie:")
        for voce in esiti:
            dettaglio = voce.get("file") or voce.get("motivo") or voce.get("stato", "")
            print(f"  {voce['nome']:<20} {voce.get('stato', ''):<10} {dettaglio}")
        if args.indice:
            fatte = [voce for voce in esiti if voce.get("file")]
            if scrivi_indice(Path(args.indice), fatte, documento, vetrina):
                print(f"indice: {args.indice}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

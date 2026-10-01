#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Job di immagini: cosa il control-plane mette in coda e come si esegue.

Perché una coda e non una chiamata diretta: ComfyUI ascolta su `127.0.0.1` sulla
macchina dove sta la scheda, mentre il control-plane vive in un container — **non
può chiamarlo**. La direzione è quella del resto del progetto (web node, canali):
il client *tira* il lavoro, il control-plane decide. Il ponte che esegue
(`integrations/comfyui/comfy_bridge.py`) non decide niente: prende un job, lo
esegue, riferisce.

Qui vive la parte decidibile — validazione, stato, scadenze, e il grafo da
mandare a ComfyUI — senza Flask, senza torch e senza rete: si testa da sola.
"""
from __future__ import annotations

import re
import json
import os
import threading
import time
import uuid
import warnings

STATI = ("pending", "running", "done", "failed")

# Tetti espliciti, come per i canali: i job sono pochi e piccoli (un prompt e
# quattro numeri), anche quando la coda e' salvata sul volume persistente.
DEFAULT_MAX_JOBS = 8
# I due tempi non sono decorativi e devono stare in quest'ordine:
#   claim > esecuzione massima del ponte (1800s) -> altrimenti il job verrebbe
#                                                   RIESEGUITO mentre è vivo
#   job   < claim + margine                      -> il risultato arriverebbe dopo la
#                                                   potatura, e andrebbe perso
# Il 2026-09-22 è successo esattamente questo: un ritratto su scheda carica ha
# superato i 600s, il claim è scaduto, il job è tornato "pending" mentre ComfyUI
# stava ancora campionando. Con 313s di misura "libera" e 700s su scheda occupata,
# i valori vecchi (900/600) erano tarati sul caso migliore.
# Il 2026-09-30 l'errore simmetrico, dal lato del ponte: con `fix: 2` e 30 passi una
# variante di Anna rende in ~900 s (890,2 s il 00084, 902,0 s il 00085), quindi un
# tetto da 900 s dichiarava fallito un job 2 s prima che il file uscisse — e quel
# file, che è buono, restava senza nessuno che lo riferisse. Tetto del ponte 1800,
# claim il doppio (3600): lo stesso rapporto del 2026-09-22, tarato sulla ricetta
# dei demo invece che sul caso migliore.
# Il ponte del Mac puo' restare spento per qualche ora (notte, update, laptop
# chiuso). Dodici ore conservano il lavoro fino al mattino senza trasformare la
# coda in un archivio di richieste vecchie.
DEFAULT_JOB_TTL_S = 43200.0
DEFAULT_CLAIM_TTL_S = 3600.0

# Limiti del grafo: la scheda di win11 ha 8 GB e un text encoder da 8B. Un tetto
# dichiarato è meglio di un OOM che si porta dietro anche il modello caricato.
LIMITE_LATO = 1536
LIMITE_PASSI = 60

# Le famiglie di modello che la coda distingue. Un job dichiara la sua famiglia:
# il ponte che esegue UNA sola famiglia (il Mac con SDXL-Turbo) prende solo i job
# di quella, e il grafo cambia di conseguenza. La famiglia di default resta
# Qwen-Image 2.1, quella che gira sulla scheda di win11.
FAMIGLIA_DEFAULT = "qwen-image-2.1"
FAMIGLIA_SDXL = "sdxl-turbo"
# SD 1.5 (2026-09-30), il modello del volto di Anna: ChickMixFlat v1.0.
# Perché è una famiglia e non un semplice `modello` dentro sdxl-turbo: il grafo
# è lo stesso (checkpoint unico, come SDXL), ma i NUMERI no. SDXL/Pony rende a
# 1024 e CFG 5; SD 1.5 rende a 512-768 e CFG 7, e a 1024 raddoppia l'anatomia
# invece di disegnarla. Un job SD 1.5 servito dalla ricetta Pony non fallisce:
# produce un'immagine sbagliata, che è peggio.
#
# Il resto del perché sta in docs/comfyui.md: 1,99 GiB contro 6,9 GB è la
# differenza fra stare e non stare su una scheda da 8 GB, e "flat, pure color,
# 2.5D" è dichiaratamente un disegno — il confine 1 di `shared/showcase.py` lo
# rispetta il modello stesso, non solo il prompt.
FAMIGLIA_SD15 = "sd15"
# Le famiglie che sono UN checkpoint unico caricato da CheckpointLoaderSimple
# (UNet + CLIP + VAE insieme): cambiano i numeri, non i nodi. Qwen-Image invece è
# GGUF + text encoder + VAE separati.
FAMIGLIE_CHECKPOINT = (FAMIGLIA_SDXL, FAMIGLIA_SD15)
FAMIGLIE = (FAMIGLIA_DEFAULT, *FAMIGLIE_CHECKPOINT)


def usa_checkpoint(famiglia: str) -> bool:
    """True se la famiglia è un checkpoint unico (CheckpointLoaderSimple).

    Il controllo era scritto cinque volte come `famiglia == FAMIGLIA_SDXL`, e
    ogni volta significava "questo è un checkpoint, non Qwen" — non "questo è
    SDXL". Con una seconda famiglia di checkpoint quelle cinque condizioni
    sarebbero diventate cinque bug silenziosi: il ponte che verifica i nodi
    sbagliati, il control-plane che non prenota la memoria del Mac. Una funzione
    con un nome dice cosa si sta davvero chiedendo.
    """
    return str(famiglia or "").strip().lower() in FAMIGLIE_CHECKPOINT


def famiglie_capaci(capace_di) -> tuple[str, ...]:
    """Le famiglie che un ponte sa eseguire, da una stringa o da una sequenza.

    La firma storica era una stringa sola (`?famiglia=sdxl-turbo`). Il Mac ha due
    famiglie di checkpoint — Pony per gli sketch, ChickMixFlat per il ritratto di
    Anna — e un ponte solo: `--model sdxl-turbo,sd15` dice entrambe. Due ponti
    sulla stessa ComfyUI non si possono avviare (il lucchetto di istanza singola
    li ferma: la scheda è una), quindi la lista è l'unico modo di servire
    entrambe senza spegnere il feed.
    """
    if not capace_di:
        return ()
    pezzi = capace_di.split(",") if isinstance(capace_di, str) else capace_di
    return tuple(str(p).strip().lower() for p in pezzi if str(p).strip())


def _modello_famiglia(famiglia: str) -> str:
    """Il nome del file che la famiglia carica se il job non ne impone uno.

    Serve a `modello_effettivo`, cioè a ciò che si legge DOPO per capire con cosa
    è stata generata un'immagine. Un nome sbagliato qui non rompe niente e non si
    vede: è il tipo di errore che si scopre guardando un ritratto.
    """
    ricetta = RICETTE_CHECKPOINT.get(str(famiglia or "").strip().lower())
    return ricetta["ckpt"] if ricetta else MODELLO_DEFAULT["unet"]

POSE_PRESET = ("standing", "arms_open", "seated", "kneeling", "lying", "walking", "dancing")
_POSE_REGOLE = (
    ("arms_open", r"\b(?:arms? (?:wide )?open|outstretched arms?|braccia aperte|braccia distese)\b"),
    ("kneeling", r"\b(?:kneel(?:ing|s)?|inginocchi\w*)\b"),
    ("lying", r"\b(?:lying down|lying on|sdraiat\w*|distes[oaie]|sul letto|on (?:a |the )?bed)\b"),
    ("seated", r"\b(?:sitting|seated|sedut\w*|su una sedia|on (?:a |the )?chair)\b"),
    ("walking", r"\b(?:walking|walks?|cammin\w*)\b"),
    ("dancing", r"\b(?:dancing|dancer|dance pose|ball\w*|danz\w*)\b"),
    ("standing", r"\b(?:standing|in piedi|erect pose)\b"),
)


def scegli_pose_preset(prompt: str) -> str:
    """Select one-body presets conservatively; empty means text-only."""
    testo = " ".join(str(prompt or "").lower().split())
    if re.search(r"\b(?:two people|two persons|couple|coppia|due persone|"
                 r"man and (?:a )?woman|woman and (?:a )?man|uomo e (?:una )?donna)\b", testo):
        return ""
    for nome, pattern in _POSE_REGOLE:
        if re.search(pattern, testo, re.IGNORECASE):
            return nome
    return ""


def _posa_dichiarata(famiglia: str) -> bool:
    """True se la ricetta di questa famiglia dichiara un ControlNet openpose.

    La domanda giusta non è «è SDXL?» ma «c'è il ControlNet?»: il 2026-09-30 SD 1.5
    non ce l'aveva e la deduzione dal testo valeva solo per il Pony; ora il file c'è
    (`control_v11p_sd15_openpose_fp16`, vedi `MODELLO_SD15`) e la stessa deduzione
    vale per entrambe. Legarla al NOME della famiglia avrebbe voluto dire riscrivere
    quel `if` il giorno in cui il file arrivava — o dimenticarsene, e avere una
    famiglia che sa posare e non posa mai.
    """
    ricetta = RICETTE_CHECKPOINT.get(str(famiglia or "").strip().lower()) or {}
    return bool(ricetta.get("controlnet_openpose"))


def posa_supportata(famiglia: str) -> bool:
    """Vero se la famiglia ha il ControlNet che sa fare una posa (openpose).

    È `_posa_dichiarata` con un nome pubblico, e serve a chi decide le pose FUORI
    da qui: la serie di Anna sceglie variante per variante quale posa chiedere, e
    deve poter fare la stessa domanda che fa `nuovo_job` senza importare una
    funzione privata. La domanda resta «c'è il ControlNet?», non «è SDXL?».
    """
    return _posa_dichiarata(famiglia)


def riferimento_supportato(famiglia: str) -> bool:
    """Vero se la famiglia ha l'adattatore del volto (IP-Adapter + CLIP-ViT).

    Servono entrambi: l'adattatore è ciò che aggancia il riferimento al modello, il
    CLIP-ViT è l'occhio che lo legge. Una famiglia che ne dichiara uno solo non è
    «quasi pronta» — è una famiglia che un riferimento non lo sa usare, e il grafo
    lo direbbe fallendo a ogni job. Dirlo PRIMA di accodare (lo fa la vetrina) è la
    differenza fra un documento che si corregge e dodici varianti morte.
    """
    ricetta = RICETTE_CHECKPOINT.get(str(famiglia or "").strip().lower()) or {}
    return bool(ricetta.get("ipadapter") and ricetta.get("clip_vision"))


# Il peso del riferimento (0..2). Il default 0.8 è quello che la scheda di
# IP-Adapter Plus Face indica per un ritratto: a 1.0 il volto del riferimento
# comincia a vincere sull'abito e sulla scena. Sta qui e non due volte nel modulo
# (la firma di `nuovo_job` e il grafo) perché due copie dello stesso numero sono
# due default che si allontanano: il grafo usava 0.8 anche quando il job ne
# dichiarava un altro solo perché qualcuno se ne ricordava.
RIFERIMENTO_FORZA_DEFAULT = 0.8


def nuovo_job(prompt: str, *, negativo: str = "", larghezza: int = 768,
              altezza: int = 768, passi: int = 25, seed: int = 0,
              fix: int = 0,
              richiedente: str = "", canale: str = "", modello: str = "",
              destinazione: str = "", famiglia: str = "",
              pose_image: str = "", pose_preset: str = "", pose_strength: float = 1.0,
              reference_image: str = "", reference_strength: float = RIFERIMENTO_FORZA_DEFAULT,
              lora_name: str = "", lora_strength: float = 0.8,
              adesso: float | None = None) -> dict:
    """Costruisce un job valido. Solleva ValueError se il prompt è vuoto.

    I numeri si limitano invece di essere rifiutati: chi chiede 4096 px ha chiesto
    qualcosa di impossibile, non di sbagliato, e il tetto si vede nel job.

    `fix` è il secondo passaggio del grafo: 0 (default, un passaggio solo) o 2
    (il doppio del lato, come i demo del modello). Un fattore 1 non è un fix —
    è un giro di denoise senza ingrandire — quindi cade su 0 come un 3 cade
    su 2: si limita, non si rifiuta.

    `destinazione` è DOVE va consegnata l'immagine finita (l'id della chat): il
    control-plane non sa parlare con Telegram, sa solo che quel file è per quella
    conversazione. È il driver a consegnarla, perché è lui che ha il file.

    `famiglia` dice quale modello eseguirà il job (default Qwen-Image 2.1;
    `sdxl-turbo` per i job leggeri del Mac). Una famiglia sconosciuta cade sul
    default invece di essere rifiutata.

    `reference_image` è il volto di riferimento, un nome dentro ComfyUI/input (come
    `pose_image`): l'adattatore lo mostra al modello a ogni passo di sampling, ed è
    ciò che tiene la stessa identità fra due generazioni — il seed da solo no.
    """
    testo = " ".join(str(prompt or "").split())
    if not testo:
        raise ValueError("prompt vuoto: un job immagine senza prompt non esiste")
    famiglia = str(famiglia or "").strip().lower()
    if famiglia not in FAMIGLIE:
        famiglia = FAMIGLIA_DEFAULT
    pose_image = str(pose_image or "").strip().replace("\\", "/")
    # LoadImage legge esclusivamente da ComfyUI/input. Un nome relativo evita
    # che un job remoto trasformi il ponte in un lettore di file arbitrari.
    if pose_image.startswith("/") or ".." in pose_image.split("/") or "://" in pose_image:
        raise ValueError("pose_image deve essere un nome relativo dentro ComfyUI/input")
    pose_preset = str(pose_preset or "").strip().lower()
    if pose_preset and pose_preset not in POSE_PRESET:
        raise ValueError("pose_preset sconosciuto")
    # La posa si deduce dal testo SOLO dove la famiglia ha il ControlNet che la sa
    # fare (`_posa_dichiarata`): un preset senza il suo ControlNet non farebbe una
    # posa — farebbe un'immagine diversa. Dove il ControlNet manca, una posa chiesta
    # a mano arriva comunque al grafo e lì `workflow_checkpoint` solleva: meglio un
    # job fallito e visibile che un ritratto sbagliato.
    reference_image = str(reference_image or "").strip().replace("\\", "/")
    # Stessa regola di pose_image, e per la stessa ragione: `LoadImage` legge
    # esclusivamente da ComfyUI/input, e un nome relativo impedisce a un job remoto
    # di trasformare il ponte in un lettore di file arbitrari.
    if (reference_image.startswith("/") or ".." in reference_image.split("/")
            or "://" in reference_image):
        raise ValueError("reference_image deve essere un nome relativo dentro ComfyUI/input")
    lora_name = str(lora_name or "").strip().replace("\\", "/")
    if lora_name.startswith("/") or ".." in lora_name.split("/") or "://" in lora_name:
        raise ValueError("lora_name deve essere un nome relativo dentro ComfyUI/models/loras")
    # `modello` finisce in `CheckpointLoaderSimple.ckpt_name`: è un input di
    # percorso, come pose_image e lora_name. Dall'uscita della vetrina di Anna
    # (2026-09-30) quel nome può arrivare da un DOCUMENTO, quindi vale la stessa
    # regola: un nome dentro i modelli di ComfyUI, non un percorso.
    modello = str(modello or "").strip().replace("\\", "/")
    if modello.startswith("/") or ".." in modello.split("/") or "://" in modello:
        raise ValueError("modello deve essere un nome relativo dentro i modelli di ComfyUI")
    return {
        "id": uuid.uuid4().hex[:12],
        "prompt": testo[:2000],
        "negativo": " ".join(str(negativo or "").split())[:1000],
        "larghezza": max(64, min(int(larghezza), LIMITE_LATO)),
        "altezza": max(64, min(int(altezza), LIMITE_LATO)),
        "passi": max(1, min(int(passi), LIMITE_PASSI)),
        "fix": FIX_FATTORE if int(fix or 0) >= FIX_FATTORE else 0,
        "seed": int(seed),
        "richiedente": str(richiedente or "")[:64],
        "canale": str(canale or "")[:32],
        "modello": modello[:120],
        "modello_effettivo": modello[:120] or _modello_famiglia(famiglia),
        "famiglia": famiglia,
        "pose_image": pose_image[:240],
        "pose_preset": pose_preset,
        "pose_strength": max(0.0, min(float(pose_strength), 2.0)),
        # Il peso del riferimento: 0..2, come la posa. Il default 0.8 è quello che
        # la scheda di IP-Adapter Plus Face indica per un ritratto: a 1.0 il volto
        # del riferimento comincia a vincere sull'abito e sulla scena.
        "reference_image": reference_image[:240],
        "reference_strength": max(0.0, min(float(reference_strength), 2.0)),
        "lora_name": lora_name[:240],
        "lora_strength": max(-2.0, min(float(lora_strength), 2.0)),
        "destinazione": str(destinazione or "")[:64],
        "consegnato": False,
        "stato": "pending",
        "creato_ts": float(adesso if adesso is not None else time.time()),
        "preso_ts": 0.0,
        "esito": {},
    }

# ── La richiesta a parole ("mandami una foto di X") ──────────────────────────
# Perché esiste, visto che il comando `!immagine` funziona: chiedere la sintassi
# giusta è chiedere di ricordarsi un comando, e in una chat si scrive così.
#
# Perché il riconoscimento sta QUI e non nel modello: la scheda è una sola e un
# falso positivo costa 5-12 minuti di GPU. Quindi regole poche, dichiarate e
# leggibili — ognuna con un nome che finisce nei log, perché "perché ha
# disegnato?" deve restare una frase. Chi PUO' chiederla non si decide qui:
# è `CHANNEL_OPERATOR`, nel control-plane (vedi `_channel_immagine`).
#
# Le due regole non si sovrappongono per caso: la prima è "dammi", la seconda
# "vorrei". Tutto il resto è silenzio, e il silenzio non costa niente.
_FOTO = (r"(?:foto|fotografia|immagine|ritratto|selfie|disegno|quadro|scatto|"
         r"scena|illustrazione|paesaggio|poster|vignetta|bozzetto|schizzo|copertina|"
         r"close-up|primo piano|dettaglio|macro|figura intera|mezzo busto)")
# Tra il verbo e la cosa chiesta ci stanno solo parole che non cambiano la
# richiesta. Senza questo elenco, "mandami il numero e poi la foto" passerebbe:
# un false positive che nessuno vede finché la scheda non è occupata.
_RIEMPI_W = (r"(?:una|un|il|lo|la|le|gli|i|dei|delle|degli|dell|altro|altra|"
             r"un'altra|nuova|nuovo|bella|bello|piccola|piccolo|grande|mia|mio|tua|"
             r"tuo|di|con|per|che|mi|me|un'|l'|all'|dall'|nell')")
# Lo stile fra verbo e soggetto ("disegnami con tecnica a carboncino un
# close-up"): si lascia passare, ma solo i MATERIALI entrano nel prompt (lo
# stile è richiesta, non riempitivo da scartare).
_STILE_W = (r"(?:tecnica|stile|tratto|a|in|ad|al|alla|matita|carboncino|inchiostro|"
            r"acquerello|acquerelli|pastello|pastelli|olio|acrilico|grafite|penna|"
            r"biro|gesso|sanguigna|tempera|gouache|puntinismo)")
_STILE_KEEP = frozenset({"matita", "carboncino", "inchiostro", "acquerello",
                         "acquerelli", "pastello", "pastelli", "olio", "acrilico",
                         "grafite", "penna", "biro", "gesso", "sanguigna", "tempera",
                         "gouache", "puntinismo"})
_MEZZO = rf"(?P<mezzo>(?:\s+(?:{_RIEMPI_W}|{_STILE_W}))*)"
REGOLE_IMMAGINE = (
    # "puoi mandarmi una foto": potere + infinito. È la forma più comune, e senza
    # questa regola resta muta (il verbo vero è l'infinito, non "puoi").
    ("potere-infinito", re.compile(
        rf"^\s*(?:aurora[\s,]+)?(?:mi\s+)?(?:puoi|potresti|riesci\s+a|sapresti)\s+"
        rf"(?:mandarmi|mandare|inviarmi|inviare|farmi|fare|generarmi|generare|"
        rf"crearmi|creare|disegnarmi|disegnare|illustrarmi|illustrare)\b"
        rf"{_MEZZO}\s*{_FOTO}\b", re.IGNORECASE)),
    ("mandare", re.compile(
        rf"^\s*(?:aurora[\s,]+)?(?:mi\s+|me\s+la\s+)?(?:manda|mandami|mandi|mandate|"
        rf"mandarmi|mandarmela|invia|inviami|inviate|fammi|fai|fate|genera|"
        rf"generami|generate|crea|creami|create|disegna|disegnami|disegnate|"
        rf"illustra|illustrami|abbozza|schizza)\b{_MEZZO}\s*{_FOTO}\b", re.IGNORECASE)),
    ("volere", re.compile(
        rf"^\s*(?:aurora[\s,]+)?(?:vorrei|voglio|mi\s+piacerebbe|mi\s+serve|"
        rf"mi\s+servirebbe|potrei\s+avere)\b{_MEZZO}\s*{_FOTO}\b", re.IGNORECASE)),
)

# Davanti all'idea si toglie solo la sintassi della domanda. Le preposizioni
# restano: "di te esplicita" e "con un cappello" sono CONTENUTO, e toglierle
# cambierebbe quello che si chiede di disegnare.
_SINTASSI = re.compile(r"^\s*(?:che|dove|in\s+cui|:|-|–|,)\s*", re.IGNORECASE)


def richiesta_immagine(testo) -> dict | None:
    """La frase chiede un'immagine? -> ``{"idea": ..., "regola": ...}``, o None.

    ``idea`` vuota significa "ha chiesto un'immagine senza dire quale": chi
    chiama chiede cosa disegnare — che è diverso da "non ha chiesto niente".
    """
    t = " ".join(str(testo or "").split())
    if not t:
        return None
    for nome, regola in REGOLE_IMMAGINE:
        trovata = regola.search(t)
        if trovata:
            mezzo = (trovata.groupdict().get("mezzo") or "").strip()
            stile = " ".join(w for w in mezzo.split() if w.lower() in _STILE_KEEP)
            dopo = _SINTASSI.sub("", t[trovata.end():], count=1).strip()
            idea = " ".join(p for p in (stile, dopo) if p).strip()
            return {"idea": idea[:400], "regola": nome}
    return None




class ImmagineQueue:
    """Coda dei job immagine: stato per job, tetti, scadenze.

    Un job preso e mai concluso torna disponibile dopo `claim_ttl_s`: un ponte
    morto a metà non deve bloccare il lavoro per sempre, ma nemmeno farlo
    eseguire due volte nello stesso minuto.
    """

    def __init__(self, *, clock=time.time, max_jobs: int = DEFAULT_MAX_JOBS,
                 ttl_s: float = DEFAULT_JOB_TTL_S,
                 claim_ttl_s: float = DEFAULT_CLAIM_TTL_S,
                 state_path: str = ""):
        self.clock = clock
        self.max_jobs = max(1, int(max_jobs))
        self.ttl_s = max(30.0, float(ttl_s))
        self.claim_ttl_s = max(30.0, float(claim_ttl_s))
        self._lock = threading.Lock()
        self._job: dict = {}
        self._storico: list = []
        self.state_path = str(state_path or "")
        self._restore()

    def accoda(self, job: dict) -> dict:
        """Mette in coda un job e pota i vecchi (scaduti o conclusi)."""
        with self._lock:
            self._pota_locked()
            attivi = [j for j in self._job.values() if j["stato"] in ("pending", "running")]
            if len(attivi) >= self.max_jobs:
                raise RuntimeError(f"coda piena ({self.max_jobs} job in attesa o in corso)")
            self._job[job["id"]] = dict(job)
            self._save_locked()
            return dict(self._job[job["id"]])

    def prossimo(self, capace_di: str | tuple | None = None) -> dict | None:
        """Il job più vecchio da eseguire, marcato `running` (claim).

        `capace_di` limita ai job di una famiglia di modello: un ponte che sa
        eseguire solo SDXL-Turbo (il Mac) non deve prendere un job Qwen-Image.
        Può essere una stringa o una lista separata da virgole quando il ponte ne
        esegue più di una (`sdxl-turbo,sd15`): il vecchio job più vecchio resta il
        criterio, la famiglia non riordina la coda.
        """
        with self._lock:
            self._pota_locked()
            candidati = [j for j in self._job.values() if j["stato"] == "pending"]
            capaci = famiglie_capaci(capace_di)
            if capaci:
                candidati = [j for j in candidati
                             if j.get("famiglia", FAMIGLIA_DEFAULT) in capaci]
            candidati.sort(key=lambda j: j["creato_ts"])
            if not candidati:
                return None
            job = candidati[0]
            job["stato"] = "running"
            job["preso_ts"] = self.clock()
            self._save_locked()
            return dict(job)

    def concludi(self, job_id: str, ok: bool, *, file: str = "", errore: str = "",
                 durata_ms: int = 0) -> dict | None:
        """Chiude un job. Un id sconosciuto non è un errore: è un doppione."""
        with self._lock:
            job = self._job.get(str(job_id or ""))
            if job is None:
                return None
            if job["stato"] in ("done", "failed"):
                return {**job, "_already_concluded": True}
            job["stato"] = "done" if ok else "failed"
            job["esito"] = {"file": str(file or ""),
                            "errore": str(errore or "")[:400],
                            "durata_ms": int(durata_ms or 0), "ts": self.clock()}
            self._storico.append(dict(job))
            self._storico = self._storico[-20:]
            self._save_locked()
            return dict(job)

    def rinvia(self, job_id: str) -> bool:
        """Return a claimed job to the queue after temporary host pressure."""
        with self._lock:
            job = self._job.get(str(job_id or ""))
            if not job or job["stato"] != "running":
                return False
            job["stato"] = "pending"
            job["preso_ts"] = 0.0
            self._save_locked()
            return True

    def job(self, job_id: str) -> dict | None:
        with self._lock:
            trovato = self._job.get(str(job_id or ""))
            return dict(trovato) if trovato else None

    def running_ids(self, famiglia: str = "") -> list[str]:
        with self._lock:
            self._pota_locked()
            capaci = famiglie_capaci(famiglia)
            return [j["id"] for j in self._job.values()
                    if j["stato"] == "running"
                    and (not capaci or j.get("famiglia") in capaci)]

    def ha_in_coda(self, famiglia: str = "") -> bool:
        """True se resta un job pending per una delle famiglie richieste."""
        with self._lock:
            self._pota_locked()
            capaci = famiglie_capaci(famiglia)
            return any(j["stato"] == "pending"
                       and (not capaci or j.get("famiglia") in capaci)
                       for j in self._job.values())

    def da_consegnare(self, canale: str) -> list:
        """Le immagini PRONTE da consegnare a questo canale, non ancora consegnate.

        Perché un outbox e non una chiamata del CP verso la piattaforma: il CP non
        ha il file e non sa parlare con Telegram. Il driver invece ha entrambi —
        quindi *tira* anche questo, come tira le decisioni da /channel/reply.
        """
        canale = str(canale or "").strip().lower()
        with self._lock:
            self._pota_locked()
            pronte = []
            for job in self._job.values():
                if (job["stato"] == "done" and not job.get("consegnato")
                        and job.get("destinazione")
                        and str(job.get("canale", "")).lower() == canale):
                    pronte.append({"id": job["id"], "file": job["esito"].get("file", ""),
                                   "destinazione": job["destinazione"],
                                   "prompt": job["prompt"][:200],
                                   "richiedente": job["richiedente"]})
            pronte.sort(key=lambda j: j["id"])
            return pronte

    def consegnato(self, job_id: str) -> bool:
        """Segna un'immagine come consegnata: senza questo si ripeterebbe."""
        with self._lock:
            job = self._job.get(str(job_id or ""))
            if job is None or job["stato"] != "done":
                return False
            job["consegnato"] = True
            self._save_locked()
            return True

    def stato(self) -> dict:
        with self._lock:
            self._pota_locked()
            per_stato = {stato: 0 for stato in STATI}
            for job in self._job.values():
                per_stato[job["stato"]] += 1
            da_consegnare = [j["id"] for j in self._job.values()
                             if j["stato"] == "done" and not j.get("consegnato")
                             and j.get("destinazione")]
            return {
                "per_stato": per_stato,
                "in_coda": per_stato["pending"],
                "in_esecuzione": per_stato["running"],
                "da_consegnare": len(da_consegnare),
                "max_jobs": self.max_jobs,
                "ttl_s": self.ttl_s,
                "ultimi": [{"id": j["id"], "stato": j["stato"],
                            "prompt": j["prompt"],
                            "famiglia": j["famiglia"],
                            "modello_effettivo": j["modello_effettivo"],
                            "esito": j.get("esito", {})} for j in self._storico[-5:]],
            }

    def _pota_locked(self) -> None:
        """Toglie i job scaduti e libera i claim morti. Chiamata con il lock."""
        adesso = self.clock()
        changed = False
        for chiave, job in list(self._job.items()):
            eta = adesso - job["creato_ts"]
            if job["stato"] in ("pending", "running") and eta > self.ttl_s:
                del self._job[chiave]
                changed = True
                continue
            if (job["stato"] == "running"
                    and adesso - job["preso_ts"] > self.claim_ttl_s):
                job["stato"] = "pending"
                job["preso_ts"] = 0.0
                changed = True
            if job["stato"] in ("done", "failed") and eta > self.ttl_s:
                del self._job[chiave]
                changed = True
        if changed:
            self._save_locked()

    def _restore(self) -> None:
        """Restore jobs; keep live claims until their lease expires.

        A bridge can still be rendering while the control-plane restarts. Its
        result must be accepted before the job can be offered a second time.
        """
        if not self.state_path:
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            jobs = saved.get("jobs", {})
            history = saved.get("history", [])
            if not isinstance(jobs, dict) or not isinstance(history, list):
                return
            self._job = {str(key): dict(value) for key, value in jobs.items()
                         if isinstance(value, dict) and value.get("id")}
            self._storico = [dict(value) for value in history if isinstance(value, dict)][-20:]
            with self._lock:
                self._pota_locked()
                self._save_locked()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Una coda corrotta non deve impedire l'avvio del control-plane.
            self._job = {}
            self._storico = []

    def _save_locked(self) -> None:
        """Scrittura atomica: un kill del container non lascia mezzo JSON."""
        if not self.state_path:
            return
        directory = os.path.dirname(os.path.abspath(self.state_path))
        try:
            os.makedirs(directory, exist_ok=True)
            temporary = self.state_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "jobs": self._job, "history": self._storico},
                          handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        except OSError as error:
            # La coda in memoria resta utilizzabile, ma il mancato salvataggio
            # deve comparire nei log: altrimenti il prossimo restart perde job.
            warnings.warn(f"coda immagini non salvata in {self.state_path}: {error}",
                          RuntimeWarning, stacklevel=2)


# ── Il grafo da mandare a ComfyUI ────────────────────────────────────────────
# Non è inventato: è la prova che ha funzionato su questa macchina (run
# 19e69776), con i suoi valori. Due scelte non ovvie che si leggono lì e che non
# vanno "semplificate":
#
#   - il text encoder da 8B gira sulla CPU (`device: cpu`): con 8 GB di VRAM è
#     esattamente ciò che lascia spazio al diffusion;
#   - il negativo sta DENTRO il prompt ("no text, no watermark, no logos"):
#     Qwen-Image 2.1 segue le istruzioni, e `resolution` segue il lato del latente.
#
# I pesi sono i GGUF **non censurati** di Qwen-Image 2.1 (variante `-UC`): gli
# stessi pesi base senza safety checker, quindi l'immagine dipende solo dal
# prompt. Q5_K_M è la quantizzazione con cui la run 19e69776 è passata — 4,86 GiB
# in VRAM a 768×768, 25 passi, text encoder sulla CPU.
#
# Il nome del file e la sua impronta SHA-256 non si scrivono qui a memoria: stanno
# in `integrations/comfyui/modelli.json`, che `install-model.ps1` legge per
# scaricarli, e `tests/test_comfyui_modelli.py` tiene i due lati allineati:
# rinominare il file da un lato solo fa fallire un test, non un job in silenzio.
MODELLO_DEFAULT = {
    "unet": "qwen-image-2.1-UC-Q5_K_M.gguf",
    "clip": "qwen3vl_8b_int8_convrot.safetensors",
    "clip_type": "qwen_image",
    "clip_device": "cpu",
    "vae": "qwen_image_2.1_vae_bf16.safetensors",
    "cfg": 1.0,
    "sampler": "euler",
    "scheduler": "simple",
}

# La ricetta CyberRealistic Pony V18: checkpoint SDXL/Pony unico. I parametri
# seguono la scheda ufficiale del modello: CFG 5, 30+ passi e Clip Skip 2.
MODELLO_SDXL = {
    "ckpt": "CyberRealisticPony_V18.0_F16.safetensors",
    "cfg": 5.0,
    "clip_skip": -2,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "controlnet_openpose": "xinsir-controlnet-openpose-sdxl-1.0.safetensors",
    # `scale_stick_for_xinsr_cn` è l'accorgimento che il ControlNet di xinsir
    # chiede a DWPose (scheletri addestrati con lo «stick» largo). È una proprietà
    # del FILE, non della posa: per questo sta nella ricetta e non nel grafo.
    "dw_scale_stick": "enable",
    "lora": os.getenv("SDXL_LORA_NAME", "").strip(),
    "lora_strength": float(os.getenv("SDXL_LORA_STRENGTH", "0.8")),
    # Niente `ipadapter`/`clip_vision`: su questa macchina i pesi del riferimento
    # esistono per SD 1.5 (`modelli-riferimento.json`), e l'adattatore di un'altra
    # famiglia non è «quasi giusto» — ViT-bigG contro ViT-H, altre dimensioni. Chi
    # chiede un riferimento al Pony riceve un errore, non un volto diverso.
}

# La ricetta ChickMixFlat v1.0 (SD 1.5): il volto di Anna. Stesso grafo del Pony
# — checkpoint unico, un CLIP per lato — con i numeri di SD 1.5: CFG 7 (a 5 il
# modello resta tiepido), Clip Skip 2, DPM++ SDE Karras: è la coppia dichiarata
# dai demo di ChickMixFlat che l'operatore ha scelto come default di Anna. Il file
# lo dichiara la vetrina di Anna (`modello`), ma il default
# della famiglia è qui: due posti che dicono lo stesso nome, tenuti allineati dal
# test del manifest, come per il Pony.
#
# Il ControlNet openpose di SD 1.5 (2026-09-30). Fino a qui la famiglia non faceva
# pose, e lo diceva: `xinsir-controlnet-openpose-sdxl-1.0` non è quello di SD 1.5,
# e un ControlNet sbagliato deforma invece di posare. Il file giusto è
# `control_v11p_sd15_openpose_fp16`: il ControlNet 1.1 ufficiale, nella variante
# fp16 di comfyanonymous (689 MiB invece di 1,38 GiB, stesso grafo). DWPose lo
# alimenta con `dw_scale_stick: disable` — lo stick largo è una richiesta del
# modello di xinsir, non di questo.
#
# `ipadapter`/`clip_vision` sono il RIFERIMENTO, ed è la cosa che il seed non sa
# fare: il volto resta lo stesso perché il modello lo **rivede** a ogni
# generazione, non perché ricorda il numero. Il file è
# `ip-adapter-plus-face_sd15`, la variante per i volti di IP-Adapter Plus: passa
# dal CLIP-ViT-H e **non** da InsightFace, quindi niente onnxruntime, niente
# rilevatore di volti, e nessuna impronta di un volto reale nel giro — il
# riferimento è un disegno di Anna. FaceID sarebbe la strada se un giorno
# servisse agganciare un volto *fotografico*: qui sarebbe un'altra cosa.
MODELLO_SD15 = {
    "ckpt": "chickmixflat_v10.ckpt",
    "cfg": 7.0,
    "clip_skip": -2,
    "sampler": "dpmpp_sde",
    "scheduler": "karras",
    "controlnet_openpose": "control_v11p_sd15_openpose_fp16.safetensors",
    "dw_scale_stick": "disable",
    "ipadapter": "ip-adapter-plus-face_sd15.safetensors",
    "clip_vision": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
    "lora": os.getenv("SD15_LORA_NAME", "").strip(),
    "lora_strength": float(os.getenv("SD15_LORA_STRENGTH", "0.8")),
}

# Il lato con cui ogni famiglia rende bene. Non è un limite (LIMITE_LATO lo è) ed
# è la ragione per cui la famiglia non può essere solo un nome di file: SD 1.5
# nasce a 512 e a 1024 ripete l'anatomia; SDXL/Pony è addestrato a 1024.
MISURA_SD15 = 768
LATO_CONSIGLIATO = {FAMIGLIA_SDXL: 1024, FAMIGLIA_SD15: MISURA_SD15}

# La ricetta di ogni famiglia di checkpoint, in un posto solo: la usano il grafo
# (per costruirlo) e `_modello_famiglia` (per dire con cosa è stata generata
# un'immagine). Una famiglia nuova si aggiunge qui, non in tre `if`.
RICETTE_CHECKPOINT = {FAMIGLIA_SDXL: MODELLO_SDXL, FAMIGLIA_SD15: MODELLO_SD15}

# Il "fix" è l'ingranditore dei demo di ChickMixFlat. I demo dichiarano
# `hires 2× R-ESRGAN 4x+`; qui si decodifica il primo passaggio e lo si
# ingrandisce con quel modello neurale, senza un secondo sampling. Ingrandire il
# *latente* (`LatentUpscale` `bislerp`)
# NON è la stessa cosa, ed è stato misurato il 2026-09-30: la serie a 2×
# (`bridge_00081_`–`_083_`, 40 passi) esce con aloni cromatici su ogni contorno,
# bianchi bruciati e la composizione rifatta, perché a 1024×1536 il checkpoint —
# SD 1.5, nativa 512, quindi 4× i pixel — disegna da capo quello che non sa
# invece di rifinire qualcosa che c'è. Il difetto c'era già a 30 passi
# (`anna_due_00001_`). Un ingranditore neurale è la differenza fra i due grafi:
# senza il modello installato il fix non si costruisce, e lo dice ComfyUI —
# meglio un errore del nodo che un'immagine che sembra buona e non lo è.
FIX_UPSCALER = os.getenv("FIX_UPSCALER", "RealESRGAN_x4plus.pth")
#
# Il fix è a 2× o non c'è: un fattore 1 sarebbe un giro di denoise senza
# ingrandire, cioè un'altra cosa (rifinitura), e non si chiama fix.
FIX_FATTORE = 2


def _save_node(prefisso: str, jpeg: bool, sorgente: str = "457") -> dict:
    """Il nodo di salvataggio, puntato sulla decodifica giusta.

    `sorgente` è `457` (primo passaggio) o `476` (dopo il fix): col fix acceso
    il file da salvare è il secondo, non il primo — altrimenti uscirebbe
    l'immagine della misura chiesta e il fix sarebbe solo tempo perso.
    """
    if jpeg:
        return {"class_type": "HyperSpaceSaveJPEG",
                "inputs": {"images": [sorgente, 0], "filename_prefix": str(prefisso),
                           "quality": 92}}
    return {"class_type": "SaveImage",
            "inputs": {"images": [sorgente, 0], "filename_prefix": str(prefisso)}}


def workflow(job: dict, *, modello: dict | None = None, prefisso: str = "HyperSpace",
             risoluzione: int | None = None, jpeg: bool = True) -> dict:
    """Il grafo ComfyUI per un job, scelto dalla sua famiglia di modello.

    ``modello`` sovrascrive i file (un'altra macchina avrà altri nomi) e il job può
    indicare un ``modello`` suo. La famiglia decide QUALE grafo: Qwen-Image 2.1
    (GGUF + text encoder su CPU, default) oppure un checkpoint unico — SDXL/Pony
    (``sdxl-turbo``) o SD 1.5 (``sd15``), che condividono il grafo e cambiano solo
    i numeri della ricetta.

    Un ``reference_image`` su Qwen-Image non è un campo da ignorare: quel grafo non
    ha un IP-Adapter, quindi il volto uscirebbe qualunque — e il job riuscirebbe.
    Qui si solleva, come fa ``workflow_checkpoint`` per le famiglie senza adattatore.
    """
    if usa_checkpoint((job or {}).get("famiglia")):
        return workflow_checkpoint(job, modello=modello, prefisso=prefisso, jpeg=jpeg)
    # Il riferimento è per famiglia come la posa: Qwen-Image non ha un IP-Adapter,
    # quindi il campo non arriverebbe al modello. Ignorarlo darebbe un ritratto che
    # *sembra* riuscito con un altro volto — cioè il difetto che il riferimento
    # esiste per togliere — e in silenzio. La vetrina lo rifiuta già prima di
    # accodare (`verifica_vetrina`), ma un job può arrivare da un client che non è
    # la vetrina: il grafo è l'ultima difesa, e l'ultima difesa non tace.
    if str((job or {}).get("reference_image") or "").strip():
        raise ValueError(
            f"riferimento richiesto per la famiglia {FAMIGLIA_DEFAULT!r}, che non ha un "
            "IP-Adapter dichiarato in shared/image_jobs.py")
    # Stessa regola per la posa: qui non c'è nessun ControlNet (`workflow_checkpoint`
    # solleva per le famiglie che non lo dichiarano, e questa è una di quelle), quindi
    # un `pose_preset` non sposterebbe lo scheletro — non farebbe niente, e il job
    # tornerebbe "done" con l'immagine di prima. `scripts/serie.py` manda la posa solo
    # dove `posa_supportata` è vero; chi scrive l'API a mano non ha quel filtro.
    if (str((job or {}).get("pose_image") or "").strip()
            or str((job or {}).get("pose_preset") or "").strip()):
        raise ValueError(
            f"posa richiesta per la famiglia {FAMIGLIA_DEFAULT!r}, che non ha un "
            "ControlNet openpose dichiarato in shared/image_jobs.py")
    scelte = {**MODELLO_DEFAULT, **(modello or {})}
    if str(job.get("modello") or "").strip():
        scelte["unet"] = str(job["modello"]).strip()
    lato = int(risoluzione or max(int(job["larghezza"]), int(job["altezza"])))
    return {
        "451": {"class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": scelte["unet"]}},
        "453": {"class_type": "CLIPLoader",
                "inputs": {"clip_name": scelte["clip"], "type": scelte["clip_type"],
                           "device": scelte["clip_device"]}},
        "454": {"class_type": "VAELoader", "inputs": {"vae_name": scelte["vae"]}},
        "452": {"class_type": "TextEncodeQwenImage21",
                "inputs": {"clip": ["453", 0], "prompt": job["prompt"],
                           "negative_prompt": job.get("negativo", ""),
                           "resolution": lato}},
        "456": {"class_type": "EmptyLatentImage",
                "inputs": {"width": int(job["larghezza"]), "height": int(job["altezza"]),
                           "batch_size": 1}},
        "458": {"class_type": "KSampler",
                "inputs": {"model": ["451", 0], "positive": ["452", 0],
                           "negative": ["452", 1], "latent_image": ["456", 0],
                           "seed": int(job["seed"]), "steps": int(job["passi"]),
                           "cfg": float(scelte["cfg"]),
                           "sampler_name": scelte["sampler"],
                           "scheduler": scelte["scheduler"], "denoise": 1.0}},
        "457": {"class_type": "VAEDecode",
                "inputs": {"samples": ["458", 0], "vae": ["454", 0]}},
        "470": _save_node(prefisso, jpeg),
    }


def workflow_checkpoint(job: dict, *, modello: dict | None = None,
                        prefisso: str = "HyperSpace", jpeg: bool = True,
                        famiglia: str = "") -> dict:
    """Il grafo di un checkpoint unico: Pony (SDXL) o ChickMixFlat (SD 1.5).

    Un grafo per entrambe, perché ``CheckpointLoaderSimple`` porta con sé UNet,
    CLIP e VAE: quello che cambia è la RICETTA della famiglia (CFG, Clip Skip,
    sampler, scheduler), non la forma del grafo. Gli id dei nodi sono gli stessi
    del grafo Qwen, così ``immagini_da_history`` e il ponte non cambiano.

    A differenza di Qwen, un checkpoint usa il negativo (non le istruzioni dentro
    il prompt) e un ``CLIPTextEncode`` per lato.

    Con ``job["fix"]`` a 2 il grafo ingrandisce l'immagine decodificata con
    R-ESRGAN e la porta al doppio del lato, senza ricampionarla: il risultato
    conserva posa e silhouette del primo passaggio e resta leggero sul Mac.
    """
    famiglia = str(famiglia or job.get("famiglia") or FAMIGLIA_SDXL).strip().lower()
    scelte = {**RICETTE_CHECKPOINT.get(famiglia, MODELLO_SDXL), **(modello or {})}
    if str(job.get("modello") or "").strip():
        scelte["ckpt"] = str(job["modello"]).strip()
    lora_name = str(job.get("lora_name") or scelte.get("lora") or "").strip()
    modello_ref = ["451", 0]
    clip_ref = ["451", 1]
    grafo = {
        "451": {"class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": scelte["ckpt"]}},
        "459": {"class_type": "CLIPSetLastLayer",
                "inputs": {"clip": ["451", 1],
                           "stop_at_clip_layer": int(scelte["clip_skip"])}},
        "452": {"class_type": "CLIPTextEncode",
                "inputs": {"clip": ["459", 0], "text": job["prompt"]}},
        "453": {"class_type": "CLIPTextEncode",
                "inputs": {"clip": ["459", 0],
                           "text": str(job.get("negativo") or "")}},
        "456": {"class_type": "EmptyLatentImage",
                "inputs": {"width": int(job["larghezza"]),
                           "height": int(job["altezza"]), "batch_size": 1}},
        "458": {"class_type": "KSampler",
                "inputs": {"model": ["451", 0], "positive": ["452", 0],
                           "negative": ["453", 0], "latent_image": ["456", 0],
                           "seed": int(job["seed"]), "steps": int(job["passi"]),
                           "cfg": float(scelte["cfg"]),
                           "sampler_name": scelte["sampler"],
                           "scheduler": scelte["scheduler"], "denoise": 1.0}},
        "457": {"class_type": "VAEDecode",
                "inputs": {"samples": ["458", 0], "vae": ["451", 2]}},
        "470": _save_node(prefisso, jpeg),
    }
    if lora_name:
        forza = float(job.get("lora_strength", scelte.get("lora_strength", 0.8)))
        grafo["455"] = {"class_type": "LoraLoader", "inputs": {
            "model": ["451", 0], "clip": ["451", 1], "lora_name": lora_name,
            "strength_model": forza, "strength_clip": forza}}
        modello_ref = ["455", 0]
        clip_ref = ["455", 1]
        grafo["459"]["inputs"]["clip"] = clip_ref
        grafo["458"]["inputs"]["model"] = modello_ref
    pose_image = str(job.get("pose_image") or "").strip()
    pose_preset = str(job.get("pose_preset") or "").strip()
    if (pose_image or pose_preset) and not scelte.get("controlnet_openpose"):
        # Il ControlNet openpose è per famiglia di modello: quello di SDXL non
        # capisce i latenti di SD 1.5 e viceversa. Con la famiglia giusta mancante
        # il grafo non si costruisce: senza questo controllo la posa verrebbe
        # ignorata in silenzio, e il job tornerebbe "done" con l'immagine sbagliata.
        raise ValueError(
            f"posa richiesta per la famiglia {famiglia!r}, che non ha un ControlNet "
            "openpose dichiarato in shared/image_jobs.py")
    if pose_image or pose_preset:
        # La foto di riferimento resta un input locale di ComfyUI. DWPose ne
        # estrae lo scheletro; ControlNet vincola la geometria senza copiare
        # volto, vestiti o sfondo della sorgente.
        grafo.update({
            "462": {"class_type": "ControlNetLoader", "inputs": {
                "control_net_name": scelte["controlnet_openpose"]}},
            "463": {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": ["452", 0], "negative": ["453", 0],
                "control_net": ["462", 0], "image": ["464", 0],
                "strength": float(job.get("pose_strength", 1.0)),
                "start_percent": 0.0, "end_percent": 1.0}},
        })
        if pose_image:
            grafo.update({
                "460": {"class_type": "LoadImage", "inputs": {"image": pose_image}},
                "461": {"class_type": "DWPreprocessor", "inputs": {
                    "image": ["460", 0], "detect_hand": "enable",
                    "detect_body": "enable", "detect_face": "disable",
                    "resolution": max(int(job["larghezza"]), int(job["altezza"])),
                    "bbox_detector": "yolox_l.onnx",
                    "pose_estimator": "dw-ll_ucoco_384.onnx",
                    # Lo «stick» largo è quello che chiede il ControlNet di xinsir
                    # (SDXL): per il ControlNet 1.1 di SD 1.5 sarebbe uno scheletro
                    # troppo grosso, cioè una posa peggiore. La ricetta lo dichiara.
                    "scale_stick_for_xinsr_cn": scelte.get("dw_scale_stick", "disable")}},
                "464": {"class_type": "ImageScale", "inputs": {
                    "image": ["461", 0], "upscale_method": "nearest-exact",
                    "width": int(job["larghezza"]), "height": int(job["altezza"]),
                    "crop": "center"}},
            })
        else:
            grafo["464"] = {"class_type": "HyperSpacePosePreset", "inputs": {
                "preset": pose_preset, "width": int(job["larghezza"]),
                "height": int(job["altezza"])}}
        grafo["458"]["inputs"]["positive"] = ["463", 0]
        grafo["458"]["inputs"]["negative"] = ["463", 1]
    reference_image = str(job.get("reference_image") or "").strip()
    if reference_image and not scelte.get("ipadapter"):
        # Il riferimento è per famiglia come il ControlNet: l'adattatore di SDXL
        # non è quello di SD 1.5 (CLIP-ViT-H contro ViT-bigG, altre dimensioni).
        # Senza questo controllo l'immagine uscirebbe lo stesso — con un altro
        # volto, cioè il difetto che il riferimento esiste per togliere, e in
        # silenzio. Lo stesso vale per il CLIP-ViT: se manca quello, l'adattatore
        # non ha da cosa leggere il riferimento.
        raise ValueError(
            f"riferimento richiesto per la famiglia {famiglia!r}, che non ha un "
            "IP-Adapter dichiarato in shared/image_jobs.py")
    if reference_image:
        if not scelte.get("clip_vision"):
            raise ValueError(
                f"riferimento richiesto per la famiglia {famiglia!r}, che dichiara "
                "un IP-Adapter ma non il CLIP-ViT da cui leggere il riferimento")
        # Il riferimento si innesta sul MODELLO (non sul condizionamento): entra fra
        # il checkpoint/LoRA e il KSampler, così vale per ogni passo di sampling
        # senza toccare il prompt — che con SD 1.5 ha 77 token e non può crescere.
        # `weight_type: linear` con `embeds_scaling: V only` è la lettura consigliata
        # per PLUS FACE; il peso lo decide il job (default 0.8), non il grafo.
        grafo.update({
            "465": {"class_type": "LoadImage", "inputs": {"image": reference_image}},
            "466": {"class_type": "IPAdapterModelLoader",
                    "inputs": {"ipadapter_file": scelte["ipadapter"]}},
            "467": {"class_type": "CLIPVisionLoader",
                    "inputs": {"clip_name": scelte["clip_vision"]}},
            "468": {"class_type": "IPAdapterAdvanced", "inputs": {
                "model": modello_ref, "ipadapter": ["466", 0], "image": ["465", 0],
                "clip_vision": ["467", 0],
                "weight": float(job.get("reference_strength", RIFERIMENTO_FORZA_DEFAULT)),
                "weight_type": "linear", "combine_embeds": "concat",
                "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only"}},
        })
        grafo["458"]["inputs"]["model"] = ["468", 0]
    fattore = int(job.get("fix") or 0)
    if fattore > 1:
        # L'R-ESRGAN genera il dettaglio del fix; ricampionare a 1024×1536 con
        # SD 1.5 richiedeva 20–40 minuti e tendeva a ridisegnare un bordo bianco
        # attorno alla figura. Il fix finisce quindi qui, senza un secondo KSampler.
        grafo["474"] = {"class_type": "UpscaleModelLoader",
                        "inputs": {"model_name": FIX_UPSCALER}}
        grafo["475"] = {"class_type": "ImageUpscaleWithModel",
                        "inputs": {"upscale_model": ["474", 0], "image": ["457", 0]}}
        grafo["476"] = {"class_type": "ImageScale", "inputs": {
            "image": ["475", 0], "upscale_method": "lanczos",
            "width": int(job["larghezza"]) * fattore,
            "height": int(job["altezza"]) * fattore, "crop": "disabled"}}
        grafo["470"]["inputs"]["images"] = ["476", 0]
    return grafo


def workflow_sdxl(job: dict, *, modello: dict | None = None,
                  prefisso: str = "HyperSpace", jpeg: bool = True) -> dict:
    """Il grafo CyberRealistic Pony (SDXL/Pony): CFG 5, Clip Skip 2, 1024.

    Resta come nome proprio — è la ricetta del Mac per gli sketch — ma il grafo
    lo costruisce `workflow_checkpoint`: due funzioni che disegnano lo stesso
    grafo sarebbero due grafi da tenere allineati a mano.
    """
    return workflow_checkpoint(job, modello=modello, prefisso=prefisso, jpeg=jpeg,
                               famiglia=FAMIGLIA_SDXL)


def workflow_sd15(job: dict, *, modello: dict | None = None,
                  prefisso: str = "HyperSpace", jpeg: bool = True) -> dict:
    """Il grafo ChickMixFlat (SD 1.5): CFG 7, Clip Skip 2, 768, posa e riferimento."""
    return workflow_checkpoint(job, modello=modello, prefisso=prefisso, jpeg=jpeg,
                               famiglia=FAMIGLIA_SD15)


def immagini_da_history(run: dict) -> list:
    """I file prodotti da una run di ComfyUI, letti dal suo blocco `history`.

    La forma è annidata (`outputs -> <nodo> -> images -> [...]`) e cambia fra
    versioni: si legge quello che c'è invece di assumere una struttura.
    """
    file = []
    for uscite in (run.get("outputs") or {}).values():
        for immagine in (uscite or {}).get("images") or []:
            nome = str(immagine.get("filename") or "").strip()
            if not nome:
                continue
            cartella = str(immagine.get("subfolder") or "").strip("/")
            file.append(f"{cartella}/{nome}".lstrip("/"))
    return file

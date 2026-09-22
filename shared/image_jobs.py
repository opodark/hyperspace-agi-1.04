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

import threading
import time
import uuid

STATI = ("pending", "running", "done", "failed")

# Tetti espliciti, come per i canali: la coda è memoria del control-plane, non un
# disco. I job sono pochi e piccoli (un prompt e quattro numeri).
DEFAULT_MAX_JOBS = 8
DEFAULT_JOB_TTL_S = 900.0
DEFAULT_CLAIM_TTL_S = 600.0

# Limiti del grafo: la scheda di win11 ha 8 GB e un text encoder da 8B. Un tetto
# dichiarato è meglio di un OOM che si porta dietro anche il modello caricato.
LIMITE_LATO = 1536
LIMITE_PASSI = 60


def nuovo_job(prompt: str, *, negativo: str = "", larghezza: int = 768,
              altezza: int = 768, passi: int = 25, seed: int = 0,
              richiedente: str = "", canale: str = "", modello: str = "",
              adesso: float | None = None) -> dict:
    """Costruisce un job valido. Solleva ValueError se il prompt è vuoto.

    I numeri si limitano invece di essere rifiutati: chi chiede 4096 px ha chiesto
    qualcosa di impossibile, non di sbagliato, e il tetto si vede nel job.
    """
    testo = " ".join(str(prompt or "").split())
    if not testo:
        raise ValueError("prompt vuoto: un job immagine senza prompt non esiste")
    return {
        "id": uuid.uuid4().hex[:12],
        "prompt": testo[:2000],
        "negativo": " ".join(str(negativo or "").split())[:1000],
        "larghezza": max(64, min(int(larghezza), LIMITE_LATO)),
        "altezza": max(64, min(int(altezza), LIMITE_LATO)),
        "passi": max(1, min(int(passi), LIMITE_PASSI)),
        "seed": int(seed),
        "richiedente": str(richiedente or "")[:64],
        "canale": str(canale or "")[:32],
        "modello": str(modello or "")[:120],
        "stato": "pending",
        "creato_ts": float(adesso if adesso is not None else time.time()),
        "preso_ts": 0.0,
        "esito": {},
    }


class ImmagineQueue:
    """Coda dei job immagine: stato per job, tetti, scadenze.

    Un job preso e mai concluso torna disponibile dopo `claim_ttl_s`: un ponte
    morto a metà non deve bloccare il lavoro per sempre, ma nemmeno farlo
    eseguire due volte nello stesso minuto.
    """

    def __init__(self, *, clock=time.time, max_jobs: int = DEFAULT_MAX_JOBS,
                 ttl_s: float = DEFAULT_JOB_TTL_S,
                 claim_ttl_s: float = DEFAULT_CLAIM_TTL_S):
        self.clock = clock
        self.max_jobs = max(1, int(max_jobs))
        self.ttl_s = max(30.0, float(ttl_s))
        self.claim_ttl_s = max(30.0, float(claim_ttl_s))
        self._lock = threading.Lock()
        self._job: dict = {}
        self._storico: list = []

    def accoda(self, job: dict) -> dict:
        """Mette in coda un job e pota i vecchi (scaduti o conclusi)."""
        with self._lock:
            self._pota_locked()
            attivi = [j for j in self._job.values() if j["stato"] in ("pending", "running")]
            if len(attivi) >= self.max_jobs:
                raise RuntimeError(f"coda piena ({self.max_jobs} job in attesa o in corso)")
            self._job[job["id"]] = dict(job)
            return dict(self._job[job["id"]])

    def prossimo(self) -> dict | None:
        """Il job più vecchio da eseguire, marcato `running` (claim)."""
        with self._lock:
            self._pota_locked()
            candidati = [j for j in self._job.values() if j["stato"] == "pending"]
            candidati.sort(key=lambda j: j["creato_ts"])
            if not candidati:
                return None
            job = candidati[0]
            job["stato"] = "running"
            job["preso_ts"] = self.clock()
            return dict(job)

    def concludi(self, job_id: str, ok: bool, *, file: str = "", errore: str = "",
                 durata_ms: int = 0) -> dict | None:
        """Chiude un job. Un id sconosciuto non è un errore: è un doppione."""
        with self._lock:
            job = self._job.get(str(job_id or ""))
            if job is None:
                return None
            job["stato"] = "done" if ok else "failed"
            job["esito"] = {"file": str(file or ""),
                            "errore": str(errore or "")[:400],
                            "durata_ms": int(durata_ms or 0), "ts": self.clock()}
            self._storico.append(dict(job))
            self._storico = self._storico[-20:]
            return dict(job)

    def job(self, job_id: str) -> dict | None:
        with self._lock:
            trovato = self._job.get(str(job_id or ""))
            return dict(trovato) if trovato else None

    def stato(self) -> dict:
        with self._lock:
            self._pota_locked()
            per_stato = {stato: 0 for stato in STATI}
            for job in self._job.values():
                per_stato[job["stato"]] += 1
            return {
                "per_stato": per_stato,
                "in_coda": per_stato["pending"],
                "in_esecuzione": per_stato["running"],
                "max_jobs": self.max_jobs,
                "ttl_s": self.ttl_s,
                "ultimi": [{"id": j["id"], "stato": j["stato"],
                            "prompt": j["prompt"][:60],
                            "esito": j.get("esito", {})} for j in self._storico[-5:]],
            }

    def _pota_locked(self) -> None:
        """Toglie i job scaduti e libera i claim morti. Chiamata con il lock."""
        adesso = self.clock()
        for chiave, job in list(self._job.items()):
            eta = adesso - job["creato_ts"]
            if job["stato"] in ("pending", "running") and eta > self.ttl_s:
                del self._job[chiave]
                continue
            if (job["stato"] == "running"
                    and adesso - job["preso_ts"] > self.claim_ttl_s):
                job["stato"] = "pending"
                job["preso_ts"] = 0.0
            if job["stato"] in ("done", "failed") and eta > self.ttl_s:
                del self._job[chiave]


# ── Il grafo da mandare a ComfyUI ────────────────────────────────────────────
# Non è inventato: è la prova che ha funzionato su questa macchina (run
# 19e69776), con i suoi valori. Due scelte non ovvie che si leggono lì e che non
# vanno "semplificate":
#
#   - il text encoder da 8B gira sulla CPU (`device: cpu`): con 8 GB di VRAM è
#     esattamente ciò che lascia spazio al diffusion;
#   - il negativo sta DENTRO il prompt ("no text, no watermark, no logos"):
#     Qwen-Image 2.1 segue le istruzioni, e `resolution` segue il lato del latente.
MODELLO_DEFAULT = {
    "unet": "qwen-image-2.1-Q5_K_M.gguf",
    "clip": "qwen3vl_8b_int8_convrot.safetensors",
    "clip_type": "qwen_image",
    "clip_device": "cpu",
    "vae": "qwen_image_2.1_vae_bf16.safetensors",
    "cfg": 1.0,
    "sampler": "euler",
    "scheduler": "simple",
}


def workflow(job: dict, *, modello: dict | None = None, prefisso: str = "HyperSpace",
             risoluzione: int | None = None) -> dict:
    """Il grafo in formato API per un job. Gli id sono fissi, i valori no.

    `modello` sovrascrive i file (un'altra macchina avrà altri nomi) e il job può
    indicare un `modello` suo: il resto è la ricetta che ha funzionato.
    """
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
        "470": {"class_type": "SaveImage",
                "inputs": {"images": ["457", 0], "filename_prefix": str(prefisso)}},
    }


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



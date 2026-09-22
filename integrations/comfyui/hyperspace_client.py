#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Client HyperSpace per i nodi ComfyUI: logica pura + un trasporto iniettabile.

Perché un client separato dai nodi: qui vive tutto ciò che si può sbagliare
(comporre la richiesta, ripulire la risposta, il timeout, l'errore di rete) e si
testa SENZA ComfyUI, senza torch e senza rete. I nodi restano un adattatore
sottile, la stessa divisione dei driver di canale del repo.

Perché solo la libreria standard: il pacchetto viene copiato dentro
l'installazione di ComfyUI, che ha un virtualenv gestito dall'app. Una
dipendenza in più qui significa rompere il grafo di qualcun altro al primo
aggiornamento dell'app.

Dove punta: il control-plane sulla STESSA macchina del nodo. Non è un servizio
da esporre — `127.0.0.1:8085` è la porta canonica del gateway HyperSpace.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

CONTROL_PLANE_DEFAULT = "http://127.0.0.1:8085"

# Il flag che rende una richiesta UNA sola chiamata: senza, il control-plane
# inietta i suoi tool (web_search, omega_*, get_mesh_status) e "scrivimi un
# prompt" diventa un giro di ricerca con due chiamate al modello. Vedi
# control-plane/main.py — `_tools_requested_off`.
HEADERS_BASE = {
    "Content-Type": "application/json",
    "X-Hyperspace-Tools": "off",
}

# La superficie dice al control-plane DOVE sta parlando l'agente (istruzione
# separata dall'identità): "comfyui" chiede solo il prompt, senza preamboli.
SUPERFICIE = "comfyui"

ISTRUZIONE = (
    "Scrivi il prompt per un modello text-to-image. Rispondi SOLO con il prompt: "
    "descrizioni separate da virgole, dettagli visivi concreti (soggetto, "
    "inquadratura, luce, stile, materiali), niente frasi, niente spiegazioni, "
    "niente virgolette, nessuna riga che inizi con 'Prompt'. "
    "Lingua del prompt: {lingua}. Massimo {max_caratteri} caratteri."
)

# Righe che un modello aggiunge anche quando gli si chiede di non farlo: si
# tolgono qui, perché un prompt sporco si vede nell'immagine, non nel grafo.
_PREFISSI = re.compile(
    r"^\s*(?:prompt|image prompt|positive prompt|risposta|output|ecco(?: il prompt)?)\s*[:\-]\s*",
    re.IGNORECASE)
# L'etichetta può stare anche a METÀ riga: verificato dal vivo, qwen3.5 ha
# risposto con il prompt e poi "**Prompt:** <lo stesso prompt>".
_ETICHETTA = re.compile(r"prompt\s*[:\-]", re.IGNORECASE)
# Grassetto e corsivo finiscono dentro CLIP come asterischi letterali.
_MARKDOWN = re.compile(r"[*_`]{1,3}")


def _una_volta_sola(testo: str) -> str:
    """Se il prompt compare due volte, tiene la parte più lunga.

    Una ripetizione dentro la condizionatura di CLIP pesa due volte, e la parte
    più lunga è quella completa (la seconda copia è spesso troncata).
    """
    pezzi = [p.strip(" ,.;") for p in _ETICHETTA.split(testo)]
    pezzi = [p for p in pezzi if p]
    if len(pezzi) <= 1:
        return testo
    return max(pezzi, key=len)


def _http(url: str, *, payload=None, headers=None, timeout: float = 30.0):
    """Trasporto reale. Ritorna (status, dizionario). Non solleva mai per rete.

    È iniettato dai test (stessa firma): è l'unico punto che tocca la rete,
    quindi tutto il resto si verifica senza un server acceso.
    """
    dati = None
    if payload is not None:
        dati = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    richiesta = urllib.request.Request(url, data=dati, headers=headers or {},
                                       method="POST" if dati is not None else "GET")
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as risposta:
            corpo = risposta.read().decode("utf-8", errors="replace")
            return risposta.status, (json.loads(corpo) if corpo.strip() else {})
    except urllib.error.HTTPError as errore:
        corpo = errore.read().decode("utf-8", errors="replace")[:400]
        return int(errore.code), {"errore": corpo}
    except urllib.error.URLError as errore:
        return 0, {"errore": f"control-plane non raggiungibile: {errore.reason}"}
    except TimeoutError:
        return 0, {"errore": f"timeout dopo {timeout}s"}


def pulisci_prompt(testo, max_caratteri: int = 600) -> str:
    """Da testo di modello a prompt utilizzabile: una riga, senza contorno.

    Non è estetica: una code fence o un "Ecco il prompt:" finiscono DENTRO la
    condizionatura di CLIP e si vedono nell'immagine.
    """
    righe = []
    for riga in str(testo or "").replace("\r", "").split("\n"):
        riga = riga.strip()
        if not riga or riga.startswith("```"):
            continue
        righe.append(_PREFISSI.sub("", riga))
    unito = " ".join(" ".join(righe).split())
    # Prima il markdown (gli asterischi del grassetto finirebbero in CLIP), poi
    # l'eventuale doppione, poi le virgolette ai bordi.
    unito = _una_volta_sola(_MARKDOWN.sub("", unito))
    unito = unito.strip().strip('"').strip("'").strip().strip("-–—").strip()
    tetto = max(20, int(max_caratteri))
    if len(unito) > tetto:
        tagliato = unito[:tetto]
        # Non si tronca a metà parola: l'ultima virgola intera è un confine
        # leggibile per il modello d'immagine.
        virgola = tagliato.rfind(",")
        unito = (tagliato[:virgola] if virgola > tetto * 0.5 else tagliato).strip().rstrip(",")
    return unito


def suggerimento(errore) -> str:
    """Il "cosa fare" per gli errori che si vedono davvero in questa catena.

    Perché esiste: "control-plane HTTP 502" non dice niente a chi guarda il nodo,
    mentre le tre cause reali sono distinte e si risolvono in tre modi diversi —
    e le ho incontrate tutte e tre provando il nodo dal vivo.
    """
    testo = str(errore or "").lower()
    if "not found" in testo and "model" in testo:
        return ("il modello richiesto non esiste in Ollama: metti in `modello` un nome "
                "di `ollama list`, oppure allinea il modello di default del control-plane")
    if "required" in testo and "model" in testo:
        return "serve un nome di modello: lascia vuoto l'ingresso e il CP usa il suo default"
    if "non raggiungibile" in testo or "timeout" in testo:
        return "lo stack è su? Verifica con: .\\scripts\\start.ps1 -Check"
    if "vuota" in testo:
        return "il modello non ha prodotto testo: riprova, o alza il timeout"
    return ""


def chiedi_prompt(idea: str, *, stile: str = "", lingua: str = "inglese",
                  max_caratteri: int = 600, modello: str = "", temperatura: float = 0.8,
                  base_url: str = CONTROL_PLANE_DEFAULT, timeout: float = 180.0,
                  transport=None) -> dict:
    """Chiede al control-plane un prompt per il modello d'immagine.

    Ritorna sempre un dizionario: `{"ok", "prompt", "report", "modello",
    "errore"}`. Non solleva: se un errore sia fatale lo decide il grafo (il nodo
    lo trasforma in un errore visibile, uno script può ripiegare).
    """
    trasporto = transport or _http
    pezzi = ["Idea: " + " ".join(str(idea or "").split())]
    if str(stile or "").strip():
        pezzi.append("Stile richiesto: " + " ".join(str(stile).split()))
    payload = {
        "messages": [
            {"role": "system",
             "content": ISTRUZIONE.format(lingua=lingua, max_caratteri=int(max_caratteri))},
            {"role": "user", "content": "\n".join(pezzi)},
        ],
        "temperature": float(temperatura),
        "max_tokens": 320,
        "stream": False,
        "surface": SUPERFICIE,
    }
    # `model` si OMETTE quando è vuoto: nel control-plane il default si applica
    # se la chiave manca, mentre una stringa vuota arriva fino al backend e
    # diventa un 502 "model is required" — verificato dal vivo, non dedotto.
    if str(modello or "").strip():
        payload["model"] = str(modello).strip()
    status, dati = trasporto(f"{str(base_url).rstrip('/')}/v1/chat/completions",
                             payload=payload, headers=dict(HEADERS_BASE), timeout=timeout)
    if status != 200:
        motivo = ""
        if isinstance(dati, dict):
            motivo = str(dati.get("errore") or dati.get("error") or "")[:200]
        return {"ok": False, "prompt": "", "report": "", "modello": "",
                "errore": f"control-plane HTTP {status}: {motivo}".strip()}
    messaggio = ((dati.get("choices") or [{}])[0] or {}).get("message") or {}
    prompt = pulisci_prompt(messaggio.get("content") or "", max_caratteri)
    if not prompt:
        return {"ok": False, "prompt": "", "report": "", "modello": "",
                "errore": "risposta vuota dal control-plane"}
    modello_usato = str(dati.get("model") or modello or "?")
    return {"ok": True, "prompt": prompt, "modello": modello_usato,
            "report": f"HyperSpace: prompt di {len(prompt)} caratteri da {modello_usato}",
            "errore": ""}


def stato_rete(*, base_url: str = CONTROL_PLANE_DEFAULT, timeout: float = 10.0,
               transport=None) -> dict:
    """Stato della rete HyperSpace (`/health`): se è viva, quanti nodi, ricordi."""
    trasporto = transport or _http
    status, dati = trasporto(f"{str(base_url).rstrip('/')}/health",
                             payload=None, headers={}, timeout=timeout)
    if status != 200 or not isinstance(dati, dict):
        return {"ok": False, "vivo": False, "nodi": 0, "report": "",
                "errore": f"control-plane HTTP {status}: {str(dati)[:160]}"}
    nodi = int(dati.get("nodes_active") or 0)
    report = (f"HyperSpace {dati.get('version', '?')} — {dati.get('status', '?')}, "
              f"{nodi} nod{'o' if nodi == 1 else 'i'} attiv{'o' if nodi == 1 else 'i'}, "
              f"{dati.get('memories', '?')} ricordi")
    return {"ok": True, "vivo": str(dati.get("status", "")) == "ok", "nodi": nodi,
            "report": report, "errore": ""}

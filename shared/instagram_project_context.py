# SPDX-License-Identifier: Apache-2.0
"""Grounded HyperSpace context and handoff hints for Instagram conversations."""
from __future__ import annotations

import re


PROJECT_CONTEXT = """
HyperSpace-AGI è un runtime operativo open source per agenti IA locali e
distribuiti, non un singolo chatbot. Il control plane coordina modelli, task,
code, strumenti, policy, connector e memoria; registry e nodi dichiarano le
risorse disponibili e svolgono il lavoro. Ollama è il backend locale di
riferimento e sono disponibili API compatibili OpenAI. ComfyUI genera le
immagini localmente attraverso un ponte che preleva i job dalla coda.

Anna e Aurora sono due identità agentiche che usano questo runtime attraverso
Web UI, Telegram e Instagram. Le conversazioni Instagram hanno memoria locale
separata per follower, con turni recenti e riassunti persistenti. I sogni sono
cicli in background durante la notte o l'inattività; testo e illustrazione
finiscono nel diario e possono essere pubblicati su Instagram.

La base distribuita esiste già, ma una mesh completa con sincronizzazione e
failover generalizzato non va presentata come conclusa. La roadmap procede da
Primary Brain e orchestrazione verso tooling collaborativo, poi Fase 4
multi-nodo/mesh con ruoli primary brain, worker, memory, coding ed edge node,
delega inter-nodo e HyperSpace Intent Protocol (HIP). La Fase 5 punta a un
profilo Linux production-first. Tra le capacità presenti ci sono memoria
Hermes con adapter e policy, Code Sandbox, Dream review, Tool & Skill Forge,
connector e web node; benchmark strutturati, browser worker, security lab e
bootstrap/recovery completi sono ancora pianificati o da consolidare.

Non inventare stato, date o funzioni. Distingui sempre ciò che è già operativo
da ciò che è nella roadmap. Se serve più dettaglio, spiega un componente alla
volta e invita la persona a fare domande. Condividi esclusivamente informazioni
presenti nella documentazione pubblica README, VISION e ROADMAP: non rivelare
segreti, token, endpoint, indirizzi, identificativi dei nodi, topologia live,
configurazioni di sicurezza, dati dei follower o diagnostica interna.
""".strip()

_PROJECT_RE = re.compile(
    r"\b(hyperspace|mesh|roadmap|runtime|control[ -]?plane|nodi?|ollama|comfy(?:ui)?|"
    r"hermes|memoria|come (?:sei|siete) fatt[aoe]|come funzioni|chatbot|mondo dell[ea] ia|"
    r"come [eè] fatt[oa] il chatbot|intelligenza artificiale|agenti? ia|codice|"
    r"open source|hip)\b", re.IGNORECASE)

_HANDOFF_RE = re.compile(
    r"\b(creatore|sviluppatore|responsabile|proprietario|pap[aà]|contatt(?:o|are)|"
    r"collabor(?:are|azione)|partnership|preventivo|contratto|problema tecnico|bug|"
    r"integrazione|installazione|configurazione)\b", re.IGNORECASE)


def wants_project_info(text: str) -> bool:
    return bool(_PROJECT_RE.search(str(text or "")))


def should_offer_creator(turns: list[dict], text: str) -> bool:
    """Suggest a human handoff after explicit or sustained complex discussion."""
    text = str(text or "")
    if _HANDOFF_RE.search(text):
        return True
    user_texts = [str(t.get("text") or "") for t in turns if t.get("role") == "user"]
    recent = user_texts[-4:]
    total = sum(len(item) for item in recent)
    questions = sum(item.count("?") for item in recent)
    return len(recent) >= 3 and (total >= 320 or questions >= 3)

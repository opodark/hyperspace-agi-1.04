"""Stima se un modello entra nella VRAM di un nodo, PRIMA di provarci.

Perche' esiste: un profilo dichiarava modelli da 14B mai installati e non
installabili in 8 GB di VRAM. Il sintomo osservato era "il modello e' lento", la
causa era che i pesi non entravano e Ollama li splittava su CPU. Una stima fatta
al boot avrebbe detto la verita' subito. Questa e' quella stima.

L'idea dell'*Estimate* viene dal progetto hyperspaceai/agi (stima i parametri dal
nome del modello, tiene conto della quantizzazione e del 20% di KV cache). Qui
ci sono due correzioni che vengono da misure fatte su QUESTA mesh:

1. **I bit per peso non si assumono.** Loro fissano Q4 = 4.5 bit/parametro. I
   quant a 2 bit esistono, girano, e su questa mesh sono la differenza fra
   "impossibile" e "possibile": un 27B in IQ2_S e un 35B in Q2_K_P stanno negli
   8 GB della RTX 3060. Assumere Q4 avrebbe dichiarato impossibili modelli che
   funzionano.
2. **I MoE si trattano a parte.** Per decidere se i pesi entrano contano i
   parametri TOTALI (devono risiedere tutti). Per la velocita' contano gli
   ATTIVI, e questo spiega un dato misurato che sembra controintuitivo: un 35B
   MoE con ~3B attivi fa 11.04 t/s, un 9B denso ne fa 3.00. Chi ordinasse i
   modelli per dimensione li ordinerebbe al contrario.

Limite dichiarato: e' una STIMA, non una misura. Non conosce l'architettura
(numero di layer, dimensione di testa) e tratta la KV cache come una quota
percentuale dei pesi. Serve a rispondere "questo non ci sta", non a promettere
"questo ci sta al byte".

Quanto sbaglia, misurato: la stima sta SOTTO la VRAM che Ollama riporta davvero
— un 4B dichiarato in uso occupava 5.51 GB, la stima ne prevede 2.5. Le due
ragioni sono note e non correggibili senza conoscere il modello: la quant reale
non e' nel nome quando il tag e' assente dai tag di Ollama (il default di 4.5
bit e' sotto il Q4_K_M che Ollama usa), e l'allocazione include overhead del
runtime. Percio' un "entra" con margine sottile NON e' una garanzia, e chi
decide qualcosa di irreversibile deve passare `reserve_gb`.
"""
from __future__ import annotations

import re

# Bit per peso per tag di quantizzazione (pesi + overhead del formato).
# I valori dei tag K-quant/I-quant sono quelli di riferimento di llama.cpp.
QUANT_BITS = {
    "F32": 32.0, "F16": 16.0, "BF16": 16.0,
    "Q8_0": 8.5,
    "Q6_K": 6.6,
    "Q5_K_M": 5.6, "Q5_K_S": 5.5,
    "Q4_K_M": 4.8, "Q4_K_S": 4.6, "Q4_0": 4.5,
    "Q3_K_M": 3.9, "Q3_K_S": 3.5,
    "Q2_K": 2.6, "IQ4_XS": 4.3, "IQ3_XXS": 3.1, "IQ2_S": 2.2, "IQ1_S": 1.6,
}
DEFAULT_BITS = 4.5          # come il Q4 delle loro raccomandazioni, ma esplicito
DEFAULT_KV_OVERHEAD = 0.20  # quota dei pesi, alla finestra di contesto di default
GIB = 1024 ** 3

# Un nome puo' contenere piu' numeri: "qwen3.5:4b" ha 3.5 (versione) e 4 (taglia).
# Si prende l'ULTIMA taglia prima del tag di quantizzazione.
_PARAMS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[bB](?![\w])")
_EXPERTS = re.compile(r"(?<![\w])[aA](\d+(?:\.\d+)?)[bB](?![\w])")
_QUANT_TAG = re.compile(r"[:@]([A-Za-z0-9_]{2,10})$")


def _split(name: str):
    """(parte-nome, tag-quant). Il tag e' l'ultimo `:XXX` se sembra una quant."""
    nome = str(name or "").strip()
    m = _QUANT_TAG.search(nome)
    if m and _looks_like_quant(m.group(1)):
        return nome[:m.start()], m.group(1).upper()
    return nome, ""


def _looks_like_quant(tag: str) -> bool:
    t = tag.upper()
    if t in QUANT_BITS:
        return True
    # Forme tipo Q2_K_P, Q4_1, IQ3_M: famiglia Q/I + cifra, e non conteggi di
    # parametri (un "4B" non e' una quant).
    return bool(re.fullmatch(r"[QI][0-9][A-Z0-9_]*", t))


def quant_bits(name: str):
    """(bit per peso, tag). Senza tag riconoscibile: (DEFAULT_BITS, "").

    Un tag della famiglia giusta ma non in tabella (es. Q2_K_P) viene stimato
    dalla sua cifra invece di ricadere sul default: e' comunque un'informazione
    migliore del "non lo so", ed e' il caso che rende possibile un 35B in 8 GB.
    """
    _, tag = _split(name)
    if tag in QUANT_BITS:
        return QUANT_BITS[tag], tag
    if _looks_like_quant(tag):
        cifra = int(re.search(r"[0-9]", tag).group())
        return float(max(cifra, 2)) + 0.5, tag
    return DEFAULT_BITS, ""


def sizes(name: str):
    """(parametri totali in miliardi, attivi o None).

    Gli esperti attivi vanno tolti PRIMA di cercare la taglia: in
    `Qwen3.6-35B-A3B` l'ultimo numero-B e' 3, ma il modello e' da 35 e per il
    fit conta 35 (tutti i pesi devono risiedere). Prendere l'ultimo match e
    basta darebbe 3B, cioe' un modello che "entra" per sbaglio.
    """
    nome, _ = _split(name)
    attivi = None
    m = _EXPERTS.search(nome)
    if m:
        attivi = float(m.group(1))
    senza_esperti = _EXPERTS.sub(" ", nome) if m else nome

    taglie = _PARAMS.findall(senza_esperti)
    if not taglie:
        return None, attivi
    totale = float(taglie[-1])

    # `gemma4:e4b`: la taglia sta nell'"e", e il "4" di gemma4 e' un numero di
    # versione. Se c'e' un eXB esplicito, quello e' la taglia.
    e = re.search(r"(?<![\w])e(\d+(?:\.\d+)?)[bB](?![\w])", nome)
    if e and not m:
        totale = float(e.group(1))
    return totale, attivi


def assess(name: str, vram_gb, *, context_tokens: int = 8192,
           kv_overhead: float = DEFAULT_KV_OVERHEAD, reserve_gb: float = 0.0) -> dict:
    """Il verdetto completo. Non solleva mai: un nome illeggibile da' `known=False`.

    La KV cache si scala linearmente col contesto richiesto: alla finestra di
    default vale `kv_overhead`, al doppio vale il doppio. E' la ragione per cui
    due richieste con contesti diversi hanno verdetti diversi sullo stesso nodo.
    """
    bits, tag = quant_bits(name)
    totale, attivi = sizes(name)
    verdetto = {
        "model": str(name), "quant": tag or None, "bits_per_weight": bits,
        "params_b": totale, "active_params_b": attivi,
        "vram_gb": float(vram_gb or 0), "context_tokens": int(context_tokens),
        "known": totale is not None,
    }
    if totale is None:
        verdetto.update(fits=None, verdict="taglia non deducibile dal nome",
                        note="Nessun '<numero>B' nel nome: la stima si ferma qui "
                             "invece di inventare un numero.")
        return verdetto

    pesi = totale * 1e9 * bits / 8 / GIB
    kv = pesi * kv_overhead * (int(context_tokens) / 8192)
    tot = pesi + kv
    disponibile = max(float(vram_gb or 0) - float(reserve_gb or 0), 0.0)
    verdetto.update(weights_gb=round(pesi, 2), kv_gb=round(kv, 2),
                    total_gb=round(tot, 2), available_gb=round(disponibile, 2))

    if not disponibile:
        verdetto.update(
            fits=False, verdict="VRAM assente o non dichiarata",
            note="Con vram_gb=0 il controllo non puo' decidere: il nodo non ha "
                 "dichiarato la sua capacita'. Vedi docs/windows-node-handoff.md, sezione 2.")
    elif tot <= disponibile:
        verdetto.update(fits=True, verdict="entra interamente in VRAM",
                        note=f"margine {disponibile - tot:.1f} GB sul totale stimato")
    elif attivi and attivi < totale * 0.5:
        verdetto.update(
            fits=False, verdict="non entra: i pesi vanno in split su CPU",
            note=f"MoE con {attivi:g}B attivi su {totale:g}B: l'offload costa in "
                 "proporzione agli ATTIVI, quindi resta usabile (misurato: 35B A3B a "
                 "11.04 t/s contro 3.00 di un 9B denso tutto in VRAM). Non e' 'entra', "
                 "ma non va scartato.")
    else:
        verdetto.update(
            fits=False, verdict="non entra: i pesi vanno in split su CPU",
            note="Modello denso: l'offload costa in proporzione a tutti i parametri, "
                 "quindi un piu' piccolo che entra intero e' quasi sempre piu' veloce.")
    return verdetto


def describe(name: str, vram_gb, **kwargs) -> str:
    """Una riga per i log e per l'operatore."""
    v = assess(name, vram_gb, **kwargs)
    if not v["known"]:
        return f"{name}: taglia non deducibile dal nome"
    quant = v["quant"] or "quant non dichiarata"
    moe = f", {v['active_params_b']:g}B attivi" if v["active_params_b"] else ""
    return (f"{name}: {v['params_b']:g}B{moe}, {quant} ({v['bits_per_weight']} bit/peso)"
            f" -> {v['total_gb']:.1f} GB stimati su {v['vram_gb']:.1f} GB dichiarati"
            f" — {v['verdict']}")

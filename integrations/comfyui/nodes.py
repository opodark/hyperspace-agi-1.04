#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Nodi ComfyUI di HyperSpace: Aurora scrive il prompt, e lo stato della rete.

Il nodo è un ADATTATORE, non un posto dove mettere logica: la richiesta, la
pulizia della risposta e gli errori vivono in `hyperspace_client.py`, che si
testa senza ComfyUI, senza torch e senza rete.

Non si importa `torch` né `comfy`: i tipi usati sono STRING, INT, FLOAT e
BOOLEAN, quindi il pacchetto non pesa sull'avvio di ComfyUI e non rompe nulla
quando l'app aggiorna le proprie API.
"""
from __future__ import annotations

try:                       # importato come pacchetto da custom_nodes/
    from .hyperspace_client import (CONTROL_PLANE_DEFAULT, chiedi_prompt, stato_rete,
                                    suggerimento)
except ImportError:        # importato come file singolo (test, REPL)
    from hyperspace_client import (CONTROL_PLANE_DEFAULT, chiedi_prompt, stato_rete,
                                   suggerimento)

CATEGORIA = "HyperSpace"
IDEA_DEFAULT = "una donna che guarda la città di notte da un tetto, vento tra i capelli"
STILE_DEFAULT = "fotografia cinematografica, luce al neon, grana sottile"


class HyperSpacePrompt:
    """Aurora scrive il prompt per il modello d'immagine.

    L'uscita `prompt` va collegata a CLIPTextEncode; `report` dice da chi e con
    quale modello è arrivato (da collegare a un display o a un SaveImage, per
    sapere *cosa* ha generato quell'immagine).

    La scrittura avviene sulla rete HyperSpace: il modello di linguaggio non
    occupa la VRAM che serve al diffusion — con 8 GB di scheda è la differenza
    tra generare e non generare.
    """

    CATEGORY = CATEGORIA
    FUNCTION = "esegui"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "report")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "idea": ("STRING", {"multiline": True, "default": IDEA_DEFAULT,
                                    "tooltip": "Cosa deve mostrare l'immagine. In italiano va bene: "
                                               "la traduzione la fa la rete."}),
                "stile": ("STRING", {"multiline": False, "default": STILE_DEFAULT,
                                     "tooltip": "Come deve essere resa: luce, epoca, mezzo, lente."}),
                "variante": ("INT", {"default": 0, "min": 0, "max": 99999,
                                     "tooltip": "Cambia il numero per avere un prompt NUOVO: "
                                                "ComfyUI tiene in cache i nodi per i loro ingressi, "
                                                "quindi con gli stessi valori non chiama la rete."}),
            },
            "optional": {
                "lingua": (["inglese", "italiano"], {"default": "inglese",
                           "tooltip": "I modelli text-to-image rispondono meglio all'inglese."}),
                "max_caratteri": ("INT", {"default": 600, "min": 60, "max": 2000}),
                "modello": ("STRING", {"default": "",
                                       "tooltip": "Vuoto = modello di default del control-plane."}),
                "temperatura": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 2.0, "step": 0.05}),
                "control_plane": ("STRING", {"default": CONTROL_PLANE_DEFAULT,
                                             "tooltip": "Base URL del gateway HyperSpace."}),
                "timeout_s": ("INT", {"default": 180, "min": 20, "max": 900}),
            },
        }

    def esegui(self, idea, stile, variante, lingua="inglese", max_caratteri=600,
               modello="", temperatura=0.8, control_plane=CONTROL_PLANE_DEFAULT,
               timeout_s=180):
        esito = chiedi_prompt(idea, stile=stile, lingua=lingua,
                              max_caratteri=int(max_caratteri), modello=modello,
                              temperatura=float(temperatura), base_url=control_plane,
                              timeout=float(timeout_s))
        if not esito["ok"]:
            # Errore VISIBILE, non ripiego silenzioso: un prompt sostituito in
            # silenzio dall'idea grezza produce un'immagine sbagliata senza dirlo.
            # Il "cosa fare" viene dalle cause reali incontrate provando il nodo,
            # non da un elenco immaginato.
            consiglio = suggerimento(esito["errore"])
            coda = f"\n→ {consiglio}" if consiglio else ""
            raise RuntimeError(f"HyperSpace ({control_plane}): {esito['errore']}{coda}")
        return (esito["prompt"], esito["report"])


class HyperSpaceMesh:
    """Stato della rete HyperSpace: viva, quanti nodi, quanta memoria.

    Non solleva quando la rete non c'è: il suo mestiere è proprio dirlo. Serve a
    vedere lo stato dalla Consolle e a condizionare un grafo (`vivo` → genera
    solo se la rete risponde).
    """

    CATEGORY = CATEGORIA
    FUNCTION = "esegui"
    RETURN_TYPES = ("BOOLEAN", "INT", "STRING")
    RETURN_NAMES = ("vivo", "nodi", "report")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "control_plane": ("STRING", {"default": CONTROL_PLANE_DEFAULT,
                                             "tooltip": "Base URL del gateway HyperSpace."}),
            },
            "optional": {
                "timeout_s": ("INT", {"default": 10, "min": 2, "max": 120}),
            },
        }

    def esegui(self, control_plane=CONTROL_PLANE_DEFAULT, timeout_s=10):
        esito = stato_rete(base_url=control_plane, timeout=float(timeout_s))
        if not esito["ok"]:
            return (False, 0, f"HyperSpace non raggiungibile: {esito['errore']}")
        return (bool(esito["vivo"]), int(esito["nodi"]), esito["report"])


NODE_CLASS_MAPPINGS = {
    "HyperSpacePrompt": HyperSpacePrompt,
    "HyperSpaceMesh": HyperSpaceMesh,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "HyperSpacePrompt": "Aurora scrive il prompt (HyperSpace)",
    "HyperSpaceMesh": "Stato della rete HyperSpace",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS",
           "HyperSpacePrompt", "HyperSpaceMesh"]

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pacchetto ComfyUI di HyperSpace: le mappature che l'app cerca all'avvio.

ComfyUI importa la cartella dentro `custom_nodes` come pacchetto e legge
`NODE_CLASS_MAPPINGS`. Qui non c'e' logica: solo l'ingresso.
"""
from .nodes import (NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS,  # noqa: F401
                    HyperSpaceMesh, HyperSpacePrompt)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS",
           "HyperSpacePrompt", "HyperSpaceMesh"]

#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/tool.sh — i tool che il control-plane esegue per conto del modello.
#
# Nessun modello finto serve: `/tools/execute` chiama un tool per nome, e
# `get_mesh_status` è l'unico che risponde senza rete né permessi.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

avvia_server tool
esegui_baseline tool_baseline.py "$@"

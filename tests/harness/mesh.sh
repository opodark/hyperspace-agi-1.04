#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/mesh.sh — registro, punteggio e nodi web.
#
#   tests/harness/mesh.sh              scrive la fixture
#   tests/harness/mesh.sh --confronta  confronta con quella gia' scritta
#
# Nessun modello finto serve qui: le rotte del registro rispondono senza.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

avvia_server mesh
esegui_baseline mesh_baseline.py "$@"

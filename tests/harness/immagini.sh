#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/immagini.sh — la coda immagini e il contratto col ponte.
#
# Serve un cliente configurato, come per i canali: senza, la rotta che chiama il
# ponte risponde 401 e non viene provato il percorso felice.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

avvia_server immagini
esegui_baseline immagini_baseline.py "$@"

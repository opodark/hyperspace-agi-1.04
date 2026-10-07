#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/memoria.sh — la memoria: archivio locale o Hermes.
#
# Gira con il backend `legacy` (il default), perché è quello che non ha bisogno
# di un archivio esterno. Il ramo Hermes prende strade diverse nelle stesse
# rotte, e i messaggi che tornano dicono quale ha risposto.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

# Il file della memoria va nella temporanea: senza, l'harness leggerebbe la
# memoria vera di sviluppo e la finirebbe dentro la fixture.
avvia_server memoria MEMORY_BACKEND=legacy MEMORY_FILE="@BASE@/memoria.json"
esegui_baseline memoria_baseline.py "$@"

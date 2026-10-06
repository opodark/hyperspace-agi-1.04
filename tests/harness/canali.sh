#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/canali.sh — le rotte dei canali.
#
# Serve un cliente configurato: senza, le richieste che portano un token
# rispondono 401 e la baseline verifica solo il caso di errore.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

avvia_server canali
esegui_baseline canali_baseline.py "$@"

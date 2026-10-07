#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/sogni.sh — i sogni: identità, nodi, sviluppo.
#
# Nessun modello finto serve: i sogni girano di notte, e in un ambiente di prova
# non c'è né un sogno da mostrare né un modello che lo produca. La baseline
# verifica che ogni rotta risponda qualcosa di definito anche a vuoto.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

# `PERSONA_FILE` va isolato perche' il journal dei sogni e il suo stato stanno
# nella directory del file persona, e senza questa riga la baseline legge (e il
# dominio scrive) la persona di sviluppo: il risultato dipende da cosa c'e' in
# `./data`, quindi la baseline non e' riproducibile e il test rovina dati veri.
avvia_server sogni \
  PERSONA_FILE="@BASE@/persona.json"
esegui_baseline sogni_baseline.py "$@"

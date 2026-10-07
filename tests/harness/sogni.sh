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

avvia_server sogni
esegui_baseline sogni_baseline.py "$@"

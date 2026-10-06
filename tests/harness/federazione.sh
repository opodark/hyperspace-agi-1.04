#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/federazione.sh — la federazione fra control-plane.
#
# Nessun modello finto serve: `/federate/execute` e `/federate/view` in baseline
# vengono chiamati SENZA firma, e quello che si verifica è che la difesa risponda.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

avvia_server federazione
esegui_baseline federazione_baseline.py "$@"

#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/instagram.sh — le rotte di Instagram.
#
# I due segreti (app secret e verify token) vanno passati sia al server sia allo
# script: la firma del webhook e' calcolata con lo stesso segreto da entrambe le
# parti, e se i due non coincidono quattro richieste su dieci rispondono 401/403.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

export INSTAGRAM_APP_SECRET=baseline-secret
export INSTAGRAM_WEBHOOK_VERIFY_TOKEN=baseline-verify

ferma_server
trap ferma_server EXIT

avvia_server instagram \
  INSTAGRAM_APP_SECRET="$INSTAGRAM_APP_SECRET" \
  INSTAGRAM_WEBHOOK_VERIFY_TOKEN="$INSTAGRAM_WEBHOOK_VERIFY_TOKEN"
esegui_baseline instagram_baseline.py "$@"

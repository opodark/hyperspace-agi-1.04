#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/forge.sh — il forge: gli artefatti che il CP può scrivere e usare.
#
# Serve un token di amministrazione del forge, altrimenti ogni rotta che scrive
# risponde "non configurato" e la baseline verifica solo la guardia.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

FERG="token-del-forge-di-prova-0000000000000000"

ferma_server
trap ferma_server EXIT

export FORGE_ADMIN_TOKEN="$FERG"
avvia_server forge FORGE_ADMIN_TOKEN="$FERG" FORGE_DIR="@BASE@/forge"
set +e
esegui_baseline forge_baseline.py "$@"
rc=$?
[ $rc -ne 0 ] && cp "$BASE/server.log" "${TMPDIR:-/tmp}/forge-server.log" && echo "  log copiato in ${TMPDIR:-/tmp}/forge-server.log"
exit $rc

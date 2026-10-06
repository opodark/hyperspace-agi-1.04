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

# Aspetta il primo ciclo del battito. Senza, `/hb/status` direbbe `cycle: 0` — che
# è anche la risposta di un thread che non parte mai, ed è indistinguibile da
# quella di un thread che non è ancora partito. Con l'attesa, la baseline
# verifica che il loop giri, che è metà del motivo per cui questa rotta esiste.
ciclo=0
for _ in $(seq 1 20); do
  ciclo=$(curl -s -m 3 "$BASE_URL/hb/status" 2>/dev/null \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("cycle", 0))' 2>/dev/null || echo 0)
  [ "$ciclo" -ge 1 ] 2>/dev/null && break
  sleep 1
done
if [ "${ciclo:-0}" -lt 1 ] 2>/dev/null; then
  echo "  ATTENZIONE: il battito non ha girato nessun ciclo in 20s; la baseline"
  echo "  registrarebbe cycle=0, che non distingue 'non partito' da 'non ancora partito'"
fi

esegui_baseline mesh_baseline.py "$@"

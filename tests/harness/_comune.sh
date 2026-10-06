#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/_comune.sh — le tre cose che tutte le baseline fanno.
#
# Ogni baseline è uno script separato perché ognuna ha bisogno di un ambiente
# diverso: la chat richiede un modello finto, Instagram dei segreti, i canali un
# cliente. Ma le tre operazioni — avviare il server, aspettarlo, eseguire la
# baseline — sono identiche, ed è la parte che si rompe: una copia divergente per
# dominio è il modo tipico di trovarsi una baseline che passa senza provare niente.
#
# Uso:  source tests/harness/_comune.sh   (da uno degli script che richiamano)

set -e

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RADICE="$(cd "$HARNESS_DIR/../.." && pwd)"
PY="$RADICE/.venv/bin/python3"
BASE_URL="${BASE_URL:-http://127.0.0.1:8085}"
ADMIN_TOKEN="$(printf 'a%.0s' {1..40})"
CANALE_CLIENTI="${CANALE_CLIENTI:-com4=token-di-prova-lungo-000000000000}"
BASE=""

# Il server gira su file temporanei: ogni baseline parte da zero e i file del
# control-plane di sviluppo non vengono toccati. I *_FILE di cui sopra allontanano
# i thread che scrivono (outbox Instagram, diario, coda immagini) e l'identità,
# così due esecuzioni consecutive non si riconoscono.
#
# Il token di amministrazione di rete è lungo a caso: sotto la soglia il server lo
# rifiuta e ogni route protetta risponde 401 — verde su tutto, e non si prova niente.
#
# Uso:  avvia_server <nome-base> [VAR=valore ...]
avvia_server() {
  local nome="$1"; shift
  BASE="$(mktemp -d "${TMPDIR:-/tmp}/hyperspace-$nome-XXXXXX")"
  ( cd "$RADICE" && env PYTHONPATH=.:control-plane \
      NETWORK_ADMIN_TOKEN="$ADMIN_TOKEN" \
      INSTAGRAM_REPLY_OUTBOX_FILE="$BASE/replies.json" \
      INSTAGRAM_VIP_FILE="$BASE/vips.json" \
      INSTAGRAM_MEMORY_FILE="$BASE/mem.json" \
      FEED_DIARIO_FILE="$BASE/diario.json" \
      IMAGE_QUEUE_FILE="$BASE/queue.json" \
      IDENTITY_FILE="$BASE/id.json" \
      INSTAGRAM_INBOX_POLL_ENABLED=false \
      CHANNEL_CLIENTS="$CANALE_CLIENTI" \
      "$@" \
      "$PY" control-plane/main.py > "$BASE/server.log" 2>&1 & )
  aspetta_server
}

# Un server che non parte è la metà delle baseline "verdi": lo script aspetta
# /health, e se non arriva dice cosa ha scritto il server invece di proseguire con
# connessione rifiutata.
aspetta_server() {
  local codice=000 i
  for i in $(seq 1 45); do
    codice=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "$BASE_URL/health" 2>/dev/null || echo 000)
    [ "$codice" = "200" ] && return 0
    sleep 1
  done
  echo "  il server non si e' alzato (HTTP $codice). Ultime righe:"
  tail -20 "$BASE/server.log" 2>/dev/null | sed 's/^/    /'
  return 1
}

# Uso:  esegui_baseline <nome-script-baseline> [argomenti per lo script ...]
# Le variabili d'ambiente le prende dal chiamante, che le ha gia' passate al
# server: qui si aggiungono solo quelle che servono anche al client.
esegui_baseline() {
  local script="$1"; shift
  ( cd "$RADICE" && env PYTHONPATH=. BASE_URL="$BASE_URL" \
      NETWORK_ADMIN_TOKEN="$ADMIN_TOKEN" \
      CHANNEL_CLIENTS="$CANALE_CLIENTI" \
      INSTAGRAM_APP_SECRET="${INSTAGRAM_APP_SECRET:-baseline-secret}" \
      INSTAGRAM_WEBHOOK_VERIFY_TOKEN="${INSTAGRAM_WEBHOOK_VERIFY_TOKEN:-baseline-verify}" \
      MOCK_MODEL="${MOCK_MODEL:-}" \
      "$PY" "tests/$script" "$@" )
}

ferma_server() {
  # Disown prima di uccidere: senza, bash annuncia il job terminato e la riga
  # finisce in mezzo all'output della baseline, che e' rumore per chi legge.
  disown -a 2>/dev/null || true
  pkill -f 'control-plane/main.py' 2>/dev/null || true
  [ -n "$BASE" ] && rm -rf "$BASE"
  BASE=""
  return 0
}

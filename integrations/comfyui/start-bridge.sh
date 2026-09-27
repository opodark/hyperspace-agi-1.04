#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Avvia il ponte ComfyUI sul Mac (SDXL-Turbo per gli sketch di Anna).
# Doppio clic dal Desktop (`.command`), oppure:  bash start-bridge.command
#
# Il ponte tira i job `sdxl-turbo` dalla coda del control-plane, li esegue su
# ComfyUI (mac-mps) e riporta il file al diario. Serve il token del canale
# "comfy" dentro CHANNEL_CLIENTS, generato con:
#   python scripts/channel_token.py comfy --write
#
# Uso:
#   integrations/comfyui/start-bridge.sh --check   # verifica e basta
#   integrations/comfyui/start-bridge.sh --once    # un job e esce
#   integrations/comfyui/start-bridge.sh           # in attesa, in ciclo
set -euo pipefail

REPO="${HYPERSPACE_REPO:-/Users/opo/hyperspace-agi-1.04}"
cd "$REPO"

# ── CHANNEL_TOKEN (dal canale "comfy" in CHANNEL_CLIENTS) ────────────────
riga="$(grep -E '^CHANNEL_CLIENTS=' .env 2>/dev/null | head -1 || true)"
export CHANNEL_TOKEN="$(printf '%s' "$riga" | grep -oE 'comfy=[^;"]+' | head -1 | cut -d= -f2-)"
if [[ -z "$CHANNEL_TOKEN" ]]; then
  echo "[comfy] errore: canale 'comfy' non trovato in CHANNEL_CLIENTS" >&2
  echo "          genera il token con:  python scripts/channel_token.py comfy --write" >&2
  exit 1
fi

export CHANNEL_URL="${CHANNEL_URL:-http://127.0.0.1:8085}"
export COMFY_URL="${COMFY_URL:-http://127.0.0.1:8188}"
export COMFY_OUTPUT_DIR="${COMFY_OUTPUT_DIR:-/Users/opo/ComfyUI-Shared/output}"
export BRIDGE_MODEL="${BRIDGE_MODEL:-sdxl-turbo}"

PYTHON=".venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="python3"

echo "[comfy] avvio ponte (modello=$BRIDGE_MODEL, ComfyUI=$COMFY_URL, CP=$CHANNEL_URL)..."
exec "$PYTHON" integrations/comfyui/comfy_bridge.py "$@"

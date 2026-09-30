#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Avvia il ponte ComfyUI sul Mac (SDXL-Turbo per gli sketch, ChickMixFlat per il
# volto di Anna). Doppio clic dal Desktop (`.command`), oppure:  bash start-bridge.command
#
# Il ponte tira i job delle famiglie che dichiara — `sdxl-turbo,sd15` — dalla coda
# del control-plane, li esegue su ComfyUI (mac-mps) e riporta il file al diario.
# Serve il token del canale "comfy" dentro CHANNEL_CLIENTS, generato con:
#   python scripts/channel_token.py comfy --write
#
# Le due famiglie stanno sulla stessa scheda perché una è SD 1.5 (2 GB) e l'altra
# SDXL (6,9 GB): ComfyUI tiene in memoria quella che serve, e i job arrivano uno
# alla volta (il ponte è a istanza singola). Se il modello SD 1.5 non è ancora
# installato, `--check` lo dice per nome: `install-model.sh --manifest
# integrations/comfyui/modelli-sd15.json`.
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
# 8189 e non 8188: su questo Mac ComfyUI Desktop ha preso la porta 8189 (il .env lo
# dice gia' — `COMFYUI_BASE_URL=http://host.docker.internal:8189` — e il gateway della
# WebUI pure). Con il default vecchio il ponte partiva, diceva "pronto" e falliva ogni
# job con "ComfyUI non risponde su 8188": la porta e' la prima cosa di cui accorgersi.
export COMFY_URL="${COMFY_URL:-http://127.0.0.1:8189}"
# COMFY_OUTPUT_DIR NON si imposta qui: senza, il ponte riferisce il file con il
# suo percorso RELATIVO (es. HyperSpace/bridge_00001_.jpg). Il diario lo serve
# da /app/comfy-output (volume montato dal docker-compose): un percorso assoluto
# del Mac non sarebbe leggibile dal control-plane in container.
# Le famiglie che questo ponte esegue. Il default le serve entrambe: senza, un job
# `sd15` (il ritratto di Anna) resterebbe in coda per sempre — e in coda non si
# vede che manca qualcuno: sembra solo che non ci sia lavoro.
export BRIDGE_MODEL="${BRIDGE_MODEL:-sdxl-turbo,sd15}"

PYTHON=".venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="python3"

echo "[comfy] avvio ponte (modello=$BRIDGE_MODEL, ComfyUI=$COMFY_URL, CP=$CHANNEL_URL)..."
exec "$PYTHON" integrations/comfyui/comfy_bridge.py "$@"

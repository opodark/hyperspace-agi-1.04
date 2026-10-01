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
# Comfy Desktop espone il server nativo su 8188. La 8189 e' la porta del gateway
# WebUI (`webui_gateway.py`), che puo' non essere avviato dopo un riavvio Docker;
# il ponte parla direttamente a ComfyUI e non dipende dal gateway.
export COMFY_URL="${COMFY_URL:-http://127.0.0.1:8188}"
# Lo stesso raddrizzamento che fa `comfy_url_utilizzabile` dentro il ponte, ma qui
# serve al ramo «doppio clic dal Desktop», dove l'ambiente e' quello della shell e
# non quello di launchd. Il 2026-10-01 un COMFY_URL rimasto a ...:8189 ha tenuto il
# ponte a rinviare ogni job per una notte: «ComfyUI non raggiungibile (HTTP 0)» in
# ciclo, e in coda non si distingue da «non c'e' lavoro».
case "$COMFY_URL" in
  *:8189)
    echo "[comfy] COMFY_URL=$COMFY_URL e' la porta del gateway WebUI, non di ComfyUI: uso http://127.0.0.1:8188" >&2
    export COMFY_URL="http://127.0.0.1:8188"
    ;;
esac
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

#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Serve la pagina web-node pubblica sul Mac e la espone via Tailscale Funnel.
#
# È il pezzo "Mac" della ridondanza: la stessa pagina STATICA può essere servita
# da Mac e da Windows, e il dominio (os.zerozerocomputer.com) fa round-robin tra
# i due (Cloudflare Load Balancing, o DNS con più record). Se un nodo è spento,
# risponde l'altro. Il calcolo NON avviene qui: avviene nel browser del visitatore
# e nella mesh, via federation-gateway (endpoint separato, vedi join-config.js).
#
# Uso:
#   ./scripts/start-web-node-mac.sh                    # serve + Funnel sulla 8790
#   WEB_NODE_PORT=9000 ./scripts/start-web-node-mac.sh
#   WEB_NODE_NO_FUNNEL=1 ./scripts/start-web-node-mac.sh   # solo LAN/tailnet
#
# Da lasciare acceso con Amphetamine (caffeina) sul Mac.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_NODE="$REPO/web-node"
PORT="${WEB_NODE_PORT:-8790}"

if ! command -v node >/dev/null 2>&1; then
  echo "[web-node] ERRORE: node non trovato (serve node >= 18)" >&2
  exit 1
fi
cd "$WEB_NODE"

# Esposizione pubblica. Funnel è una configurazione di tailscaled (persistente):
# una volta impostata, resta attiva anche chiudendo questo script.
if [ "${WEB_NODE_NO_FUNNEL:-0}" != "1" ] && command -v tailscale >/dev/null 2>&1 \
   && tailscale status >/dev/null 2>&1; then
  if tailscale funnel "$PORT" >/dev/null 2>&1; then
    echo "[web-node] Funnel attivo sulla porta $PORT (URL pubblico *.ts.net di questo Mac)"
  else
    echo "[web-node] Funnel non attivo: abilitalo nella ACL del tailnet per l'accesso pubblico" >&2
  fi
else
  echo "[web-node] senza Funnel: la pagina resta su LAN/tailnet"
fi

echo "[web-node] servizio statico su http://localhost:$PORT"
exec node serve.mjs

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
#   ./scripts/start-web-node-mac.sh                        # serve + Funnel (default)
#   WEB_NODE_TUNNEL=funnel      ./scripts/start-web-node-mac.sh   # Tailscale Funnel
#   WEB_NODE_TUNNEL=cloudflared ./scripts/start-web-node-mac.sh   # quick tunnel Cloudflare
#   WEB_NODE_TUNNEL=none        ./scripts/start-web-node-mac.sh   # solo LAN/tailnet
#   WEB_NODE_PORT=9000          ./scripts/start-web-node-mac.sh
#
# Da lasciare acceso con Amphetamine (caffeina) sul Mac.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_NODE="$REPO/web-node"
PORT="${WEB_NODE_PORT:-8790}"
TUNNEL="${WEB_NODE_TUNNEL:-funnel}"

if ! command -v node >/dev/null 2>&1; then
  echo "[web-node] ERRORE: node non trovato (serve node >= 18)" >&2
  exit 1
fi
cd "$WEB_NODE"

# Esposizione pubblica (per il round-robin tra le macchine vedi
# docs/web-node-round-robin.md).
case "$TUNNEL" in
  none|off|0|no)
    echo "[web-node] senza tunnel: la pagina resta su LAN/tailnet"
    ;;
  cloudflared)
    if command -v cloudflared >/dev/null 2>&1; then
      if [ -n "${CLOUDFLARED_TUNNEL_NAME:-}" ]; then
        echo "[web-node] tunnel Cloudflare nominato: $CLOUDFLARED_TUNNEL_NAME (log: /tmp/cloudflared-web-node.log)"
        nohup cloudflared tunnel run "$CLOUDFLARED_TUNNEL_NAME" >/tmp/cloudflared-web-node.log 2>&1 &
      else
        echo "[web-node] tunnel Cloudflare quick (URL temporaneo; log: /tmp/cloudflared-web-node.log)"
        nohup cloudflared tunnel --url "http://localhost:$PORT" >/tmp/cloudflared-web-node.log 2>&1 &
      fi
    else
      echo "[web-node] cloudflared non trovato: installalo (brew install cloudflared)" >&2
    fi
    ;;
  funnel|*)
    # Funnel è una configurazione di tailscaled (persistente): resta attiva
    # anche chiudendo questo script.
    if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
      if tailscale funnel "$PORT" >/dev/null 2>&1; then
        echo "[web-node] Funnel attivo sulla porta $PORT (URL pubblico *.ts.net di questo Mac)"
      else
        echo "[web-node] Funnel non attivo: abilitalo nella ACL del tailnet per l'accesso pubblico" >&2
      fi
    else
      echo "[web-node] tailscale non disponibile: la pagina resta su LAN/tailnet" >&2
    fi
    ;;
esac

echo "[web-node] servizio statico su http://localhost:$PORT"
exec node serve.mjs

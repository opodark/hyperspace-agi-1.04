#!/usr/bin/env bash
# =============================================================
# HyperSpace AGI v1.02 — join-mesh.sh
# Onboarding rapido di un nuovo nodo leaf nella mesh.
#
# Uso:
#   chmod +x join-mesh.sh && ./join-mesh.sh
#
# Il nodo si connette automaticamente ai due hub:
#   Mac:    https://charlesetta-haptical-unconcentratedly.ngrok-free.dev
#   Ubuntu: https://sanctuary-mower-plated.ngrok-free.dev
# =============================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${GREEN}[join-mesh]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET}      $*"; }
err()  { echo -e "${RED}[error]${RESET}     $*"; exit 1; }
hdr()  { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }

HUB_MAC="https://charlesetta-haptical-unconcentratedly.ngrok-free.dev"
HUB_UBUNTU="https://sanctuary-mower-plated.ngrok-free.dev"
BOOT_PEERS_DEFAULT="${HUB_MAC},${HUB_UBUNTU}"

echo -e ""
echo -e "${BOLD}${CYAN}⧢  HyperSpace AGI v1.02 — Join Mesh${RESET}"
echo -e "    Connetti questo nodo alla mesh distribuita"
echo -e ""

# ── 1. Dipendenze ───────────────────────────────────────
hdr "1/5 — Verifica dipendenze"

command -v docker &>/dev/null   || err "Docker non trovato: https://docs.docker.com/get-docker/"
docker compose version &>/dev/null || err "'docker compose' plugin non trovato"
command -v curl &>/dev/null     || err "curl non trovato (apt install curl)"
log "docker ✓  docker compose ✓  curl ✓"

# ── 2. Verifica hub raggiungibili ──────────────────────────
hdr "2/5 — Verifica connettività hub"

HUB_OK=0
for HUB in "$HUB_MAC" "$HUB_UBUNTU"; do
    if curl -sf --max-time 6 "${HUB}/health" &>/dev/null; then
        log "Hub raggiungibile: ${CYAN}${HUB}${RESET}"
        HUB_OK=$((HUB_OK + 1))
    else
        warn "Hub non risponde: ${HUB}"
    fi
done

if [ "$HUB_OK" -eq 0 ]; then
    err "Nessun hub raggiungibile. Verifica la connessione e che gli hub siano attivi."
fi
log "${HUB_OK}/2 hub raggiungibili ✓"

# ── 3. Endpoint pubblico ngrok ───────────────────────────
hdr "3/5 — Endpoint pubblico di questo nodo"

echo ""
echo "  Per essere raggiungibile dagli hub, questo nodo ha bisogno"
echo "  di un endpoint pubblico (URL ngrok o IP pubblico con porta aperta)."
echo ""
echo "  Se hai ngrok:"
echo "    ngrok http 8084   → copia l'URL https://xxxx.ngrok-free.app"
echo ""
read -rp "  PUBLIC_ENDPOINT (vuoto = solo locale, non visibile dalla mesh): " PUBLIC_EP
PUBLIC_EP=$(echo "$PUBLIC_EP" | tr -d ' ' | sed 's|/$||')

if [ -z "$PUBLIC_EP" ]; then
    warn "Nessun endpoint pubblico — il nodo potrà contattare gli hub"
    warn "ma gli hub non potranno contattare questo nodo direttamente."
else
    log "PUBLIC_ENDPOINT: ${CYAN}${PUBLIC_EP}${RESET}"
fi

# ── 4. Backend LLM ──────────────────────────────────────
hdr "4/5 — Backend LLM locale"

OS_TYPE=$(uname -s)

echo ""
echo "  1) Ollama nativo  (raccomandato)"
echo "  2) LM Studio      (Local Server attivo)"
echo "  3) Nessuno        (solo relay, senza inferenza locale)"
echo ""
read -rp "  Scelta [1/2/3, default 1]: " LLM_CHOICE
LLM_CHOICE=${LLM_CHOICE:-1}

case "$LLM_CHOICE" in
1)
    read -rp "  URL Ollama [default: http://localhost:11434]: " OLLAMA_INPUT
    OLLAMA_INPUT=${OLLAMA_INPUT:-http://localhost:11434}
    if curl -sf --max-time 4 "${OLLAMA_INPUT}/api/tags" &>/dev/null; then
        log "Ollama raggiungibile su ${OLLAMA_INPUT} ✓"
    else
        warn "Ollama non risponde su ${OLLAMA_INPUT} — avvialo prima di usare tasks LLM"
    fi
    OLLAMA_PORT=$(echo "$OLLAMA_INPUT" | grep -oE '[0-9]+$' || echo "11434")
    if [ "$OS_TYPE" = "Darwin" ]; then
        OLLAMA_DOCKER_URL="http://host.docker.internal:${OLLAMA_PORT}"
    else
        OLLAMA_DOCKER_URL="http://host.docker.internal:${OLLAMA_PORT}"
    fi
    OLLAMA_MODEL_DEFAULT="phi3"
    read -rp "  Modello Ollama [default: ${OLLAMA_MODEL_DEFAULT}]: " OLLAMA_MODEL
    OLLAMA_MODEL=${OLLAMA_MODEL:-$OLLAMA_MODEL_DEFAULT}
    ;;
2)
    read -rp "  URL LM Studio [default: http://localhost:1234]: " LMS_INPUT
    LMS_INPUT=${LMS_INPUT:-http://localhost:1234}
    LMS_PORT=$(echo "$LMS_INPUT" | grep -oE '[0-9]+$' || echo "1234")
    OLLAMA_DOCKER_URL="http://host.docker.internal:${LMS_PORT}"
    OLLAMA_MODEL="mistral"
    log "LM Studio URL: ${OLLAMA_DOCKER_URL}"
    ;;
3)
    OLLAMA_DOCKER_URL="http://host.docker.internal:11434"
    OLLAMA_MODEL="phi3"
    warn "Nessun backend LLM — il nodo funzionerà come relay puro."
    ;;
*)
    OLLAMA_DOCKER_URL="http://host.docker.internal:11434"
    OLLAMA_MODEL="phi3"
    ;;
esac

# ── 5. Genera .env e avvia ───────────────────────────────
hdr "5/5 — Configurazione e avvio"

cat > .env << EOF
# Generato da join-mesh.sh — $(date -u +"%Y-%m-%dT%H:%M:%SZ")
OLLAMA_URL=${OLLAMA_DOCKER_URL}
OLLAMA_MODEL=${OLLAMA_MODEL}
NODE_HOSTNAME=node
NODE_PORT=8084
NODE_TIER=leaf
VRAM_GB=0.0
PUBLIC_ENDPOINT=${PUBLIC_EP}
BOOT_PEERS=${BOOT_PEERS_DEFAULT}
CONTROL_PLANE_URL=http://control-plane:8085
REGISTRY_URL=http://registry:8086
REGISTRY_PORT=8086
NODE_ENDPOINTS=node:8084
HEARTBEAT_EVERY=30
HEARTBEAT_INTERVAL=30
NODE_TTL=90
SIGN_REQUESTS=true
WEBUI_SECRET_KEY=hyperspace-secret-$(date +%s | sha256sum | head -c 12)
INFERENCE_BACKEND=ollama
EOF

log ".env generato ✓"

COMPOSE_FILE="docker-compose.prod.yml"
[ -f "$COMPOSE_FILE" ] || COMPOSE_FILE="docker-compose.yml"

log "Build + avvio nodo leaf..."
docker compose -f "$COMPOSE_FILE" up -d --build

# ── Verifica finale ────────────────────────────────────────
echo ""
log "Attendo avvio nodo (15s)..."
sleep 15

NODE_STATUS=$(curl -sf --max-time 5 http://localhost:8084/status 2>/dev/null || echo "{}")
NODE_ID=$(echo "$NODE_STATUS" | grep -o '"node_id":"[^"]*"' | cut -d'"' -f4 | head -c 12)
TIER=$(echo "$NODE_STATUS" | grep -o '"tier":"[^"]*"' | cut -d'"' -f4)

if [ -n "$NODE_ID" ]; then
    log "Nodo attivo: ${CYAN}${NODE_ID}${RESET} (tier: ${TIER})"
else
    warn "Nodo non ancora risponde su :8084 — controlla: docker compose logs node"
fi

echo ""
echo -e "  ${BOLD}Nodo leaf unito alla mesh! ✓${RESET}"
echo ""
echo -e "  Node API:    ${CYAN}http://localhost:8084/status${RESET}"
echo -e "  Dashboard:   ${CYAN}http://localhost:8085/dashboard${RESET}"
echo -e "  Hub Mac:     ${CYAN}${HUB_MAC}/dashboard${RESET}"
echo -e "  Hub Ubuntu:  ${CYAN}${HUB_UBUNTU}/dashboard${RESET}"
echo ""
echo -e "  Logs live:   docker compose -f ${COMPOSE_FILE} logs -f node"
echo -e "  Per uscire:  docker compose -f ${COMPOSE_FILE} down"
echo ""

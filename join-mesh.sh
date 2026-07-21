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

# IP di questa macchina sulla rete locale attiva ora — WiFi ufficio/casa o
# hotspot del telefono, è lo stesso meccanismo: solo l'IP cambia. Usato per
# suggerire un PUBLIC_ENDPOINT senza bisogno di ngrok quando tutti sono
# sulla stessa rete (demo locale).
detect_lan_ip() {
    if command -v ipconfig &>/dev/null; then
        for IFACE in en0 en1 en2 bridge0; do
            local IP; IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null)
            [ -n "$IP" ] && { echo "$IP"; return 0; }
        done
    fi
    if command -v hostname &>/dev/null; then
        local IP; IP=$(hostname -I 2>/dev/null | awk '{print $1}')
        [ -n "$IP" ] && { echo "$IP"; return 0; }
    fi
    return 1
}
LAN_IP=$(detect_lan_ip || true)

HUB_MAC="https://charlesetta-haptical-unconcentratedly.ngrok-free.dev"
HUB_UBUNTU="https://sanctuary-mower-plated.ngrok-free.dev"
BOOT_PEERS_DEFAULT="${HUB_MAC},${HUB_UBUNTU}"

# URL del web-node (deploy separato, vedi repo minimesh) — override con
# WEB_NODE_URL=... ./join-mesh.sh se il tuo deploy vive altrove.
WEB_NODE_URL="${WEB_NODE_URL:-http://localhost:3000}"

echo -e ""
echo -e "${BOLD}${CYAN}⧢  HyperSpace AGI v1.02 — Join Mesh${RESET}"
echo -e "    Connetti questo nodo alla mesh distribuita"
echo -e ""

# ── 0. Tipo di nodo ──────────────────────────────────────
hdr "0 — Che tipo di nodo vuoi unire?"

echo ""
echo "  1) Nodo Docker pesante  (Ollama nativo, GPU reale — raccomandato per hub/relay)"
echo "  2) Nodo browser         (leggero, WebGPU in-tab, nessuna installazione)"
echo ""
read -rp "  Scelta [1/2, default 1]: " NODE_KIND
NODE_KIND=${NODE_KIND:-1}

if [ "$NODE_KIND" = "2" ]; then
    echo ""
    log "Nodo browser selezionato — nessuna installazione richiesta su questa macchina."
    echo ""
    echo -e "  Apri nel browser:  ${CYAN}${WEB_NODE_URL}${RESET}"
    echo "  e clicca 'Join the mesh'. Il tab diventa un nodo leggero della mesh"
    echo "  (WebLLM/transformers.js via WebGPU), senza Docker né modelli locali."
    echo ""
    echo "  Il web-node è un pacchetto Docker a sé (repo minimesh): se non è"
    echo "  ancora deployato, vedi il suo README per build/avvio, poi rilancia"
    echo "  questo script con WEB_NODE_URL=<url-del-deploy> ./join-mesh.sh"
    echo ""
    if [ -n "$LAN_IP" ]; then
        echo "  Se chi apre quel link è su un'altra macchina (stessa WiFi/hotspot),"
        echo "  nel web-node vai su 'Node panel' → control_plane_url e incolla:"
        echo -e "    ${CYAN}http://${LAN_IP}:8085${RESET}"
        echo "  (l'URL del control plane su QUESTA rete — cambia se cambi rete)."
        echo ""
    fi
    exit 0
fi

# ── 1. Dipendenze ───────────────────────────────────────
hdr "1/6 — Verifica dipendenze"

command -v docker &>/dev/null   || err "Docker non trovato: https://docs.docker.com/get-docker/"
docker compose version &>/dev/null || err "'docker compose' plugin non trovato"
command -v curl &>/dev/null     || err "curl non trovato (apt install curl)"
log "docker ✓  docker compose ✓  curl ✓"

# ── 2. Quale hub? ───────────────────────────────────────
hdr "2/6 — Quale hub vuoi raggiungere?"

echo ""
echo "  1) Hub pubblici predefiniti (mesh remota, via ngrok)"
echo "  2) Hub locale su questa rete (demo LAN/hotspot — indirizzo IP)"
echo ""
read -rp "  Scelta [1/2, default 1]: " HUB_CHOICE
HUB_CHOICE=${HUB_CHOICE:-1}

if [ "$HUB_CHOICE" = "2" ]; then
    HUB_SUGGESTION="${LAN_IP:+http://${LAN_IP}:8085}"
    echo ""
    echo "  Indirizzo del laptop/macchina che fa da hub su questa rete."
    [ -n "$HUB_SUGGESTION" ] && echo -e "  Se l'hub è QUESTA macchina, il suo indirizzo è: ${CYAN}${HUB_SUGGESTION}${RESET}"
    read -rp "  URL hub locale${HUB_SUGGESTION:+ [default: $HUB_SUGGESTION]}: " HUB_LOCAL
    HUB_LOCAL=$(echo "${HUB_LOCAL:-$HUB_SUGGESTION}" | tr -d ' ' | sed 's|/$||')
    [ -z "$HUB_LOCAL" ] && err "Serve un URL hub (es. http://192.168.1.42:8085)."
    HUB_MAC="$HUB_LOCAL"
    HUB_UBUNTU=""
    BOOT_PEERS_DEFAULT="$HUB_LOCAL"
fi

hdr "3/6 — Verifica connettività hub"

HUB_OK=0
for HUB in "$HUB_MAC" "$HUB_UBUNTU"; do
    [ -z "$HUB" ] && continue
    if curl -sf --max-time 6 "${HUB}/health" &>/dev/null; then
        log "Hub raggiungibile: ${CYAN}${HUB}${RESET}"
        HUB_OK=$((HUB_OK + 1))
    else
        warn "Hub non risponde: ${HUB} (${HUB}/health)"
    fi
done

if [ "$HUB_OK" -eq 0 ]; then
    if [ "$HUB_CHOICE" = "2" ]; then
        err "Hub locale non raggiungibile su ${HUB_MAC}. È già su (./setup.sh sull'altra macchina) e sulla stessa rete/hotspot?"
    else
        err "Nessun hub raggiungibile. Verifica la connessione e che gli hub siano attivi."
    fi
fi
log "hub raggiungibili: ${HUB_OK} ✓"

# ── 4. Endpoint pubblico ───────────────────────────
hdr "4/6 — Endpoint di questo nodo"

echo ""
echo "  Per essere raggiungibile dagli hub, questo nodo ha bisogno di un"
echo "  endpoint che gli altri possano chiamare."
echo ""
if [ -n "$LAN_IP" ]; then
    LAN_SUGGESTION="http://${LAN_IP}:8084"
    echo -e "  Rilevata rete locale attiva (WiFi o hotspot del telefono): ${CYAN}${LAN_SUGGESTION}${RESET}"
    echo "  Usalo se chi deve contattarti è sulla STESSA rete/hotspot — niente ngrok."
    echo ""
    echo "  Se invece ti serve raggiungibilità da internet, usa ngrok:"
    echo "    ngrok http 8084   → copia l'URL https://xxxx.ngrok-free.app"
    echo ""
    read -rp "  PUBLIC_ENDPOINT [default: ${LAN_SUGGESTION}]: " PUBLIC_EP
    PUBLIC_EP=$(echo "${PUBLIC_EP:-$LAN_SUGGESTION}" | tr -d ' ' | sed 's|/$||')
else
    echo "  Nessuna rete locale rilevata automaticamente. Se hai ngrok:"
    echo "    ngrok http 8084   → copia l'URL https://xxxx.ngrok-free.app"
    echo ""
    read -rp "  PUBLIC_ENDPOINT (vuoto = solo locale, non visibile dalla mesh): " PUBLIC_EP
    PUBLIC_EP=$(echo "$PUBLIC_EP" | tr -d ' ' | sed 's|/$||')
fi

if [ -z "$PUBLIC_EP" ]; then
    warn "Nessun endpoint pubblico — il nodo potrà contattare gli hub"
    warn "ma gli hub non potranno contattare questo nodo direttamente."
else
    log "PUBLIC_ENDPOINT: ${CYAN}${PUBLIC_EP}${RESET}"
fi

# ── 5. Backend LLM ──────────────────────────────────────
hdr "5/6 — Backend LLM locale"

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
    OLLAMA_MODEL_DEFAULT=""
    read -rp "  Modello Ollama [nessun default, va specificato]: " OLLAMA_MODEL
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
    OLLAMA_MODEL=""
    warn "Nessun backend LLM — il nodo funzionerà come relay puro."
    ;;
*)
    OLLAMA_DOCKER_URL="http://host.docker.internal:11434"
    OLLAMA_MODEL=""
    ;;
esac

# ── 6. Genera .env e avvia ───────────────────────────────
hdr "6/6 — Configurazione e avvio"

cat > .env << EOF
# Generato da join-mesh.sh — $(date -u +"%Y-%m-%dT%H:%M:%SZ")
OLLAMA_URL=${OLLAMA_DOCKER_URL}
OLLAMA_MODEL=${OLLAMA_MODEL}
NODE_HOSTNAME=node-1
NODE_PORT=8084
NODE_TIER=leaf
VRAM_GB=0.0
PUBLIC_ENDPOINT=${PUBLIC_EP}
BOOT_PEERS=${BOOT_PEERS_DEFAULT}
CONTROL_PLANE_URL=http://control-plane:8085
REGISTRY_URL=http://registry:8086
REGISTRY_PORT=8086
NODE_ENDPOINTS=node-1:8084
HEARTBEAT_EVERY=30
HEARTBEAT_INTERVAL=30
NODE_TTL=90
SIGN_REQUESTS=true
WEBUI_SECRET_KEY=hyperspace-secret-$(date +%s | sha256sum | head -c 12)
INFERENCE_BACKEND=ollama
EOF

log ".env generato ✓"

COMPOSE_FILE="docker-compose.yml"

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
echo -e "  Hub:         ${CYAN}${HUB_MAC}/dashboard${RESET}"
[ -n "$HUB_UBUNTU" ] && echo -e "  Hub Ubuntu:  ${CYAN}${HUB_UBUNTU}/dashboard${RESET}"
echo ""
echo -e "  Logs live:   docker compose -f ${COMPOSE_FILE} logs -f node"
echo -e "  Per uscire:  docker compose -f ${COMPOSE_FILE} down"
echo ""

#!/usr/bin/env bash
# =============================================================
# HyperSpace AGI — demo-lan.sh
#
# Setup più veloce possibile per una demo su rete locale (WiFi di
# ufficio/casa, o hotspot del cellulare): NIENTE ngrok, NIENTE tunnel.
# Chi è sulla stessa rete raggiunge questa macchina direttamente via IP.
#
# Non avvia/ricostruisce nulla di suo: guarda cosa gira già e ti dice
# esattamente cosa dare in mano a chi si unisce alla demo. Se qualcosa
# non è su, ti dice il comando esatto per avviarlo.
#
# Uso:
#   chmod +x demo-lan.sh && ./demo-lan.sh
# =============================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${GREEN}[demo-lan]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET}     $*"; }
err()  { echo -e "${RED}[error]${RESET}    $*"; }
hdr()  { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }

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

check_url() {
    # check_url <url> -> stampa "up"/"down" senza uscire dallo script
    curl -sf --max-time 3 "$1" &>/dev/null && echo "up" || echo "down"
}

echo -e ""
echo -e "${BOLD}${CYAN}⧢  HyperSpace AGI — Demo su rete locale${RESET}"
echo -e "    Nessun tunnel, nessun ngrok: solo IP sulla rete di questa stanza"
echo -e ""

# ── 1. Rete ──────────────────────────────────────────────
hdr "1/3 — Rete rilevata"

LAN_IP=$(detect_lan_ip || true)
if [ -z "$LAN_IP" ]; then
    err "Nessuna rete locale attiva rilevata. Connettiti a una WiFi o attiva"
    err "l'hotspot del telefono e collegati, poi rilancia questo script."
    exit 1
fi
log "Questa macchina è raggiungibile su: ${CYAN}${LAN_IP}${RESET}"
echo "   (WiFi ufficio/casa o hotspot del telefono: è lo stesso meccanismo,"
echo "   cambia solo l'IP a seconda della rete — se cambi rete, rilancia)"

# ── 2. Stato dei servizi ──────────────────────────────────
hdr "2/3 — Stato dei servizi (niente viene avviato/ricostruito qui)"

CP_STATUS=$(check_url "http://localhost:8085/health")
WEBUI_STATUS=$(check_url "http://localhost:3000/")
REGISTRY_STATUS=$(check_url "http://localhost:8086/health")

if [ "$CP_STATUS" = "up" ]; then
    log "control-plane (1.04)  ✓ su   → http://${LAN_IP}:8085"
else
    warn "control-plane (1.04)  ✗ giù  → avvialo con: ./setup.sh  (o: docker compose up -d --build)"
fi

if [ "$WEBUI_STATUS" = "up" ]; then
    log "Open WebUI (chat)     ✓ su   → http://${LAN_IP}:3000"
else
    warn "Open WebUI (chat)     ✗ giù  → parte insieme al resto con ./setup.sh"
fi

if [ "$REGISTRY_STATUS" = "up" ]; then
    log "registry              ✓ su"
else
    warn "registry              ✗ giù"
fi

# Il proxy Caddy di minimesh (control-plane-proxy) ha senso solo quando gli
# upstream CP_*_UPSTREAM puntano a microservizi reali con quei nomi — non è
# il caso qui (control-plane di 1.04 è un unico Flask su una porta). Se gira,
# occupa 8085 e impedisce al control-plane vero di pubblicarsi sull'host.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "minimesh-control-plane-proxy"; then
    warn "Il proxy Caddy di minimesh (control-plane-proxy) è ancora attivo e"
    warn "potrebbe occupare la porta 8085 al posto del control-plane vero."
    warn "Per questa demo va bypassato: docker stop minimesh-control-plane-proxy-1"
fi

# ── 3. Cosa dare agli ospiti ───────────────────────────────
hdr "3/3 — Cosa dare a chi si unisce (stessa WiFi/hotspot)"

echo ""
echo -e "  ${BOLD}Chat + inferenza${RESET} (Open WebUI, già in esecuzione — nessun redeploy):"
echo -e "    ${CYAN}http://${LAN_IP}:3000${RESET}"
echo ""
echo -e "  ${BOLD}Nodo browser leggero${RESET} (web-node, zero installazione):"
echo "    1. apri l'URL del web-node (repo minimesh, es. http://<ip-di-chi-lo-ospita>:3001)"
echo "    2. vai su 'Node panel', campo control_plane_url, incolla:"
echo -e "         ${CYAN}http://${LAN_IP}:8085${RESET}"
echo "    3. clicca 'save & reload', poi 'Join the mesh'"
echo ""
echo -e "  ${BOLD}Nodo Docker pesante${RESET} (colleghi con Docker/Ollama locali):"
echo -e "    ${CYAN}WEB_NODE_URL=... ./join-mesh.sh${RESET}  → scegli '1' (Docker),"
echo "    poi '2' (hub locale) quando richiesto: l'IP suggerito è già questo."
echo ""

hdr "Attenzione"
echo "- Prima connessione da un dispositivo nuovo: macOS può chiedere il"
echo "  permesso 'Local Network' a Docker/Terminal — vai in Impostazioni ›"
echo "  Privacy e sicurezza › Rete locale e abilitalo, altrimenti gli ospiti"
echo "  non si connettono anche se l'IP è giusto."
echo "- Hotspot del telefono: la stragrande maggioranza NON isola i client"
echo "  tra loro (funziona come una WiFi normale). Solo alcuni operatori/"
echo "  modelli lo fanno — se un ospite non raggiunge l'IP, è la prima cosa"
echo "  da sospettare (prova a fargli pingare l'IP)."
echo "- Se cambi rete (es. da WiFi a hotspot), l'IP cambia: rilancia questo"
echo "  script e ridai il nuovo IP a chi si è già collegato."
echo ""

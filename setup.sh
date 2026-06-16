#!/usr/bin/env bash
# =============================================================
# HyperSpace AGI v0.2 — Setup Script (macOS / Linux)
# =============================================================
# Cosa fa:
#   1. Verifica dipendenze (docker, docker compose)
#   2. Chiede quale backend di inferenza usare:
#        a) Ollama (installato nativamente sul host)
#        b) LM Studio (già in esecuzione o da avviare manualmente)
#        c) Ollama in Docker (legacy, opt-in)
#   3. Configura OLLAMA_URL / LMS_URL in .env
#   4. Avvia i container HyperSpace (node + control-plane)
# =============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${GREEN}[setup]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET}  $*"; }
err()  { echo -e "${RED}[error]${RESET} $*"; exit 1; }
head() { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }

# ────────────────────────────────────────────────────────────────
echo -e ""
echo -e "${BOLD}${CYAN}⬢  HyperSpace AGI v0.2 — Setup${RESET}"
echo -e "    Mesh di agenti IA locali su Docker + modelli LLM"
echo -e ""

# ── 1. Verifica dipendenze ────────────────────────────────────────────────────
head "1/4 — Verifica dipendenze"

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        err "'$1' non trovato. Installalo prima di continuare.\n    → $2"
    fi
    log "$1 ✓"
}

check_cmd docker  "https://docs.docker.com/get-docker/"
if ! docker compose version &>/dev/null; then
    err "'docker compose' plugin non trovato.\n    → https://docs.docker.com/compose/install/"
fi
log "docker compose ✓"

# ── 2. Copia .env se non esiste ───────────────────────────────────────────────────
head "2/4 — Configurazione .env"

if [ ! -f .env ]; then
    cp .env.example .env
    log ".env creato da .env.example"
else
    log ".env già presente — non sovrascritto"
fi

# Funzione per leggere/aggiornare una variabile nel .env
set_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" .env 2>/dev/null; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" .env && rm -f .env.bak
    else
        echo "${key}=${val}" >> .env
    fi
}

# ── 3. Scelta backend inferenza ──────────────────────────────────────────────────
head "3/4 — Backend di inferenza LLM"

echo -e ""
echo "  Quale backend vuoi usare per i modelli LLM?"
echo ""
echo "  ${BOLD}1)${RESET} Ollama nativo   — installato/avviato sull'host (consigliato)"
echo "  ${BOLD}2)${RESET} LM Studio       — usa l'API OpenAI-compatibile di LM Studio"
echo "  ${BOLD}3)${RESET} Ollama in Docker — legacy, avvia ollama come container (più lento)"
echo ""
read -rp "  Scelta [1/2/3, default 1]: " BACKEND_CHOICE
BACKEND_CHOICE=${BACKEND_CHOICE:-1}

case "$BACKEND_CHOICE" in

# ────────── OLLAMA NATIVO ────────────────────────────────────────────────────────
case "$BACKEND_CHOICE" in
1)
    log "Backend: Ollama nativo"
    set_env "INFERENCE_BACKEND" "ollama"

    if command -v ollama &>/dev/null; then
        OLLAMA_VER=$(ollama --version 2>/dev/null || echo "?")
        log "Ollama già installato: $OLLAMA_VER"
    else
        warn "Ollama non trovato. Vuoi installarlo ora? (richiede curl)"
        read -rp "  Installa Ollama? [Y/n]: " INSTALL_OLLAMA
        INSTALL_OLLAMA=${INSTALL_OLLAMA:-Y}
        if [[ "$INSTALL_OLLAMA" =~ ^[Yy] ]]; then
            log "Installazione Ollama in corso..."
            curl -fsSL https://ollama.com/install.sh | sh
            log "Ollama installato con successo."
        else
            warn "Ollama non installato. Assicurati di avviarlo manualmente."
            warn "  → https://ollama.com/download"
        fi
    fi

    # Avvia Ollama se non in esecuzione
    if ! curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
        log "Avvio Ollama in background..."
        nohup ollama serve &>/tmp/ollama.log &
        sleep 3
        if curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
            log "Ollama in ascolto su :11434"
        else
            warn "Ollama non risponde ancora. Avvialo manualmente con: ollama serve"
        fi
    else
        log "Ollama già attivo su :11434"
    fi

    # Suggerisci pull modello
    echo ""
    MODELS_JSON=$(curl -sf http://127.0.0.1:11434/api/tags 2>/dev/null || echo '{"models":[]}')
    MODELS_COUNT=$(echo "$MODELS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo 0)
    if [ "$MODELS_COUNT" -eq 0 ]; then
        warn "Nessun modello installato in Ollama."
        echo "  Modelli consigliati per hardware consumer:"
        echo "    phi3          — 3.8B  ~2.3 GB  (veloce, CPU)"
        echo "    llama3:8b     — 8B    ~5 GB    (bilanciato)"
        echo "    mistral:7b    — 7B    ~4.5 GB  (buona qualità)"
        echo "    qwen2:7b      — 7B    ~4.5 GB  (multilingual)"
        echo ""
        read -rp "  Quale modello scaricare? [default: phi3]: " PULL_MODEL
        PULL_MODEL=${PULL_MODEL:-phi3}
        log "Download $PULL_MODEL (potrebbe richiedere qualche minuto)..."
        ollama pull "$PULL_MODEL"
        set_env "OLLAMA_MODEL" "$PULL_MODEL"
        log "Modello $PULL_MODEL pronto."
    else
        DEFAULT_MODEL=$(echo "$MODELS_JSON" | python3 -c "import sys,json; ms=json.load(sys.stdin).get('models',[]); print(ms[0]['name'] if ms else 'phi3')" 2>/dev/null || echo "phi3")
        log "Modelli disponibili: $MODELS_COUNT. Default: $DEFAULT_MODEL"
        set_env "OLLAMA_MODEL" "$DEFAULT_MODEL"
    fi

    # Su macOS/Linux l'host Docker è raggiungibile come host.docker.internal
    OS_TYPE=$(uname -s)
    if [ "$OS_TYPE" = "Darwin" ]; then
        OLLAMA_HOST="host.docker.internal"
    else
        # Linux: verifica se host.docker.internal è disponibile, altrimenti usa docker0
        if getent hosts host.docker.internal &>/dev/null; then
            OLLAMA_HOST="host.docker.internal"
        else
            DOCKER0_IP=$(ip addr show docker0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 || echo "172.17.0.1")
            OLLAMA_HOST="$DOCKER0_IP"
            warn "host.docker.internal non disponibile. Usando docker0 IP: $OLLAMA_HOST"
        fi
    fi
    OLLAMA_URL="http://${OLLAMA_HOST}:11434"
    set_env "OLLAMA_URL" "$OLLAMA_URL"
    log "OLLAMA_URL impostato: $OLLAMA_URL"
    COMPOSE_PROFILE=""
    ;;

# ────────── LM STUDIO ───────────────────────────────────────────────────────────
2)
    log "Backend: LM Studio"
    set_env "INFERENCE_BACKEND" "lmstudio"
    echo ""
    echo "  LM Studio espone un server OpenAI-compatibile."
    echo "  Per abilitarlo: LM Studio → Local Server → Start Server"
    echo "  Porta default: 1234"
    echo ""
    read -rp "  URL LM Studio API [default: http://localhost:1234]: " LMS_INPUT
    LMS_URL=${LMS_INPUT:-http://localhost:1234}
    set_env "LMS_URL" "$LMS_URL"

    # Per i container, rimappa localhost → host.docker.internal / docker0
    OS_TYPE=$(uname -s)
    LMS_DOCKER_URL="$LMS_URL"
    if echo "$LMS_URL" | grep -q "localhost\|127.0.0.1"; then
        LMS_PORT=$(echo "$LMS_URL" | grep -oE '[0-9]+$' || echo "1234")
        if [ "$OS_TYPE" = "Darwin" ]; then
            LMS_DOCKER_URL="http://host.docker.internal:${LMS_PORT}"
        else
            DOCKER0_IP=$(ip addr show docker0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 || echo "172.17.0.1")
            LMS_DOCKER_URL="http://${DOCKER0_IP}:${LMS_PORT}"
        fi
    fi
    # I worker usano OLLAMA_URL ma puntano all'endpoint OpenAI di LM Studio
    set_env "OLLAMA_URL" "$LMS_DOCKER_URL"
    set_env "LMS_URL" "$LMS_DOCKER_URL"
    log "OLLAMA_URL (LM Studio) impostato: $LMS_DOCKER_URL"

    # Verifica connessione
    if curl -sf "${LMS_URL}/v1/models" &>/dev/null; then
        log "LM Studio raggiungibile e risponde."
        MODEL_LIST=$(curl -sf "${LMS_URL}/v1/models" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['id'] for m in d.get('data',[])))" 2>/dev/null || echo "?")
        log "Modelli disponibili: $MODEL_LIST"
        FIRST_MODEL=$(curl -sf "${LMS_URL}/v1/models" | python3 -c "import sys,json; d=json.load(sys.stdin); ms=d.get('data',[]); print(ms[0]['id'] if ms else 'local-model')" 2>/dev/null || echo "local-model")
        set_env "OLLAMA_MODEL" "$FIRST_MODEL"
    else
        warn "LM Studio non risponde su ${LMS_URL}."
        warn "Avvia LM Studio, carica un modello e abilita il Local Server prima di avviare i nodi."
        read -rp "  Continuare comunque? [y/N]: " CONT
        [[ "$CONT" =~ ^[Yy] ]] || { echo "Setup interrotto."; exit 0; }
    fi
    COMPOSE_PROFILE=""
    ;;

# ────────── OLLAMA IN DOCKER (legacy) ───────────────────────────────────────────
3)
    warn "Modalità Ollama-in-Docker (legacy). Più lenta, consigliata solo per test."
    set_env "INFERENCE_BACKEND" "ollama-docker"
    set_env "OLLAMA_URL" "http://ollama:11434"
    COMPOSE_PROFILE="--profile with-ollama"
    log "Il container ollama sarà avviato insieme ai nodi."
    ;;

*)
    warn "Scelta non valida, usando Ollama nativo (default)."
    COMPOSE_PROFILE=""
    ;;
esac

# ── 4. Avvio container ───────────────────────────────────────────────────────────
head "4/4 — Avvio HyperSpace AGI"

COMPOSE_FILE="docker-compose.prod.yml"
if [ ! -f "$COMPOSE_FILE" ]; then
    warn "$COMPOSE_FILE non trovato, usando docker-compose.yml"
    COMPOSE_FILE="docker-compose.yml"
fi

log "Build + avvio container..."
# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" $COMPOSE_PROFILE up -d --build

echo ""
log "${GREEN}${BOLD}HyperSpace AGI avviato!"
echo ""
echo -e "  Dashboard:    ${CYAN}http://localhost:8085/dashboard${RESET}"
echo -e "  Node API:     ${CYAN}http://localhost:8084/status${RESET}"
echo -e "  Logs live:    docker compose -f $COMPOSE_FILE logs -f"
echo ""
echo -e "  Per fermare:  docker compose -f $COMPOSE_FILE down"
echo ""

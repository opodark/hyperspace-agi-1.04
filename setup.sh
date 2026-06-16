#!/usr/bin/env bash
# =============================================================
# HyperSpace AGI v0.2 — Setup Script (macOS / Linux)
# =============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${GREEN}[setup]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET}  $*"; }
err()  { echo -e "${RED}[error]${RESET} $*"; exit 1; }
hdr()  { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }

echo -e ""
echo -e "${BOLD}${CYAN}⬢  HyperSpace AGI v0.2 — Setup${RESET}"
echo -e "    Mesh di agenti IA locali su Docker + modelli LLM"
echo -e ""

# ── 1. Dipendenze ──────────────────────────────────────────────────────────
hdr "1/4 — Verifica dipendenze"

if ! command -v docker &>/dev/null; then
    err "'docker' non trovato. Installa Docker Desktop: https://docs.docker.com/get-docker/"
fi
log "docker ✓"

if ! docker compose version &>/dev/null; then
    err "'docker compose' plugin non trovato: https://docs.docker.com/compose/install/"
fi
log "docker compose ✓"

# ── 2. .env ─────────────────────────────────────────────────────────────────
hdr "2/4 — Configurazione .env"

if [ ! -f .env ]; then
    cp .env.example .env
    log ".env creato da .env.example"
else
    log ".env già presente — non sovrascritto"
fi

set_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" .env 2>/dev/null; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" .env && rm -f .env.bak
    else
        echo "${key}=${val}" >> .env
    fi
}

# ── 3. Backend inferenza ─────────────────────────────────────────────────────
hdr "3/4 — Backend di inferenza LLM"

echo ""
echo "  Quale backend vuoi usare per i modelli LLM?"
echo ""
echo "  1) Ollama nativo   — installato/avviato sull'host (consigliato)"
echo "  2) LM Studio       — API OpenAI-compatibile di LM Studio"
echo "  3) Ollama in Docker — legacy, più lento (solo per test)"
echo ""
read -rp "  Scelta [1/2/3, default 1]: " BACKEND_CHOICE
BACKEND_CHOICE=${BACKEND_CHOICE:-1}

case "$BACKEND_CHOICE" in

1)
    log "Backend: Ollama nativo"
    set_env "INFERENCE_BACKEND" "ollama"

    if command -v ollama &>/dev/null; then
        OLLAMA_VER=$(ollama --version 2>/dev/null || echo "?")
        log "Ollama già installato: $OLLAMA_VER"
    else
        warn "Ollama non trovato."
        read -rp "  Installa Ollama ora? (richiede curl) [Y/n]: " INSTALL_OLLAMA
        INSTALL_OLLAMA=${INSTALL_OLLAMA:-Y}
        if [[ "$INSTALL_OLLAMA" =~ ^[Yy] ]]; then
            log "Installazione Ollama..."
            curl -fsSL https://ollama.com/install.sh | sh
            log "Ollama installato."
        else
            warn "Installa Ollama manualmente: https://ollama.com/download"
        fi
    fi

    if ! curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
        log "Avvio Ollama in background..."
        nohup ollama serve &>/tmp/ollama.log &
        sleep 3
        if curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
            log "Ollama attivo su :11434"
        else
            warn "Ollama non risponde ancora. Avvialo con: ollama serve"
        fi
    else
        log "Ollama già attivo su :11434"
    fi

    echo ""
    MODELS_JSON=$(curl -sf http://127.0.0.1:11434/api/tags 2>/dev/null || echo '{"models":[]}')
    MODELS_COUNT=$(echo "$MODELS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo 0)
    if [ "$MODELS_COUNT" -eq 0 ]; then
        warn "Nessun modello installato in Ollama."
        echo "  Modelli consigliati:"
        echo "    phi3        — 3.8B  ~2.3 GB  (veloce, CPU)"
        echo "    llama3:8b   — 8B    ~5 GB"
        echo "    mistral:7b  — 7B    ~4.5 GB"
        echo "    qwen2:7b    — 7B    ~4.5 GB  (multilingue)"
        echo ""
        read -rp "  Modello da scaricare [default: phi3]: " PULL_MODEL
        PULL_MODEL=${PULL_MODEL:-phi3}
        log "Download $PULL_MODEL ..."
        ollama pull "$PULL_MODEL"
        set_env "OLLAMA_MODEL" "$PULL_MODEL"
        log "Modello $PULL_MODEL pronto."
    else
        DEFAULT_MODEL=$(echo "$MODELS_JSON" | python3 -c "import sys,json; ms=json.load(sys.stdin).get('models',[]); print(ms[0]['name'] if ms else 'phi3')" 2>/dev/null || echo "phi3")
        log "Modelli: $MODELS_COUNT disponibili. Default: $DEFAULT_MODEL"
        set_env "OLLAMA_MODEL" "$DEFAULT_MODEL"
    fi

    OS_TYPE=$(uname -s)
    if [ "$OS_TYPE" = "Darwin" ]; then
        OLLAMA_HOST="host.docker.internal"
    else
        if getent hosts host.docker.internal &>/dev/null; then
            OLLAMA_HOST="host.docker.internal"
        else
            DOCKER0_IP=$(ip addr show docker0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 || echo "172.17.0.1")
            OLLAMA_HOST="$DOCKER0_IP"
            warn "Usando docker0 IP: $OLLAMA_HOST"
        fi
    fi
    OLLAMA_URL="http://${OLLAMA_HOST}:11434"
    set_env "OLLAMA_URL" "$OLLAMA_URL"
    log "OLLAMA_URL: $OLLAMA_URL"
    COMPOSE_PROFILE=""
    ;;

2)
    log "Backend: LM Studio"
    set_env "INFERENCE_BACKEND" "lmstudio"
    echo ""
    echo "  LM Studio: abilita Local Server dall'app (porta default: 1234)"
    echo ""
    read -rp "  URL LM Studio [default: http://localhost:1234]: " LMS_INPUT
    LMS_URL=${LMS_INPUT:-http://localhost:1234}

    OS_TYPE=$(uname -s)
    LMS_DOCKER_URL="$LMS_URL"
    if echo "$LMS_URL" | grep -qE "localhost|127\.0\.0\.1"; then
        LMS_PORT=$(echo "$LMS_URL" | grep -oE '[0-9]+$' || echo "1234")
        if [ "$OS_TYPE" = "Darwin" ]; then
            LMS_DOCKER_URL="http://host.docker.internal:${LMS_PORT}"
        else
            DOCKER0_IP=$(ip addr show docker0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 || echo "172.17.0.1")
            LMS_DOCKER_URL="http://${DOCKER0_IP}:${LMS_PORT}"
        fi
    fi
    set_env "OLLAMA_URL" "$LMS_DOCKER_URL"
    set_env "LMS_URL" "$LMS_DOCKER_URL"
    log "OLLAMA_URL (LM Studio): $LMS_DOCKER_URL"

    if curl -sf "${LMS_URL}/v1/models" &>/dev/null; then
        log "LM Studio raggiungibile."
        FIRST_MODEL=$(curl -sf "${LMS_URL}/v1/models" | python3 -c "import sys,json; d=json.load(sys.stdin); ms=d.get('data',[]); print(ms[0]['id'] if ms else 'local-model')" 2>/dev/null || echo "local-model")
        set_env "OLLAMA_MODEL" "$FIRST_MODEL"
        log "Modello attivo: $FIRST_MODEL"
    else
        warn "LM Studio non risponde su ${LMS_URL}."
        warn "Avvia LM Studio e abilita il Local Server prima di avviare i nodi."
        read -rp "  Continuare comunque? [y/N]: " CONT
        [[ "$CONT" =~ ^[Yy] ]] || { echo "Setup interrotto."; exit 0; }
    fi
    COMPOSE_PROFILE=""
    ;;

3)
    warn "Modalità Ollama-in-Docker (legacy). Più lenta, consigliata solo per test."
    set_env "INFERENCE_BACKEND" "ollama-docker"
    set_env "OLLAMA_URL" "http://ollama:11434"
    COMPOSE_PROFILE="--profile with-ollama"
    log "Container ollama sarà avviato insieme ai nodi."
    ;;

*)
    warn "Scelta non valida. Usando Ollama nativo (default)."
    set_env "INFERENCE_BACKEND" "ollama"
    set_env "OLLAMA_URL" "http://host.docker.internal:11434"
    COMPOSE_PROFILE=""
    ;;

esac

# ── 4. Avvio container ───────────────────────────────────────────────────────────
hdr "4/4 — Avvio HyperSpace AGI"

COMPOSE_FILE="docker-compose.prod.yml"
if [ ! -f "$COMPOSE_FILE" ]; then
    warn "$COMPOSE_FILE non trovato, usando docker-compose.yml"
    COMPOSE_FILE="docker-compose.yml"
fi

log "Build + avvio container..."
# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" $COMPOSE_PROFILE up -d --build

echo ""
log "HyperSpace AGI avviato!"
echo ""
echo -e "  Dashboard:   ${CYAN}http://localhost:8085/dashboard${RESET}"
echo -e "  Node API:    ${CYAN}http://localhost:8084/status${RESET}"
echo -e "  Logs live:   docker compose -f $COMPOSE_FILE logs -f"
echo ""
echo -e "  Per fermare: docker compose -f $COMPOSE_FILE down"
echo ""

# =============================================================
# HyperSpace AGI v0.2 — Setup Script (Windows PowerShell)
# =============================================================
# Requisiti: Docker Desktop, PowerShell 5.1+
# Esegui con: .\setup.ps1
# Per bypassare la restrizione: Set-ExecutionPolicy -Scope Process Bypass
# =============================================================

$ErrorActionPreference = 'Stop'

function log   { param($m) Write-Host "[setup] $m" -ForegroundColor Green }
function warn  { param($m) Write-Host "[warn]  $m" -ForegroundColor Yellow }
function err   { param($m) Write-Host "[error] $m" -ForegroundColor Red; exit 1 }
function head  { param($m) Write-Host "`n== $m ==" -ForegroundColor Cyan }

Write-Host ""
Write-Host "⬢  HyperSpace AGI v0.2 — Setup (Windows)" -ForegroundColor Cyan -NoNewline
Write-Host ""

# ── 1. Dipendenze ───────────────────────────────────────────────────────────
head "1/4 - Verifica dipendenze"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    err "Docker non trovato. Installa Docker Desktop: https://www.docker.com/products/docker-desktop"
}
log "Docker trovato"

try { docker compose version | Out-Null } catch { err "'docker compose' non disponibile. Aggiorna Docker Desktop." }
log "docker compose trovato"

# ── 2. .env ──────────────────────────────────────────────────────────────────
head "2/4 - Configurazione .env"

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    log ".env creato da .env.example"
} else {
    log ".env già presente - non sovrascritto"
}

function Set-EnvVar {
    param($Key, $Val)
    $content = Get-Content '.env' -Raw
    if ($content -match "(?m)^${Key}=") {
        $content = $content -replace "(?m)^${Key}=.*", "${Key}=${Val}"
    } else {
        $content += "`n${Key}=${Val}"
    }
    Set-Content '.env' $content.TrimEnd()
}

# ── 3. Backend inferenza ─────────────────────────────────────────────────────────
head "3/4 - Backend di inferenza LLM"

Write-Host ""
Write-Host "  Quale backend vuoi usare per i modelli LLM?"
Write-Host ""
Write-Host "  1) Ollama nativo   - installato/avviato sull'host (consigliato)"
Write-Host "  2) LM Studio       - API OpenAI-compatibile di LM Studio"
Write-Host "  3) Ollama in Docker - legacy, più lento"
Write-Host ""
$choice = Read-Host "  Scelta [1/2/3, default 1]"
if (-not $choice) { $choice = '1' }

switch ($choice) {

    '1' {
        log "Backend: Ollama nativo"
        Set-EnvVar 'INFERENCE_BACKEND' 'ollama'

        $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $ollamaCmd) {
            warn "Ollama non trovato."
            Write-Host ""
            Write-Host "  Installa Ollama per Windows:"
            Write-Host "    https://ollama.com/download/windows" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "  Oppure con winget:"
            Write-Host "    winget install Ollama.Ollama" -ForegroundColor Cyan
            Write-Host ""
            $installWinget = Read-Host "  Installa tramite winget ora? [y/N]"
            if ($installWinget -match '^[Yy]') {
                winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
                log "Ollama installato. Riavvia il terminale poi riesegui setup.ps1"
                exit 0
            } else {
                warn "Installa Ollama manualmente e poi riesegui setup.ps1"
                exit 0
            }
        }

        log "Ollama trovato: $(ollama --version)"

        # Controlla se Ollama è attivo
        $ollamaRunning = $false
        try {
            $r = Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            $ollamaRunning = ($r.StatusCode -eq 200)
        } catch {}

        if (-not $ollamaRunning) {
            log "Avvio Ollama in background..."
            Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden
            Start-Sleep 4
            try {
                Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 3 | Out-Null
                log "Ollama attivo su :11434"
            } catch {
                warn "Ollama non risponde ancora. Prova ad avviarlo manualmente: ollama serve"
            }
        } else {
            log "Ollama già attivo"
        }

        # Lista modelli e pull se vuoto
        try {
            $tags = (Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing).Content | ConvertFrom-Json
            $models = $tags.models
        } catch { $models = @() }

        if ($models.Count -eq 0) {
            warn "Nessun modello installato in Ollama."
            Write-Host ""
            Write-Host "  Modelli consigliati:"
            Write-Host "    phi3         3.8B  ~2.3 GB (veloce, CPU)"
            Write-Host "    llama3:8b    8B    ~5 GB"
            Write-Host "    mistral:7b   7B    ~4.5 GB"
            Write-Host "    qwen2:7b     7B    ~4.5 GB (multilingual)"
            Write-Host ""
            $pullModel = Read-Host "  Quale modello scaricare? [default: phi3]"
            if (-not $pullModel) { $pullModel = 'phi3' }
            log "Download $pullModel ..."
            ollama pull $pullModel
            Set-EnvVar 'OLLAMA_MODEL' $pullModel
        } else {
            $defaultModel = $models[0].name
            log "Modelli disponibili: $($models.Count). Default: $defaultModel"
            Set-EnvVar 'OLLAMA_MODEL' $defaultModel
        }

        Set-EnvVar 'OLLAMA_URL' 'http://host.docker.internal:11434'
        log "OLLAMA_URL impostato: http://host.docker.internal:11434"
        $composeProfile = ''
    }

    '2' {
        log "Backend: LM Studio"
        Set-EnvVar 'INFERENCE_BACKEND' 'lmstudio'
        Write-Host ""
        Write-Host "  LM Studio: apri l'app, carica un modello e vai su Local Server > Start Server"
        Write-Host "  Porta default: 1234"
        Write-Host ""
        $lmsInput = Read-Host "  URL LM Studio API [default: http://localhost:1234]"
        if (-not $lmsInput) { $lmsInput = 'http://localhost:1234' }

        # Rimappa localhost per i container Docker
        $lmsDocker = $lmsInput -replace 'localhost', 'host.docker.internal' -replace '127\.0\.0\.1', 'host.docker.internal'

        # Test connessione
        try {
            $r = Invoke-WebRequest "$lmsInput/v1/models" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
            log "LM Studio raggiungibile"
            $modelData = ($r.Content | ConvertFrom-Json).data
            if ($modelData.Count -gt 0) {
                $firstModel = $modelData[0].id
                log "Modello attivo: $firstModel"
                Set-EnvVar 'OLLAMA_MODEL' $firstModel
            }
        } catch {
            warn "LM Studio non risponde su $lmsInput"
            warn "Avvia LM Studio con Local Server attivo prima di avviare i nodi."
            $cont = Read-Host "  Continuare comunque? [y/N]"
            if ($cont -notmatch '^[Yy]') { Write-Host "Setup interrotto."; exit 0 }
        }

        Set-EnvVar 'LMS_URL' $lmsDocker
        Set-EnvVar 'OLLAMA_URL' $lmsDocker
        log "OLLAMA_URL (LM Studio) impostato: $lmsDocker"
        $composeProfile = ''
    }

    '3' {
        warn "Modalità Ollama-in-Docker (legacy). Più lenta, solo per test."
        Set-EnvVar 'INFERENCE_BACKEND' 'ollama-docker'
        Set-EnvVar 'OLLAMA_URL' 'http://ollama:11434'
        $composeProfile = '--profile with-ollama'
        log "Il container ollama sarà avviato."
    }

    default {
        warn "Scelta non valida, usando Ollama nativo."
        $composeProfile = ''
    }
}

# ── 4. Avvio container ───────────────────────────────────────────────────────────
head "4/4 - Avvio HyperSpace AGI"

$composeFile = if (Test-Path 'docker-compose.prod.yml') { 'docker-compose.prod.yml' } else { 'docker-compose.yml' }

$args = @('-f', $composeFile)
if ($composeProfile) { $args += $composeProfile.Split(' ') }
$args += @('up', '-d', '--build')

log "Build + avvio container: docker compose $($args -join ' ')"
& docker compose @args

Write-Host ""
Write-Host "  HyperSpace AGI avviato!" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:   http://localhost:8085/dashboard" -ForegroundColor Cyan
Write-Host "  Node API:    http://localhost:8084/status" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Per fermare: docker compose -f $composeFile down"
Write-Host ""

# HyperSpace AGI 1.04 - Windows Setup
# Uso:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\setup.ps1

$ErrorActionPreference = 'Stop'

function Log($m)  { Write-Host "[setup] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[error] $m" -ForegroundColor Red; exit 1 }
function Head($m) { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }

function Require-File($Path) {
    if (-not (Test-Path $Path)) { Fail "File mancante: $Path" }
}

function Read-FileUtf8($Path) {
    if (Test-Path $Path) {
        return [System.IO.File]::ReadAllText((Resolve-Path $Path), [System.Text.UTF8Encoding]::new($false))
    }
    return ""
}

function Write-FileUtf8($Path, $Content) {
    $enc = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) $Path), $Content, $enc)
}

function Set-EnvVar {
    param(
        [Parameter(Mandatory=$true)][string]$Key,
        [Parameter(Mandatory=$true)][string]$Val
    )

    Require-File '.env'
    $content = Read-FileUtf8 '.env'

    if ($content -match "(?m)^$([regex]::Escape($Key))=") {
        $content = [regex]::Replace($content, "(?m)^$([regex]::Escape($Key))=.*$", "${Key}=${Val}")
    } else {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) { $content += "`r`n" }
        $content += "${Key}=${Val}`r`n"
    }

    Write-FileUtf8 '.env' ($content.TrimEnd("`r","`n") + "`r`n")
}

function Ensure-EnvFile {
    if (-not (Test-Path '.env')) {
        if (Test-Path '.env.windows') {
            Copy-Item '.env.windows' '.env'
            Log ".env creato da .env.windows"
        } elseif (Test-Path '.env.example') {
            Copy-Item '.env.example' '.env'
            Log ".env creato da .env.example"
        } else {
            Warn ".env.example non trovato, creo un .env minimo"
            $defaultEnv = @"
COMPOSE_PROJECT_NAME=hyperspace
INFERENCE_BACKEND=ollama
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=gemma2:2b
NODE_PORT=8084
DASHBOARD_PORT=8085
REGISTRY_PORT=8090
"@
            Write-FileUtf8 '.env' ($defaultEnv.Trim() + "`r`n")
        }
    } else {
        Log ".env già presente, non sovrascritto"
    }
}

function Test-OllamaApi($BaseUrl) {
    try {
        $r = Invoke-WebRequest "$BaseUrl/api/tags" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-OpenAICompatApi($BaseUrl) {
    try {
        $r = Invoke-WebRequest "$BaseUrl/models" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Detect-NvidiaGpu {
    $gpuName = $null
    try {
        $gpuName = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA|GeForce|RTX' } | Select-Object -First 1 -ExpandProperty Name)
    } catch {}
    return $gpuName
}

function Get-NvidiaVramMiB {
    try {
        $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if (-not $smi) { return $null }

        $out = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
        if (-not $out) { return $null }

        $values = @(
            $out |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ -match '^\d+$' } |
            ForEach-Object { [int]$_ }
        )

        if ($values.Count -eq 0) { return $null }
        return ($values | Measure-Object -Maximum).Maximum
    } catch {
        return $null
    }
}

function Get-FallbackVramMiB {
    try {
        $gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
            Where-Object { $_.AdapterRAM -and $_.AdapterRAM -gt 0 }

        if (-not $gpus) { return $null }

        $maxBytes = ($gpus | Measure-Object -Property AdapterRAM -Maximum).Maximum
        if (-not $maxBytes) { return $null }

        return [int][math]::Round($maxBytes / 1MB)
    } catch {
        return $null
    }
}

function Get-AvailableVramMiB {
    $nvidia = Get-NvidiaVramMiB
    if ($nvidia) { return $nvidia }

    $fallback = Get-FallbackVramMiB
    if ($fallback) { return $fallback }

    return $null
}

function Get-ModelMinVramMiB {
    param([string]$ModelName)

    switch -Regex ($ModelName) {
        '^gemma3:1b$'  { return 2048 }
        '^gemma2:2b$'  { return 3072 }
        '^mistral:7b$' { return 6144 }
        default        { return 4096 }
    }
}

function Test-ModelFitsVram {
    param([string]$ModelName)

    $available = Get-AvailableVramMiB
    $required  = Get-ModelMinVramMiB $ModelName

    if (-not $available) {
        Warn "VRAM non rilevata con affidabilità. Salto il blocco automatico ma chiedo conferma."
        return @{
            CanPull      = $false
            Unknown      = $true
            AvailableMiB = $null
            RequiredMiB  = $required
        }
    }

    return @{
        CanPull      = ($available -ge $required)
        Unknown      = $false
        AvailableMiB = $available
        RequiredMiB  = $required
    }
}

function Ask-HardwareProfile {
    Head "Profilo hardware"
    $gpu = Detect-NvidiaGpu
    $vram = Get-AvailableVramMiB

    if ($gpu) { Log "GPU rilevata: $gpu" } else { Warn "GPU NVIDIA non rilevata automaticamente" }
    if ($vram) { Log "VRAM rilevata: $vram MiB" } else { Warn "VRAM non rilevata con precisione" }

    Write-Host "Seleziona il profilo principale:"
    Write-Host "  1) RTX 3050 6GB + 16GB RAM  (safe)"
    Write-Host "  2) RTX 3050 6GB + 32GB RAM  (balanced)"
    Write-Host "  3) RTX 4070 8GB + 16GB RAM  (balanced)"
    Write-Host "  4) RTX 4070 8GB + 32GB RAM  (best consumer)"
    Write-Host "  5) CPU / fallback conservativo"
    $p = Read-Host "Scelta [1/2/3/4/5, default 3]"
    if (-not $p) { $p = '3' }
    return $p
}

function Get-RecommendedModel {
    param([string]$Profile)

    switch ($Profile) {
        '1' { return 'gemma2:2b' }
        '2' { return 'gemma2:2b' }
        '3' { return 'mistral:7b' }
        '4' { return 'mistral:7b' }
        '5' { return 'gemma3:1b' }
        default { return 'gemma2:2b' }
    }
}

function Ensure-OllamaInstalled {
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        Warn "Ollama non trovato"
        Write-Host "Installa Ollama da: https://ollama.com/download/windows" -ForegroundColor Cyan
        $install = Read-Host "Vuoi provare via winget adesso? [y/N]"
        if ($install -match '^[Yy]') {
            winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
            Log "Installazione completata. Riavvia il terminale e riesegui setup.ps1"
            exit 0
        }
        Fail "Ollama richiesto ma non installato"
    }
    Log "Ollama trovato"
}

function Ensure-OllamaRunning {
    if (Test-OllamaApi 'http://127.0.0.1:11434') {
        Log "Ollama già attivo su 127.0.0.1:11434"
        return
    }

    Warn "Ollama non risponde, provo ad avviarlo"
    Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden
    Start-Sleep -Seconds 5

    if (Test-OllamaApi 'http://127.0.0.1:11434') {
        Log "Ollama avviato correttamente"
    } else {
        Fail "Ollama non risponde ancora. Avvialo manualmente con: ollama serve"
    }
}

function Ensure-ModelWithVramCheck {
    param([string]$ModelName)

    $fit = Test-ModelFitsVram $ModelName

    if ($fit.Unknown) {
        $cont = Read-Host "Non riesco a stimare bene la VRAM. Vuoi tentare comunque il pull di $ModelName? [y/N]"
        if ($cont -notmatch '^[Yy]') {
            Warn "Pull annullato. Imposta un modello manualmente più piccolo."
            return
        }
    }
    elseif (-not $fit.CanPull) {
        Warn "VRAM insufficiente per $ModelName"
        Warn ("Disponibile: {0} MiB | Minimo prudente richiesto: {1} MiB" -f $fit.AvailableMiB, $fit.RequiredMiB)

        $fallbackModel = 'gemma2:2b'
        if ($ModelName -ne $fallbackModel) {
            $useFallback = Read-Host "Vuoi usare il fallback $fallbackModel invece? [Y/n]"
            if ($useFallback -notmatch '^[Nn]') {
                Ensure-ModelWithVramCheck $fallbackModel
                Set-EnvVar 'OLLAMA_MODEL' $fallbackModel
                return
            }
        }

        $force = Read-Host "Vuoi forzare comunque il pull di $ModelName? [y/N]"
        if ($force -notmatch '^[Yy]') {
            Warn "Pull annullato."
            return
        }
    }
    else {
        Log ("VRAM OK per {0}: disponibile {1} MiB, richiesto ~{2} MiB" -f $ModelName, $fit.AvailableMiB, $fit.RequiredMiB)
    }

    try {
        $tags = (Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        $models = @($tags.models)
    } catch {
        $models = @()
    }

    $exists = $false
    foreach ($m in $models) {
        if ($m.name -eq $ModelName) { $exists = $true; break }
    }

    if ($exists) {
        Log "Modello già disponibile: $ModelName"
        return
    }

    $pull = Read-Host "Scaricare $ModelName ora? [Y/n]"
    if ($pull -match '^[Nn]') {
        Warn "Download saltato"
        return
    }

    ollama pull $ModelName
}

Head "1/5 - Verifica prerequisiti"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker non trovato. Installa Docker Desktop."
}
Log "Docker trovato"

try {
    docker compose version | Out-Null
    Log "docker compose trovato"
} catch {
    Fail "'docker compose' non disponibile"
}

Head "2/5 - Preparazione .env"
Ensure-EnvFile

Head "3/5 - Scelta backend"
Write-Host "Backend disponibili:"
Write-Host "  1) Ollama host Windows (default consigliato)"
Write-Host "  2) LM Studio OpenAI-compatible"
Write-Host "  3) Ollama in Docker (legacy/test)"
$backend = Read-Host "Scelta [1/2/3, default 1]"
if (-not $backend) { $backend = '1' }

$composeProfile = ''
$recommendedModel = 'gemma2:2b'

switch ($backend) {
    '1' {
        Set-EnvVar 'INFERENCE_BACKEND' 'ollama'
        $profile = Ask-HardwareProfile
        $recommendedModel = Get-RecommendedModel $profile

        Ensure-OllamaInstalled
        Ensure-OllamaRunning
        Set-EnvVar 'OLLAMA_URL' 'http://host.docker.internal:11434'
        Set-EnvVar 'OLLAMA_MODEL' $recommendedModel
        Ensure-ModelWithVramCheck $recommendedModel

        Log "Backend impostato: Ollama host"
        Log "Modello consigliato: $recommendedModel"
    }

    '2' {
        Set-EnvVar 'INFERENCE_BACKEND' 'lmstudio'
        $lmUrl = Read-Host "URL LM Studio [default: http://localhost:1234/v1]"
        if (-not $lmUrl) { $lmUrl = 'http://localhost:1234/v1' }

        $dockerUrl = $lmUrl -replace 'localhost', 'host.docker.internal' -replace '127\.0\.0\.1', 'host.docker.internal'
        Set-EnvVar 'OPENAI_BASE_URL' $dockerUrl
        Set-EnvVar 'LMS_URL' $dockerUrl

        if (Test-OpenAICompatApi $lmUrl) {
            Log "LM Studio raggiungibile su $lmUrl"
            $loadedModel = $null
            try {
                $resp = Invoke-RestMethod "$lmUrl/models" -TimeoutSec 4
                $loadedModel = $resp.data | Select-Object -First 1 -ExpandProperty id
            } catch {}

            if ($loadedModel) {
                Set-EnvVar 'OLLAMA_MODEL' $loadedModel
                Log "Modello rilevato in LM Studio: $loadedModel"
            } else {
                Warn "Nessun modello caricato in LM Studio: imposta OLLAMA_MODEL manualmente in .env"
            }
        } else {
            Warn "LM Studio non raggiungibile su $lmUrl - verifica che il local server sia attivo"
            $continue = Read-Host "Continuare comunque? [y/N]"
            if ($continue -notmatch '^[Yy]') { Fail "Setup interrotto: LM Studio non raggiungibile" }
        }

        Set-EnvVar 'OLLAMA_URL' $dockerUrl
        Log "Backend impostato: LM Studio"
    }

    '3' {
        Set-EnvVar 'INFERENCE_BACKEND' 'ollama-docker'
        Set-EnvVar 'OLLAMA_URL' 'http://ollama:11434'
        $profile = Ask-HardwareProfile
        $recommendedModel = Get-RecommendedModel $profile
        Set-EnvVar 'OLLAMA_MODEL' $recommendedModel

        if (Detect-NvidiaGpu) {
            $composeProfile = '--profile nvidia'
        } else {
            $composeProfile = '--profile cpu'
            Warn "GPU NVIDIA non rilevata: uso il profilo Ollama CPU. Per AMD/Intel/Vulkan avvia manualmente con 'docker compose --profile amd|intel|vulkan up -d'."
        }
        Warn "Modalità legacy: Ollama in Docker"
    }

    default {
        Warn "Scelta non valida, uso Ollama host come default"
        Set-EnvVar 'INFERENCE_BACKEND' 'ollama'
        $profile = Ask-HardwareProfile
        $recommendedModel = Get-RecommendedModel $profile

        Ensure-OllamaInstalled
        Ensure-OllamaRunning
        Set-EnvVar 'OLLAMA_URL' 'http://host.docker.internal:11434'
        Set-EnvVar 'OLLAMA_MODEL' $recommendedModel
        Ensure-ModelWithVramCheck $recommendedModel

        Log "Backend impostato: Ollama host"
        Log "Modello consigliato: $recommendedModel"
    }
}

Head "4/5 - Compose file"
$composeFile = $null
if (Test-Path 'docker-compose.windows.yml') {
    $composeFile = 'docker-compose.windows.yml'
} elseif (Test-Path 'docker-compose.yml') {
    $composeFile = 'docker-compose.yml'
} elseif (Test-Path 'docker-compose.prod.yml') {
    $composeFile = 'docker-compose.prod.yml'
} else {
    Fail "Nessun docker-compose trovato"
}
Log "Compose selezionato: $composeFile"

Head "5/5 - Avvio stack"
$args = @('-f', $composeFile)
if ($composeProfile) { $args += $composeProfile.Split(' ') }
$args += @('up', '-d', '--build')

Log "Comando: docker compose $($args -join ' ')"
& docker compose @args

Write-Host ""
Write-Host "HyperSpace AGI avviato" -ForegroundColor Green
Write-Host "Dashboard: http://localhost:8085/dashboard" -ForegroundColor Cyan
Write-Host "Node API:  http://localhost:8084/status" -ForegroundColor Cyan
Write-Host ""
Write-Host "Per fermare:"
Write-Host "docker compose -f $composeFile down" -ForegroundColor Yellow
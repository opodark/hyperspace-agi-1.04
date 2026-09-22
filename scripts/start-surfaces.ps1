# SPDX-License-Identifier: Apache-2.0
# HyperSpace AGI 1.04 - avvio delle SUPERFICI ESTERNE (Windows/PowerShell)
#
# Perché esiste: il driver Telegram e il ponte di ComfyUI sono due processi che
# vivono sulla macchina e non dentro lo stack: se non li avvii, il canale tace e
# le immagini restano in coda. Due comandi diversi da ricordare sono due comandi
# che si dimenticano — qui diventano uno, con un verdetto.
#
#   .\scripts\start-surfaces.ps1            # avvia entrambi (nascosti, con log)
#   .\scripts\start-surfaces.ps1 -Check     # non avvia: dice cosa gira e cosa manca
#   .\scripts\start-surfaces.ps1 -Telegram  # solo il driver Telegram
#   .\scripts\start-surfaces.ps1 -Comfy     # solo il ponte di ComfyUI
#   .\scripts\start-surfaces.ps1 -Stop      # ferma quelli avviati da qui
#
# I log finiscono in data\ (ignorata da git): telegram-driver.log,
# comfy-bridge.log. I due processi si riconoscono dalla riga di comando, quindi
# -Stop non tocca nient'altro che gira sulla macchina.

param(
    [switch]$Telegram,
    [switch]$Comfy,
    [switch]$Check,
    [switch]$Stop
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Log($m)  { Write-Host "[surfaces] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]     $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[error]    $m" -ForegroundColor Red }
function Head($m) { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }

if (-not $Telegram -and -not $Comfy -and -not $Stop) {
    # Nessuna scelta = entrambe: è quello che serve in uso normale.
    $Telegram = $true
    $Comfy = $true
}

function Get-Processi([string]$Frammento) {
    # Il processo vero è python (il launcher powershell è solo il contenitore):
    # si cerca il frammento nella riga di comando, che è l'unica cosa che dice
    # COSA sta girando.
    return @(Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Frammento) })
}

function Avvia([string]$Etichetta, [string[]]$Argomenti, [string]$LogNome) {
    $percorso = Join-Path $Root "data\$LogNome"
    Start-Process -FilePath 'powershell.exe' -ArgumentList $Argomenti -WindowStyle Hidden `
        -RedirectStandardOutput $percorso `
        -RedirectStandardError ($percorso -replace '\.log$', '.err.log') | Out-Null
    Log "$Etichetta avviato (log: data\$LogNome)"
}

$driverTelegram = Join-Path $Root 'scripts\start-telegram.ps1'
$ponteComfy = Join-Path $Root 'integrations\comfyui\comfy_bridge.py'
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'py' }

$telegramAttivi = Get-Processi 'telegram_bot.py'
$comfyAttivi = Get-Processi 'comfy_bridge.py'

if ($Stop) {
    Head "Stop"
    foreach ($processo in @($telegramAttivi + $comfyAttivi)) {
        Stop-Process -Id $processo.ProcessId -Force -ErrorAction SilentlyContinue
        Log "fermato PID $($processo.ProcessId)"
    }
    if (-not $telegramAttivi -and -not $comfyAttivi) { Log "niente da fermare" }
    exit 0
}

Head "Stato"
if ($telegramAttivi.Count) { Log "driver Telegram: attivo (PID $($telegramAttivi.ProcessId -join ', '))" }
else { Warn "driver Telegram: spento (Aurora non legge i messaggi)" }
if ($comfyAttivi.Count) { Log "ponte ComfyUI: attivo (PID $($comfyAttivi.ProcessId -join ', '))" }
else { Warn "ponte ComfyUI: spento (le immagini restano in coda)" }

if ($Check) {
    # Il verdetto vero arriva dai due `-Check`: qui si aggiunge solo cosa manca
    # lato stack, che è la causa più frequente.
    $statoCp = 0
    try {
        $statoCp = [int](Invoke-WebRequest -Uri 'http://127.0.0.1:8085/health' -UseBasicParsing -TimeoutSec 5).StatusCode
    } catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) { $statoCp = [int]$_.Exception.Response.StatusCode }
    }
    if ($statoCp -eq 200) { Log "control-plane: ok" } else { Warn "control-plane: HTTP $statoCp (avvialo con .\scripts\start.ps1)" }
    Head "Dettagli"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $driverTelegram -Check -Mention
    # Il ponte va invocato dal SUO launcher: legge il token "comfy" dal .env.
    # Chiamato diretto risponderebbe 401, che è come è nato questo fix.
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'integrations\comfyui\start-bridge.ps1') -Check
    exit 0
}

if ($Telegram -and -not $telegramAttivi.Count) {
    Avvia 'driver Telegram' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $driverTelegram, '-Mention') 'telegram-driver.log'
}
if ($Comfy -and -not $comfyAttivi.Count) {
    Avvia 'ponte ComfyUI' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                            (Join-Path $Root 'integrations\comfyui\start-bridge.ps1')) 'comfy-bridge.log'
}
if (($Telegram -and $telegramAttivi.Count) -or ($Comfy -and $comfyAttivi.Count)) {
    Log "i processi già attivi sono stati lasciati come sono (niente doppioni)"
}
Head "Fatto"
Log "verifica con: .\scripts\start-surfaces.ps1 -Check"

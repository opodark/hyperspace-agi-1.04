# SPDX-License-Identifier: Apache-2.0
# HyperSpace AGI 1.04 - avvio del gateway immagini di Open WebUI (Windows/PowerShell)
#
# Perché un launcher e non `python webui_gateway.py`: il gateway decide *se* e *cosa*
# scaricare dalla scheda leggendo CHANNEL_MODEL e IMAGE_FREE_GPU, che stanno in .env —
# un file che il processo non vede da solo. Senza, lo scarico semplicemente non parte
# e la contesa con Ollama torna a presentarsi come `CUDA error: unknown error`
# (2026-09-22: 6170 MiB a Ollama, 1730 liberi, il diffusion non ci stava).
#
#   .\integrations\comfyui\start-gateway.ps1            # in ascolto su 127.0.0.1:8189
#   .\integrations\comfyui\start-gateway.ps1 -Check     # verifica e basta
#   .\integrations\comfyui\start-gateway.ps1 -Porta 8190

param(
    [switch]$Check,
    [string]$Comfy = "",
    [int]$Porta = 0
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

function Log($m)  { Write-Host "[gateway] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]    $m" -ForegroundColor Yellow }

function Read-EnvFile {
    param([string]$Path)
    $valori = @{}
    if (-not (Test-Path $Path)) { return $valori }
    foreach ($riga in Get-Content $Path) {
        $t = $riga.Trim()
        if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $i = $t.IndexOf('=')
        $valori[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim().Trim('"')
    }
    return $valori
}

$envDati = Read-EnvFile (Join-Path $Root '.env')
# Solo le chiavi che il gateway legge: il resto del .env non ha motivo di finire
# nell'ambiente di questo processo.
foreach ($chiave in @('CHANNEL_MODEL', 'IMAGE_FREE_GPU', 'OLLAMA_RAW_BASE_URL',
                      'COMFY_URL', 'WEBUI_GATEWAY_PORT')) {
    if ($envDati.ContainsKey($chiave) -and $envDati[$chiave]) {
        Set-Item -Path ("env:" + $chiave) -Value $envDati[$chiave]
    }
}
if (-not $env:IMAGE_FREE_GPU) {
    Warn "IMAGE_FREE_GPU non dichiarato in .env: di default lo scarico e' ATTIVO"
    Warn "  (serve CHANNEL_MODEL: e' quello che dice quale modello tenere d'occhio)"
}

$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'py' }
$gateway = Join-Path $Root 'integrations\comfyui\webui_gateway.py'

$argomenti = @($gateway)
if ($Comfy) { $argomenti += @('--comfy', $Comfy) }
if ($Porta -gt 0) { $argomenti += @('--port', "$Porta") }
if ($Check) { $argomenti += '--check' }

$porta = if ($Porta -gt 0) { $Porta } elseif ($env:WEBUI_GATEWAY_PORT) { $env:WEBUI_GATEWAY_PORT } else { 8189 }
$comfyUrl = if ($env:COMFY_URL) { $env:COMFY_URL } else { 'http://127.0.0.1:8188' }
$modo = if ($Check) { 'verifica' } else { 'in ascolto' }
Log "avvio webui_gateway.py ($modo, porta $porta, ComfyUI=$comfyUrl)"
& $python @argomenti

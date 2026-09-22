# SPDX-License-Identifier: Apache-2.0
# HyperSpace AGI 1.04 - avvio del ponte ComfyUI (Windows/PowerShell)
#
# Perché: il ponte ha bisogno del token del canale "comfy" e della posizione di
# ComfyUI. Copiarli a mano è il punto in cui si sbaglia (token troncato -> 401 che
# non dice quale lato è sbagliato): qui si leggono da .env e si passano al figlio.
#
#   .\integrations\comfyui\start-bridge.ps1            # in attesa, in ciclo
#   .\integrations\comfyui\start-bridge.ps1 -Once      # un job ed esce
#   .\integrations\comfyui\start-bridge.ps1 -Check     # verifica e basta

param(
    [switch]$Once,
    [switch]$Check,
    [string]$Url = "",
    [string]$Comfy = ""
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

function Log($m)  { Write-Host "[bridge] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]   $m" -ForegroundColor Yellow }

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

function Get-ChannelToken {
    # CHANNEL_CLIENTS è "cam4=<token>;comfy=<token>": il ponte usa il SUO token,
    # che è quello che il control-plane si aspetta per il nome "comfy".
    param([string]$Elenco, [string]$Nome)
    foreach ($voce in ($Elenco -split ';')) {
        if (-not $voce.Trim()) { continue }
        $i = $voce.IndexOf('=')
        if ($i -lt 1) { continue }
        if ($voce.Substring(0, $i).Trim() -ieq $Nome) { return $voce.Substring($i + 1).Trim() }
    }
    return ""
}

$envDati = Read-EnvFile (Join-Path $Root '.env')
$token = Get-ChannelToken -Elenco $envDati['CHANNEL_CLIENTS'] -Nome 'comfy'
if (-not $token) {
    Warn "nessun token 'comfy' in CHANNEL_CLIENTS: generane uno con"
    Warn "  python scripts\channel_token.py comfy --write"
}
if (-not $Url) { $Url = 'http://127.0.0.1:8085' }

$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'py' }
$ponte = Join-Path $Root 'integrations\comfyui\comfy_bridge.py'

$argomenti = @($ponte, '--url', $Url, '--token', $token)
if ($Comfy) { $argomenti += @('--comfy', $Comfy) }
if ($Once) { $argomenti += '--once' }
if ($Check) { $argomenti += '--check' }

$modo = if ($Check) { 'verifica' } elseif ($Once) { 'un job ed esce' } else { 'in ciclo' }
Log "avvio comfy_bridge.py ($modo, CP=$Url, canale=comfy)"
& $python @argomenti

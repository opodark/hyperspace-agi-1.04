# SPDX-License-Identifier: Apache-2.0
# Installa i nodi HyperSpace dentro ComfyUI (componente del solo nodo win11).
#
# Perché una JUNCTION e non una copia: il codice vive nel repo (versionato, con i
# suoi test) e ComfyUI lo vede nella propria cartella dei nodi. Una copia va
# riallineata a mano, e "a mano" vuol dire dimenticarsene: i due lati divergono
# in silenzio, ed è il difetto che il repo evita ovunque (identità, token, .env).
# La junction si toglie senza toccare il sorgente: vedi -Remove.
#
# Uso:
#   .\integrations\comfyui\install.ps1            # crea o riallinea la junction
#   .\integrations\comfyui\install.ps1 -Check     # non tocca niente: dice cosa farebbe
#   .\integrations\comfyui\install.ps1 -Remove    # toglie la junction (il repo resta)
#
# Dopo l'installazione ComfyUI deve ricaricare i nodi: dalla UI, "Refresh" del
# pannello nodi (o riavvio dell'app). Il nome del pacchetto dentro custom_nodes
# è l'unica cosa che ComfyUI vede: cambiarlo lo scollega.

param(
    [string]$CustomNodes = "",
    [string]$Nome = "hyperspace_nodes",
    [switch]$Check,
    [switch]$Remove
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # radice del repo
$Sorgente = Join-Path $Root 'integrations\comfyui'

function Log($m)  { Write-Host "[comfyui] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]    $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[error]   $m" -ForegroundColor Red }
function Head($m) { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }

function Trova-CustomNodes {
    # Le installazioni di ComfyUI Desktop mettono i nodi qui; l'utente può
    # averne più di una (versioni diverse), quindi si cercano e si dice quale
    # si è scelta: indovinare in silenzio significherebbe installare nel posto
    # sbagliato e vedere "non c'è nessun nodo nuovo" senza capire perché.
    param([string]$Esplicito)
    if ($Esplicito) {
        if (Test-Path $Esplicito) { return (Resolve-Path $Esplicito).Path }
        Fail "cartella custom_nodes indicata non trovata: $Esplicito"
        return ""
    }
    $base = Join-Path $env:LOCALAPPDATA 'Comfy-Desktop'
    if (-not (Test-Path $base)) { return "" }
    $trovati = @(Get-ChildItem -Path $base -Directory -Recurse -Depth 4 -Filter 'custom_nodes' `
                 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    if ($trovati.Count -eq 0) { return "" }
    if ($trovati.Count -gt 1) {
        Warn "piu' di una installazione ComfyUI: uso la prima, le altre con -CustomNodes"
        foreach ($c in $trovati) { Warn "  $c" }
    }
    return $trovati[0]
}

$customNodes = Trova-CustomNodes -Esplicito $CustomNodes
if (-not $customNodes) {
    Fail "non trovo custom_nodes di ComfyUI: passalo con -CustomNodes <percorso>"
    exit 1
}
$destinazione = Join-Path $customNodes $Nome

Head "Dove"
Log "sorgente (repo):     $Sorgente"
Log "installazione nodi:  $customNodes"
Log "pacchetto:           $destinazione"

Head "Stato attuale"
# Come si riconosce una junction su PowerShell 5.1: non con `.Target` (esiste da
# PS 6), ma confrontando il CONTENUTO del pacchetto installato con quello del
# repo. E' anche il controllo che conta davvero: dice se ciò che ComfyUI carica
# è questo codice, non se esiste un collegamento.
$installato = Test-Path (Join-Path $destinazione 'nodes.py')
$allineato = $false
if ($installato) {
    $h1 = (Get-FileHash (Join-Path $destinazione 'nodes.py') -Algorithm SHA256).Hash
    $h2 = (Get-FileHash (Join-Path $Sorgente 'nodes.py') -Algorithm SHA256).Hash
    $allineato = ($h1 -eq $h2)
    if ($allineato) { Log "esiste ed e' allineato al repo (stesso nodes.py)" }
    else { Warn "esiste ma il contenuto NON e' quello del repo: lo sostituisco" }
} else {
    Log "non installato"
}

if ($Remove) {
    if (-not $installato) { Log "niente da rimuovere"; exit 0 }
    if ($Check) { Log "(check) rimuoverei solo la junction, non il contenuto"; exit 0 }
    # `rmdir` su una junction rimuove il COLLEGAMENTO, non il contenuto: con
    # Remove-Item -Recurse -Force il rischio e' cancellare anche il repo.
    & cmd /c rmdir "$destinazione"
    if ($LASTEXITCODE -eq 0) { Log "junction rimossa (il sorgente nel repo e' intatto)" }
    else { Fail "non sono riuscito a rimuovere $destinazione" }
    exit 0
}

if ($Check) {
    Head "Verdetto"
    if ($allineato) { Log "pronto: i nodi sono installati (ricarica i nodi in ComfyUI)" }
    elseif ($installato) { Warn "installato ma non allineato: lancia lo script senza -Check" }
    else { Log "da installare: lancia lo script senza -Check" }
    exit 0
}

if ($allineato) { exit 0 }

if ($installato) {
    & cmd /c rmdir "$destinazione"
    if (Test-Path $destinazione) {
        Fail "non sono riuscito a rimuovere $destinazione (chiudilo in ComfyUI e riprova)"
        exit 1
    }
}

& cmd /c mklink /J "$destinazione" "$Sorgente"
if ($LASTEXITCODE -ne 0) {
    Fail "mklink non e' riuscito (serve Windows e una cartella locale)"
    exit 1
}
Log "installato: $destinazione -> $Sorgente"
Log "ora ricarica i nodi in ComfyUI (Refresh nel pannello nodi)"

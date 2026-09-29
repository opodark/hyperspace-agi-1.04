# SPDX-License-Identifier: Apache-2.0
# Installa il nodo HyperSpaceSaveJPEG in ComfyUI Desktop su Windows.
param([string]$CustomNodes = "")

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'custom_nodes\hyperspace_save_jpeg.py'
if (-not (Test-Path $source)) { throw "Nodo JPG non trovato: $source" }

if (-not $CustomNodes) {
    $base = Join-Path $env:LOCALAPPDATA 'Comfy-Desktop'
    $matches = @(Get-ChildItem -Path $base -Directory -Recurse -Depth 4 -Filter 'custom_nodes' `
        -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    if ($matches.Count -ne 1) {
        throw "Trovate $($matches.Count) cartelle custom_nodes: indica -CustomNodes <percorso>"
    }
    $CustomNodes = $matches[0]
}
if (-not (Test-Path $CustomNodes -PathType Container)) {
    throw "Cartella custom_nodes non trovata: $CustomNodes"
}
$target = Join-Path $CustomNodes 'hyperspace_save_jpeg.py'
Copy-Item -LiteralPath $source -Destination $target -Force
Write-Host "Nodo JPG installato: $target"
Write-Host 'Riavvia ComfyUI, poi verifica /object_info/HyperSpaceSaveJPEG.'

<#
.SYNOPSIS
    Riconcilia .env con .env.windows.

.DESCRIPTION
    Su una macchina già avviata `.env` esiste e viene copiato da .env.windows
    SOLO la prima volta (Ensure-EnvFile in setup.ps1). `.env` è in .gitignore,
    quindi nessun `git pull` lo aggiorna: senza questo passaggio le modifiche
    fatte a .env.windows non hanno alcun effetto.

    Il default è sola lettura: mostra le differenze e non tocca nulla.
    Con -Apply aggiorna in .env SOLO le chiavi divergenti, preservando ogni
    altra riga e i commenti del file esistente (non è una copia distruttiva:
    le personalizzazioni locali non presenti nel template restano intatte).

.EXAMPLE
    .\scripts\sync_env_windows.ps1
.EXAMPLE
    .\scripts\sync_env_windows.ps1 -Apply
#>
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$template = '.env.windows'
$target   = '.env'

foreach ($f in @($template, $target)) {
    if (-not (Test-Path $f)) { Write-Error "$f non trovato nella directory corrente ($PWD)" }
}

# Chiavi del template: ultima occorrenza vince, i commenti si ignorano.
$wanted = @{}
foreach ($line in (Get-Content $template -Encoding UTF8)) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $wanted[$k.Trim()] = $v
}

$current = @{}
$targetLines = Get-Content $target -Encoding UTF8
foreach ($line in $targetLines) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $current[$k.Trim()] = $v
}

$missing = @()
$differs = @()
foreach ($k in $wanted.Keys | Sort-Object) {
    if (-not $current.ContainsKey($k))      { $missing += $k }
    elseif ($current[$k] -ne $wanted[$k])   { $differs += $k }
}

if (-not $missing -and -not $differs) {
    Write-Host "OK: .env è allineato a $template (nessuna differenza)."
    exit 0
}

if ($missing) {
    Write-Host "`nChiavi presenti in $template e ASSENTI in .env:" -ForegroundColor Yellow
    foreach ($k in $missing) { Write-Host ("  + {0}={1}" -f $k, $wanted[$k]) }
}
if ($differs) {
    Write-Host "`nChiavi con valore DIVERSO:" -ForegroundColor Yellow
    foreach ($k in $differs) {
        Write-Host ("  ~ {0}: .env={1}  ->  {2}" -f $k, $current[$k], $wanted[$k])
    }
}

if (-not $Apply) {
    Write-Host "`nNessuna modifica applicata (dry-run). Rilancia con -Apply per applicare." -ForegroundColor Cyan
    exit 0
}

$out = New-Object System.Collections.Generic.List[string]
foreach ($line in $targetLines) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { $out.Add($line); continue }
    $k = ($line -split '=', 2)[0].Trim()
    if ($wanted.ContainsKey($k)) { $out.Add("$k=$($wanted[$k])") } else { $out.Add($line) }
}
foreach ($k in $missing) { $out.Add("$k=$($wanted[$k])") }

# NON usare Set-Content -Encoding UTF8: su PowerShell 5.1 scrive il BOM e
# Compose leggerebbe la prima chiave come "\ufeffCOMPOSE_PROJECT_NAME".
# UTF8Encoding($false) = niente BOM, come il file originale.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $target).Path, (($out -join "`r`n") + "`r`n"), $utf8NoBom)
Write-Host "`nApplicato: $($missing.Count) chiavi aggiunte, $($differs.Count) aggiornate in .env." -ForegroundColor Green
Write-Host "Ora ricostruisci:  docker compose -f docker-compose.windows.yml up -d --build" -ForegroundColor Green

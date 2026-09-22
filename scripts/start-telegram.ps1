# SPDX-License-Identifier: Apache-2.0
# HyperSpace AGI 1.04 - avvio del driver Telegram (Windows/PowerShell)
#
# Perché esiste: avviare il driver Telegram richiede DUE segreti diversi, e
# copiarli a mano è esattamente il punto in cui si sbaglia (token troncato,
# incollato nel posto sbagliato) — e l'errore si manifesta come un 401 che non
# dice quale dei due lati è sbagliato.
#   1. TELEGRAM_BOT_TOKEN: token del bot da @BotFather, in data\telegram-bot.env
#      (file ignorato da git: il container NON deve poter pubblicare su Telegram)
#   2. CHANNEL_TOKEN: token del canale dentro CHANNEL_CLIENTS (.env), generato
#      con: python scripts\channel_token.py telegram --write
# In più il control-plane deve essere raggiungibile: su Windows è 127.0.0.1:8085,
# la porta canonica pubblicata da hyperspace-gateway (gateway\Caddyfile).
#
# Uso:
#   .\scripts\start-telegram.ps1           # avvia il driver (resta in foreground)
#   .\scripts\start-telegram.ps1 -Check    # non avvia nulla: verifica e basta
#   .\scripts\start-telegram.ps1 -Mention  # parla solo se lo nominano (gruppi con più bot)
#   .\scripts\start-telegram.ps1 -Channel telegram -Url http://127.0.0.1:8085
#
# Nota: questo driver vive NEL repo (scripts\telegram_bot.py) perché non guida un
# browser — usa l'API del bot. I driver su DOM (cam4_chatbot.py, quello Discord)
# restano fuori dal repo, come dice docs/channel.md.

param(
    [string]$Channel = "telegram",
    [string]$Url = "",
    [string]$EnvFile = "data\telegram-bot.env",
    [string]$Python = "",
    [string]$Persona = "",
    [switch]$Mention,
    [switch]$Check
)

$ErrorActionPreference = 'Continue'
# Invoke-WebRequest in PowerShell 5.1 scrive la barra di progresso su stderr:
# qui renderebbe il verdetto illeggibile proprio quando serve leggerlo.
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Log($m)  { Write-Host "[telegram] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]     $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[error]    $m" -ForegroundColor Red }
function Head($m) { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }

function Read-EnvFile {
    # Stessa forma di start.ps1: una riga per chiave, i commenti si ignorano.
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
    # CHANNEL_CLIENTS è "cam4=<token>;telegram=<token>": il token che il driver
    # usa per parlare col CP deve essere quello che il CP si aspetta per quel nome.
    param([string]$Elenco, [string]$Nome)
    foreach ($voce in ($Elenco -split ';')) {
        if (-not $voce.Trim()) { continue }
        $i = $voce.IndexOf('=')
        if ($i -lt 1) { continue }
        if ($voce.Substring(0, $i).Trim() -ieq $Nome) { return $voce.Substring($i + 1).Trim() }
    }
    return ""
}

function Get-StatoHttp {
    # Status HTTP senza eccezioni: 0 = irraggiungibile, che è un'informazione
    # diversa da "risponde 401" e va distinta.
    param([string]$Uri, [hashtable]$Headers = @{})
    try {
        return [int](Invoke-WebRequest -Uri $Uri -Headers $Headers -UseBasicParsing -TimeoutSec 5).StatusCode
    } catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}

$envDati = Read-EnvFile (Join-Path $Root '.env')
$ChannelClients = $envDati['CHANNEL_CLIENTS']
$secret = Read-EnvFile (Join-Path $Root $EnvFile)
$tokenBot = $secret['TELEGRAM_BOT_TOKEN']
$tokenCanale = Get-ChannelToken -Elenco $ChannelClients -Nome $Channel
if (-not $Url) { $Url = 'http://127.0.0.1:8085' }

$problemi = @()

Head "Verifica"
# 1. il token del bot
$percorsoEnv = Join-Path $Root $EnvFile
if (-not (Test-Path $percorsoEnv)) {
    Fail "manca ${EnvFile}: serve una riga TELEGRAM_BOT_TOKEN=<token da @BotFather>"
    $problemi += "token del bot assente"
} elseif (-not $tokenBot) {
    Fail "${EnvFile} esiste ma TELEGRAM_BOT_TOKEN è vuoto"
    $problemi += "token del bot vuoto"
} else {
    Log "token del bot: presente in $EnvFile (non stampato)"
}

# 2. il token del canale, quello che autorizza il driver sul control-plane
if ($tokenCanale) {
    Log "token del canale '$Channel': presente in CHANNEL_CLIENTS"
} else {
    Warn "nessun token per il canale '$Channel' in CHANNEL_CLIENTS (.env)"
    Warn "  genera con: python scripts\channel_token.py $Channel --write"
    $problemi += "token del canale assente"
}

# 3. il bot risponde? (e la privacy mode, che decide se in gruppo legge tutto)
if ($tokenBot) {
    try {
        $me = Invoke-RestMethod -Uri "https://api.telegram.org/bot$tokenBot/getMe" -Method Post -TimeoutSec 10
        Log "bot: @$($me.result.username) (id $($me.result.id)), nome $($me.result.first_name)"
        if ($me.result.can_read_all_group_messages -eq $false) {
            Warn "privacy mode ATTIVA: in gruppo vedrà solo menzioni e comandi"
            Warn "  @BotFather -> /setprivacy -> Disable, oppure rendilo admin del gruppo"
        } else {
            Log "legge tutti i messaggi di gruppo"
        }
    } catch {
        Fail "getMe non ha risposto: token non valido? @BotFather -> /revoke"
        $problemi += "bot non raggiungibile con questo token"
    }
}

# 4. il control-plane: irraggiungibile, 401 e 503 sono tre cose diverse
$statoHealth = Get-StatoHttp -Uri "$Url/health"
Log "control-plane $Url/health -> $statoHealth"
if ($statoHealth -eq 0) {
    Warn "control-plane NON raggiungibile: avvialo con .\scripts\start.ps1 (o docker compose up -d)"
    Warn "  senza CP il driver parte lo stesso ma non ha nessuno che decide: resta muto"
} elseif ($tokenCanale) {
    $statoCanale = Get-StatoHttp -Uri "$Url/channel/status" -Headers @{ "X-Hyperspace-Channel-Token" = $tokenCanale }
    switch ($statoCanale) {
        200 { Log "canale $Channel autorizzato (/channel/status -> 200)" }
        401 { Fail "/channel/status -> 401: il token del driver non è quello che il CP ha in CHANNEL_CLIENTS"
              $problemi += "token del canale non accettato" }
        503 { Warn "/channel/status -> 503: canali disattivati (CHANNEL_ENABLED=false) o nessun canale configurato" }
        default { Warn "/channel/status -> $statoCanale" }
    }
}

# 5. quale identità vedrà il CP (il driver non la legge: la inietta il CP)
$personaFile = $envDati['PERSONA_FILE']
if ($personaFile) {
    Log "identità lato CP: $personaFile (su Windows: data\runtime\data\persona-aurora.json)"
}

if ($Check) {
    Head "Verdetto"
    if ($problemi.Count -eq 0) {
        Log "pronto: .\scripts\start-telegram.ps1  (il driver resta in foreground)"
    } else {
        Fail "non pronto: " + ($problemi -join '; ')
    }
    exit 0
}

if ($problemi -contains "token del bot assente" -or $problemi -contains "token del bot vuoto") {
    Fail "senza TELEGRAM_BOT_TOKEN il driver non parte"
    exit 1
}

$env:TELEGRAM_BOT_TOKEN = $tokenBot
$env:CHANNEL_URL = $Url
if ($tokenCanale) { $env:CHANNEL_TOKEN = $tokenCanale }
$env:CHANNEL_NAME = $Channel
# Gruppo con più bot: si risponde solo se lo nominano (o se rispondono a un suo
# messaggio). I messaggi degli altri bot si ignorano comunque: vedi docs/channel.md.
if ($Mention) { $env:TELEGRAM_REQUIRE_MENTION = '1' }
if (-not $env:CHANNEL_TIMEOUT_S) { $env:CHANNEL_TIMEOUT_S = '35' }
$env:HYPERSPACE_REPO = $Root
if ($Persona) {
    $env:AURORA_PERSONA_FILE = (Resolve-Path $Persona).Path
}

if (-not $Python) {
    $candidato = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path $candidato) { $Python = $candidato } else { $Python = 'py' }
}

Head "Driver"
$statoMention = if ($Mention -or $env:TELEGRAM_REQUIRE_MENTION -eq '1') { 'solo se nominata' } else { 'automatica' }
Log "avvio scripts\telegram_bot.py con $Python (CP=$Url, canale=$Channel, risposta=$statoMention)"
& $Python (Join-Path $Root 'scripts\telegram_bot.py')


# SPDX-License-Identifier: Apache-2.0
# HyperSpace AGI 1.04 - avvio completo con un comando (Windows/PowerShell)
#
# Perché esiste: finora "avviare tutto" erano quattro comandi diversi (setup,
# docker compose up, profilo GPU, driver del canale) e nessuno diceva se i
# servizi erano DAVVERO su. Qui c'è un comando, e alla fine un verdetto.
#
# Uso:
#   .\scripts\start.ps1                     # avvia lo stack e verifica
#   .\scripts\start.ps1 -Profile nvidia     # aggiunge un profilo GPU del compose
#   .\scripts\start.ps1 -NoBuild            # avvio veloce senza ricostruire
#   .\scripts\start.ps1 -Check              # non avvia nulla: dice solo cosa gira
#   .\scripts\start.ps1 -Driver             # avvia anche il driver del canale
#   .\scripts\start.ps1 -Stop               # ferma tutto
#
# Il driver (es. cam4_chatbot.py) è FUORI dal repo: si indica con -DriverPath,
# con la variabile CHANNEL_DRIVER_PATH, o si lascia vuoto per non avviarlo.

param(
    [string]$Compose = "",
    [string]$Profile = "",
    [string]$Channel = "cam4",
    [string]$DriverPath = "",
    [string]$Python = "",
    [int]$Timeout = 240,
    [switch]$Check,
    [switch]$Stop,
    [switch]$Driver,
    [switch]$NoBuild,
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
# Invoke-WebRequest in PowerShell 5.1 scrive la barra di progresso su stderr: qui
# renderebbe il verdetto illeggibile proprio quando serve leggerlo.
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Log($m)   { if (-not $Quiet) { Write-Host "[start] $m" -ForegroundColor Green } }
function Warn($m)  { Write-Host "[warn]  $m" -ForegroundColor Yellow }
function Fail($m)  { Write-Host "[error] $m" -ForegroundColor Red; exit 1 }
function Head($m)  { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }

function Resolve-ComposeFile {
    # Su Windows il file dedicato è quello allineato a .env.windows; altrove
    # docker-compose.yml. La scelta è esplicita e stampata: quale stack parte
    # non deve essere un indovinello quando qualcosa non risponde.
    param([string]$Richiesto)
    if ($Richiesto) {
        if (-not (Test-Path $Richiesto)) { Fail "File compose non trovato: $Richiesto" }
        return (Resolve-Path $Richiesto).Path
    }
    $candidati = @()
    if ($env:OS -eq 'Windows_NT') { $candidati += 'docker-compose.windows.yml' }
    $candidati += 'docker-compose.yml'
    foreach ($c in $candidati) { if (Test-Path $c) { return (Resolve-Path $c).Path } }
    Fail "Nessun docker-compose*.yml nella cartella $Root"
}

function Resolve-ComposeCmd {
    # docker compose v2 (plugin) o docker-compose v1: entrambi esistono in giro.
    $null = docker --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        $null = docker compose version 2>$null
        if ($LASTEXITCODE -eq 0) { return @('docker', 'compose') }
    }
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) { return @('docker-compose') }
    return $null
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    $base = @('-f', $script:ComposeFile)
    if ($Profile) { $base += @('--profile', $Profile) }
    $cmd = $script:ComposeBin
    if ($cmd.Count -gt 1) { & $cmd[0] $cmd[1] @base @ComposeArgs } else { & $cmd[0] @base @ComposeArgs }
}

function Read-EnvFile {
    # Lettura minimale di KEY=VALUE: serve per CHANNEL_CLIENTS e le porte, non per
    # interpretare tutte le regole di docker compose.
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    foreach ($line in (Get-Content -Path $Path)) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $i = $line.IndexOf('=')
        $k = $line.Substring(0, $i).Trim()
        $v = $line.Substring($i + 1).Trim().Trim('"')
        if ($v -match '\s{2,}#') { $v = ($v -split '\s{2,}#')[0].Trim() }
        if ($k) { $map[$k] = $v }
    }
    return $map
}

function Restart-GatewayIfAny {
    # Caddy risolve gli upstream all'AVVIO: dopo una ricostruzione di
    # hyperspace-core tiene in cache il vecchio IP del container e risponde 502
    # ("Gateway non valido") anche se il control-plane e' sanissimo. Un riavvio
    # del gateway (un secondo) riallinea tutto e toglie di mezzo un errore che
    # sembra "il CP e' morto" e non lo e'.
    $esiste = (& docker ps -a --format '{{.Names}}' 2>$null) -contains 'hyperspace-gateway'
    if (-not $esiste) { return }
    Log "riallineo il gateway (gli upstream di Caddy si risolvono all'avvio)"
    & docker restart hyperspace-gateway *> $null
}

function Get-HttpStatus {
    param([string]$Url, [int]$TimeoutSec = 4)
    try {
        return [int](Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec).StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

# --- PREFLIGHT ----------------------------------------------------------------
$script:ComposeFile = Resolve-ComposeFile -Richiesto $Compose
$script:ComposeBin = Resolve-ComposeCmd
if (-not $script:ComposeBin) {
    Fail "Docker non trovato (o il plugin 'docker compose' non e attivo). Avvia Docker Desktop e riprova."
}

if (-not (Test-Path '.env')) {
    # In -Check il file mancante non e un errore: "dimmi cosa gira" deve
    # funzionare anche mentre si sta ancora preparando l'ambiente.
    if ($Check) {
        Warn ".env mancante: uso i default (canali e porte non saranno letti dal file)"
    } elseif (Test-Path '.env.windows') {
        Fail ".env mancante. Su Windows: Copy-Item .env.windows .env (poi rileggi i valori con attenzione: i segreti sono tuoi)."
    } else {
        Fail ".env mancante: copia .env.example (o .env.windows) in .env."
    }
}

$EnvMap = Read-EnvFile -Path '.env'
$CpPort = if ($EnvMap['CONTROL_PLANE_PORT']) { $EnvMap['CONTROL_PLANE_PORT'] } else { '8085' }
$CpBase = "http://127.0.0.1:$CpPort"
$ChannelClients = $EnvMap['CHANNEL_CLIENTS']

Head "HyperSpace - avvio"
Log "compose: $script:ComposeFile"
if ($Profile) { Log "profilo: $Profile" }
Log "control-plane: $CpBase"

# --- STOP ---------------------------------------------------------------------
if ($Stop) {
    Log "fermo i servizi (i volumi restano: l'Identita e la memoria sopravvivono)"
    Invoke-Compose @('down')
    Log "fatto. Per ripartire: .\scripts\start.ps1"
    exit 0
}

# --- UP -----------------------------------------------------------------------
if (-not $Check) {
    $up = @('up', '-d')
    if (-not $NoBuild) { $up += '--build' }
    Log "avvio container ($($up -join ' '))"
    Invoke-Compose $up
    if ($LASTEXITCODE -ne 0) { Fail "docker compose up non e riuscito (vedi l'output sopra)." }
    Restart-GatewayIfAny
}

# --- ATTESA: il control-plane è la porta d'ingresso, quindi si aspetta lui ----
if (-not $Check) {
    Log "attendo $CpBase/health (max ${Timeout}s)"
    $scaduto = (Get-Date).AddSeconds($Timeout)
    $pronto = $false
    while ((Get-Date) -lt $scaduto) {
        if ((Get-HttpStatus -Url "$CpBase/health" -TimeoutSec 3) -eq 200) { $pronto = $true; break }
        Start-Sleep -Seconds 3
    }
    if (-not $pronto) {
        Warn "il control-plane non ha risposto entro ${Timeout}s"
        Invoke-Compose @('ps')
        Warn "log utili: $(($script:ComposeBin -join ' ')) -f $script:ComposeFile logs --tail 60"
        Warn "se il container e' 'Up' ma qui non risponde, il gateway ha in cache il vecchio IP: docker restart hyperspace-gateway"
        exit 1
    }
    Log "control-plane pronto"
}

# --- VERIFICA -----------------------------------------------------------------
function Show-Services {
    Head "Servizi"
    # Il control-plane è obbligatorio (senza di lui non c'è nulla da comandare);
    # gli altri sono facoltativi: un Obsidian spento non e un guasto.
    $servizi = @(
        @{ Nome = 'Control Plane  '; Url = "$CpBase/health";              Obbligatorio = $true },
        @{ Nome = 'Dashboard      '; Url = "$CpBase/dashboard";           Obbligatorio = $true },
        @{ Nome = 'Open WebUI     '; Url = 'http://127.0.0.1:3000';       Obbligatorio = $false },
        @{ Nome = 'Registry       '; Url = 'http://127.0.0.1:8086/nodes'; Obbligatorio = $false },
        @{ Nome = 'Node 1         '; Url = 'http://127.0.0.1:8081/status'; Obbligatorio = $false },
        @{ Nome = 'Bridge         '; Url = 'http://127.0.0.1:8099';       Obbligatorio = $false },
        @{ Nome = 'Memory Graph   '; Url = 'http://127.0.0.1:8090/status'; Obbligatorio = $false },
        @{ Nome = 'Obsidian       '; Url = 'http://127.0.0.1:8091';       Obbligatorio = $false },
        @{ Nome = 'SearXNG        '; Url = 'http://127.0.0.1:8092';       Obbligatorio = $false },
        @{ Nome = 'Gateway        '; Url = 'http://127.0.0.1:8095';       Obbligatorio = $false }
    )
    $guasti = 0
    foreach ($s in $servizi) {
        $stato = Get-HttpStatus -Url $s.Url
        if ($stato -ge 200 -and $stato -lt 500) {
            Write-Host ("  {0} {1}  {2}" -f $s.Nome, ($stato.ToString().PadLeft(3)), $s.Url) -ForegroundColor Green
        } elseif ($s.Obbligatorio) {
            $guasti++
            Write-Host ("  {0} ---  {1}  <-- non risponde" -f $s.Nome, $s.Url) -ForegroundColor Red
        } else {
            Write-Host ("  {0} ---  {1}  (spento)" -f $s.Nome, $s.Url) -ForegroundColor DarkGray
        }
    }
    return $guasti
}

function Show-Channels {
    Head "Canali esterni"
    if (-not $ChannelClients) {
        Write-Host "  nessun canale configurato: imposta CHANNEL_CLIENTS in .env" -ForegroundColor DarkGray
        Write-Host "  genera un token: python scripts\channel_token.py" -ForegroundColor DarkGray
        return
    }
    try {
        $stato = Invoke-RestMethod -Uri "$CpBase/channel/status" -TimeoutSec 5
        Write-Host ("  canali: {0}" -f (($stato.policy.channels) -join ', ')) -ForegroundColor Green
        Write-Host ("  modello: {0}   max_tokens: {1}" -f $stato.model, $stato.max_tokens)
        Write-Host ("  guardia: autori={0} spam attivi={1} mute>={2} ban>={3}" -f `
            $stato.guard.authors_tracked, $stato.guard.spam_authors_active, `
            $stato.guard.strike_mute, $stato.guard.strike_ban)
        foreach ($problema in $stato.policy.problems) {
            Write-Host ("  problema: {0}" -f $problema) -ForegroundColor Yellow
        }
    } catch {
        Warn "GET /channel/status non ha risposto: $($_.Exception.Message)"
    }
}

function Show-Persona {
    Head "Identita e sogno"
    try {
        $p = Invoke-RestMethod -Uri "$CpBase/persona" -TimeoutSec 5
        Write-Host ("  {0} ({1})  attiva={2}  annotazioni={3}/{4}" -f `
            $p.name, $p.kind, $p.enabled, $p.observation_count, $p.max_observations) -ForegroundColor Green
        if ($p.boundaries) {
            Write-Host ("  confini: {0}" -f (($p.boundaries | Select-Object -First 2) -join ' | '))
        }
        $d = $p.dream
        $acceso = if ($d.enabled) { 'attivo' } else { 'spento' }
        Write-Host ("  sogno: {0}  finestra={1}-{2}  in attesa di revisione={3}" -f `
            $acceso, $d.window[0], $d.window[1], $d.pending_review)
        if ($d.pending_review -gt 0) {
            Write-Host "  rivedi le proposte su GET /persona/dreams" -ForegroundColor Yellow
            Write-Host "  promuovi: POST /persona/dreams/<id>/review (Authorization: Bearer DREAM_REVIEW_TOKEN)" -ForegroundColor Yellow
        }
    } catch {
        Warn "GET /persona non ha risposto: $($_.Exception.Message)"
    }
}

$guasti = Show-Services
Show-Channels
Show-Persona

# --- DRIVER DEL CANALE (opzionale) --------------------------------------------
function Resolve-DriverPath {
    if ($DriverPath) { return $DriverPath }
    if ($env:CHANNEL_DRIVER_PATH) { return $env:CHANNEL_DRIVER_PATH }
    foreach ($c in @((Join-Path $HOME 'cam4_chatbot.py'), 'cam4_chatbot.py')) {
        if (Test-Path $c) { return (Resolve-Path $c).Path }
    }
    return ""
}

function Resolve-DriverPython {
    # Il driver ha bisogno di `ollama` e `playwright`. L'interprete del repo
    # (il venv in .venv) NON li ha: serve al control-plane. Il primo `python`
    # del PATH, quindi, e' quasi sempre quello sbagliato — e il sintomo e'
    # "import delle librerie fallito" a bot avviato, quando te ne accorgi
    # tardi. Qui si prova e si sceglie quello che li ha davvero.
    param([string]$Esplicito)
    $probe = 'import ollama, playwright'
    $candidati = @()
    if ($Esplicito) { $candidati += ,@($Esplicito) }
    elseif ($env:CHANNEL_DRIVER_PYTHON) { $candidati += ,@($env:CHANNEL_DRIVER_PYTHON) }
    else {
        $candidati += ,@('py', '-3')    # il piu' probabile su Windows
        $candidati += ,@('python')
        $candidati += ,@('py')
    }
    foreach ($cand in $candidati) {
        $exe = $cand[0]
        $pre = @()
        if ($cand.Count -gt 1) { $pre = $cand[1..($cand.Count - 1)] }
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $null = & $exe @pre -c $probe 2>$null
        if ($LASTEXITCODE -eq 0) { return @{ Exe = $exe; Args = $pre } }
    }
    return $null
}

function Get-ChannelToken([string]$Elenco, [string]$Nome) {
    # CHANNEL_CLIENTS è "cam4=<token>;cb=<token>": il token che il driver usa per
    # parlare col CP deve essere quello che il CP si aspetta per quel nome.
    foreach ($voce in ($Elenco -split ';')) {
        if (-not $voce.Trim()) { continue }
        $i = $voce.IndexOf('=')
        if ($i -lt 1) { continue }
        if ($voce.Substring(0, $i).Trim() -ieq $Nome) { return $voce.Substring($i + 1).Trim() }
    }
    return ""
}

if ($Driver -and -not $Check) {
    Head "Driver del canale"
    $eseguibile = Resolve-DriverPath
    if (-not $eseguibile) {
        Warn "driver non trovato: usa -DriverPath <percorso> o imposta CHANNEL_DRIVER_PATH"
    } else {
        $token = Get-ChannelToken -Elenco $ChannelClients -Nome $Channel
        if ($token) {
            $env:CHANNEL_TOKEN = $token
        } else {
            # Nessun token: si toglie anche quello eventualmente rimasto nella
            # sessione, altrimenti l'avvertenza qui sotto sarebbe falsa.
            Remove-Item Env:CHANNEL_TOKEN -ErrorAction SilentlyContinue
            Warn "nessun token per il canale '$Channel': il driver usera il modello locale (fallback), non il CP"
        }
        # L'ambiente passa al figlio: il driver trova URL e token già giusti,
        # senza copiarli a mano (ed è il modo più semplice per sbagliare).
        $env:CHANNEL_URL = $CpBase
        $env:CHANNEL_NAME = $Channel
        if (-not $env:CHANNEL_TIMEOUT_S) {
            # Un battuta con 20 messaggi di contesto arriva in ~16s: il default
            # del driver (20s) lascia troppo poco margine e al primo intoppo
            # scivola sul modello locale senza dirlo abbastanza forte. Si
            # allarga, e si stampa, cosi' resta una scelta e non un mistero.
            $env:CHANNEL_TIMEOUT_S = '35'
            Log "timeout del canale: 35s (imposta CHANNEL_TIMEOUT_S per cambiarlo)"
        }
        $env:HYPERSPACE_REPO = $Root
        $personaFile = Join-Path $Root 'data\runtime\data\persona-aurora.json'
        if (-not (Test-Path $personaFile)) { $personaFile = Join-Path $Root 'data\persona-aurora.json' }
        if (Test-Path $personaFile) {
            $env:AURORA_PERSONA_FILE = $personaFile
            Log "identita del driver: $personaFile"
        }
        $interprete = Resolve-DriverPython -Esplicito $Python
        if (-not $interprete) {
            if ($Python) {
                Warn "-Python $Python non ha ollama+playwright: usa l'interprete che li ha (di solito: py -3)"
            } else {
                Warn "nessun interprete con ollama+playwright trovato: passa -Python <percorso>"
            }
        } else {
            $etichetta = (@($interprete.Exe) + $interprete.Args) -join ' '
            Log "avvio $eseguibile con $etichetta (canale=$Channel, CP=$CpBase)"
            Start-Process -FilePath $interprete.Exe `
                -ArgumentList ($interprete.Args + @("`"$eseguibile`"")) `
                -WorkingDirectory (Split-Path -Parent $eseguibile)
            Log "driver avviato in una finestra separata (diagnosi senza browser: $etichetta <driver> --check)"
        }
    }
}

Head "Fatto"
Write-Host "  dashboard: $CpBase/dashboard" -ForegroundColor Green
Write-Host "  Setup -> Canali esterni: token, moderazione, modello del canale" -ForegroundColor DarkGray
if (-not $Driver) { Write-Host "  driver del canale: .\scripts\start.ps1 -Driver" -ForegroundColor DarkGray }
if ($guasti) { exit 1 } else { exit 0 }

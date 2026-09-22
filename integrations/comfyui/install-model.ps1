# SPDX-License-Identifier: Apache-2.0
# Installa i pesi del modello d'immagine (Qwen-Image 2.1 uncensored, GGUF) dentro
# la cartella modelli di ComfyUI.
#
# Perche' uno script e non un download a mano: i pesi sono 3 file (~15 GB) e un
# errore qui non si vede. Un GGUF troncato non da' un errore: da' un'immagine
# rumorosa, o un OOM a meta' campionamento che si porta dietro anche il modello
# caricato (docs/comfyui.md). Qui il file prende il NOME VERO solo dopo che
# l'impronta SHA-256 corrisponde a quella del manifest: fino a quel momento resta
# `<nome>.parziale`, che ComfyUI non vede nemmeno.
#
# Perche' il manifest e non i nomi qui dentro: `integrations/comfyui/modelli.json`
# e' letto anche da `tests/test_comfyui_modelli.py`, che lo confronta con il grafo
# di `shared/image_jobs.py`. Rinominare un file da un lato solo fa fallire un test,
# invece di far fallire un job in silenzio.
#
# Uso:
#   .\integrations\comfyui\install-model.ps1 -Check          # non scarica: dice cosa farebbe
#   .\integrations\comfyui\install-model.ps1                 # i pesi del quant di default
#   .\integrations\comfyui\install-model.ps1 -Quant Q4_K_M   # un altro quant (meno VRAM)
#   .\integrations\comfyui\install-model.ps1 -SoloGGUF       # solo il diffusion
#   .\integrations\comfyui\install-model.ps1 -Progresso      # barra di avanzamento di curl
#
# Un download interrotto si RIPRENDE: rilanciare lo script continua dal byte dove
# era arrivato (curl -C -), non ricomincia da zero.

param(
    [string]$Quant = "",
    [string]$Modelli = "",
    [string]$CustomNodes = "",
    [switch]$Check,
    [switch]$SoloGGUF,
    [switch]$Forza,
    [switch]$Progresso
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Manifest = Join-Path $PSScriptRoot 'modelli.json'

function Log($m)  { Write-Host "[modelli] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn]    $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[error]   $m" -ForegroundColor Red }

function Leggi-Manifest {
    # Un manifest illeggibile e' un guasto dello script, non dell'utente: si dice
    # cosa manca invece di proseguire con valori vuoti (che non scaricherebbero
    # nulla e direbbero "fatto").
    if (-not (Test-Path $Manifest)) { Fail "manifest non trovato: $Manifest"; return $null }
    try {
        $letto = Get-Content $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Fail "manifest illeggibile ($Manifest): $($_.Exception.Message)"
        return $null
    }
    foreach ($campo in 'repo', 'revisione', 'unet', 'accompagnatori') {
        if (-not $letto.$campo) { Fail "manifest senza '$campo': $Manifest"; return $null }
    }
    return $letto
}

function Trova-CartellaModelli {
    # Dove stanno i pesi lo decide ComfyUI, non lo script: si chiede alla sua
    # configurazione. Indovinare un percorso significa scaricare 15 GB nel posto
    # sbagliato e poi leggere "il file non c'e'" nel grafo.
    param([string]$Esplicito)
    if ($Esplicito) {
        if (Test-Path $Esplicito) { return (Resolve-Path $Esplicito).Path }
        Fail "cartella modelli indicata non trovata: $Esplicito"
        return ""
    }
    $impostazioni = Join-Path $env:APPDATA 'Comfy Desktop\settings.json'
    if (Test-Path $impostazioni) {
        try {
            $json = Get-Content $impostazioni -Raw | ConvertFrom-Json
            foreach ($voce in @($json.modelsDirs)) {
                if ($voce -and (Test-Path $voce)) { return (Resolve-Path $voce).Path }
            }
        } catch {
            Warn "settings.json illeggibile: $($_.Exception.Message)"
        }
    }
    $condivisi = Join-Path $env:APPDATA 'Comfy Desktop\shared_model_paths.yaml'
    if (Test-Path $condivisi) {
        $riga = Select-String -Path $condivisi -Pattern '^\s*base_path:\s*' | Select-Object -First 1
        if ($riga) {
            $percorso = ($riga.Line -replace '^\s*base_path:\s*', '') -replace '["'']', '' -replace '/', '\'
            $percorso = $percorso.Trim()
            if ($percorso -and (Test-Path $percorso)) { return (Resolve-Path $percorso).Path }
        }
    }
    $serie = Join-Path $env:LOCALAPPDATA 'Comfy-Desktop\ComfyUI-Shared\models'
    if (Test-Path $serie) { return (Resolve-Path $serie).Path }
    return ""
}

function Trova-CustomNodes {
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

function Verifica-LettoreGGUF {
    # Un file presente non basta: se il lettore non conosce l'architettura il grafo
    # muore con `Unknown model architecture!` DOPO aver speso 15 GB di download.
    param([string]$CustomNodes)
    $problemi = @()
    if (-not $CustomNodes) {
        $problemi += "custom_nodes non trovato: il controllo del lettore GGUF si salta (-CustomNodes <percorso\custom_nodes>)"
        return $problemi
    }
    $pacchetto = Join-Path $CustomNodes 'ComfyUI-GGUF'
    if (-not (Test-Path $pacchetto)) {
        $problemi += "ComfyUI-GGUF non installato: il nodo UnetLoaderGGUF non esiste"
        $problemi += "  git clone https://github.com/leejet/ComfyUI-GGUF `"$pacchetto`"   # fork leejet, non city96"
        return $problemi
    }
    $conosce = $false
    foreach ($f in @((Join-Path $pacchetto 'loader.py'), (Join-Path $pacchetto 'nodes.py'))) {
        if ((Test-Path $f) -and (Select-String -Path $f -Pattern 'qwen_image21' -Quiet)) { $conosce = $true }
    }
    if ($conosce) { Log "lettore GGUF: 'qwen_image21' supportato ($pacchetto)" }
    else {
        $problemi += "ComfyUI-GGUF non conosce 'qwen_image21' (Qwen-Image 2.1): i pesi si installano, il grafo fallisce"
        $problemi += "  aggiorna al fork leejet: https://github.com/leejet/ComfyUI-GGUF"
    }
    return $problemi
}

function Head($m) { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }
function Giga($byte) { return [math]::Round(($byte / 1GB), 2) }

function Installa-Peso {
    # Un peso alla volta: (1) c'e' gia' e l'impronta torna -> non si tocca;
    # (2) c'e' ma l'impronta NO -> ci si ferma (e' un file di qualcun altro:
    # cancellarlo di iniziativa sarebbe distruggere dati); (3) manca o e'
    # parziale -> si scarica in `<nome>.parziale` e si rinomina solo a impronta
    # verificata.
    param($Voce, [string]$CartellaModelli, [string]$UrlBase, [switch]$Forza,
          [switch]$Anteprima, [switch]$Progresso)

    $nome = Split-Path -Leaf $Voce.file
    $cartella = Join-Path $CartellaModelli $Voce.destinazione
    $destinazione = Join-Path $cartella $nome
    $parziale = "$destinazione.parziale"
    $esito = [pscustomobject]@{ nome = $nome; stato = 'da_fare'; byte = $Voce.byte }

    if ((Test-Path $destinazione) -and -not $Forza) {
        $lunghezza = (Get-Item $destinazione).Length
        if ($lunghezza -eq $Voce.byte) {
            $impronta = (Get-FileHash $destinazione -Algorithm SHA256).Hash.ToLower()
            if ($impronta -eq $Voce.sha256) {
                Log "presente e verificato: $nome ($(Giga $lunghezza) GB)"
                $esito.stato = 'presente'
                return $esito
            }
            Warn "impronta diversa da quella attesa per $nome"
            Warn "  attesa: $($Voce.sha256)"
            Warn "  letta:  $impronta"
        } else {
            Warn "$nome misura $lunghezza byte invece di $($Voce.byte)"
        }
        Fail "mi fermo: $destinazione esiste ma non e' il file del manifest"
        Fail "  se e' un doppione tuo, toglilo; se vuoi sostituirlo: -Forza"
        $esito.stato = 'fallito'
        return $esito
    }

    if ($Anteprima) { return $esito }

    if (-not (Test-Path $cartella)) { New-Item -ItemType Directory -Path $cartella -Force | Out-Null }
    if ($Forza -and (Test-Path $parziale)) { Remove-Item $parziale -Force }

    if (Test-Path $parziale) {
        $ripreso = (Get-Item $parziale).Length
        if ($ripreso -gt $Voce.byte) {
            Fail "$parziale e' piu' grande del previsto ($ripreso > $($Voce.byte)): non e' un download troncato"
            Fail "  toglilo e rilancia (o usa -Forza per cancellarlo e ricominciare)"
            $esito.stato = 'fallito'
            return $esito
        }
        if ($ripreso -gt 0) { Log "riprendo $nome da $(Giga $ripreso) GB" }
    }

    # La revisione e' un commit, non 'main': la URL puntata a un commit serve
    # sempre lo stesso byte, quindi le impronte del manifest restano valide.
    $url = "$UrlBase/$($Voce.file)?download=true"
    Log "scarico $nome ($(Giga $Voce.byte) GB)"
    $argomenti = @('-L', '--fail', '--retry', '5', '--retry-delay', '5', '--retry-all-errors',
                   '-C', '-', '-o', $parziale)
    if (-not $Progresso) { $argomenti += '--no-progress-meter' }
    $argomenti += $url
    & curl.exe @argomenti
    if ($LASTEXITCODE -ne 0) {
        Fail "curl ha fallito (codice $LASTEXITCODE): il file resta come .parziale e si riprende al prossimo giro"
        $esito.stato = 'fallito'
        return $esito
    }

    $lunghezza = (Get-Item $parziale).Length
    if ($lunghezza -ne $Voce.byte) {
        Fail "$nome misura $lunghezza byte, attesi $($Voce.byte): download incompleto (rilancia per riprendere)"
        $esito.stato = 'fallito'
        return $esito
    }
    $impronta = (Get-FileHash $parziale -Algorithm SHA256).Hash.ToLower()
    if ($impronta -ne $Voce.sha256) {
        Fail "impronta SHA-256 diversa per ${nome}: il file NON viene installato"
        Fail "  attesa: $($Voce.sha256)"
        Fail "  letta:  $impronta"
        $esito.stato = 'fallito'
        return $esito
    }
    Move-Item -Path $parziale -Destination $destinazione -Force
    Log "installato e verificato: $nome -> $cartella"
    $esito.stato = 'installato'
    return $esito
}


# ── Il piano: cosa serve, dove va, quanto pesa ───────────────────────────────
$manifest = Leggi-Manifest
if (-not $manifest) { exit 1 }

$tutti = @($manifest.unet.varianti.PSObject.Properties.Name)
if (-not $Quant) { $Quant = $manifest.unet.quant_default }
if ($tutti -notcontains $Quant) {
    Fail "quant '$Quant' non previsto dal manifest. Disponibili: $($tutti -join ', ')"
    exit 1
}
$quantScelto = $manifest.unet.varianti.$Quant

$cartellaModelli = Trova-CartellaModelli -Esplicito $Modelli
if (-not $cartellaModelli) {
    Fail "non trovo la cartella modelli di ComfyUI: passala con -Modelli <percorso\models>"
    exit 1
}

# Il GGUF va dove UnetLoaderGGUF lo cerca, i due accompagnatori dove li cercano
# CLIPLoader e VAELoader: sono tre cartelle diverse, non una.
$pesi = @([pscustomobject]@{ ruolo = 'unet'; file = $quantScelto.file;
                            destinazione = $manifest.unet.destinazione;
                            sha256 = $quantScelto.sha256; byte = $quantScelto.byte })
if (-not $SoloGGUF) {
    foreach ($accompagnatore in $manifest.accompagnatori) { $pesi += $accompagnatore }
}

$urlBase = "https://huggingface.co/$($manifest.repo)/resolve/$($manifest.revisione)"

Head "Dove"
Log "cartella modelli:  $cartellaModelli"
Log "sorgente:          $($manifest.repo) @ $($manifest.revisione.Substring(0, 8))"
Log "licenza dei pesi:  $($manifest.licenza)"
Log "quant:             $Quant  (default del progetto: $($manifest.unet.quant_default))"
if ($Quant -ne $manifest.unet.quant_default) {
    Warn "il ponte usa il quant di default ($($manifest.unet.quant_default)):"
    Warn "  questo resta installato ma va chiesto per singolo job (campo 'modello')"
}

Head "Spazio"
$daScaricare = 0
foreach ($peso in $pesi) {
    $destinazione = Join-Path (Join-Path $cartellaModelli $peso.destinazione) (Split-Path -Leaf $peso.file)
    if (-not (Test-Path $destinazione)) { $daScaricare += $peso.byte }
    elseif ((Get-Item $destinazione).Length -ne $peso.byte) { $daScaricare += $peso.byte }
}
$radice = (Get-Item $cartellaModelli).PSDrive.Name
$libero = (Get-PSDrive -Name $radice).Free
Log "da scaricare:      $(Giga $daScaricare) GB"
Log "libero su ${radice}:       $(Giga $libero) GB"
if ($daScaricare -gt 0 -and ($libero - $daScaricare) -lt 2GB) {
    Fail "spazio insufficiente: servono anche ~2 GB di lavoro per il download"
    exit 1
}

Head "Pesi"
$esiti = @()
foreach ($peso in $pesi) {
    $esiti += Installa-Peso -Voce $peso -CartellaModelli $cartellaModelli -UrlBase $urlBase `
                           -Forza:$Forza -Anteprima:$Check -Progresso:$Progresso
}

Head "Lettore GGUF"
$customNodes = Trova-CustomNodes -Esplicito $CustomNodes
$problemi = @(Verifica-LettoreGGUF -CustomNodes $customNodes)
foreach ($esito in $esiti) { if ($esito.stato -eq 'fallito') { $problemi += "peso non installato: $($esito.nome)" } }

Head "Verdetto"
$mancanti = @($esiti | Where-Object { $_.stato -eq 'da_fare' })
if ($Check) {
    foreach ($esito in $mancanti) { Log "da scaricare: $($esito.nome) ($(Giga $esito.byte) GB)" }
    if ($problemi.Count -eq 0) {
        if ($mancanti.Count -eq 0) { Log "pronto: i pesi ci sono e le impronte tornano" }
        else { Log "pronto: lancia lo script senza -Check per scaricare" }
    }
} elseif ($problemi.Count -eq 0) {
    Log "pronto: i pesi sono installati e verificati"
    Log "ComfyUI legge l'elenco dei file all'avvio: riavvialo (o aggiorna) prima di generare"
    Log "verifica del ponte: python integrations\comfyui\comfy_bridge.py --check"
} else {
    Log "pesi a posto, ma il ponte non e' pronto: vedi qui sotto"
}
if ($problemi.Count -gt 0) {
    Write-Host ""
    Write-Host "PROBLEMI:" -ForegroundColor Yellow
    foreach ($problema in $problemi) { Warn $problema }
    exit 1
}
exit 0


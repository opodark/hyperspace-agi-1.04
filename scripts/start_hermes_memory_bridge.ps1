param(
    [string]$BindAddress = "127.0.0.1",
    [int]$Port = 8098,
    [string]$TokenFile = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $TokenFile) {
    $TokenFile = Join-Path $repoRoot "data\hermes-memory.token"
}
$python = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
$bridge = Join-Path $PSScriptRoot "hermes_memory_bridge.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Hermes Python not found at $python. Install Hermes Agent first."
}
if (-not (Test-Path -LiteralPath $TokenFile -PathType Leaf)) {
    throw "Token file not found at $TokenFile. Generate a 64-hex token before starting the bridge."
}

$env:HERMES_HOME = Join-Path $env:LOCALAPPDATA "hermes"
$env:HERMES_MEMORY_TOKEN_FILE = (Resolve-Path -LiteralPath $TokenFile).Path
& $python $bridge --host $BindAddress --port $Port --token-file $env:HERMES_MEMORY_TOKEN_FILE

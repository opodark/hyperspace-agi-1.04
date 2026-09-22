# SPDX-License-Identifier: Apache-2.0
param(
    [string]$GatewayUrl = "https://mesh.zerozerocomputer.it",
    [string]$OutputDir = "dist/web-node-aruba"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$target = [IO.Path]::GetFullPath((Join-Path $repo $OutputDir))
$distRoot = [IO.Path]::GetFullPath((Join-Path $repo "dist"))
if (-not $target.StartsWith($distRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDir deve restare dentro $distRoot"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $target "src") | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "web-node/join.html") `
    -Destination (Join-Path $target "index.html") -Force
foreach ($name in @("capabilities.js", "index.js", "protocol.js", "task-runner.js", "transport.js", "webgpu.js")) {
    Copy-Item -LiteralPath (Join-Path $repo "web-node/src/$name") `
        -Destination (Join-Path $target "src/$name") -Force
}

$escapedUrl = $GatewayUrl.TrimEnd("/").Replace("\", "\\").Replace('"', '\"')
$config = @"
// SPDX-License-Identifier: Apache-2.0
window.HYPERSPACE_JOIN = Object.freeze({
  gatewayUrl: "$escapedUrl",
  siteName: "ZeroZeroComputer",
  meshName: "HyperSpace",
});
"@
[IO.File]::WriteAllText((Join-Path $target "join-config.js"), $config,
    [Text.UTF8Encoding]::new($false))

$zip = "$target.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $target "*") -DestinationPath $zip
Write-Host "Pacchetto Aruba: $zip"

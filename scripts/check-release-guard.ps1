param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$RuntimeFiles = @(
    "docker-compose.yml",
    "docker-compose.llamacpp.yml",
    ".env.example",
    ".env.llamacpp.example"
)

$latestHits = @()
foreach ($file in $RuntimeFiles) {
    $path = Join-Path $Root $file
    if (Test-Path $path) {
        $latestHits += Select-String -Path $path -Pattern ":latest" -SimpleMatch
    }
}
if ($latestHits.Count -gt 0) {
    $latestHits | ForEach-Object { Write-Error "$($_.Path):$($_.LineNumber): $($_.Line)" }
    exit 1
}

$composePath = Join-Path $Root "docker-compose.yml"
$inSearxng = $false
foreach ($line in Get-Content $composePath) {
    if ($line -match "^  searxng:\s*$") {
        $inSearxng = $true
        continue
    }
    if ($inSearxng -and $line -match "^  [A-Za-z0-9_-]+:\s*$") {
        $inSearxng = $false
    }
    if ($inSearxng -and $line -match "^\s+build:\s*$") {
        Write-Error "docker-compose.yml: searxng service must not use build:"
        exit 1
    }
}

Write-Host "Release guard passed."

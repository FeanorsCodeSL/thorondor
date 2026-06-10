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

$fallbackHits = @()
foreach ($file in $RuntimeFiles) {
    $path = Join-Path $Root $file
    if (Test-Path $path) {
        $fallbackHits += Select-String -Path $path -Pattern '\$\{[^}]+:-|\$\{[^}:]+-[^}]'
    }
}
if ($fallbackHits.Count -gt 0) {
    $fallbackHits | ForEach-Object { Write-Error "$($_.Path):$($_.LineNumber): $($_.Line)" }
    Write-Error "Runtime Compose configuration must not use interpolation fallback values; declare values explicitly in env files."
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

$composeText = Get-Content $composePath -Raw
if ($composeText -notmatch "image:\s+unclecode/crawl4ai@sha256:") {
    Write-Error "docker-compose.yml: crawl4ai image must be pinned by digest."
    exit 1
}

foreach ($dockerfile in @("orchestrator/Dockerfile", "semantic-chunking-service/Dockerfile", "ssrf-proxy/Dockerfile")) {
    $path = Join-Path $Root $dockerfile
    if ((Get-Content $path -Raw) -notmatch "(?m)^USER\s+") {
        Write-Error "${dockerfile}: first-party images must run as a non-root user."
        exit 1
    }
}

if ((Get-Content (Join-Path $Root "scripts/deploy.ps1") -Raw) -notmatch "health\.dependencies\.PSObject\.Properties") {
    Write-Error "scripts/deploy.ps1: deploy health polling must inspect dependency values."
    exit 1
}

if ((Get-Content (Join-Path $Root "scripts/deploy.sh") -Raw) -notmatch "dependencies") {
    Write-Error "scripts/deploy.sh: deploy health polling must inspect dependency values."
    exit 1
}

Write-Host "Release guard passed."

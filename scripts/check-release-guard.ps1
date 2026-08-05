param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$RuntimeFiles = @(
    "docker-compose.yml",
    "docker-compose.llamacpp.yml",
    "docker-compose.production.yml",
    ".env.example",
    ".env.llamacpp.example",
    ".env.production.example"
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

$productionComposePath = Join-Path $Root "docker-compose.production.yml"
$productionBuildHits = @(Select-String -Path $productionComposePath -Pattern "^\s+build:\s*$")
if ($productionBuildHits.Count -gt 0) {
    $productionBuildHits | ForEach-Object { Write-Error "$($_.Path):$($_.LineNumber): $($_.Line)" }
    Write-Error "docker-compose.production.yml must consume published images only."
    exit 1
}

$productionEnvText = Get-Content (Join-Path $Root ".env.production.example") -Raw
$requiredFirstPartyImages = @{
    "THORONDOR_ORCHESTRATOR_IMAGE" = "ghcr.io/feanorscodesl/thorondor-orchestrator"
    "THORONDOR_CHUNKER_IMAGE" = "ghcr.io/feanorscodesl/thorondor-chunker"
    "THORONDOR_EGRESS_PROXY_IMAGE" = "ghcr.io/feanorscodesl/thorondor-egress-proxy"
}
foreach ($entry in $requiredFirstPartyImages.GetEnumerator()) {
    $pattern = "(?m)^$($entry.Key)=$([regex]::Escape($entry.Value))(:|@sha256:)"
    if ($productionEnvText -notmatch $pattern) {
        Write-Error ".env.production.example: $($entry.Key) must point at $($entry.Value) by tag or digest."
        exit 1
    }
}

foreach ($imageVar in @("THORONDOR_SEARXNG_IMAGE", "THORONDOR_CRAWL4AI_IMAGE")) {
    if ($productionEnvText -notmatch "(?m)^${imageVar}=.+@sha256:") {
        Write-Error ".env.production.example: ${imageVar} must be pinned by digest."
        exit 1
    }
}

foreach ($dockerfile in @("orchestrator/Dockerfile", "semantic-chunking-service/Dockerfile", "ssrf-proxy/Dockerfile")) {
    $path = Join-Path $Root $dockerfile
    if ((Get-Content $path -Raw) -notmatch "(?m)^USER\s+") {
        Write-Error "${dockerfile}: first-party images must run as a non-root user."
        exit 1
    }
}

& $Python scripts/check_requirement_locks.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ((Get-Content (Join-Path $Root "scripts/deploy.ps1") -Raw) -notmatch "health\.dependencies\.PSObject\.Properties") {
    Write-Error "scripts/deploy.ps1: deploy health polling must inspect dependency values."
    exit 1
}

if ((Get-Content (Join-Path $Root "scripts/deploy.sh") -Raw) -notmatch "dependencies") {
    Write-Error "scripts/deploy.sh: deploy health polling must inspect dependency values."
    exit 1
}

$templatePairs = @(
    @(".env.example", "thorondor_cli/templates/env.example"),
    @(".env.llamacpp.example", "thorondor_cli/templates/env.llamacpp.example"),
    @(".env.production.example", "thorondor_cli/templates/env.production.example")
)
foreach ($pair in $templatePairs) {
    $left = Join-Path $Root $pair[0]
    $right = Join-Path $Root $pair[1]
    if ((Get-FileHash $left).Hash -ne (Get-FileHash $right).Hash) {
        Write-Error "$($pair[1]) must match $($pair[0])."
        exit 1
    }
}

$assetPairs = @(
    @("docker-compose.llamacpp.yml", "thorondor_cli/assets/docker-compose.llamacpp.yml"),
    @("searxng/settings.yml", "thorondor_cli/assets/searxng/settings.yml"),
    @("searxng/limiter.toml", "thorondor_cli/assets/searxng/limiter.toml")
)
foreach ($pair in $assetPairs) {
    $left = Join-Path $Root $pair[0]
    $right = Join-Path $Root $pair[1]
    if ((Get-FileHash $left).Hash -ne (Get-FileHash $right).Hash) {
        Write-Error "$($pair[1]) must match $($pair[0])."
        exit 1
    }
}

if (Select-String -Path (Join-Path $Root "thorondor_cli/assets/docker-compose.yml") -Pattern "^\s+build:\s*$") {
    Write-Error "thorondor_cli/assets/docker-compose.yml must consume published images only."
    exit 1
}

docker compose `
    --env-file thorondor_cli/templates/env.example `
    -f thorondor_cli/assets/docker-compose.yml `
    config | Out-Null

$installCommand = "uv tool install --force git+https://github.com/FeanorsCodeSL/thorondor"
if ((Get-Content (Join-Path $Root "scripts/install.sh") -Raw) -notmatch [regex]::Escape($installCommand)) {
    Write-Error "scripts/install.sh must install the canonical Thorondor repository with uv tool install."
    exit 1
}
if ((Get-Content (Join-Path $Root "scripts/install.ps1") -Raw) -notmatch [regex]::Escape($installCommand)) {
    Write-Error "scripts/install.ps1 must install the canonical Thorondor repository with uv tool install."
    exit 1
}
if ((Get-Content (Join-Path $Root "scripts/install.sh") -Raw) -match "checkout") {
    Write-Error "scripts/install.sh must not require a Thorondor checkout after installation."
    exit 1
}
if ((Get-Content (Join-Path $Root "scripts/install.ps1") -Raw) -match "checkout") {
    Write-Error "scripts/install.ps1 must not require a Thorondor checkout after installation."
    exit 1
}
if (Get-Command sh -ErrorAction SilentlyContinue) {
    sh -n scripts/install.sh
}

$overlayDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
$overlay = Join-Path $overlayDir "docker-compose.host-endpoints.yml"
try {
    New-Item -ItemType Directory -Force $overlayDir | Out-Null
    & $Python -c "import sys; from thorondor_cli.state import write_host_endpoints_overlay; write_host_endpoints_overlay(sys.argv[1], host_rewritten=True)" $overlayDir
    docker compose --env-file .env.example -f docker-compose.yml -f $overlay config | Out-Null
}
finally {
    if (Test-Path $overlayDir) {
        Remove-Item -Recurse -Force $overlayDir
    }
}

Write-Host "Release guard passed."

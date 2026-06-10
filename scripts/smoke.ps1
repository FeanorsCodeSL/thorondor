param(
    [string[]]$ComposeFiles = @("docker-compose.yml"),
    [string[]]$EnvFiles = @(".env"),
    [string]$Profile = $(if ($env:PROFILE) { $env:PROFILE } else { "bundled-models" }),
    [string]$HealthUrl = $(if ($env:HEALTH_URL) { $env:HEALTH_URL } else { "http://localhost:8080/healthz" }),
    [string]$SearchUrl = $(if ($env:SEARCH_URL) { $env:SEARCH_URL } else { "http://localhost:8080/search" }),
    [switch]$Investigation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# The bundled embedding/reranker servers are GPU-oriented. CPU alternatives can
# be substituted through .env for low-volume development.

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (($EnvFiles -contains ".env") -and -not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

function New-ComposeArgs {
    param(
        [string[]]$Files,
        [string[]]$EnvFilePaths,
        [string]$SelectedProfile
    )

    $result = @("compose")
    foreach ($envFile in $EnvFilePaths) {
        if ($envFile) {
            $result += @("--env-file", $envFile)
        }
    }
    foreach ($file in $Files) {
        $result += @("-f", $file)
    }
    if ($SelectedProfile) {
        $result += @("--profile", $SelectedProfile)
    }
    return $result
}

$ComposeArgs = New-ComposeArgs -Files $ComposeFiles -EnvFilePaths $EnvFiles -SelectedProfile $Profile

& docker @ComposeArgs up -d

for ($attempt = 1; $attempt -le 90; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -Method Get
        $values = @($health.dependencies.PSObject.Properties | ForEach-Object { $_.Value })
        if ($values.Count -gt 0 -and -not ($values -contains $false)) {
            break
        }
    }
    catch {
        $health = $null
    }

    if ($attempt -eq 90) {
        Write-Error "Timed out waiting for all dependencies at $HealthUrl"
    }
    Start-Sleep -Seconds 2
}

function Invoke-SearchSmoke {
    param(
        [hashtable]$Body,
        [switch]$RequirePrefilter
    )

    $bodyJson = $Body | ConvertTo-Json
    Write-Output "REQUEST_JSON:"
    Write-Output $bodyJson
    $response = Invoke-RestMethod -Uri $SearchUrl -Method Post -ContentType "application/json" -Body $bodyJson
    Write-Output "RESPONSE_JSON:"
    $response | ConvertTo-Json -Depth 30

    if (-not $response.passages -or $response.passages.Count -eq 0) {
        Write-Error "Expected non-empty passages"
    }
    if ($null -eq $response.citations) {
        Write-Error "Expected citations array"
    }
    if ($response.stats.reranked -ne $true) {
        Write-Error "Expected stats.reranked=true"
    }
    if ($response.stats.tokens_returned -gt $Body.token_budget) {
        Write-Error "Token budget exceeded"
    }
    if ($RequirePrefilter -and $response.stats.chunks_produced -ge 50 -and
        $response.stats.chunks_sent_to_reranker -ge $response.stats.chunks_produced) {
        Write-Error "Expected prefilter to reduce broad chunk set"
    }
    if ($Investigation -and (-not $response.stats.url_diagnostics -or $response.stats.url_diagnostics.Count -eq 0)) {
        Write-Error "Expected URL diagnostics in investigation mode"
    }
}

if ($Investigation) {
    Invoke-SearchSmoke -Body @{
        query = "Where was J. Robert Oppenheimer born?"
        search_profile = "research"
        token_budget = 5000
        max_urls = 8
        max_passages = 8
        include_raw_markdown = $false
    }
    Invoke-SearchSmoke -RequirePrefilter -Body @{
        query = "J. Robert Oppenheimer Manhattan Project early life education security hearing"
        search_profile = "research"
        token_budget = 8000
        max_urls = 12
        max_passages = 12
        include_raw_markdown = $false
    }
}
else {
    Invoke-SearchSmoke -Body @{
        query = "what changed in the EU AI Act timeline in 2025"
        token_budget = 4000
    }
}

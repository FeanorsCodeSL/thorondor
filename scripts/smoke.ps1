param(
    [string[]]$ComposeFiles = @("docker-compose.yml"),
    [string[]]$EnvFiles = @(".env"),
    [string]$Profile = $(if ($env:PROFILE) { $env:PROFILE } else { "bundled-models" }),
    [string]$HealthUrl = $env:HEALTH_URL,
    [string]$SearchUrl = $env:SEARCH_URL,
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

function Read-DotEnv {
    param([string]$Path)

    $values = @{}
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $index = $trimmed.IndexOf("=")
        if ($index -le 0) {
            continue
        }

        $key = $trimmed.Substring(0, $index).Trim()
        $value = $trimmed.Substring($index + 1).Trim()
        $value = $value.Trim('"')
        $value = $value.Trim("'")
        $values[$key] = $value
    }
    return $values
}

function Get-RequiredDotEnvValue {
    param(
        [hashtable]$Values,
        [string]$Key,
        [string]$Path
    )

    if (-not $Values.ContainsKey($Key) -or [string]::IsNullOrWhiteSpace($Values[$Key])) {
        throw "$Key must be set explicitly in $Path."
    }

    return $Values[$Key]
}

function Join-OrchestratorUrl {
    param(
        [string]$HostName,
        [string]$Port,
        [string]$Path
    )

    return "http://${HostName}:${Port}${Path}"
}

$envValues = Read-DotEnv ".env"
$orchestratorHost = Get-RequiredDotEnvValue -Values $envValues -Key "ORCHESTRATOR_HOST" -Path ".env"
$orchestratorPort = Get-RequiredDotEnvValue -Values $envValues -Key "ORCHESTRATOR_PORT" -Path ".env"

if ([string]::IsNullOrWhiteSpace($HealthUrl)) {
    $HealthUrl = Join-OrchestratorUrl -HostName $orchestratorHost -Port $orchestratorPort -Path "/health"
}
if ([string]::IsNullOrWhiteSpace($SearchUrl)) {
    $SearchUrl = Join-OrchestratorUrl -HostName $orchestratorHost -Port $orchestratorPort -Path "/search"
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
        if ($health.status -eq "ok") {
            break
        }
    }
    catch {
        $health = $null
    }

    if ($attempt -eq 90) {
        Write-Error "Timed out waiting for service health at $HealthUrl"
    }
    Start-Sleep -Seconds 2
}

function Invoke-SearchSmoke {
    param(
        [hashtable]$Body,
        [string[]]$ExpectedText = @(),
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
    $searchableText = (($response.passages | ForEach-Object { $_.text }) + ($response.citations | ForEach-Object { "$($_.title) $($_.url)" })) -join "`n"
    foreach ($needle in $ExpectedText) {
        if ($searchableText.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            Write-Error "Expected response to contain '$needle'"
        }
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
    Invoke-SearchSmoke -ExpectedText @("Oppenheimer") -Body @{
        query = "Where was J. Robert Oppenheimer born?"
        search_profile = "research"
        token_budget = 5000
        max_urls = 8
        max_passages = 8
        include_raw_markdown = $false
    }
    Invoke-SearchSmoke -ExpectedText @("Oppenheimer") -RequirePrefilter -Body @{
        query = "J. Robert Oppenheimer Manhattan Project early life education security hearing"
        search_profile = "research"
        token_budget = 8000
        max_urls = 12
        max_passages = 12
        include_raw_markdown = $false
    }
}
else {
    Invoke-SearchSmoke -ExpectedText @("AI Act") -Body @{
        query = "what changed in the EU AI Act timeline in 2025"
        token_budget = 4000
    }
}

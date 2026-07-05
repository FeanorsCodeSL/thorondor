param(
    [string]$Profile = "llamacpp-models",
    [string]$HealthUrl = $env:HEALTH_URL,
    [string]$SearchUrl = $env:SEARCH_URL,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}
if (-not (Test-Path ".env.llamacpp")) {
    Copy-Item ".env.llamacpp.example" ".env.llamacpp"
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

function ConvertTo-LocalModelPath {
    param([string]$ContainerPath)

    if (-not $ContainerPath.StartsWith("/models/")) {
        return $null
    }

    $relative = $ContainerPath.Substring("/models/".Length).Replace("/", [string][System.IO.Path]::DirectorySeparatorChar)
    return Join-Path "models" $relative
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

$envValues = Read-DotEnv ".env.llamacpp"
$embeddingModel = Get-RequiredDotEnvValue -Values $envValues -Key "LLAMACPP_EMBEDDING_MODEL" -Path ".env.llamacpp"
$rerankerModel = Get-RequiredDotEnvValue -Values $envValues -Key "LLAMACPP_RERANKER_MODEL" -Path ".env.llamacpp"

$missing = @()
foreach ($item in @(
    @{ Name = "LLAMACPP_EMBEDDING_MODEL"; Path = $embeddingModel },
    @{ Name = "LLAMACPP_RERANKER_MODEL"; Path = $rerankerModel }
)) {
    $localPath = ConvertTo-LocalModelPath $item["Path"]
    if ($localPath -and -not (Test-Path $localPath)) {
        $missing += "$($item["Name"]) ($($item["Path"]) -> $localPath)"
    }
}

if ($missing.Count -gt 0) {
    Write-Host "Missing llama.cpp GGUF model file(s): $($missing -join '; '). Fetching via 'thorondor download-models'..."
    $thorondorCmd = Get-Command thorondor -ErrorAction SilentlyContinue
    if ($thorondorCmd) {
        & thorondor download-models
        if ($LASTEXITCODE -ne 0) {
            throw "thorondor download-models failed with exit code $LASTEXITCODE."
        }
    }
    else {
        Write-Error "thorondor CLI not found on PATH. Install with 'uv tool install .' or set up the project, then re-run this script."
    }

    $stillMissing = @()
    foreach ($item in @(
        @{ Name = "LLAMACPP_EMBEDDING_MODEL"; Path = $embeddingModel },
        @{ Name = "LLAMACPP_RERANKER_MODEL"; Path = $rerankerModel }
    )) {
        $localPath = ConvertTo-LocalModelPath $item["Path"]
        if ($localPath -and -not (Test-Path $localPath)) {
            $stillMissing += "$($item["Name"]) ($($item["Path"]) -> $localPath)"
        }
    }
    if ($stillMissing.Count -gt 0) {
        Write-Error "Still missing llama.cpp GGUF model file(s) after download: $($stillMissing -join '; ')."
    }
}

$deployArgs = @{
    ComposeFiles = @("docker-compose.yml", "docker-compose.llamacpp.yml")
    EnvFiles = @(".env", ".env.llamacpp")
    Profile = $Profile
    HealthUrl = $HealthUrl
    SearchUrl = $SearchUrl
}

if ($SkipSmoke) {
    & (Join-Path $PSScriptRoot "deploy.ps1") @deployArgs -SkipSmoke
}
else {
    & (Join-Path $PSScriptRoot "deploy.ps1") @deployArgs
}

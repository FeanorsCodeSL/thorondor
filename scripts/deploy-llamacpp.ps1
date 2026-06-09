param(
    [string]$Profile = "llamacpp-models",
    [string]$HealthUrl = $(if ($env:HEALTH_URL) { $env:HEALTH_URL } else { "http://localhost:8080/healthz" }),
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

$envValues = Read-DotEnv ".env.llamacpp"
$embeddingModel = if ($envValues.ContainsKey("LLAMACPP_EMBEDDING_MODEL")) {
    $envValues["LLAMACPP_EMBEDDING_MODEL"]
}
else {
    "/models/bge-m3.gguf"
}
$rerankerModel = if ($envValues.ContainsKey("LLAMACPP_RERANKER_MODEL")) {
    $envValues["LLAMACPP_RERANKER_MODEL"]
}
else {
    "/models/bge-reranker-v2-m3.gguf"
}

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
    Write-Error "Missing llama.cpp GGUF model file(s): $($missing -join '; '). Place the files under .\models or edit .env.llamacpp."
}

$deployArgs = @{
    ComposeFiles = @("docker-compose.yml", "docker-compose.llamacpp.yml")
    EnvFiles = @(".env", ".env.llamacpp")
    Profile = $Profile
    HealthUrl = $HealthUrl
}

if ($SkipSmoke) {
    & (Join-Path $PSScriptRoot "deploy.ps1") @deployArgs -SkipSmoke
}
else {
    & (Join-Path $PSScriptRoot "deploy.ps1") @deployArgs
}

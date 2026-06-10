param(
    [string[]]$ComposeFiles = @("docker-compose.yml"),
    [string[]]$EnvFiles = @(".env"),
    [string]$Profile = $(if ($env:PROFILE) { $env:PROFILE } else { "bundled-models" }),
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

function New-SecretValue {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return ([Convert]::ToBase64String($bytes) -replace "[+/=]", "")
}

function Ensure-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $lines = @(Get-Content $Path)
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match "^$([regex]::Escape($Key))=(.*)$") {
            if ([string]::IsNullOrWhiteSpace($Matches[1])) {
                $lines[$index] = "$Key=$Value"
                $updated = $true
            }
            else {
                return
            }
        }
    }

    if (-not $updated) {
        $lines += "$Key=$Value"
    }

    Set-Content -Path $Path -Value $lines
}

Ensure-DotEnvValue -Path ".env" -Key "SEARXNG_SECRET" -Value (New-SecretValue)

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

& docker @ComposeArgs config | Out-Null
& docker @ComposeArgs build
& docker @ComposeArgs up -d

for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        Invoke-RestMethod -Uri $HealthUrl -Method Get | Out-Null
        break
    }
    catch {
        if ($attempt -eq 60) {
            Write-Error "Timed out waiting for $HealthUrl"
        }
        Start-Sleep -Seconds 2
    }
}

if (-not $SkipSmoke) {
    & (Join-Path $PSScriptRoot "smoke.ps1") -ComposeFiles $ComposeFiles -EnvFiles $EnvFiles -Profile $Profile
}

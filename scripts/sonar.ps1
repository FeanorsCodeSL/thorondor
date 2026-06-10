param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($env:SONAR_HOST_URL)) {
    throw "SONAR_HOST_URL must be set explicitly before running SonarQube analysis."
}

if ([string]::IsNullOrWhiteSpace($env:SONAR_TOKEN)) {
    throw "SONAR_TOKEN must be set explicitly before running SonarQube analysis."
}

$scannerPath = $null
$scanner = Get-Command sonar-scanner -ErrorAction SilentlyContinue
if ($scanner) {
    $scannerPath = $scanner.Source
}
else {
    $scannerHome = [Environment]::GetEnvironmentVariable("SONAR_SCANNER_HOME", "Process")
    if ([string]::IsNullOrWhiteSpace($scannerHome)) {
        $scannerHome = [Environment]::GetEnvironmentVariable("SONAR_SCANNER_HOME", "User")
    }
    if (-not [string]::IsNullOrWhiteSpace($scannerHome)) {
        $candidate = Join-Path $scannerHome "bin\sonar-scanner.bat"
        if (Test-Path $candidate) {
            $scannerPath = $candidate
        }
    }
}

if (-not $scannerPath) {
    throw "sonar-scanner was not found on PATH. Install the generic SonarScanner CLI before running this script."
}

if (-not $SkipTests) {
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $python = "python"
    }

    & $python -m coverage run `
        --source=orchestrator `
        --source=semantic-chunking-service/chunking `
        --source=ssrf-proxy `
        -m pytest semantic-chunking-service\tests orchestrator\tests -v
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    & $python -m coverage xml -o coverage.xml
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $scannerPath "-Dsonar.host.url=$env:SONAR_HOST_URL" "-Dsonar.qualitygate.wait=true"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

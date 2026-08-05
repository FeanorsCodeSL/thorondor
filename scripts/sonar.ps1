param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Get-RequiredEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
    }

    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name must be set explicitly before running SonarQube analysis."
    }

    Set-Item -Path "Env:$Name" -Value $value
    return $value
}

$sonarHostUrl = Get-RequiredEnvironmentValue -Name "SONAR_HOST_URL"
Get-RequiredEnvironmentValue -Name "SONAR_TOKEN" | Out-Null

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

function Remove-AnalysisCache {
    foreach ($path in @(".pytest_cache", ".scannerwork")) {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        if ($item) {
            try {
                Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction Stop
            }
            catch {
                Write-Warning "Could not remove ignored analysis cache '$path': $($_.Exception.Message)"
            }
        }
    }
}

if (-not $SkipTests) {
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $python = "python"
    }

    & $python -m coverage run `
        --source=orchestrator,semantic-chunking-service/chunking,ssrf-proxy `
        -m pytest semantic-chunking-service\tests orchestrator\tests -v -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    & $python -m coverage xml -o coverage.xml
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Remove-AnalysisCache
& $scannerPath "-Dsonar.host.url=$sonarHostUrl" "-Dsonar.qualitygate.wait=true"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

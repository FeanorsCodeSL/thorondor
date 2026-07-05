$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    irm https://astral.sh/uv/install.ps1 | iex
}

$UvBin = Join-Path $HOME ".local/bin"
if (Test-Path $UvBin) {
    $env:PATH = "$UvBin$([System.IO.Path]::PathSeparator)$env:PATH"
}

uv tool install --force git+https://github.com/FeanorsCodeSL/thorondor

Write-Output @"
Installed Thorondor commands:
  thorondor       Textual search stack configurator
  thorondor-mcp   Native stdio MCP proxy

Run `thorondor` next.
"@

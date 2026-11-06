#Requires -RunAsAdministrator
# Go version: single static exe, no Python/venv. NSSM preferred, sc.exe fallback.
$ErrorActionPreference = "Stop"
$Exe = "$PSScriptRoot\..\bin\token-logger.exe"
$DataDir = "$PSScriptRoot\..\..\data"
if (-not (Test-Path $Exe)) {
  Write-Host "Building first..."
  & "$PSScriptRoot\..\build.ps1"
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$env:TOKEN_LOGGER_JSONL = "$DataDir\calls.jsonl"
$env:TOKEN_LOGGER_CONFDIR = "$DataDir\mitmproxy"

if (Get-Command nssm -ErrorAction SilentlyContinue) {
  nssm install TokenLogger $Exe
  nssm set TokenLogger DisplayName "Token Logger (Go)"
  nssm set TokenLogger Start SERVICE_AUTO_START
  nssm set TokenLogger AppExit Default Restart
  nssm set TokenLogger AppStdout "$DataDir\token-logger.out.log"
  nssm set TokenLogger AppStderr "$DataDir\token-logger.err.log"
  nssm set TokenLogger AppEnvironmentExtra "TOKEN_LOGGER_JSONL=$env:TOKEN_LOGGER_JSONL"
  nssm set TokenLogger AppEnvironmentExtra "TOKEN_LOGGER_CONFDIR=$env:TOKEN_LOGGER_CONFDIR"
  nssm start TokenLogger
} else {
  $BinPath = "`"$Exe`""
  sc.exe create TokenLogger binPath= $BinPath start= auto
  sc.exe failure TokenLogger reset= 86400 actions= restart/1000/restart/2000/restart/5000
  sc.exe start TokenLogger
}
Write-Host "Installed. CA: $DataDir\mitmproxy\ca.pem"
Write-Host "Trust it (admin): certutil -addstore Root $DataDir\mitmproxy\ca.pem"

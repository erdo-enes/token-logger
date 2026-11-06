#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$AppDir = if ($env:TOKEN_LOGGER_DIR) { $env:TOKEN_LOGGER_DIR } else { "$PSScriptRoot\..\.." }
$AppDir = (Resolve-Path $AppDir).Path
$DataDir = Join-Path $AppDir "data"
$Venv = Join-Path $AppDir ".venv"
$Nssm = Get-Command nssm -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
if (-not (Test-Path $Venv)) { python -m venv $Venv }
& "$Venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Venv\Scripts\python.exe" -m pip install $AppDir

if (-not $Nssm) {
  Write-Host "NSSM not found. Download nssm.exe (https://nssm.cc/download) and re-run, or use the sc.exe fallback below."
}
$Config = Join-Path $AppDir "config.toml"
if (-not (Test-Path $Config)) { Copy-Item (Join-Path $AppDir "config.example.toml") $Config }

# Point DB at data dir
$env:TOKEN_LOGGER_DB = Join-Path $DataDir "token_logger.db"
$Pythonw = Join-Path $Venv "Scripts\pythonw.exe"
$ModuleArgs = "-m token_logger.main `"$Config`""

if ($Nssm) {
  & nssm install TokenLogger $Pythonw $ModuleArgs.Split(" ")
  & nssm set TokenLogger DisplayName "Token Logger (LLM proxy)"
  & nssm set TokenLogger Start SERVICE_AUTO_START
  & nssm set TokenLogger AppExit Default Restart
  & nssm set TokenLogger AppStdout (Join-Path $DataDir "token-logger.out.log")
  & nssm set TokenLogger AppStderr (Join-Path $DataDir "token-logger.err.log")
  & nssm set TokenLogger AppEnvironmentExtra "TOKEN_LOGGER_DB=$env:TOKEN_LOGGER_DB"
  & nssm set TokenLogger AppDirectory $AppDir
  & nssm start TokenLogger
} else {
  $BinPath = "`"$Pythonw`" $ModuleArgs"
  sc.exe create TokenLogger binPath= $BinPath start= auto
  sc.exe failure TokenLogger reset= 86400 actions= restart/2000/restart/5000/restart/10000
  sc.exe start TokenLogger
}
Write-Host "Service installed. CA cert: $DataDir\mitmproxy\ca.pem"
Write-Host "Trust it (admin): certutil -addstore Root $DataDir\mitmproxy\ca.pem"
Write-Host "Then set HTTPS_PROXY=http://127.0.0.1:8080 for your CLI sessions (see README)."

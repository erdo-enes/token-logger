$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path "$PSScriptRoot\bin" | Out-Null
go build -trimpath -ldflags="-s -w" -o "$PSScriptRoot\bin\token-logger.exe" $PSScriptRoot
Write-Host "Built: $PSScriptRoot\bin\token-logger.exe"
Get-Item "$PSScriptRoot\bin\token-logger.exe" | Select-Object Name, Length

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
if (Get-Command nssm -ErrorAction SilentlyContinue) {
  nssm stop TokenLogger
  nssm remove TokenLogger confirm
} else {
  sc.exe stop TokenLogger
  sc.exe delete TokenLogger
}
Write-Host "Removed. Cert remains in LocalMachine Root - remove manually if desired."

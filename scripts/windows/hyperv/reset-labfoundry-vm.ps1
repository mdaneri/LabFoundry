[CmdletBinding()]
param([string]$Name = 'LabFoundry')

$ErrorActionPreference = 'Stop'
Restart-VM -Name $Name -Force
Write-Host "Reset VM: $Name"

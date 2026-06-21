[CmdletBinding()]
param([string]$Name = 'LabFoundry')

$ErrorActionPreference = 'Stop'
Stop-VM -Name $Name -Force
Write-Host "Stopped VM: $Name"

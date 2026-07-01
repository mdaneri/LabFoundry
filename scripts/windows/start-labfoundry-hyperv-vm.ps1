[CmdletBinding()]
param([string]$Name = 'LabFoundry')

$ErrorActionPreference = 'Stop'
Start-VM -Name $Name
Write-Host "Started VM: $Name"

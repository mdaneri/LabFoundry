[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$Name = 'LabFoundry')

$ErrorActionPreference = 'Stop'

if ($PSCmdlet.ShouldProcess($Name, 'Remove LabFoundry VM')) {
    Stop-VM -Name $Name -Force -ErrorAction SilentlyContinue
    Remove-VM -Name $Name -Force
    Write-Host "Removed VM: $Name"
}

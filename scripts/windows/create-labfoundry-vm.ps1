[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Name = 'LabFoundry',
    [Parameter(Mandatory = $true)]
    [string]$VhdxPath,
    [int64]$MemoryStartupBytes = 4GB,
    [int]$ProcessorCount = 2
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $VhdxPath)) {
    throw "VHDX not found: $VhdxPath"
}

if ($PSCmdlet.ShouldProcess($Name, 'Create LabFoundry Hyper-V VM')) {
    New-VM -Name $Name -Generation 2 -MemoryStartupBytes $MemoryStartupBytes -VHDPath $VhdxPath -SwitchName 'LabFoundry-Mgmt' | Out-Null
    Set-VMProcessor -VMName $Name -Count $ProcessorCount
    Set-VMFirmware -VMName $Name -EnableSecureBoot Off
    Write-Host "Created VM: $Name"
}

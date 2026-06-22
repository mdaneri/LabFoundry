[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Name = 'LabFoundry',
    [Parameter(Mandatory = $true)]
    [string]$VhdxPath,
    [int64]$MemoryStartupBytes = 4GB,
    [int]$ProcessorCount = 2,
    [string]$DepotVhdxPath,
    [string]$BackupVhdxPath,
    [int64]$DepotDiskSizeBytes = 500GB,
    [int64]$BackupDiskSizeBytes = 500GB
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $VhdxPath)) {
    throw "VHDX not found: $VhdxPath"
}

function Resolve-DataDiskPath {
    param(
        [string]$ExplicitPath,
        [string]$DefaultName
    )

    if ($ExplicitPath) {
        return $ExplicitPath
    }

    $osDiskDirectory = Split-Path -Parent (Resolve-Path -LiteralPath $VhdxPath)
    return Join-Path $osDiskDirectory $DefaultName
}

function Ensure-DataDisk {
    param(
        [string]$Path,
        [int64]$SizeBytes,
        [string]$Label
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }

    if (Test-Path -LiteralPath $Path) {
        Write-Host "$Label data disk already exists: $Path"
        return
    }

    if ($PSCmdlet.ShouldProcess($Path, "Create dynamic $Label data VHDX")) {
        New-VHD -Path $Path -Dynamic -SizeBytes $SizeBytes | Out-Null
        Write-Host "Created $Label data disk: $Path"
    }
}

$resolvedDepotVhdxPath = Resolve-DataDiskPath -ExplicitPath $DepotVhdxPath -DefaultName 'LabFoundry-Depot.vhdx'
$resolvedBackupVhdxPath = Resolve-DataDiskPath -ExplicitPath $BackupVhdxPath -DefaultName 'LabFoundry-Backups.vhdx'

if ($PSCmdlet.ShouldProcess($Name, 'Create LabFoundry Hyper-V VM')) {
    Ensure-DataDisk -Path $resolvedDepotVhdxPath -SizeBytes $DepotDiskSizeBytes -Label 'VCF Offline Depot'
    Ensure-DataDisk -Path $resolvedBackupVhdxPath -SizeBytes $BackupDiskSizeBytes -Label 'VCF Backups'

    New-VM -Name $Name -Generation 2 -MemoryStartupBytes $MemoryStartupBytes -VHDPath $VhdxPath -SwitchName 'LabFoundry-Mgmt' | Out-Null
    Set-VMProcessor -VMName $Name -Count $ProcessorCount
    Set-VMFirmware -VMName $Name -EnableSecureBoot Off
    Add-VMHardDiskDrive -VMName $Name -ControllerType SCSI -Path $resolvedDepotVhdxPath
    Add-VMHardDiskDrive -VMName $Name -ControllerType SCSI -Path $resolvedBackupVhdxPath
    Write-Host "Created VM: $Name"
    Write-Host "Attached VCF Offline Depot disk: $resolvedDepotVhdxPath"
    Write-Host "Attached VCF Backups disk: $resolvedBackupVhdxPath"
}

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
    [int64]$BackupDiskSizeBytes = 500GB,
    [switch]$SkipLabNetworkAdapters,
    [int]$SiteVlanId = 12,
    [int]$TaggedVlanId = 50
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

function Add-LabNetworkAdapters {
    param(
        [string]$VMName,
        [int]$SiteTag,
        [int]$TaggedVlanTag
    )

    if ($PSCmdlet.ShouldProcess("$VMName/SiteA", 'Add SiteA lab NIC')) {
        Add-VMNetworkAdapter -VMName $VMName -Name 'SiteA' -SwitchName 'LabFoundry-SiteA'
        Set-VMNetworkAdapterVlan -VMName $VMName -VMNetworkAdapterName 'SiteA' -Trunk -AllowedVlanIdList "$SiteTag" -NativeVlanId 0
        Write-Host "Attached SiteA NIC on LabFoundry-SiteA as trunk VLAN $SiteTag"
    }
    if ($PSCmdlet.ShouldProcess("$VMName/Trunk", 'Add tagged VLAN lab NIC')) {
        Add-VMNetworkAdapter -VMName $VMName -Name 'Trunk' -SwitchName 'LabFoundry-Trunk'
        Set-VMNetworkAdapterVlan -VMName $VMName -VMNetworkAdapterName 'Trunk' -Trunk -AllowedVlanIdList "$TaggedVlanTag" -NativeVlanId 0
        Write-Host "Attached Trunk NIC on LabFoundry-Trunk as trunk VLAN $TaggedVlanTag"
    }
    if ($PSCmdlet.ShouldProcess("$VMName/WAN-Test", 'Add WAN test lab NIC')) {
        Add-VMNetworkAdapter -VMName $VMName -Name 'WAN-Test' -SwitchName 'LabFoundry-SiteB'
        Set-VMNetworkAdapterVlan -VMName $VMName -VMNetworkAdapterName 'WAN-Test' -Untagged
        Write-Host "Attached WAN-Test NIC on LabFoundry-SiteB as untagged"
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
    if (-not $SkipLabNetworkAdapters) {
        Add-LabNetworkAdapters -VMName $Name -SiteTag $SiteVlanId -TaggedVlanTag $TaggedVlanId
    }
    Write-Host "Created VM: $Name"
    Write-Host "Attached VCF Offline Depot disk: $resolvedDepotVhdxPath"
    Write-Host "Attached VCF Backups disk: $resolvedBackupVhdxPath"
}

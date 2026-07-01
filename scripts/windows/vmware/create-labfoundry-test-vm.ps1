[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Name = 'LabFoundry-VMware',
    [string]$ApplianceVmxPath = '',
    [string]$OutputDirectory = '',
    [string]$VmrunPath = '',
    [string]$ManagementNetwork = 'vmnet8',
    [string]$SiteANetwork = 'vmnet2',
    [string]$SiteBNetwork = 'vmnet3',
    [string]$TrunkNetwork = 'vmnet4',
    [string]$VdiskManagerPath = '',
    [string]$DepotVmdkPath = '',
    [string]$BackupVmdkPath = '',
    [string]$DepotDiskSize = '500GB',
    [string]$BackupDiskSize = '500GB',
    [switch]$Redeploy,
    [switch]$SkipLabNetworkAdapters,
    [switch]$IncludeLabNetworkAdapters,
    [switch]$ResetDataDisks,
    [switch]$NoStart,
    [switch]$SkipNetworkPrepare,
    [switch]$WaitForIp,
    [int]$IpTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

function Find-LatestApplianceVmx {
    param([string]$RepoRoot)

    $outputRoot = Join-Path $RepoRoot 'image\vmware-workstation\output'
    if (-not (Test-Path -LiteralPath $outputRoot)) {
        throw "VMware Workstation output directory not found: $outputRoot. Build the image first or pass -ApplianceVmxPath."
    }

    $selected = Get-ChildItem -LiteralPath $outputRoot -Recurse -Filter '*.vmx' -File |
        Sort-Object -Property LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $selected) {
        throw "No appliance VMX found under $outputRoot. Build the Workstation image first or pass -ApplianceVmxPath."
    }
    return $selected.FullName
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path

if ($SkipLabNetworkAdapters -and $IncludeLabNetworkAdapters) {
    throw "Pass either -SkipLabNetworkAdapters or -IncludeLabNetworkAdapters, not both."
}

$effectiveSkipLabNetworkAdapters = -not $IncludeLabNetworkAdapters
if ($SkipLabNetworkAdapters) {
    $effectiveSkipLabNetworkAdapters = $true
}

if (-not $ApplianceVmxPath) {
    $ApplianceVmxPath = Find-LatestApplianceVmx -RepoRoot $repoRoot
}
$resolvedSourceVmx = (Resolve-Path -LiteralPath $ApplianceVmxPath).Path

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "image\vmware-workstation\test-vms\$Name"
}
$resolvedOutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$targetVmx = Join-Path $resolvedOutputDirectory "$Name.vmx"
$resolvedDepotVmdkPath = if ($DepotVmdkPath) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DepotVmdkPath)
} else {
    Join-Path $resolvedOutputDirectory 'LabFoundry-Depot.vmdk'
}
$resolvedBackupVmdkPath = if ($BackupVmdkPath) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BackupVmdkPath)
} else {
    Join-Path $resolvedOutputDirectory 'LabFoundry-Backups.vmdk'
}

if ((Test-Path -LiteralPath $targetVmx) -and -not $Redeploy) {
    throw "VM already exists: $targetVmx. Pass -Redeploy to remove and recreate it, or pass -Name/-OutputDirectory for a new test VM."
}

if (-not $SkipNetworkPrepare) {
    & (Join-Path $PSScriptRoot 'prepare-networks.ps1') `
        -VmrunPath $VmrunPath `
        -ManagementNetwork $ManagementNetwork `
        -SiteANetwork $SiteANetwork `
        -SiteBNetwork $SiteBNetwork `
        -TrunkNetwork $TrunkNetwork `
        -ManagementOnly:$effectiveSkipLabNetworkAdapters
    if (-not $?) {
        throw "VMware Workstation network validation failed. Plain test VM creation uses management only by default; pass -IncludeLabNetworkAdapters only after vmnet2, vmnet3, and vmnet4 exist."
    }
}

if ((Test-Path -LiteralPath $resolvedOutputDirectory) -and $Redeploy) {
    if ($PSCmdlet.ShouldProcess($targetVmx, 'Remove existing LabFoundry Workstation test VM')) {
        if (Test-Path -LiteralPath $targetVmx) {
            & (Join-Path $PSScriptRoot 'remove-labfoundry-vm.ps1') `
                -VmxPath $targetVmx `
                -VmrunPath $VmrunPath
        } else {
            Remove-Item -LiteralPath $resolvedOutputDirectory -Recurse -Force
        }
    }
}

if ($ResetDataDisks) {
    foreach ($diskPath in @($resolvedDepotVmdkPath, $resolvedBackupVmdkPath)) {
        if (-not (Test-Path -LiteralPath $diskPath)) {
            continue
        }
        $resolvedDiskPath = (Resolve-Path -LiteralPath $diskPath).Path
        if (-not $resolvedDiskPath.StartsWith($resolvedOutputDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to reset VMware data disk outside the VM output directory: $resolvedDiskPath"
        }
        if ($PSCmdlet.ShouldProcess($resolvedDiskPath, 'Remove existing LabFoundry VMware data disk')) {
            Remove-Item -LiteralPath $resolvedDiskPath -Force
            Write-Host "Removed existing data disk: $resolvedDiskPath"
        }
    }
}

if ($PSCmdlet.ShouldProcess($targetVmx, "Create LabFoundry Workstation test VM from $resolvedSourceVmx")) {
    & (Join-Path $PSScriptRoot 'create-labfoundry-vm.ps1') `
        -Name $Name `
        -ApplianceVmxPath $resolvedSourceVmx `
        -OutputDirectory $resolvedOutputDirectory `
        -VmrunPath $VmrunPath `
        -VdiskManagerPath $VdiskManagerPath `
        -DepotVmdkPath $resolvedDepotVmdkPath `
        -BackupVmdkPath $resolvedBackupVmdkPath `
        -DepotDiskSize $DepotDiskSize `
        -BackupDiskSize $BackupDiskSize `
        -ManagementNetwork $ManagementNetwork `
        -SiteANetwork $SiteANetwork `
        -SiteBNetwork $SiteBNetwork `
        -TrunkNetwork $TrunkNetwork `
        -SkipLabNetworkAdapters:$effectiveSkipLabNetworkAdapters
    if (-not $?) {
        throw "LabFoundry VMware Workstation VM creation failed."
    }
}

if (-not $NoStart -and -not $WhatIfPreference) {
    & (Join-Path $PSScriptRoot 'start-labfoundry-vm.ps1') `
        -VmxPath $targetVmx `
        -VmrunPath $VmrunPath `
        -Mode gui
    if (-not $?) {
        throw "LabFoundry VMware Workstation VM start failed."
    }
}

Write-Host "LabFoundry Workstation test VM ready: $Name"
Write-Host "Appliance VMX: $targetVmx"

if ($WaitForIp -and -not $NoStart -and -not $WhatIfPreference) {
    $ip = & (Join-Path $PSScriptRoot 'get-labfoundry-vm-ip.ps1') `
        -VmxPath $targetVmx `
        -VmrunPath $VmrunPath `
        -TimeoutSeconds $IpTimeoutSeconds
    Write-Host "Management IP: $ip"
}

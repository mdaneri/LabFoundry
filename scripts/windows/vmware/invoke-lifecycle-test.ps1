[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', '')]
[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [Parameter(ParameterSetName = 'CleanupVms')]
    [string]$LabName = '',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$ApplianceVmxPath = '',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$ClientVmdkPath = '',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$VmrunPath = '',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$ManagementNetwork = 'vmnet8',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$SiteANetwork = 'vmnet2',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$SiteBNetwork = 'vmnet3',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$TrunkNetwork = 'vmnet4',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$ApplianceIPAddress = '192.168.167.10',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$ApplianceUrl = '',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$SiteInterface = 'eth1',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$SiteCidr = '192.168.12.1/24',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$AdminUsername = 'admin',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$AdminPassword = 'VMware01!',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$ApplianceSshUser = 'admin',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$ClientSshUser = 'alpine',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$SshPassword = 'VMware01!',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$VcfBackupPassword = 'VMware01!Test',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [int]$VlanId = 50,

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$TaggedVlanCidr = '192.168.60.1/24',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [string]$WanCidr = '172.31.50.1/24',

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [switch]$KeepVms,

    [Parameter(ParameterSetName = 'Run')]
    [switch]$SkipClientPrepare,

    [Parameter(Mandatory = $true, ParameterSetName = 'PrepareNetworks')]
    [switch]$PrepareNetworksOnly,

    [Parameter(Mandatory = $true, ParameterSetName = 'CleanupVms')]
    [switch]$CleanupVmsOnly,

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [switch]$AllowDryRunApply,

    [Parameter(ParameterSetName = 'Run')]
    [Parameter(ParameterSetName = 'Plan')]
    [switch]$SkipBackupRestoreTest,

    [Parameter(Mandatory = $true, ParameterSetName = 'Plan')]
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')

function Find-LatestApplianceVmx {
    $outputRoot = Join-Path $repoRoot 'image\vmware-workstation\output'
    if (-not (Test-Path -LiteralPath $outputRoot)) {
        throw "VMware Workstation output directory not found: $outputRoot"
    }
    $selected = Get-ChildItem -Path $outputRoot -Recurse -Filter '*.vmx' |
        Sort-Object -Property LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $selected) {
        throw "No appliance VMX found under $outputRoot. Build the Workstation image or pass -ApplianceVmxPath."
    }
    return $selected.FullName
}

if (-not $LabName) {
    if ($PSCmdlet.ParameterSetName -eq 'CleanupVms') {
        $LabName = 'LabFoundryWorkstationLifecycle'
    } else {
        $LabName = "LabFoundryWorkstationLifecycle-$(Get-Date -Format 'yyyyMMddHHmmss')"
    }
}

if ($PSCmdlet.ParameterSetName -eq 'PrepareNetworks') {
    & (Join-Path $PSScriptRoot 'prepare-networks.ps1') `
        -VmrunPath $VmrunPath `
        -ManagementNetwork $ManagementNetwork `
        -SiteANetwork $SiteANetwork `
        -SiteBNetwork $SiteBNetwork `
        -TrunkNetwork $TrunkNetwork
    if (-not $?) {
        throw "VMware Workstation network preparation failed."
    }
    return
}

if (-not $SshPassword) {
    $SshPassword = $AdminPassword
}
if (-not $VcfBackupPassword) {
    $VcfBackupPassword = 'VMware01!Test'
}
if (-not $ApplianceVmxPath) {
    $ApplianceVmxPath = Find-LatestApplianceVmx
}
if (-not $ClientVmdkPath) {
    $ClientVmdkPath = Join-Path $repoRoot 'image\vmware-workstation\clients\alpine-cloud\labfoundry-tiny-linux-client.vmdk'
}
$effectiveApplianceUrl = if ($ApplianceUrl) { $ApplianceUrl } else { "http://${ApplianceIPAddress}" }

if ($PSCmdlet.ParameterSetName -eq 'CleanupVms') {
    & (Join-Path $PSScriptRoot 'remove-lifecycle-vms.ps1') `
        -LabName $LabName `
        -VmrunPath $VmrunPath
    if (-not $?) {
        throw "VMware Workstation lifecycle VM cleanup failed."
    }
    return
}

if (-not $SkipClientPrepare -and -not $PlanOnly) {
    & (Join-Path $PSScriptRoot 'prepare-tiny-linux-client.ps1')
    if (-not $?) {
        throw "Tiny Linux VMware client preparation failed."
    }
}

$arguments = @(
    '-ExecutionPolicy', 'Bypass',
    '-File', (Join-Path $PSScriptRoot 'run-lifecycle-test.ps1'),
    '-LabName', $LabName,
    '-ApplianceVmxPath', $ApplianceVmxPath,
    '-ClientVmdkPath', $ClientVmdkPath,
    '-ManagementNetwork', $ManagementNetwork,
    '-SiteANetwork', $SiteANetwork,
    '-SiteBNetwork', $SiteBNetwork,
    '-TrunkNetwork', $TrunkNetwork,
    '-ApplianceIPAddress', $ApplianceIPAddress,
    '-ApplianceUrl', $effectiveApplianceUrl,
    '-SiteInterface', $SiteInterface,
    '-SiteCidr', $SiteCidr,
    '-AdminUsername', $AdminUsername,
    '-AdminPassword', $AdminPassword,
    '-ApplianceSshUser', $ApplianceSshUser,
    '-ClientSshUser', $ClientSshUser,
    '-SshPassword', $SshPassword,
    '-VcfBackupPassword', $VcfBackupPassword,
    '-VlanId', "$VlanId",
    '-TaggedVlanCidr', $TaggedVlanCidr,
    '-WanCidr', $WanCidr
)
if ($VmrunPath) { $arguments += @('-VmrunPath', $VmrunPath) }
if (-not $KeepVms) { $arguments += '-CleanupCreatedLab' }
if ($AllowDryRunApply) { $arguments += '-AllowDryRunApply' }
if ($SkipBackupRestoreTest) { $arguments += '-SkipBackupRestoreTest' }
if ($PlanOnly) { $arguments += '-PlanOnly' }

Write-Host "Workstation lifecycle lab: $LabName"
Write-Host "Appliance VMX: $ApplianceVmxPath"
Write-Host "Client VMDK: $ClientVmdkPath"
Write-Host "Appliance URL: $effectiveApplianceUrl"
Write-Host ("Backup/restore validation: {0}" -f (-not $SkipBackupRestoreTest))
Write-Host ("Cleanup created VMs: {0}" -f (-not $KeepVms))

& powershell.exe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "VMware Workstation lifecycle test failed with exit code $LASTEXITCODE"
}

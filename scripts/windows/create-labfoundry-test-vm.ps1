[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Name = 'LabFoundry',
    [string]$VhdxPath = '',
    [int64]$MemoryStartupBytes = 4GB,
    [int]$ProcessorCount = 2,
    [switch]$Redeploy,
    [switch]$NoStart,
    [switch]$SkipNetworkPrepare,
    [switch]$WaitForIp,
    [int]$IpTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-LatestApplianceVhdx {
    param([string]$RepoRoot)

    $outputRoot = Join-Path $RepoRoot 'image\hyperv\output'
    if (-not (Test-Path -LiteralPath $outputRoot)) {
        throw "Hyper-V output directory not found: $outputRoot. Build the image first or pass -VhdxPath."
    }

    $candidates = Get-ChildItem -LiteralPath $outputRoot -Recurse -Filter '*.vhdx' -File |
        Where-Object {
            $_.Name -notmatch 'Depot|Backups' -and
            $_.FullName -notmatch '\\clients\\'
        } |
        Sort-Object -Property LastWriteTime -Descending

    $selected = $candidates | Select-Object -First 1
    if (-not $selected) {
        throw "No appliance VHDX found under $outputRoot. Build the image first or pass -VhdxPath."
    }
    return $selected.FullName
}

if (-not (Test-IsAdministrator)) {
    throw "Run this script from an elevated PowerShell session."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if (-not $VhdxPath) {
    $VhdxPath = Find-LatestApplianceVhdx -RepoRoot $repoRoot
}
$resolvedVhdxPath = (Resolve-Path -LiteralPath $VhdxPath).Path

$existing = Get-VM -Name $Name -ErrorAction SilentlyContinue
if ($existing -and -not $Redeploy) {
    throw "VM already exists: $Name. Pass -Redeploy to remove and recreate it, or pass -Name for a new test VM."
}

if (-not $SkipNetworkPrepare) {
    & (Join-Path $PSScriptRoot 'create-hyperv-switches.ps1')
    if (-not $?) {
        throw "Hyper-V network preparation failed."
    }
}

if ($existing -and $Redeploy) {
    if ($PSCmdlet.ShouldProcess($Name, 'Remove existing LabFoundry test VM')) {
        Stop-VM -Name $Name -Force -ErrorAction SilentlyContinue
        Remove-VM -Name $Name -Force
        Write-Host "Removed existing VM: $Name"
    }
}

if ($PSCmdlet.ShouldProcess($Name, "Create LabFoundry test VM from $resolvedVhdxPath")) {
    & (Join-Path $PSScriptRoot 'create-labfoundry-vm.ps1') `
        -Name $Name `
        -VhdxPath $resolvedVhdxPath `
        -MemoryStartupBytes $MemoryStartupBytes `
        -ProcessorCount $ProcessorCount
    if (-not $?) {
        throw "LabFoundry VM creation failed."
    }
}

if (-not $NoStart -and -not $WhatIfPreference) {
    & (Join-Path $PSScriptRoot 'start-labfoundry-vm.ps1') -Name $Name
    if (-not $?) {
        throw "LabFoundry VM start failed."
    }
}

Write-Host "LabFoundry test VM ready: $Name"
Write-Host "Appliance VHDX: $resolvedVhdxPath"

if ($WaitForIp -and -not $NoStart -and -not $WhatIfPreference) {
    $ip = & (Join-Path $PSScriptRoot 'get-labfoundry-vm-ip.ps1') `
        -Name $Name `
        -SwitchName 'LabFoundry-Mgmt' `
        -TimeoutSeconds $IpTimeoutSeconds
    Write-Host "Management IP: $ip"
}

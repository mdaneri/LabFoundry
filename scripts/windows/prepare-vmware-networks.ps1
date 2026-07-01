[CmdletBinding()]
param(
    [string]$VmrunPath = '',
    [string]$ManagementNetwork = 'vmnet8',
    [string]$ManagementSubnet = '192.168.167.0',
    [string]$SiteANetwork = 'vmnet2',
    [string]$SiteBNetwork = 'vmnet3',
    [string]$TrunkNetwork = 'vmnet4',
    [switch]$ManagementOnly,
    [switch]$AllowExistingManagementSubnet,
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'

function Resolve-VmrunPath {
    param([string]$Path)

    if ($Path) {
        if (-not (Test-Path -LiteralPath $Path)) {
            throw "vmrun.exe not found: $Path"
        }
        return (Resolve-Path -LiteralPath $Path).Path
    }

    $candidates = @(
        'C:\Program Files\VMware\VMware Workstation\vmrun.exe',
        'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    $command = Get-Command vmrun -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw 'vmrun.exe was not found. Install VMware Workstation Pro or pass -VmrunPath.'
}

function Get-WorkstationNetworks {
    param([string]$ResolvedVmrunPath)

    $lines = & $ResolvedVmrunPath -T ws listHostNetworks
    if ($LASTEXITCODE -ne 0) {
        throw "vmrun listHostNetworks failed with exit code $LASTEXITCODE."
    }

    $networks = foreach ($line in $lines) {
        if ($line -match '^\s*(\d+)\s+(vmnet\d+)\s+(\S+)\s+(\S+)\s+([0-9.]+)\s+([0-9.]+)\s*$') {
            [pscustomobject]@{
                Index  = [int]$Matches[1]
                Name   = $Matches[2].ToLowerInvariant()
                Type   = $Matches[3]
                Dhcp   = $Matches[4]
                Subnet = $Matches[5]
                Mask   = $Matches[6]
            }
        }
    }
    return @($networks)
}

$resolvedVmrun = Resolve-VmrunPath -Path $VmrunPath
$networks = Get-WorkstationNetworks -ResolvedVmrunPath $resolvedVmrun
$requiredNetworks = if ($ManagementOnly) {
    @($ManagementNetwork)
} else {
    @($ManagementNetwork, $SiteANetwork, $SiteBNetwork, $TrunkNetwork)
}

$required = $requiredNetworks |
    ForEach-Object { $_.ToLowerInvariant() } |
    Select-Object -Unique

$missing = @($required | Where-Object { $name = $_; -not ($networks | Where-Object { $_.Name -eq $name }) })
$management = $networks | Where-Object { $_.Name -eq $ManagementNetwork.ToLowerInvariant() } | Select-Object -First 1

$summary = [pscustomobject]@{
    vmrun               = $resolvedVmrun
    management_network  = $ManagementNetwork
    management_subnet   = $ManagementSubnet
    site_a_network      = $SiteANetwork
    site_b_network      = $SiteBNetwork
    trunk_network       = $TrunkNetwork
    management_only     = [bool]$ManagementOnly
    discovered_networks = $networks
    missing_networks    = $missing
    ready               = ($missing.Count -eq 0 -and $management -and ($AllowExistingManagementSubnet -or $management.Subnet -eq $ManagementSubnet))
}

if ($PlanOnly) {
    $summary | ConvertTo-Json -Depth 5
    return
}

if ($missing.Count -gt 0) {
    throw "Missing VMware Workstation networks: $($missing -join ', '). Create them in Virtual Network Editor, then rerun this script."
}

if (-not $management) {
    throw "Management VMware network was not found: $ManagementNetwork"
}

if (-not $AllowExistingManagementSubnet -and $management.Subnet -ne $ManagementSubnet) {
    throw "Management network $ManagementNetwork is $($management.Subnet), expected $ManagementSubnet. Reconfigure it in Virtual Network Editor or pass -AllowExistingManagementSubnet and override appliance addresses."
}

$summary | ConvertTo-Json -Depth 5

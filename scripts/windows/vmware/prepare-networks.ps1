[CmdletBinding()]
param(
    [string]$VmrunPath = '',
    [string]$ManagementNetwork = 'vmnet8',
    [string]$ManagementSubnet = '',
    [string]$BridgedInterfaceAlias = '',
    [string]$SiteANetwork = 'VMnet2',
    [string]$SiteBNetwork = 'VMnet3',
    [string]$TrunkNetwork = 'VMnet4',
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
                Gateway = ''
            }
        }
    }
    return @($networks)
}

function ConvertTo-Ipv4Integer {
    param([Parameter(Mandatory = $true)][string]$Address)

    $bytes = [System.Net.IPAddress]::Parse($Address).GetAddressBytes()
    if ($bytes.Count -ne 4) {
        throw "Expected an IPv4 address, got: $Address"
    }
    return (([uint32]$bytes[0] -shl 24) -bor ([uint32]$bytes[1] -shl 16) -bor ([uint32]$bytes[2] -shl 8) -bor [uint32]$bytes[3])
}

function ConvertFrom-Ipv4Integer {
    param([Parameter(Mandatory = $true)][uint32]$Address)

    $bytes = [byte[]]@(
        (($Address -shr 24) -band 0xff),
        (($Address -shr 16) -band 0xff),
        (($Address -shr 8) -band 0xff),
        ($Address -band 0xff)
    )
    return ([System.Net.IPAddress]::new($bytes)).ToString()
}

function ConvertTo-Ipv4Netmask {
    param([Parameter(Mandatory = $true)][int]$PrefixLength)

    if ($PrefixLength -lt 0 -or $PrefixLength -gt 32) {
        throw "Invalid IPv4 prefix length: $PrefixLength"
    }
    $mask = if ($PrefixLength -eq 0) { [uint32]0 } else { ([uint32]::MaxValue -shl (32 - $PrefixLength)) }
    return ConvertFrom-Ipv4Integer -Address $mask
}

function Get-Ipv4NetworkAddress {
    param(
        [Parameter(Mandatory = $true)][string]$Address,
        [Parameter(Mandatory = $true)][int]$PrefixLength
    )

    $ip = ConvertTo-Ipv4Integer -Address $Address
    $mask = if ($PrefixLength -eq 0) { [uint32]0 } else { ([uint32]::MaxValue -shl (32 - $PrefixLength)) }
    return ConvertFrom-Ipv4Integer -Address ($ip -band $mask)
}

function Get-BridgedHostNetwork {
    param([string]$InterfaceAlias)

    try {
        $configs = @(Get-NetIPConfiguration -ErrorAction Stop | Where-Object {
                $_.IPv4Address -and
                $_.NetAdapter.Status -eq 'Up' -and
                $_.InterfaceAlias -notlike 'VMware Network Adapter VMnet*' -and
                $_.InterfaceAlias -notlike 'vEthernet*' -and
                $_.InterfaceAlias -notlike '*Loopback*' -and
                $_.InterfaceAlias -notlike '*Bluetooth*'
            })
    } catch {
        throw "Could not inspect host IPv4 configuration for bridged vmnet0. Run from a PowerShell session that can access Get-NetIPConfiguration, or pass a non-bridged VMware vmnet."
    }

    if (-not [string]::IsNullOrWhiteSpace($InterfaceAlias)) {
        $configs = @($configs | Where-Object { $_.InterfaceAlias -eq $InterfaceAlias })
        if ($configs.Count -eq 0) {
            throw "No active IPv4 host interface matched BridgedInterfaceAlias '$InterfaceAlias'."
        }
    }

    $selected = @($configs | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1)
    if ($selected.Count -eq 0) {
        $selected = @($configs | Select-Object -First 1)
    }
    if ($selected.Count -eq 0) {
        throw 'No active host IPv4 interface is available for bridged vmnet0 discovery.'
    }

    $address = $selected[0].IPv4Address | Select-Object -First 1
    $gateway = $selected[0].IPv4DefaultGateway | Select-Object -First 1
    $prefix = [int]$address.PrefixLength
    return [pscustomobject]@{
        Index          = 0
        Name           = 'vmnet0'
        Type           = 'bridged'
        Dhcp           = 'host'
        Subnet         = Get-Ipv4NetworkAddress -Address $address.IPAddress -PrefixLength $prefix
        Mask           = ConvertTo-Ipv4Netmask -PrefixLength $prefix
        Gateway        = if ($gateway) { $gateway.NextHop } else { '' }
        InterfaceAlias = $selected[0].InterfaceAlias
    }
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

if (($required -contains 'vmnet0') -and -not ($networks | Where-Object { $_.Name -eq 'vmnet0' })) {
    $networks += Get-BridgedHostNetwork -InterfaceAlias $BridgedInterfaceAlias
}

$missing = @($required | Where-Object { $name = $_; -not ($networks | Where-Object { $_.Name -eq $name }) })
$management = $networks | Where-Object { $_.Name -eq $ManagementNetwork.ToLowerInvariant() } | Select-Object -First 1
$managementSubnetWasPassed = $PSBoundParameters.ContainsKey('ManagementSubnet')
if (-not $managementSubnetWasPassed -and $management) {
    $ManagementSubnet = $management.Subnet
}

$summary = [pscustomobject]@{
    vmrun               = $resolvedVmrun
    management_network  = $ManagementNetwork
    management_subnet   = $ManagementSubnet
    bridged_interface   = $BridgedInterfaceAlias
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

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', '')]
[CmdletBinding()]
param(
    [Parameter()]
    [string]$IsoUrl = 'https://packages.broadcom.com/photon/5.0/GA/iso/photon-5.0-dde71ec57.x86_64.iso',

    [Parameter()]
    [string]$IsoChecksum = 'sha512:6a7a258399a258da742032987c043ab25503698d35edafaf1ae000f12127da1a161d8b84caa17fd8f23d129e81e1faa7ab087c20ab9229772a643f8f9475305f',

    [string]$SshPassword = 'VMware01!',
    [string]$BootstrapAdminPassword = 'VMware01!',
    [string]$VmName = 'LabFoundry-Photon-Builder-VMware',
    [string]$OutputDirectory = '',
    [string]$SshHost = '',
    [string]$SharedSourceDirectory = '',
    [string]$VmrunPath = '',
    [string]$VmnetName = 'vmnet8',
    [string]$BridgedInterfaceAlias = '',
    # Legacy fallbacks; normal builds replace these from the selected VMware vmnet unless explicitly passed.
    [string]$BuilderStaticIp = '192.168.167.30/24',
    [string]$BuilderStaticNetmask = '255.255.255.0',
    [string]$BuilderStaticGateway = '192.168.167.2',
    [string[]]$BuilderStaticDns = @(),
    [string]$FinalMgmtAddress = '192.168.167.10/24',
    [string]$FinalMgmtGateway = '192.168.167.2',
    [string]$FinalMgmtInterface = 'eth0',
    [string]$PipGlobalIndex = '',
    [string]$PipGlobalIndexUrl = '',
    [string]$PackerDirectory = '',
    [string]$PreparedIsoPath = '',
    [ValidateSet('cleanup', 'abort', 'ask', 'run-cleanup-provisioner')]
    [string]$PackerOnError = 'cleanup',
    [switch]$AllowExistingManagementSubnet,
    [switch]$SkipNetworkCheck,
    [switch]$Headless,
    [switch]$KeepExistingOutput,
    [switch]$EnableRealSystemAdapters,
    [switch]$ValidateOnly,
    [switch]$PrepareIsoOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot '..\common\LabFoundry.PhotonImage.psm1') -Force

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

function Get-Ipv4PrefixLength {
    param([Parameter(Mandatory = $true)][string]$Netmask)

    $mask = ConvertTo-Ipv4Integer -Address $Netmask
    $prefix = 0
    $seenZero = $false
    for ($bit = 31; $bit -ge 0; $bit--) {
        $isSet = (($mask -shr $bit) -band 1) -eq 1
        if ($isSet -and $seenZero) {
            throw "Netmask is not contiguous: $Netmask"
        }
        if ($isSet) {
            $prefix++
        } else {
            $seenZero = $true
        }
    }
    return $prefix
}

function Get-Ipv4CidrFromSubnetOffset {
    param(
        [Parameter(Mandatory = $true)][string]$Subnet,
        [Parameter(Mandatory = $true)][string]$Netmask,
        [Parameter(Mandatory = $true)][uint32]$HostOffset
    )

    $prefix = Get-Ipv4PrefixLength -Netmask $Netmask
    $hostBits = 32 - $prefix
    if ($hostBits -lt 2) {
        throw "VMware network $Subnet/$prefix does not have enough host addresses for a static LabFoundry appliance address."
    }

    $hostCapacity = [uint64]1 -shl $hostBits
    if ([uint64]$HostOffset -ge ($hostCapacity - 1)) {
        throw "Host offset $HostOffset is outside VMware network $Subnet/$prefix."
    }

    $network = ConvertTo-Ipv4Integer -Address $Subnet
    $address = $network + $HostOffset
    return "$(ConvertFrom-Ipv4Integer -Address $address)/$prefix"
}

function Get-Ipv4AddressFromSubnetOffset {
    param(
        [Parameter(Mandatory = $true)][string]$Subnet,
        [Parameter(Mandatory = $true)][string]$Netmask,
        [Parameter(Mandatory = $true)][uint32]$HostOffset
    )

    return (Get-Ipv4CidrFromSubnetOffset -Subnet $Subnet -Netmask $Netmask -HostOffset $HostOffset) -split '/', 2 | Select-Object -First 1
}

function Get-WorkstationManagementNetwork {
    param(
        [string]$NetworkName,
        [string]$ResolvedVmrunPath,
        [string]$BridgedInterfaceAlias
    )

    $networkArgs = @{
        VmrunPath         = $ResolvedVmrunPath
        ManagementNetwork = $NetworkName
        BridgedInterfaceAlias = $BridgedInterfaceAlias
        ManagementOnly    = $true
        PlanOnly          = $true
    }
    if ([string]::IsNullOrWhiteSpace($ResolvedVmrunPath)) {
        $networkArgs.Remove('VmrunPath')
    }
    if ([string]::IsNullOrWhiteSpace($BridgedInterfaceAlias)) {
        $networkArgs.Remove('BridgedInterfaceAlias')
    }

    $planText = (& (Join-Path $PSScriptRoot 'prepare-networks.ps1') @networkArgs | Out-String).Trim()
    if (-not $?) {
        throw 'VMware Workstation network discovery failed.'
    }
    $plan = $planText | ConvertFrom-Json
    if ($plan.missing_networks.Count -gt 0) {
        throw "Missing VMware Workstation networks: $($plan.missing_networks -join ', '). Create them in Virtual Network Editor, then rerun this script."
    }

    $name = $NetworkName.ToLowerInvariant()
    $management = $plan.discovered_networks | Where-Object { $_.Name -eq $name } | Select-Object -First 1
    if (-not $management) {
        throw "Management VMware network was not found: $NetworkName"
    }
    if ([string]::IsNullOrWhiteSpace($management.Subnet) -or [string]::IsNullOrWhiteSpace($management.Mask)) {
        throw "Management VMware network $NetworkName did not report an IPv4 subnet and mask."
    }
    return $management
}

if ([string]::IsNullOrWhiteSpace($PackerDirectory)) {
    $PackerDirectory = Join-Path $PSScriptRoot '..\..\..\image\vmware-workstation'
}

$builderIpWasPassed = $PSBoundParameters.ContainsKey('BuilderStaticIp')
$builderNetmaskWasPassed = $PSBoundParameters.ContainsKey('BuilderStaticNetmask')
$builderGatewayWasPassed = $PSBoundParameters.ContainsKey('BuilderStaticGateway')
$finalAddressWasPassed = $PSBoundParameters.ContainsKey('FinalMgmtAddress')
$finalGatewayWasPassed = $PSBoundParameters.ContainsKey('FinalMgmtGateway')

if (-not $SkipNetworkCheck) {
    $management = Get-WorkstationManagementNetwork -NetworkName $VmnetName -ResolvedVmrunPath $VmrunPath -BridgedInterfaceAlias $BridgedInterfaceAlias
    $managementGateway = if ($management.PSObject.Properties['Gateway'] -and -not [string]::IsNullOrWhiteSpace($management.Gateway)) {
        $management.Gateway
    } else {
        Get-Ipv4AddressFromSubnetOffset -Subnet $management.Subnet -Netmask $management.Mask -HostOffset 2
    }
    if (-not $builderNetmaskWasPassed) {
        $BuilderStaticNetmask = $management.Mask
    }
    if (-not $builderIpWasPassed) {
        $BuilderStaticIp = Get-Ipv4CidrFromSubnetOffset -Subnet $management.Subnet -Netmask $management.Mask -HostOffset 30
    }
    if (-not $builderGatewayWasPassed) {
        $BuilderStaticGateway = $managementGateway
    }
    if (-not $finalAddressWasPassed) {
        $FinalMgmtAddress = Get-Ipv4CidrFromSubnetOffset -Subnet $management.Subnet -Netmask $management.Mask -HostOffset 10
    }
    if (-not $finalGatewayWasPassed) {
        $FinalMgmtGateway = $managementGateway
    }
    Write-Host "Using VMware management network $($management.Name) on $($management.Subnet)/$($management.Mask)."
    Write-Host "Photon builder SSH address: $BuilderStaticIp; final appliance management address: $FinalMgmtAddress."
}

if (-not $ValidateOnly -and -not $PrepareIsoOnly -and -not $SkipNetworkCheck) {
    $builderAddress = if ($BuilderStaticIp) { ($BuilderStaticIp -split '/', 2)[0] } else { '' }
    $managementSubnet = if ($builderAddress -match '^(\d+)\.(\d+)\.(\d+)\.') { "$($Matches[1]).$($Matches[2]).$($Matches[3]).0" } else { '192.168.49.0' }
    $networkArgs = @{
        VmrunPath          = $VmrunPath
        ManagementNetwork = $VmnetName
        ManagementSubnet  = $managementSubnet
        BridgedInterfaceAlias = $BridgedInterfaceAlias
        ManagementOnly    = $true
    }
    if ([string]::IsNullOrWhiteSpace($VmrunPath)) {
        $networkArgs.Remove('VmrunPath')
    }
    if ([string]::IsNullOrWhiteSpace($BridgedInterfaceAlias)) {
        $networkArgs.Remove('BridgedInterfaceAlias')
    }
    if ($AllowExistingManagementSubnet) {
        $networkArgs['AllowExistingManagementSubnet'] = $true
    }
    & (Join-Path $PSScriptRoot 'prepare-networks.ps1') @networkArgs | Out-Host
    if (-not $?) {
        throw 'VMware Workstation network validation failed.'
    }
}

$packerVariables = @{
    vmnet_name = $VmnetName
    headless   = [bool]$Headless
}

Invoke-LabFoundryPhotonImageBuild `
    -IsoUrl $IsoUrl `
    -IsoChecksum $IsoChecksum `
    -PackerDirectory $PackerDirectory `
    -SshPassword $SshPassword `
    -BootstrapAdminPassword $BootstrapAdminPassword `
    -VmName $VmName `
    -OutputDirectory $OutputDirectory `
    -SshHost $SshHost `
    -SharedSourceDirectory $SharedSourceDirectory `
    -BuilderStaticIp $BuilderStaticIp `
    -BuilderStaticNetmask $BuilderStaticNetmask `
    -BuilderStaticGateway $BuilderStaticGateway `
    -BuilderStaticDns $BuilderStaticDns `
    -FinalMgmtAddress $FinalMgmtAddress `
    -FinalMgmtGateway $FinalMgmtGateway `
    -FinalMgmtInterface $FinalMgmtInterface `
    -PipGlobalIndex $PipGlobalIndex `
    -PipGlobalIndexUrl $PipGlobalIndexUrl `
    -PreparedIsoPath $PreparedIsoPath `
    -PackerOnError $PackerOnError `
    -GuestPackages @('open-vm-tools') `
    -GuestPostInstallCommands @('systemctl enable vmtoolsd || true') `
    -AdditionalPackerVariables $packerVariables `
    -KeepExistingOutput:$KeepExistingOutput `
    -EnableRealSystemAdapters:$EnableRealSystemAdapters `
    -ValidateOnly:$ValidateOnly `
    -PrepareIsoOnly:$PrepareIsoOnly

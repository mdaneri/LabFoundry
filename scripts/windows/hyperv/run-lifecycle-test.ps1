[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', '')]
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$LabName = 'LabFoundryLifecycle',
    [Parameter(Mandatory = $true)]
    [string]$ApplianceVhdxPath,
    [string]$ClientVhdxPath = '',
    [string]$EsxIsoPath = '',
    [string]$ClientManagementSwitch = 'Default Switch',
    [string]$ApplianceIPAddress = '192.168.49.1',
    [string]$ApplianceUrl = '',
    [int64]$ApplianceMemoryStartupBytes = 4GB,
    [int64]$ClientMemoryStartupBytes = 1GB,
    [int]$ApplianceProcessorCount = 2,
    [int]$ClientProcessorCount = 1,
    [string]$SiteInterface = 'eth1.12',
    [string]$SiteCidr = '192.168.12.1/24',
    [string]$SiteIPv6Cidr = 'fd00:12::1/64',
    [int]$SiteVlanId = 12,
    [switch]$EsxStorageTest,
    [switch]$ConfirmEsxStorageFormat,
    [int64]$EsxStorageDiskSizeBytes = 20GB,
    [string]$EsxStorageIPv4Client = '192.168.12.210/32',
    [string]$EsxStorageIPv6Client = 'fd00:12::210/128',
    [string]$EsxManagementCidr = '192.168.49.210/24',
    [string]$EsxRootPassword = 'vmware01!',
    [switch]$SkipCurrentSourceDeploy,
    [int]$VlanId = 50,
    [string]$TaggedVlanCidr = '192.168.60.1/24',
    [string]$WanCidr = '172.31.50.1/24',
    [string]$AdminUsername = 'admin',
    [Parameter(Mandatory = $true)]
    [string]$AdminPassword,
    [string]$SshUser = '',
    [string]$ApplianceSshUser = 'admin',
    [string]$ClientSshUser = 'alpine',
    [string]$SshKeyPath = '',
    [string]$SshPassword = '',
    [string]$VcfBackupPassword = 'VMware01!Test',
    [switch]$AllowDryRunApply,
    [switch]$SkipBackupRestoreTest,
    [switch]$AllowExistingLifecycleLab,
    [switch]$CleanupCreatedLab,
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')
if (-not $ApplianceUrl) {
    $ApplianceUrl = "https://${ApplianceIPAddress}"
}
if (-not $ClientVhdxPath) {
    $ClientVhdxPath = Join-Path $repoRoot 'image\hyperv\clients\alpine-cloud\labfoundry-tiny-linux-client.vhdx'
}
$resultStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$resultRoot = Join-Path $repoRoot "test-results\hyperv-lifecycle\$resultStamp"
$diskRoot = Join-Path $resultRoot 'disks'
$seedRoot = Join-Path $resultRoot 'seed'
$createdVms = New-Object System.Collections.Generic.List[string]

if ($EsxStorageTest -and -not $EsxIsoPath) {
    throw '-EsxStorageTest requires -EsxIsoPath so the NFS acceptance runs on ESX 9.'
}
if ($EsxStorageTest -and -not $ConfirmEsxStorageFormat) {
    throw '-EsxStorageTest requires explicit -ConfirmEsxStorageFormat authorization for the lifecycle blank disk.'
}

function Assert-SafeLifecycleName {
    param([string]$Name)

    $reserved = @('LabFoundry', 'LabFoundry-Photon-Builder')
    if ($reserved -contains $Name) {
        throw "Refusing to use reserved VM name '$Name'. Lifecycle tests must use a separate VM set."
    }
    if (-not $Name.StartsWith($LabName)) {
        throw "Refusing VM name '$Name' because it does not start with lifecycle lab prefix '$LabName'."
    }
}

function Assert-InputVhdx {
    param([string]$Path, [string]$Label)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label VHDX not found: $Path"
    }
}

function New-LifecycleDifferencingDisk {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string]$ParentPath,
        [string]$ChildPath,
        [string]$Label
    )

    if (Test-Path -LiteralPath $ChildPath) {
        throw "$Label differencing disk already exists: $ChildPath"
    }
    if ($PSCmdlet.ShouldProcess($ChildPath, "Create $Label differencing disk")) {
        New-VHD -Path $ChildPath -ParentPath (Resolve-Path -LiteralPath $ParentPath) -Differencing | Out-Null
    }
}

function New-LifecycleVm {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string]$Name,
        [string]$VhdxPath,
        [string]$SwitchName,
        [int64]$MemoryStartupBytes,
        [int]$ProcessorCount
    )

    Assert-SafeLifecycleName -Name $Name
    $existing = Get-VM -Name $Name -ErrorAction SilentlyContinue
    if ($existing -and -not $AllowExistingLifecycleLab) {
        throw "Lifecycle VM already exists: $Name. Use a new -LabName or pass -AllowExistingLifecycleLab to reuse it."
    }
    if ($existing) {
        Write-Host "Reusing lifecycle VM: $Name"
        return
    }
    if ($PSCmdlet.ShouldProcess($Name, 'Create lifecycle Hyper-V VM')) {
        New-VM -Name $Name -Generation 2 -MemoryStartupBytes $MemoryStartupBytes -VHDPath $VhdxPath -SwitchName $SwitchName | Out-Null
        Set-VMProcessor -VMName $Name -Count $ProcessorCount
        Set-VMFirmware -VMName $Name -EnableSecureBoot Off
        if (-not $createdVms.Contains($Name)) {
            $createdVms.Add($Name)
        }
        Write-Host "Created lifecycle VM: $Name"
    }
}

function Ensure-ClientSshKey {
    if (-not $SshKeyPath) {
        if ($SshPassword) {
            return ''
        }
        throw 'Client SSH access requires -SshPassword or an existing -SshKeyPath.'
    }
    $publicPath = "$SshKeyPath.pub"
    if (-not (Test-Path -LiteralPath $publicPath)) {
        throw "SSH public key not found: $publicPath"
    }
    return (Get-Content -LiteralPath $publicPath -Raw).Trim()
}

function New-CloudInitSeedIso {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string]$Path,
        [string]$HostName,
        [string]$PublicKey = ''
    )

    if (Test-Path -LiteralPath $Path) {
        if (-not $AllowExistingLifecycleLab) {
            throw "Cloud-init seed disk already exists: $Path"
        }
        return
    }

    if ($PSCmdlet.ShouldProcess($Path, "Create NoCloud seed disk for $HostName")) {
        python -c 'import pycdlib' 2>$null
        if ($LASTEXITCODE -ne 0) {
            python -m pip install pycdlib
        }
        $helper = Join-Path $repoRoot 'scripts\interop\create_nocloud_seed_iso.py'
        $arguments = @(
            $helper,
            '--output', $Path,
            '--hostname', $HostName,
            '--user', $ClientSshUser
        )
        if ($PublicKey) {
            $arguments += @('--public-key', $PublicKey)
        }
        if ($SshPassword) {
            $arguments += @('--password', $SshPassword)
        }
        python @arguments | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create NoCloud seed ISO for $HostName"
        }
    }
}

function Ensure-HardDisk {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string]$VMName,
        [string]$Path
    )

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $existing = Get-VMHardDiskDrive -VMName $VMName | Where-Object { $_.Path -eq $resolved }
    if ($existing) {
        return
    }
    if ($PSCmdlet.ShouldProcess($VMName, "Attach disk $resolved")) {
        Add-VMHardDiskDrive -VMName $VMName -Path $resolved
    }
}

function Ensure-DvdDrive {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string]$VMName,
        [string]$Path
    )

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $existing = Get-VMDvdDrive -VMName $VMName -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $resolved }
    if ($existing) {
        return
    }
    $dvd = Get-VMDvdDrive -VMName $VMName -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($dvd) {
        if ($PSCmdlet.ShouldProcess($VMName, "Attach seed ISO $resolved")) {
            Set-VMDvdDrive -VMName $VMName -ControllerNumber $dvd.ControllerNumber -ControllerLocation $dvd.ControllerLocation -Path $resolved
        }
        return
    }
    if ($PSCmdlet.ShouldProcess($VMName, "Add seed ISO $resolved")) {
        Add-VMDvdDrive -VMName $VMName -Path $resolved
    }
}

function Ensure-NetworkAdapter {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string]$VMName,
        [string]$Name,
        [string]$SwitchName
    )

    $adapter = Get-VMNetworkAdapter -VMName $VMName -Name $Name -ErrorAction SilentlyContinue
    if ($adapter) {
        if ($adapter.SwitchName -ne $SwitchName) {
            if ($PSCmdlet.ShouldProcess("$VMName/$Name", "Connect to $SwitchName")) {
                Connect-VMNetworkAdapter -VMName $VMName -Name $Name -SwitchName $SwitchName
            }
        }
        return
    }
    if ($PSCmdlet.ShouldProcess("$VMName/$Name", "Add NIC on $SwitchName")) {
        Add-VMNetworkAdapter -VMName $VMName -Name $Name -SwitchName $SwitchName
    }
}

function Set-LifecycleNetworkTopology {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param()
    Ensure-NetworkAdapter -VMName $applianceName -Name 'SiteA' -SwitchName 'LabFoundry-SiteA'
    Ensure-NetworkAdapter -VMName $applianceName -Name 'Trunk' -SwitchName 'LabFoundry-Trunk'
    Ensure-NetworkAdapter -VMName $applianceName -Name 'WAN-Test' -SwitchName 'LabFoundry-SiteB'
    Ensure-NetworkAdapter -VMName $clientAName -Name 'SiteA-Test' -SwitchName 'LabFoundry-SiteA'
    Ensure-NetworkAdapter -VMName $clientAName -Name 'VLAN-Test' -SwitchName 'LabFoundry-Trunk'
    Ensure-NetworkAdapter -VMName $clientAName -Name 'Appliance-Mgmt-Test' -SwitchName 'LabFoundry-Mgmt'
    Ensure-NetworkAdapter -VMName $clientBName -Name 'WAN-Test' -SwitchName 'LabFoundry-SiteB'
    Ensure-NetworkAdapter -VMName $pxeClientName -Name 'PXE-SiteA' -SwitchName 'LabFoundry-SiteA'
    if ($EsxIsoPath) {
        Ensure-NetworkAdapter -VMName $pxeClientName -Name 'ESX-Management' -SwitchName 'LabFoundry-Mgmt'
        if ($PSCmdlet.ShouldProcess("$pxeClientName/ESX-Management", 'Use untagged management traffic')) {
            Set-VMNetworkAdapterVlan -VMName $pxeClientName -VMNetworkAdapterName 'ESX-Management' -Untagged
        }
    }

    if ($PSCmdlet.ShouldProcess("$applianceName/Trunk", "Enable trunk VLAN $VlanId")) {
        Set-VMNetworkAdapterVlan -VMName $applianceName -VMNetworkAdapterName 'Trunk' -Trunk -AllowedVlanIdList "$VlanId" -NativeVlanId 0
    }
    if ($PSCmdlet.ShouldProcess("$clientAName/VLAN-Test", "Enable access VLAN $VlanId")) {
        Set-VMNetworkAdapterVlan -VMName $clientAName -VMNetworkAdapterName 'VLAN-Test' -Access -VlanId $VlanId
    }
    if ($SiteInterface -match '\.(\d+)$') {
        $siteTaggedVlanId = [int]$Matches[1]
        if ($SiteVlanId -ne $siteTaggedVlanId) {
            throw "SiteInterface $SiteInterface uses VLAN $siteTaggedVlanId but SiteVlanId is $SiteVlanId."
        }
        if ($PSCmdlet.ShouldProcess("$applianceName/SiteA", "Enable trunk VLAN $SiteVlanId")) {
            Set-VMNetworkAdapterVlan -VMName $applianceName -VMNetworkAdapterName 'SiteA' -Trunk -AllowedVlanIdList "$SiteVlanId" -NativeVlanId 0
        }
        if ($PSCmdlet.ShouldProcess("$clientAName/SiteA-Test", "Enable access VLAN $SiteVlanId")) {
            Set-VMNetworkAdapterVlan -VMName $clientAName -VMNetworkAdapterName 'SiteA-Test' -Access -VlanId $SiteVlanId
        }
        if ($PSCmdlet.ShouldProcess("$pxeClientName/PXE-SiteA", "Enable access VLAN $SiteVlanId")) {
            Set-VMNetworkAdapterVlan -VMName $pxeClientName -VMNetworkAdapterName 'PXE-SiteA' -Access -VlanId $SiteVlanId
        }
    }
    else {
        if ($PSCmdlet.ShouldProcess("$applianceName/SiteA", 'Use untagged SiteA traffic')) {
            Set-VMNetworkAdapterVlan -VMName $applianceName -VMNetworkAdapterName 'SiteA' -Untagged
        }
        if ($PSCmdlet.ShouldProcess("$clientAName/SiteA-Test", 'Use untagged SiteA traffic')) {
            Set-VMNetworkAdapterVlan -VMName $clientAName -VMNetworkAdapterName 'SiteA-Test' -Untagged
        }
        if ($PSCmdlet.ShouldProcess("$pxeClientName/PXE-SiteA", 'Use untagged SiteA traffic')) {
            Set-VMNetworkAdapterVlan -VMName $pxeClientName -VMNetworkAdapterName 'PXE-SiteA' -Untagged
        }
    }
}

function Wait-VMRunning {
    param([string]$Name)

    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {
        $vm = Get-VM -Name $Name -ErrorAction Stop
        if ($vm.State -eq 'Running') {
            return
        }
        Start-Sleep -Seconds 3
    }
    throw "VM did not reach Running state: $Name"
}

function Get-GuestIPv4 {
    param(
        [string]$Name,
        [string]$AdapterName = ''
    )

    $addresses = Get-VMNetworkAdapter -VMName $Name |
    Select-Object -ExpandProperty IPAddresses |
    Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -notlike '169.254.*' }
    return $addresses | Select-Object -First 1
}

function ConvertTo-HyphenMac {
    param([string]$MacAddress)

    $clean = ($MacAddress -replace '[^0-9A-Fa-f]', '').ToUpperInvariant()
    if ($clean.Length -ne 12) {
        return $MacAddress.ToUpperInvariant()
    }
    $pairs = for ($index = 0; $index -lt 12; $index += 2) { $clean.Substring($index, 2) }
    return ($pairs -join '-')
}

function ConvertTo-ColonMac {
    param([string]$MacAddress)

    $clean = ($MacAddress -replace '[^0-9A-Fa-f]', '').ToLowerInvariant()
    if ($clean.Length -ne 12) {
        return $MacAddress.ToLowerInvariant()
    }
    $pairs = for ($index = 0; $index -lt 12; $index += 2) { $clean.Substring($index, 2) }
    return ($pairs -join ':')
}

function ConvertTo-ShellSingleQuoted {
    param([string]$Value)

    $safe = $Value.Replace("'", "")
    return "'$safe'"
}

function New-LifecyclePxeVm {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string]$Name,
        [string]$SwitchName
    )

    Assert-SafeLifecycleName -Name $Name
    $existing = Get-VM -Name $Name -ErrorAction SilentlyContinue
    if ($existing -and -not $AllowExistingLifecycleLab) {
        throw "Lifecycle PXE VM already exists: $Name. Use a new -LabName or pass -AllowExistingLifecycleLab to reuse it."
    }
    if ($existing) {
        Write-Host "Reusing lifecycle PXE VM: $Name"
    }
    elseif ($PSCmdlet.ShouldProcess($Name, 'Create lifecycle PXE-only Hyper-V VM')) {
        $memoryBytes = if ($EsxIsoPath) { 12GB } else { 1GB }
        $processorCount = if ($EsxIsoPath) { 4 } else { 1 }
        New-VM -Name $Name -Generation 2 -MemoryStartupBytes $memoryBytes -SwitchName $SwitchName | Out-Null
        Set-VMProcessor -VMName $Name -Count $processorCount
        if ($EsxIsoPath) {
            Set-VMProcessor -VMName $Name -ExposeVirtualizationExtensions $true
        }
        Set-VMFirmware -VMName $Name -EnableSecureBoot Off
        if (-not $createdVms.Contains($Name)) {
            $createdVms.Add($Name)
        }
        Write-Host "Created lifecycle PXE VM: $Name"
    }

    $adapter = Get-VMNetworkAdapter -VMName $Name | Select-Object -First 1
    if ($adapter -and $adapter.Name -ne 'PXE-SiteA' -and -not (Get-VMNetworkAdapter -VMName $Name -Name 'PXE-SiteA' -ErrorAction SilentlyContinue)) {
        Rename-VMNetworkAdapter -VMName $Name -Name $adapter.Name -NewName 'PXE-SiteA'
        $adapter = Get-VMNetworkAdapter -VMName $Name -Name 'PXE-SiteA'
    }
    if ($adapter -and $adapter.MacAddress -eq '000000000000') {
        $suffix = (Get-VM -Name $Name).Id.ToString('N').Substring(0, 6).ToUpperInvariant()
        Set-VMNetworkAdapter -VMName $Name -Name 'PXE-SiteA' -StaticMacAddress "00155D$suffix"
        $adapter = Get-VMNetworkAdapter -VMName $Name -Name 'PXE-SiteA'
    }
    if ($adapter -and $PSCmdlet.ShouldProcess($Name, 'Prefer network adapter for PXE boot')) {
        Set-VMFirmware -VMName $Name -FirstBootDevice $adapter -EnableSecureBoot Off
    }
}

function Get-PxeClientMac {
    param([string]$Name)

    $adapter = Get-VMNetworkAdapter -VMName $Name | Select-Object -First 1
    if (-not $adapter) {
        throw "PXE VM has no network adapter: $Name"
    }
    return ConvertTo-ColonMac -MacAddress $adapter.MacAddress
}

function Copy-EsxIsoToAppliance {
    param([string]$Path)

    if (-not $Path) {
        return ''
    }
    if (-not (Get-Command plink -ErrorAction SilentlyContinue) -or -not (Get-Command pscp -ErrorAction SilentlyContinue)) {
        throw "Staging -EsxIsoPath requires plink and pscp in PATH."
    }
    $fileName = [System.IO.Path]::GetFileName($Path)
    $remoteRoot = '/mnt/labfoundry-vcf-offline-depot/PROD/COMP/ESX_HOST'
    $remoteTmp = "/tmp/$fileName"
    $remotePath = "$remoteRoot/$fileName"
    $quotedRoot = ConvertTo-ShellSingleQuoted -Value $remoteRoot
    $quotedTmp = ConvertTo-ShellSingleQuoted -Value $remoteTmp
    $quotedPath = ConvertTo-ShellSingleQuoted -Value $remotePath
    $quotedPassword = ConvertTo-ShellSingleQuoted -Value $SshPassword

    $plinkArguments = @('-batch', '-ssh')
    $pscpArguments = @('-batch')
    if ($script:applianceHostKey) {
        $plinkArguments += @('-hostkey', $script:applianceHostKey)
        $pscpArguments += @('-hostkey', $script:applianceHostKey)
    }
    $plinkArguments += @('-pw', $SshPassword, "$ApplianceSshUser@$ApplianceIPAddress")
    $pscpArguments += @('-pw', $SshPassword)

    & plink @plinkArguments "printf '%s\n' $quotedPassword | sudo -S mkdir -p $quotedRoot" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create remote ESX ISO directory on the appliance."
    }
    & pscp @pscpArguments $Path "$ApplianceSshUser@${ApplianceIPAddress}:$remoteTmp" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to copy ESX ISO to appliance staging path."
    }
    & plink @plinkArguments "printf '%s\n' $quotedPassword | sudo -S mv $quotedTmp $quotedPath" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install ESX ISO under $remoteRoot on the appliance."
    }
    & plink @plinkArguments "printf '%s\n' $quotedPassword | sudo -S chmod 0644 $quotedPath" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to chmod ESX ISO under $remoteRoot on the appliance."
    }
    & plink @plinkArguments "printf '%s\n' $quotedPassword | sudo -S chown labfoundry:labfoundry $quotedPath" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to chown ESX ISO under $remoteRoot on the appliance."
    }
    return $remotePath
}

function Invoke-PxeBootSmoke {
    param(
        [string]$Name,
        [string]$MacAddress,
        [string]$OutputPath
    )

    $leaseSeen = $false
    $leaseOutput = ''
    if ((Get-VM -Name $Name).State -ne 'Off') {
        Stop-VM -Name $Name -Force -TurnOff -ErrorAction SilentlyContinue
    }
    if ($PSCmdlet.ShouldProcess($Name, 'Start PXE boot smoke VM')) {
        Start-VM -Name $Name
    }
    Wait-VMRunning -Name $Name
    Start-Sleep -Seconds 45

    if ((Get-Command plink -ErrorAction SilentlyContinue) -and $SshPassword) {
        $quotedPassword = ConvertTo-ShellSingleQuoted -Value $SshPassword
        $quotedMac = ConvertTo-ShellSingleQuoted -Value $MacAddress
        $leaseCommand = "printf '%s\n' $quotedPassword | sudo -S grep -i $quotedMac /var/lib/labfoundry/dnsmasq/dhcp.leases 2>/dev/null || true"
        $plinkArgs = @('-batch', '-ssh')
        if ($script:applianceHostKey) {
            $plinkArgs += @('-hostkey', $script:applianceHostKey)
        }
        $plinkArgs += @('-pw', $SshPassword, "$ApplianceSshUser@$ApplianceIPAddress", $leaseCommand)
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $leaseOutput = (& plink @plinkArgs 2>&1 | Out-String).Trim()
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        $leaseSeen = $leaseOutput -match [regex]::Escape($MacAddress)
    }

    $adapter = Get-VMNetworkAdapter -VMName $Name | Select-Object -First 1
    [pscustomobject]@{
        vm_name          = $Name
        started          = ((Get-VM -Name $Name).State -eq 'Running')
        mac_address      = $MacAddress
        switch_name      = $adapter.SwitchName
        appliance_ip     = $ApplianceIPAddress
        lease_seen       = $leaseSeen
        lease_observation = if ($leaseSeen) { 'dnsmasq lease file contains the PXE VM MAC.' } else { 'PXE VM started; lease observation was not available.' }
        lease_output     = $leaseOutput
        observed_at      = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    Write-Host "PXE boot smoke result: $OutputPath"
}

function Get-NeighborIPv4ForAdapter {
    param(
        [string]$VMName,
        [string]$AdapterName
    )

    $adapter = Get-VMNetworkAdapter -VMName $VMName -Name $AdapterName -ErrorAction SilentlyContinue
    if (-not $adapter) {
        return ''
    }
    $mac = ConvertTo-HyphenMac -MacAddress $adapter.MacAddress
    $neighbors = Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.LinkLayerAddress -eq $mac -and
        $_.IPAddress -match '^\d+\.\d+\.\d+\.\d+$' -and
        $_.IPAddress -notlike '169.254.*' -and
        $_.State -ne 'Unreachable'
    } |
    Sort-Object -Property State, IPAddress
    return $neighbors | Select-Object -ExpandProperty IPAddress -First 1
}

function Wait-GuestIPv4 {
    param(
        [string]$Name,
        [string]$AdapterName = 'Network Adapter'
    )

    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline) {
        $address = Get-GuestIPv4 -Name $Name -AdapterName $AdapterName
        if ($address) {
            return $address
        }
        $address = Get-NeighborIPv4ForAdapter -VMName $Name -AdapterName $AdapterName
        if ($address) {
            return $address
        }
        Start-Sleep -Seconds 5
    }
    return ''
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMilliseconds = 1000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false)) {
            return $false
        }
        $client.EndConnect($connect)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Get-PlinkHostKey {
    param(
        [string]$HostName,
        [string]$UserName,
        [string]$Password,
        [int]$TimeoutMinutes = 4
    )

    if (-not $HostName -or -not $Password -or -not (Get-Command plink -ErrorAction SilentlyContinue)) {
        return ''
    }

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-TcpPort -HostName $HostName -Port 22 -TimeoutMilliseconds 1000)) {
            Start-Sleep -Seconds 5
            continue
        }

        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $output = & plink -batch -ssh -pw $Password "$UserName@$HostName" 'hostname' 2>&1
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        $text = ($output | Out-String)
        if ($text -match '(ssh-[A-Za-z0-9-]+\s+\d+\s+SHA256:[A-Za-z0-9+/=]+)') {
            return $Matches[1]
        }
        if ($exitCode -eq 0) {
            return ''
        }
        Start-Sleep -Seconds 5
    }
    Write-Warning "Timed out waiting for SSH host key from $UserName@$HostName; continuing without host key pinning."
    return ''
}

function ConvertTo-IPv6DnsToken {
    param([string]$Address)

    $bytes = [System.Net.IPAddress]::Parse($Address).GetAddressBytes()
    $groups = for ($index = 0; $index -lt 16; $index += 2) {
        (($bytes[$index] -shl 8) -bor $bytes[$index + 1]).ToString('x')
    }
    return ($groups -join '-')
}

function Invoke-EsxCommand {
    param(
        [string]$HostName,
        [string]$HostKey,
        [string]$Command
    )

    $plinkArguments = @('-batch', '-ssh')
    if ($HostKey) {
        $plinkArguments += @('-hostkey', $HostKey)
    }
    $plinkArguments += @('-pw', $EsxRootPassword, "root@$HostName", $Command)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & plink @plinkArguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $text = ($output | Out-String).Trim()
    if ($exitCode -ne 0) {
        throw "ESX command failed with exit code ${exitCode}: $text"
    }
    return $text
}

function Invoke-EsxNfsAcceptance {
    param(
        [string]$HostName,
        [string]$HostKey,
        [string]$OutputPath,
        [switch]$AfterReboot
    )

    $siteIPv4 = $SiteCidr.Split('/')[0]
    $siteIPv6 = $SiteIPv6Cidr.Split('/')[0]
    $ipv4Target = "nfs-$($siteIPv4.Replace('.', '-')).labfoundry.internal"
    $ipv6Target = "nfs-$(ConvertTo-IPv6DnsToken -Address $siteIPv6).labfoundry.internal"
    if ($AfterReboot) {
        $command = @(
            'set -e',
            "vmkping -I vmk0 $siteIPv4",
            "vmkping -6 -I vmk0 $siteIPv6",
            "nslookup $ipv4Target $siteIPv4",
            "nslookup $ipv6Target $siteIPv4",
            "grep -Fx lifecycle-persist-v4 /vmfs/volumes/lf-persist-v4/lifecycle-persist-v4.txt",
            "grep -Fx lifecycle-persist-v6 /vmfs/volumes/lf-persist-v6/lifecycle-persist-v6.txt",
            'esxcli storage nfs list',
            'esxcli storage nfs41 list',
            'esxcli storage filesystem list'
        ) -join ' && '
    }
    else {
        $command = @(
            'set -e',
            "vmkping -I vmk0 $siteIPv4",
            "vmkping -6 -I vmk0 $siteIPv6",
            "nslookup $ipv4Target $siteIPv4",
            "nslookup $ipv6Target $siteIPv4",
            'esxcli storage nfs remove --volume-name=lf-nfs3-ipv4 >/dev/null 2>&1 || true',
            'esxcli storage nfs remove --volume-name=lf-nfs3-ipv6 >/dev/null 2>&1 || true',
            'esxcli storage nfs41 remove --volume-name=lf-nfs41-ipv4 >/dev/null 2>&1 || true',
            'esxcli storage nfs41 remove --volume-name=lf-nfs41-ipv6 >/dev/null 2>&1 || true',
            'esxcli storage nfs remove --volume-name=lf-persist-v4 >/dev/null 2>&1 || true',
            'esxcli storage nfs41 remove --volume-name=lf-persist-v6 >/dev/null 2>&1 || true',
            "esxcli storage nfs add --host=$ipv4Target --share=/srv/labfoundry/esx-storage/lifecycle-nfs3 --volume-name=lf-nfs3-ipv4",
            "printf '%s\n' nfs3-ipv4 > /vmfs/volumes/lf-nfs3-ipv4/probe.txt",
            'grep -Fx nfs3-ipv4 /vmfs/volumes/lf-nfs3-ipv4/probe.txt',
            'rm /vmfs/volumes/lf-nfs3-ipv4/probe.txt',
            'esxcli storage nfs remove --volume-name=lf-nfs3-ipv4',
            "esxcli storage nfs add --host=$ipv6Target --share=/srv/labfoundry/esx-storage/lifecycle-nfs3 --volume-name=lf-nfs3-ipv6",
            "printf '%s\n' nfs3-ipv6 > /vmfs/volumes/lf-nfs3-ipv6/probe.txt",
            'grep -Fx nfs3-ipv6 /vmfs/volumes/lf-nfs3-ipv6/probe.txt',
            'rm /vmfs/volumes/lf-nfs3-ipv6/probe.txt',
            'esxcli storage nfs remove --volume-name=lf-nfs3-ipv6',
            "esxcli storage nfs41 add --hosts=$ipv4Target --share=/lifecycle-nfs41 --volume-name=lf-nfs41-ipv4",
            "printf '%s\n' nfs41-ipv4 > /vmfs/volumes/lf-nfs41-ipv4/probe.txt",
            'grep -Fx nfs41-ipv4 /vmfs/volumes/lf-nfs41-ipv4/probe.txt',
            'rm /vmfs/volumes/lf-nfs41-ipv4/probe.txt',
            'esxcli storage nfs41 remove --volume-name=lf-nfs41-ipv4',
            "esxcli storage nfs41 add --hosts=$ipv6Target --share=/lifecycle-nfs41 --volume-name=lf-nfs41-ipv6",
            "printf '%s\n' nfs41-ipv6 > /vmfs/volumes/lf-nfs41-ipv6/probe.txt",
            'grep -Fx nfs41-ipv6 /vmfs/volumes/lf-nfs41-ipv6/probe.txt',
            'rm /vmfs/volumes/lf-nfs41-ipv6/probe.txt',
            'esxcli storage nfs41 remove --volume-name=lf-nfs41-ipv6',
            "esxcli storage nfs add --host=$ipv4Target --share=/srv/labfoundry/esx-storage/lifecycle-nfs3 --volume-name=lf-persist-v4",
            "esxcli storage nfs41 add --hosts=$ipv6Target --share=/lifecycle-nfs41 --volume-name=lf-persist-v6",
            "printf '%s\n' lifecycle-persist-v4 > /vmfs/volumes/lf-persist-v4/lifecycle-persist-v4.txt",
            "printf '%s\n' lifecycle-persist-v6 > /vmfs/volumes/lf-persist-v6/lifecycle-persist-v6.txt",
            'esxcli storage nfs list',
            'esxcli storage nfs41 list',
            'esxcli storage filesystem list'
        ) -join ' && '
    }
    $output = Invoke-EsxCommand -HostName $HostName -HostKey $HostKey -Command $command
    [pscustomobject]@{
        esx_host = $HostName
        phase = if ($AfterReboot) { 'after-reboot' } else { 'initial' }
        ipv4_target = $ipv4Target
        ipv6_target = $ipv6Target
        nfs3_ipv4 = $true
        nfs3_ipv6 = $true
        nfs41_ipv4 = $true
        nfs41_ipv6 = $true
        output = $output
        observed_at = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}

function Resolve-SafeChildPath {
    param(
        [string]$Path,
        [string]$Root
    )

    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    $rootPrefix = "$rootFull\"
    if (-not ($pathFull.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "Refusing to operate on path outside lifecycle artifact root: $pathFull"
    }
    return $pathFull
}

function Reset-LifecycleApplianceVm {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param()
    Assert-SafeLifecycleName -Name $applianceName
    $safeApplianceDisk = Resolve-SafeChildPath -Path $applianceDisk -Root $diskRoot

    $existing = Get-VM -Name $applianceName -ErrorAction SilentlyContinue
    if ($existing) {
        if ($PSCmdlet.ShouldProcess($applianceName, 'Remove lifecycle appliance VM before restore validation redeploy')) {
            Stop-VM -Name $applianceName -Force -TurnOff -ErrorAction SilentlyContinue
            Remove-VM -Name $applianceName -Force
        }
    }
    if (Test-Path -LiteralPath $safeApplianceDisk) {
        if ($PSCmdlet.ShouldProcess($safeApplianceDisk, 'Remove lifecycle appliance differencing disk before restore validation redeploy')) {
            Remove-Item -LiteralPath $safeApplianceDisk -Force
        }
    }

    New-LifecycleDifferencingDisk -ParentPath $ApplianceVhdxPath -ChildPath $safeApplianceDisk -Label 'restored appliance'
    New-LifecycleVm -Name $applianceName -VhdxPath $safeApplianceDisk -SwitchName 'LabFoundry-Mgmt' -MemoryStartupBytes $ApplianceMemoryStartupBytes -ProcessorCount $ApplianceProcessorCount
    if ($EsxStorageTest) {
        Ensure-HardDisk -VMName $applianceName -Path $lifecycleDepotDisk
        Ensure-HardDisk -VMName $applianceName -Path $lifecycleBackupDisk
        Ensure-HardDisk -VMName $applianceName -Path $esxStorageDisk
    }
    Set-LifecycleNetworkTopology
    if ((Get-VM -Name $applianceName).State -ne 'Running') {
        if ($PSCmdlet.ShouldProcess($applianceName, 'Start redeployed lifecycle appliance VM')) {
            Start-VM -Name $applianceName
        }
    }
    Wait-VMRunning -Name $applianceName
    Start-Sleep -Seconds 20
    $resetHostKey = Get-PlinkHostKey -HostName $ApplianceIPAddress -UserName $ApplianceSshUser -Password $SshPassword
    if (-not $SkipCurrentSourceDeploy) {
        & (Join-Path $repoRoot 'scripts\windows\vmware\deploy-wheel.ps1') `
            -RepoRoot $repoRoot `
            -IpAddress $ApplianceIPAddress `
            -SshUser $ApplianceSshUser `
            -SshPassword $SshPassword `
            -ReadinessTimeoutSeconds 120
        if (-not $?) {
            throw 'Deploying the current LabFoundry source to the restored lifecycle appliance failed.'
        }
    }
    if ($EsxStorageTest) {
        $quotedPassword = ConvertTo-ShellSingleQuoted -Value $SshPassword
        $packageCommand = "printf '%s\n' $quotedPassword | sudo -S tdnf -y install nfs-utils rpcbind"
        $plinkArguments = @('-batch', '-ssh')
        if ($resetHostKey) { $plinkArguments += @('-hostkey', $resetHostKey) }
        $plinkArguments += @('-pw', $SshPassword, "$ApplianceSshUser@$ApplianceIPAddress", $packageCommand)
        & plink @plinkArguments | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw 'Installing ESX Storage runtime packages on the restored lifecycle appliance failed.'
        }
    }
    return $resetHostKey
}

$applianceName = "$LabName-Appliance"
$clientAName = "$LabName-ClientA"
$clientBName = "$LabName-ClientB"
$pxeClientName = "$LabName-PxeBoot"
$lifecycleDepotDisk = Join-Path $diskRoot "$applianceName-Depot.vhdx"
$lifecycleBackupDisk = Join-Path $diskRoot "$applianceName-Backups.vhdx"
$esxStorageDisk = Join-Path $diskRoot "$applianceName-EsxStorage.vhdx"
$esxSystemDisk = Join-Path $diskRoot "$pxeClientName-System.vhdx"

foreach ($name in @($applianceName, $clientAName, $clientBName, $pxeClientName)) {
    Assert-SafeLifecycleName -Name $name
    if ((Get-VM -Name $name -ErrorAction SilentlyContinue) -and -not $AllowExistingLifecycleLab) {
        throw "Lifecycle VM already exists: $name. Use a new -LabName or pass -AllowExistingLifecycleLab to reuse it."
    }
}

Assert-InputVhdx -Path $ApplianceVhdxPath -Label 'Appliance'
Assert-InputVhdx -Path $ClientVhdxPath -Label 'Client'
if ($EsxIsoPath) {
    if (-not (Test-Path -LiteralPath $EsxIsoPath)) {
        throw "ESX ISO not found: $EsxIsoPath"
    }
    if ([System.IO.Path]::GetExtension($EsxIsoPath).ToLowerInvariant() -ne '.iso') {
        throw "-EsxIsoPath must point to an .iso file."
    }
}

if (-not $VcfBackupPassword) {
    $VcfBackupPassword = 'VMware01!Test'
}

if ($PlanOnly) {
    [pscustomobject]@{
        lab_name                 = $LabName
        appliance_vm             = $applianceName
        client_a_vm              = $clientAName
        client_b_vm              = $clientBName
        pxe_boot_vm              = $pxeClientName
        appliance_vhdx           = (Resolve-Path -LiteralPath $ApplianceVhdxPath).Path
        client_vhdx              = (Resolve-Path -LiteralPath $ClientVhdxPath).Path
        pxe_boot_test            = $true
        pxe_boot_mode            = if ($EsxIsoPath) { 'esxi' } else { 'linux' }
        esx_iso_path             = if ($EsxIsoPath) { (Resolve-Path -LiteralPath $EsxIsoPath).Path } else { '' }
        esx_storage_test         = [bool]$EsxStorageTest
        esx_storage_disk         = if ($EsxStorageTest) { $esxStorageDisk } else { '' }
        lifecycle_depot_disk     = if ($EsxStorageTest) { $lifecycleDepotDisk } else { '' }
        lifecycle_backup_disk    = if ($EsxStorageTest) { $lifecycleBackupDisk } else { '' }
        esx_storage_disk_bytes   = if ($EsxStorageTest) { $EsxStorageDiskSizeBytes } else { 0 }
        esx_storage_ipv4_client  = $EsxStorageIPv4Client
        esx_storage_ipv6_client  = $EsxStorageIPv6Client
        esx_management_cidr      = $EsxManagementCidr
        deploy_current_source    = -not [bool]$SkipCurrentSourceDeploy
        site_interface           = $SiteInterface
        site_cidr                = $SiteCidr
        site_ipv6_cidr           = $SiteIPv6Cidr
        site_vlan_id             = $SiteVlanId
        tagged_vlan_id           = $VlanId
        tagged_vlan_cidr         = $TaggedVlanCidr
        wan_cidr                 = $WanCidr
        result_root              = $resultRoot
        appliance_url            = $ApplianceUrl
        backup_restore_test      = -not [bool]$SkipBackupRestoreTest
        cleanup_created_lab      = [bool]$CleanupCreatedLab
        reserved_vms_not_touched = @('LabFoundry', 'LabFoundry-Photon-Builder')
    } | ConvertTo-Json -Depth 5
    return
}

$existingPrimary = Get-VM -Name 'LabFoundry' -ErrorAction SilentlyContinue
if ($existingPrimary -and $existingPrimary.State -eq 'Running' -and $ApplianceIPAddress -eq '192.168.49.1') {
    throw "Existing VM 'LabFoundry' is running and may already own $ApplianceIPAddress. Stop it or choose a different lifecycle management topology. This script will not modify that VM."
}

$runningLifecycleAppliances = Get-VM -ErrorAction SilentlyContinue |
Where-Object {
    $_.Name -like 'LabFoundryLifecycle*-Appliance' -and
    $_.Name -ne $applianceName -and
    $_.State -eq 'Running'
}
if ($runningLifecycleAppliances -and $ApplianceIPAddress -eq '192.168.49.1') {
    $names = ($runningLifecycleAppliances | Select-Object -ExpandProperty Name) -join ', '
    throw "Running lifecycle appliance VM(s) may already own ${ApplianceIPAddress}: $names. Run scripts/windows/hyperv/invoke-lifecycle-test.ps1 -CleanupVmsOnly or stop those VMs before starting a new lifecycle lab."
}

New-Item -ItemType Directory -Path $diskRoot -Force | Out-Null
New-Item -ItemType Directory -Path $seedRoot -Force | Out-Null

& (Join-Path $PSScriptRoot 'create-switches.ps1')

$clientPublicKey = Ensure-ClientSshKey

$applianceDisk = Join-Path $diskRoot "$applianceName.vhdx"
$clientADisk = Join-Path $diskRoot "$clientAName.vhdx"
$clientBDisk = Join-Path $diskRoot "$clientBName.vhdx"
$clientASeedIso = Join-Path $seedRoot "$clientAName-seed.iso"
$clientBSeedIso = Join-Path $seedRoot "$clientBName-seed.iso"

if ($EsxStorageTest -and -not (Test-Path -LiteralPath $esxStorageDisk)) {
    if ($PSCmdlet.ShouldProcess($esxStorageDisk, "Create $EsxStorageDiskSizeBytes-byte blank ESX Storage lifecycle disk")) {
        New-VHD -Path $esxStorageDisk -Dynamic -SizeBytes $EsxStorageDiskSizeBytes | Out-Null
    }
}
if ($EsxStorageTest -and -not (Test-Path -LiteralPath $lifecycleDepotDisk)) {
    if ($PSCmdlet.ShouldProcess($lifecycleDepotDisk, 'Create 20 GiB lifecycle depot disk')) {
        New-VHD -Path $lifecycleDepotDisk -Dynamic -SizeBytes 20GB | Out-Null
    }
}
if ($EsxStorageTest -and -not (Test-Path -LiteralPath $lifecycleBackupDisk)) {
    if ($PSCmdlet.ShouldProcess($lifecycleBackupDisk, 'Create 20 GiB lifecycle backup disk')) {
        New-VHD -Path $lifecycleBackupDisk -Dynamic -SizeBytes 20GB | Out-Null
    }
}
if ($EsxIsoPath -and -not (Test-Path -LiteralPath $esxSystemDisk)) {
    if ($PSCmdlet.ShouldProcess($esxSystemDisk, 'Create 80 GiB ESX lifecycle system disk')) {
        New-VHD -Path $esxSystemDisk -Dynamic -SizeBytes 80GB | Out-Null
    }
}

if (-not $AllowExistingLifecycleLab) {
    New-LifecycleDifferencingDisk -ParentPath $ApplianceVhdxPath -ChildPath $applianceDisk -Label 'appliance'
    New-LifecycleDifferencingDisk -ParentPath $ClientVhdxPath -ChildPath $clientADisk -Label 'client A'
    New-LifecycleDifferencingDisk -ParentPath $ClientVhdxPath -ChildPath $clientBDisk -Label 'client B'
}
else {
    if (-not (Test-Path -LiteralPath $applianceDisk)) { New-LifecycleDifferencingDisk -ParentPath $ApplianceVhdxPath -ChildPath $applianceDisk -Label 'appliance' }
    if (-not (Test-Path -LiteralPath $clientADisk)) { New-LifecycleDifferencingDisk -ParentPath $ClientVhdxPath -ChildPath $clientADisk -Label 'client A' }
    if (-not (Test-Path -LiteralPath $clientBDisk)) { New-LifecycleDifferencingDisk -ParentPath $ClientVhdxPath -ChildPath $clientBDisk -Label 'client B' }
}

New-CloudInitSeedIso -Path $clientASeedIso -HostName ($clientAName.ToLowerInvariant()) -PublicKey $clientPublicKey
New-CloudInitSeedIso -Path $clientBSeedIso -HostName ($clientBName.ToLowerInvariant()) -PublicKey $clientPublicKey

try {
    New-LifecycleVm -Name $applianceName -VhdxPath $applianceDisk -SwitchName 'LabFoundry-Mgmt' -MemoryStartupBytes $ApplianceMemoryStartupBytes -ProcessorCount $ApplianceProcessorCount
    if ($EsxStorageTest) {
        Ensure-HardDisk -VMName $applianceName -Path $lifecycleDepotDisk
        Ensure-HardDisk -VMName $applianceName -Path $lifecycleBackupDisk
        Ensure-HardDisk -VMName $applianceName -Path $esxStorageDisk
    }
    New-LifecycleVm -Name $clientAName -VhdxPath $clientADisk -SwitchName $ClientManagementSwitch -MemoryStartupBytes $ClientMemoryStartupBytes -ProcessorCount $ClientProcessorCount
    New-LifecycleVm -Name $clientBName -VhdxPath $clientBDisk -SwitchName $ClientManagementSwitch -MemoryStartupBytes $ClientMemoryStartupBytes -ProcessorCount $ClientProcessorCount
    New-LifecyclePxeVm -Name $pxeClientName -SwitchName 'LabFoundry-SiteA'
    if ($EsxIsoPath) {
        Ensure-HardDisk -VMName $pxeClientName -Path $esxSystemDisk
        $esxDiskDrive = Get-VMHardDiskDrive -VMName $pxeClientName | Where-Object { $_.Path -eq (Resolve-Path -LiteralPath $esxSystemDisk).Path } | Select-Object -First 1
        $esxPxeAdapter = Get-VMNetworkAdapter -VMName $pxeClientName -Name 'PXE-SiteA'
        if ($esxDiskDrive -and $esxPxeAdapter -and $PSCmdlet.ShouldProcess($pxeClientName, 'Prefer installed ESX disk, then PXE network')) {
            Set-VMFirmware -VMName $pxeClientName -BootOrder $esxDiskDrive, $esxPxeAdapter -EnableSecureBoot Off
        }
    }

    Ensure-DvdDrive -VMName $clientAName -Path $clientASeedIso
    Ensure-DvdDrive -VMName $clientBName -Path $clientBSeedIso

    Set-LifecycleNetworkTopology

    foreach ($name in @($applianceName, $clientAName, $clientBName)) {
        if ((Get-VM -Name $name).State -ne 'Running') {
            if ($PSCmdlet.ShouldProcess($name, 'Start lifecycle VM')) {
                Start-VM -Name $name
            }
        }
        Wait-VMRunning -Name $name
    }

    Start-Sleep -Seconds 20
    $applianceHostKey = Get-PlinkHostKey -HostName $ApplianceIPAddress -UserName $ApplianceSshUser -Password $SshPassword
    if (-not $SkipCurrentSourceDeploy) {
        & (Join-Path $repoRoot 'scripts\windows\vmware\deploy-wheel.ps1') `
            -RepoRoot $repoRoot `
            -IpAddress $ApplianceIPAddress `
            -SshUser $ApplianceSshUser `
            -SshPassword $SshPassword `
            -ReadinessTimeoutSeconds 120
        if (-not $?) {
            throw 'Deploying the current LabFoundry source to the lifecycle appliance failed.'
        }
    }
    if ($EsxStorageTest) {
        $quotedPassword = ConvertTo-ShellSingleQuoted -Value $SshPassword
        $packageCommand = "printf '%s\n' $quotedPassword | sudo -S tdnf -y install nfs-utils rpcbind"
        $plinkArguments = @('-batch', '-ssh')
        if ($applianceHostKey) { $plinkArguments += @('-hostkey', $applianceHostKey) }
        $plinkArguments += @('-pw', $SshPassword, "$ApplianceSshUser@$ApplianceIPAddress", $packageCommand)
        & plink @plinkArguments | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw 'Installing ESX Storage runtime packages on the lifecycle appliance failed.'
        }
    }
    $clientAHost = Wait-GuestIPv4 -Name $clientAName
    $clientBHost = Wait-GuestIPv4 -Name $clientBName
    $clientAHostKey = Get-PlinkHostKey -HostName $clientAHost -UserName $ClientSshUser -Password $SshPassword
    $clientBHostKey = Get-PlinkHostKey -HostName $clientBHost -UserName $ClientSshUser -Password $SshPassword
    $pxeClientMac = Get-PxeClientMac -Name $pxeClientName
    $remoteEsxIsoPath = Copy-EsxIsoToAppliance -Path $EsxIsoPath

    $basePythonArgs = @(
        (Join-Path $repoRoot 'scripts\interop\lifecycle_test.py'),
        '--appliance-url', $ApplianceUrl,
        '--appliance-ssh-host', $ApplianceIPAddress,
        '--username', $AdminUsername,
        '--password', $AdminPassword,
        '--appliance-ssh-user', $ApplianceSshUser,
        '--client-ssh-user', $ClientSshUser,
        '--vcf-backup-password', $VcfBackupPassword,
        '--site-interface', $SiteInterface,
        '--site-cidr', $SiteCidr,
        '--site-ipv6-cidr', $SiteIPv6Cidr,
        '--vlan-id', "$VlanId",
        '--vlan-cidr', $TaggedVlanCidr,
        '--wan-cidr', $WanCidr,
        '--pxe-test-mode', $(if ($EsxIsoPath) { 'esxi' } else { 'linux' }),
        '--pxe-client-mac', $pxeClientMac
    )
    if ($remoteEsxIsoPath) { $basePythonArgs += @('--pxe-installer-iso-path', $remoteEsxIsoPath) }
    if ($EsxStorageTest) {
        $basePythonArgs += @(
            '--esx-storage-test',
            '--esx-storage-only',
            '--esx-storage-ipv4-client', $EsxStorageIPv4Client,
            '--esx-storage-ipv6-client', $EsxStorageIPv6Client,
            '--esx-management-cidr', $EsxManagementCidr
        )
    }
    if ($ConfirmEsxStorageFormat) { $basePythonArgs += '--confirm-esx-storage-format' }
    if ($SshUser) { $basePythonArgs += @('--ssh-user', $SshUser) }
    if ($SshKeyPath) { $basePythonArgs += @('--ssh-key', $SshKeyPath) }
    if ($SshPassword) { $basePythonArgs += @('--ssh-password', $SshPassword) }
    if ($AllowDryRunApply) { $basePythonArgs += '--allow-dry-run' }

    function New-LifecyclePythonArgs {
        param(
            [string]$RunResultRoot,
            [string]$CurrentApplianceHostKey,
            [string]$CurrentClientAHost,
            [string]$CurrentClientBHost,
            [string]$CurrentClientAHostKey,
            [string]$CurrentClientBHostKey
        )

        $runnerArgs = @($basePythonArgs)
        $runnerArgs += @('--result-dir', $RunResultRoot)
        if ($CurrentApplianceHostKey) { $runnerArgs += @('--appliance-ssh-hostkey', $CurrentApplianceHostKey) }
        if ($CurrentClientAHost) { $runnerArgs += @('--client-a-host', $CurrentClientAHost) }
        if ($CurrentClientBHost) { $runnerArgs += @('--client-b-host', $CurrentClientBHost) }
        if ($CurrentClientAHostKey) { $runnerArgs += @('--client-a-hostkey', $CurrentClientAHostKey) }
        if ($CurrentClientBHostKey) { $runnerArgs += @('--client-b-hostkey', $CurrentClientBHostKey) }
        return $runnerArgs
    }

    $initialResultRoot = if ($SkipBackupRestoreTest) { $resultRoot } else { Join-Path $resultRoot 'initial' }
    $restoredResultRoot = Join-Path $resultRoot 'restored'
    $backupArchivePath = Join-Path $resultRoot 'settings-backup.json'
    $initialResultPath = Join-Path $initialResultRoot 'result.json'

    $initialPythonArgs = New-LifecyclePythonArgs `
        -RunResultRoot $initialResultRoot `
        -CurrentApplianceHostKey $applianceHostKey `
        -CurrentClientAHost $clientAHost `
        -CurrentClientBHost $clientBHost `
        -CurrentClientAHostKey $clientAHostKey `
        -CurrentClientBHostKey $clientBHostKey
    if (-not $SkipBackupRestoreTest) {
        $initialPythonArgs += @('--export-settings-backup', $backupArchivePath)
    }

    if ($PSCmdlet.ShouldProcess($LabName, 'Run lifecycle interop scenario')) {
        python @initialPythonArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Lifecycle interop runner failed with exit code $LASTEXITCODE"
        }
        Invoke-PxeBootSmoke -Name $pxeClientName -MacAddress $pxeClientMac -OutputPath (Join-Path $resultRoot 'pxe-boot-smoke.json')
        if ($EsxStorageTest) {
            $esxManagementAddress = $EsxManagementCidr.Split('/')[0]
            $esxHostKey = Get-PlinkHostKey -HostName $esxManagementAddress -UserName 'root' -Password $EsxRootPassword -TimeoutMinutes 45
            if (-not (Test-TcpPort -HostName $esxManagementAddress -Port 22 -TimeoutMilliseconds 2000)) {
                throw "ESX 9 did not expose SSH at $esxManagementAddress after PXE installation."
            }
            Invoke-EsxNfsAcceptance -HostName $esxManagementAddress -HostKey $esxHostKey -OutputPath (Join-Path $resultRoot 'esx-nfs-acceptance-initial.json')
            if ($PSCmdlet.ShouldProcess("$applianceName and $pxeClientName", 'Restart appliance and ESX for NFS persistence acceptance')) {
                Restart-VM -Name $applianceName -Force
                Restart-VM -Name $pxeClientName -Force
            }
            Wait-VMRunning -Name $applianceName
            Wait-VMRunning -Name $pxeClientName
            Start-Sleep -Seconds 90
            $applianceHostKey = Get-PlinkHostKey -HostName $ApplianceIPAddress -UserName $ApplianceSshUser -Password $SshPassword -TimeoutMinutes 8
            $esxHostKey = Get-PlinkHostKey -HostName $esxManagementAddress -UserName 'root' -Password $EsxRootPassword -TimeoutMinutes 15
            Invoke-EsxNfsAcceptance -HostName $esxManagementAddress -HostKey $esxHostKey -OutputPath (Join-Path $resultRoot 'esx-nfs-acceptance-after-reboot.json') -AfterReboot
        }
        if (-not $SkipBackupRestoreTest) {
            if (-not (Test-Path -LiteralPath $backupArchivePath)) {
                throw "Lifecycle backup archive was not created: $backupArchivePath"
            }
            Write-Host "Lifecycle settings backup: $backupArchivePath"
            Write-Host 'Redeploying lifecycle appliance VM for restore validation...'
            $applianceHostKey = Reset-LifecycleApplianceVm
            $clientAHost = Wait-GuestIPv4 -Name $clientAName
            $clientBHost = Wait-GuestIPv4 -Name $clientBName
            $clientAHostKey = Get-PlinkHostKey -HostName $clientAHost -UserName $ClientSshUser -Password $SshPassword
            $clientBHostKey = Get-PlinkHostKey -HostName $clientBHost -UserName $ClientSshUser -Password $SshPassword

            $restoredPythonArgs = New-LifecyclePythonArgs `
                -RunResultRoot $restoredResultRoot `
                -CurrentApplianceHostKey $applianceHostKey `
                -CurrentClientAHost $clientAHost `
                -CurrentClientBHost $clientBHost `
                -CurrentClientAHostKey $clientAHostKey `
                -CurrentClientBHostKey $clientBHostKey
            $restoredPythonArgs += @(
                '--restore-settings-backup', $backupArchivePath,
                '--restored-state-run',
                '--certificate-baseline-result', $initialResultPath
            )
            python @restoredPythonArgs
            if ($LASTEXITCODE -ne 0) {
                throw "Lifecycle restore interop runner failed with exit code $LASTEXITCODE"
            }
        }
    }
}
finally {
    if ($CleanupCreatedLab) {
        foreach ($name in ($createdVms | Select-Object -Unique)) {
            Assert-SafeLifecycleName -Name $name
            if ($PSCmdlet.ShouldProcess($name, 'Remove lifecycle VM created by this run')) {
                Stop-VM -Name $name -Force -TurnOff -ErrorAction SilentlyContinue
                if (Get-VM -Name $name -ErrorAction SilentlyContinue) {
                    Remove-VM -Name $name -Force
                }
            }
        }
    }
    else {
        Write-Host 'Lifecycle VMs were left in place. Cleanup requires -CleanupCreatedLab and only touches lifecycle-created VM names.'
    }
}

Write-Host "Lifecycle artifacts: $resultRoot"

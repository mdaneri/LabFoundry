[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', '')]
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$LabName = 'LabFoundryWorkstationLifecycle',
    [Parameter(Mandatory = $true)]
    [string]$ApplianceVmxPath,
    [Parameter(Mandatory = $true)]
    [string]$ClientVmdkPath,
    [string]$VmrunPath = '',
    [string]$ManagementNetwork = 'vmnet8',
    [string]$SiteANetwork = 'vmnet2',
    [string]$SiteBNetwork = 'vmnet3',
    [string]$TrunkNetwork = 'vmnet4',
    [string]$ApplianceIPAddress = '192.168.167.10',
    [string]$ApplianceUrl = '',
    [string]$SiteInterface = 'eth1',
    [string]$SiteCidr = '192.168.12.1/24',
    [string]$AdminUsername = 'admin',
    [string]$AdminPassword = 'VMware01!',
    [string]$ApplianceSshUser = 'admin',
    [string]$ClientSshUser = 'alpine',
    [string]$SshPassword = '',
    [string]$VcfBackupPassword = 'VMware01!Test',
    [int]$VlanId = 50,
    [string]$TaggedVlanCidr = '192.168.60.1/24',
    [string]$WanCidr = '172.31.50.1/24',
    [switch]$AllowDryRunApply,
    [switch]$SkipBackupRestoreTest,
    [switch]$CleanupCreatedLab,
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')
if (-not $ApplianceUrl) {
    $ApplianceUrl = "http://${ApplianceIPAddress}"
}
if (-not $SshPassword) {
    $SshPassword = $AdminPassword
}

$resultStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$resultRoot = Join-Path $repoRoot "test-results\vmware-workstation-lifecycle\$resultStamp"
$vmRoot = Join-Path $resultRoot 'vms'
$seedRoot = Join-Path $resultRoot 'seed'
$createdVmxPaths = New-Object System.Collections.Generic.List[string]

function Resolve-VmrunPath {
    if ($VmrunPath) {
        if (-not (Test-Path -LiteralPath $VmrunPath)) {
            throw "vmrun.exe not found: $VmrunPath"
        }
        return (Resolve-Path -LiteralPath $VmrunPath).Path
    }
    foreach ($candidate in @(
        'C:\Program Files\VMware\VMware Workstation\vmrun.exe',
        'C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'
    )) {
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

function Assert-SafeLifecycleName {
    param([string]$Name)

    $reserved = @('LabFoundry', 'LabFoundry-Photon-Builder', 'LabFoundry-Photon-Builder-VMware')
    if ($reserved -contains $Name) {
        throw "Refusing to use reserved VM name '$Name'. Lifecycle tests must use a separate VM set."
    }
    if (-not $Name.StartsWith($LabName)) {
        throw "Refusing VM name '$Name' because it does not start with lifecycle lab prefix '$LabName'."
    }
}

function ConvertTo-VmxString {
    param([string]$Value)
    return '"' + ($Value -replace '\\', '\\' -replace '"', '\"') + '"'
}

function Set-VmxValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $line = "$Key = $(ConvertTo-VmxString -Value $Value)"
    $content = if (Test-Path -LiteralPath $Path) {
        @(Get-Content -LiteralPath $Path)
    } else {
        @()
    }
    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*='
    $updated = $false
    $content = @($content | ForEach-Object {
        if ($_ -match $pattern) {
            $updated = $true
            $line
        } else {
            $_
        }
    })
    if (-not $updated) {
        $content += $line
    }
    [System.IO.File]::WriteAllLines($Path, [string[]]$content, [System.Text.UTF8Encoding]::new($false))
}

function Set-VmxNetworkAdapter {
    param(
        [string]$Path,
        [int]$Index,
        [string]$Vmnet
    )

    $prefix = "ethernet$Index"
    Set-VmxValue -Path $Path -Key "$prefix.present" -Value 'TRUE'
    Set-VmxValue -Path $Path -Key "$prefix.connectionType" -Value 'custom'
    Set-VmxValue -Path $Path -Key "$prefix.vnet" -Value $Vmnet
    Set-VmxValue -Path $Path -Key "$prefix.virtualDev" -Value 'vmxnet3'
    Set-VmxValue -Path $Path -Key "$prefix.startConnected" -Value 'TRUE'
}

function Copy-VmDirectory {
    param(
        [string]$SourceVmx,
        [string]$DestinationDirectory,
        [string]$Name
    )

    Assert-SafeLifecycleName -Name $Name
    if (Test-Path -LiteralPath $DestinationDirectory) {
        throw "Lifecycle VM directory already exists: $DestinationDirectory"
    }
    $sourceDirectory = Split-Path -Parent (Resolve-Path -LiteralPath $SourceVmx)
    if ($PSCmdlet.ShouldProcess($DestinationDirectory, "Copy Workstation VM $Name")) {
        Copy-Item -LiteralPath $sourceDirectory -Destination $DestinationDirectory -Recurse
    }
    $vmx = Get-ChildItem -LiteralPath $DestinationDirectory -Filter '*.vmx' | Select-Object -First 1
    if (-not $vmx) {
        throw "Copied Workstation VM has no VMX: $DestinationDirectory"
    }
    $targetVmx = Join-Path $DestinationDirectory "$Name.vmx"
    Rename-Item -LiteralPath $vmx.FullName -NewName "$Name.vmx"
    Set-VmxValue -Path $targetVmx -Key 'displayName' -Value $Name
    $createdVmxPaths.Add($targetVmx)
    return $targetVmx
}

function New-ClientVm {
    param(
        [string]$Name,
        [string]$Directory,
        [string]$DiskPath,
        [string]$SeedIso,
        [string[]]$Networks
    )

    Assert-SafeLifecycleName -Name $Name
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $diskTarget = Join-Path $Directory "$Name.vmdk"
    if ($PSCmdlet.ShouldProcess($diskTarget, "Copy client VMDK for $Name")) {
        Copy-Item -LiteralPath $DiskPath -Destination $diskTarget
    }
    $vmxPath = Join-Path $Directory "$Name.vmx"
    $lines = @(
        '.encoding = "windows-1252"',
        'config.version = "8"',
        'virtualHW.version = "21"',
        'firmware = "efi"',
        'uefi.secureBoot.enabled = "FALSE"',
        "displayName = $(ConvertTo-VmxString -Value $Name)",
        'guestOS = "other5xlinux-64"',
        'memsize = "1024"',
        'numvcpus = "1"',
        'scsi0.present = "TRUE"',
        'scsi0.virtualDev = "lsisas1068"',
        'scsi0:0.present = "TRUE"',
        "scsi0:0.fileName = $(ConvertTo-VmxString -Value (Split-Path -Leaf $diskTarget))",
        'sata0.present = "TRUE"',
        'sata0:0.present = "TRUE"',
        "sata0:0.fileName = $(ConvertTo-VmxString -Value $SeedIso)",
        'sata0:0.deviceType = "cdrom-image"',
        'sata0:0.startConnected = "TRUE"'
    )
    [System.IO.File]::WriteAllLines($vmxPath, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
    for ($index = 0; $index -lt $Networks.Count; $index++) {
        Set-VmxNetworkAdapter -Path $vmxPath -Index $index -Vmnet $Networks[$index]
    }
    $createdVmxPaths.Add($vmxPath)
    return $vmxPath
}

function New-CloudInitSeedIso {
    param(
        [string]$Path,
        [string]$HostName
    )

    if ($PSCmdlet.ShouldProcess($Path, "Create NoCloud seed disk for $HostName")) {
        python -c 'import pycdlib' 2>$null
        if ($LASTEXITCODE -ne 0) {
            python -m pip install pycdlib
        }
        $helper = Join-Path $repoRoot 'scripts\interop\create_nocloud_seed_iso.py'
        python $helper --output $Path --hostname $HostName --user $ClientSshUser --password $SshPassword | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create NoCloud seed ISO for $HostName"
        }
    }
}

function Invoke-Vmrun {
    param([string[]]$Arguments)
    & $resolvedVmrun @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "vmrun $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Start-WorkstationVm {
    param([string]$Path)
    if ($PSCmdlet.ShouldProcess($Path, 'Start Workstation VM')) {
        Invoke-Vmrun -Arguments @('-T', 'ws', 'start', $Path, 'nogui')
    }
}

function Stop-WorkstationVm {
    param([string]$Path)
    & $resolvedVmrun -T ws stop $Path hard 2>$null | Out-Null
}

function Wait-GuestIPv4 {
    param(
        [string]$Path,
        [int]$TimeoutSeconds = 240
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $ip = (& $resolvedVmrun -T ws getGuestIPAddress $Path -wait 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $ip -match '^\d+\.\d+\.\d+\.\d+$' -and $ip -notlike '169.254.*') {
            return $ip
        }
        Start-Sleep -Seconds 5
    }
    return ''
}

$resolvedVmrun = Resolve-VmrunPath
$applianceName = "$LabName-Appliance"
$clientAName = "$LabName-ClientA"
$clientBName = "$LabName-ClientB"

$plan = [ordered]@{
    name                  = 'vmware workstation lifecycle interop'
    lab_name              = $LabName
    appliance_vmx         = (Resolve-Path -LiteralPath $ApplianceVmxPath).Path
    client_vmdk           = (Resolve-Path -LiteralPath $ClientVmdkPath -ErrorAction SilentlyContinue).Path
    result_root           = $resultRoot
    management_network    = $ManagementNetwork
    site_a_network        = $SiteANetwork
    trunk_network         = $TrunkNetwork
    site_b_network        = $SiteBNetwork
    workstation_fidelity  = 'Workstation vmnets are isolated layer-2 segments; Hyper-V access/trunk VLAN port behavior is approximated by separate vmnets.'
}

if ($PlanOnly) {
    New-Item -ItemType Directory -Force -Path $resultRoot | Out-Null
    $plan | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $resultRoot 'plan.json') -Encoding UTF8
    $plan | ConvertTo-Json -Depth 5
    return
}

New-Item -ItemType Directory -Force -Path $vmRoot | Out-Null
New-Item -ItemType Directory -Force -Path $seedRoot | Out-Null

$clientASeedIso = Join-Path $seedRoot "$clientAName-seed.iso"
$clientBSeedIso = Join-Path $seedRoot "$clientBName-seed.iso"
New-CloudInitSeedIso -Path $clientASeedIso -HostName ($clientAName.ToLowerInvariant())
New-CloudInitSeedIso -Path $clientBSeedIso -HostName ($clientBName.ToLowerInvariant())

try {
    $applianceVmx = Copy-VmDirectory -SourceVmx $ApplianceVmxPath -DestinationDirectory (Join-Path $vmRoot $applianceName) -Name $applianceName
    Set-VmxNetworkAdapter -Path $applianceVmx -Index 0 -Vmnet $ManagementNetwork
    Set-VmxNetworkAdapter -Path $applianceVmx -Index 1 -Vmnet $SiteANetwork
    Set-VmxNetworkAdapter -Path $applianceVmx -Index 2 -Vmnet $TrunkNetwork
    Set-VmxNetworkAdapter -Path $applianceVmx -Index 3 -Vmnet $SiteBNetwork

    $clientAVmx = New-ClientVm -Name $clientAName -Directory (Join-Path $vmRoot $clientAName) -DiskPath $ClientVmdkPath -SeedIso $clientASeedIso -Networks @($ManagementNetwork, $SiteANetwork, $TrunkNetwork)
    $clientBVmx = New-ClientVm -Name $clientBName -Directory (Join-Path $vmRoot $clientBName) -DiskPath $ClientVmdkPath -SeedIso $clientBSeedIso -Networks @($ManagementNetwork, $SiteBNetwork)

    foreach ($vmx in @($applianceVmx, $clientAVmx, $clientBVmx)) {
        Start-WorkstationVm -Path $vmx
    }

    Start-Sleep -Seconds 20
    $clientAHost = Wait-GuestIPv4 -Path $clientAVmx
    $clientBHost = Wait-GuestIPv4 -Path $clientBVmx

    $basePythonArgs = @(
        (Join-Path $repoRoot 'scripts\interop\lifecycle_test.py'),
        '--appliance-url', $ApplianceUrl,
        '--appliance-ssh-host', $ApplianceIPAddress,
        '--username', $AdminUsername,
        '--password', $AdminPassword,
        '--appliance-ssh-user', $ApplianceSshUser,
        '--client-ssh-user', $ClientSshUser,
        '--ssh-password', $SshPassword,
        '--vcf-backup-password', $VcfBackupPassword,
        '--site-interface', $SiteInterface,
        '--site-cidr', $SiteCidr,
        '--vlan-id', "$VlanId",
        '--vlan-cidr', $TaggedVlanCidr,
        '--wan-cidr', $WanCidr,
        '--pxe-test-mode', 'linux',
        '--pxe-client-mac', ''
    )
    if ($clientAHost) { $basePythonArgs += @('--client-a-host', $clientAHost) }
    if ($clientBHost) { $basePythonArgs += @('--client-b-host', $clientBHost) }
    if ($AllowDryRunApply) { $basePythonArgs += '--allow-dry-run' }

    $initialResultRoot = if ($SkipBackupRestoreTest) { $resultRoot } else { Join-Path $resultRoot 'initial' }
    $restoredResultRoot = Join-Path $resultRoot 'restored'
    $backupArchivePath = Join-Path $resultRoot 'settings-backup.json'

    $initialPythonArgs = @($basePythonArgs + @('--result-dir', $initialResultRoot))
    if (-not $SkipBackupRestoreTest) {
        $initialPythonArgs += @('--export-settings-backup', $backupArchivePath)
    }

    if ($PSCmdlet.ShouldProcess($LabName, 'Run Workstation lifecycle interop scenario')) {
        python @initialPythonArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Lifecycle interop runner failed with exit code $LASTEXITCODE"
        }
        if (-not $SkipBackupRestoreTest) {
            $restoredPythonArgs = @($basePythonArgs + @(
                '--result-dir', $restoredResultRoot,
                '--restore-settings-backup', $backupArchivePath,
                '--restored-state-run',
                '--certificate-baseline-result', (Join-Path $initialResultRoot 'result.json')
            ))
            python @restoredPythonArgs
            if ($LASTEXITCODE -ne 0) {
                throw "Restored lifecycle interop runner failed with exit code $LASTEXITCODE"
            }
        }
    }
} finally {
    if ($CleanupCreatedLab) {
        foreach ($vmx in @($createdVmxPaths.ToArray())) {
            Stop-WorkstationVm -Path $vmx
        }
        if (Test-Path -LiteralPath $vmRoot) {
            Remove-Item -LiteralPath $vmRoot -Recurse -Force
        }
    } else {
        Write-Host "Workstation lifecycle VMs were left in place under: $vmRoot"
    }
}

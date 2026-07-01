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
    [string]$VmnetName = 'vmnet8',
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

Import-Module (Join-Path $PSScriptRoot 'LabFoundry.PhotonImage.psm1') -Force

if ([string]::IsNullOrWhiteSpace($PackerDirectory)) {
    $PackerDirectory = Join-Path $PSScriptRoot '..\..\image\vmware-workstation'
}

if (-not $ValidateOnly -and -not $PrepareIsoOnly -and -not $SkipNetworkCheck) {
    $builderAddress = if ($BuilderStaticIp) { ($BuilderStaticIp -split '/', 2)[0] } else { '' }
    $managementSubnet = if ($builderAddress -match '^(\d+)\.(\d+)\.(\d+)\.') { "$($Matches[1]).$($Matches[2]).$($Matches[3]).0" } else { '192.168.49.0' }
    $networkArgs = @{
        ManagementNetwork = $VmnetName
        ManagementSubnet  = $managementSubnet
        ManagementOnly    = $true
    }
    if ($AllowExistingManagementSubnet) {
        $networkArgs['AllowExistingManagementSubnet'] = $true
    }
    & (Join-Path $PSScriptRoot 'prepare-vmware-networks.ps1') @networkArgs | Out-Host
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

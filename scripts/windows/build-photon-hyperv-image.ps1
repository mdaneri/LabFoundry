[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$IsoUrl,

    [Parameter(Mandatory = $true)]
    [string]$IsoChecksum,

    [string]$SshPassword = 'VMware01!',
    [string]$BootstrapAdminPassword = 'VMware01!',
    [string]$VmName = 'LabFoundry-Photon-Builder',
    [string]$OutputDirectory = '',
    [string]$SshHost = '',
    [string]$SwitchName = 'LabFoundry-Mgmt',
    [string]$BuilderStaticIp = '192.168.49.30/24',
    [string]$BuilderStaticNetmask = '255.255.255.0',
    [string]$BuilderStaticGateway = '192.168.49.254',
    [string[]]$BuilderStaticDns = @('1.1.1.1', '9.9.9.9'),
    [string]$PackerDirectory = '',
    [string]$KickstartIsoPath = '',
    [switch]$UseHttpKickstartFallback,
    [switch]$ValidateOnly,
    [switch]$PrepareKickstartIsoOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-RepoPath {
    param([string]$Path)
    return (Resolve-Path -LiteralPath $Path).Path
}

function Get-BuilderAddress {
    param([string]$Cidr)
    if ([string]::IsNullOrWhiteSpace($Cidr)) {
        return ''
    }
    return ($Cidr -split '/', 2)[0]
}

function New-PhotonKickstart {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$RootPassword,
        [Parameter(Mandatory = $true)]
        [string]$BuildPassword,
        [Parameter(Mandatory = $true)]
        [string]$BuildUsername,
        [string]$StaticAddress,
        [string]$StaticNetmask,
        [string]$StaticGateway,
        [string[]]$StaticDns
    )

    $network = if ($StaticAddress) {
        [ordered]@{
            type       = 'static'
            ip_addr    = $StaticAddress
            netmask    = $StaticNetmask
            gateway    = $StaticGateway
            nameserver = $StaticDns[0]
        }
    } else {
        [ordered]@{ type = 'dhcp' }
    }

    $kickstart = [ordered]@{
        hostname            = 'labfoundry'
        password            = [ordered]@{
            crypted = $false
            text    = $RootPassword
        }
        disk                = '/dev/sda'
        partitions          = @(
            [ordered]@{ mountpoint = '/'; size = 0; filesystem = 'ext4' },
            [ordered]@{ mountpoint = '/boot'; size = 256; filesystem = 'ext4' },
            [ordered]@{ size = 1024; filesystem = 'swap' }
        )
        bootmode            = 'efi'
        packagelist_file    = 'packages_minimal.json'
        additional_packages = @(
            'openssh-server',
            'sudo',
            'curl',
            'rsync',
            'tar',
            'gzip',
            'shadow',
            'python3',
            'python3-pip',
            'python3-devel',
            'python3-virtualenv',
            'systemd',
            'hyper-v'
        )
        linux_flavor        = 'linux'
        network             = $network
        postinstall         = @(
            '#!/bin/sh',
            "useradd -m -G sudo -s /bin/bash $BuildUsername || true",
            "printf '%s:%s\n' '$BuildUsername' '$BuildPassword' | chpasswd",
            'systemctl enable sshd',
            "echo '$BuildUsername ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/90-labfoundry-build",
            'chmod 0440 /etc/sudoers.d/90-labfoundry-build',
            'systemctl enable hv_kvp_daemon || true',
            'systemctl enable hv_fcopy_daemon || true',
            'systemctl enable hv_vss_daemon || true'
        )
    }

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $json = $kickstart | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.UTF8Encoding]::new($false))
}

if ([string]::IsNullOrWhiteSpace($PackerDirectory)) {
    $PackerDirectory = Join-Path $PSScriptRoot '..\..\image\hyperv'
}

if ($PrepareKickstartIsoOnly -and $UseHttpKickstartFallback) {
    throw "-PrepareKickstartIsoOnly cannot be used with -UseHttpKickstartFallback."
}

if ($BuilderStaticIp -and $BuilderStaticDns.Count -eq 0) {
    throw "-BuilderStaticDns must contain at least one DNS server when -BuilderStaticIp is set."
}

$packerDir = Resolve-RepoPath $PackerDirectory
if ([string]::IsNullOrWhiteSpace($KickstartIsoPath)) {
    $KickstartIsoPath = Join-Path $packerDir 'build\kickstart\labfoundry-photon-kickstart.iso'
}

$buildDir = Join-Path $packerDir 'build'
$ksSourceDir = Join-Path $buildDir 'kickstart-src'
$kickstartJson = Join-Path $ksSourceDir 'photon-ks.json'
$newIsoFilePath = Join-Path $PSScriptRoot 'New-ISOFile.ps1'
$resolvedKickstartIsoPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($KickstartIsoPath)

if (-not $UseHttpKickstartFallback) {
    Remove-Item -LiteralPath $ksSourceDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $ksSourceDir | Out-Null
    New-PhotonKickstart `
        -Path $kickstartJson `
        -RootPassword $SshPassword `
        -BuildPassword $SshPassword `
        -BuildUsername 'labfoundry-build' `
        -StaticAddress (Get-BuilderAddress -Cidr $BuilderStaticIp) `
        -StaticNetmask $BuilderStaticNetmask `
        -StaticGateway $BuilderStaticGateway `
        -StaticDns $BuilderStaticDns

    if (-not (Test-Path -LiteralPath $newIsoFilePath)) {
        throw "New-ISOFile.ps1 was not found at $newIsoFilePath."
    }
    . $newIsoFilePath
    if (-not (Get-Command New-ISOFile -ErrorAction SilentlyContinue)) {
        throw "New-ISOFile was not loaded from $newIsoFilePath."
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $resolvedKickstartIsoPath) | Out-Null
    Set-StrictMode -Off
    try {
        New-ISOFile -source $ksSourceDir -destinationIso $resolvedKickstartIsoPath -title 'LABFOUNDRYKS' -force
    } finally {
        Set-StrictMode -Version Latest
    }
}

if ($PrepareKickstartIsoOnly) {
    Write-Host "Kickstart ISO prepared at $resolvedKickstartIsoPath"
    return
}

$packerArgs = @(
    $(if ($ValidateOnly) { 'validate' } else { 'build' }),
    '-var', "iso_url=$IsoUrl",
    '-var', "iso_checksum=$IsoChecksum",
    '-var', "ssh_password=$SshPassword",
    '-var', "bootstrap_admin_password=$BootstrapAdminPassword",
    '-var', "vm_name=$VmName",
    '-var', "switch_name=$SwitchName",
    '-var', "builder_static_ip=$BuilderStaticIp",
    '-var', "builder_static_netmask=$BuilderStaticNetmask",
    '-var', "builder_static_gateway=$BuilderStaticGateway"
)

$dnsJson = ConvertTo-Json -InputObject $BuilderStaticDns -Compress
$dnsJson = $dnsJson -replace '"', '\"'
$packerArgs += @('-var', "builder_static_dns=$dnsJson")

if (-not $UseHttpKickstartFallback) {
    $packerArgs += @('-var', "kickstart_iso_path=$resolvedKickstartIsoPath")
}

if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $packerArgs += @('-var', "output_directory=$OutputDirectory")
}

if (-not [string]::IsNullOrWhiteSpace($SshHost)) {
    $packerArgs += @('-var', "ssh_host=$SshHost")
}

$packerArgs += '.'

Push-Location $packerDir
try {
    & packer @packerArgs
    if ($LASTEXITCODE -ne 0) {
        $operation = if ($ValidateOnly) { 'validate' } else { 'build' }
        throw "packer $operation failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

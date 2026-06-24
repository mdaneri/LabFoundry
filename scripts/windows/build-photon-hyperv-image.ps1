[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', '')]
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
    [string]$PreparedIsoPath = '',
    [switch]$KeepExistingOutput,
    [switch]$ValidateOnly,
    [switch]$PrepareIsoOnly
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

function Split-PackerChecksum {
    param([Parameter(Mandatory = $true)][string]$Checksum)

    $parts = $Checksum -split ':', 2
    if ($parts.Count -ne 2 -or [string]::IsNullOrWhiteSpace($parts[0]) -or [string]::IsNullOrWhiteSpace($parts[1])) {
        throw "IsoChecksum must use Packer format such as sha512:<hex>."
    }
    return [pscustomobject]@{
        Algorithm = $parts[0].ToUpperInvariant()
        Hash      = $parts[1].ToUpperInvariant()
    }
}

function Get-FileHashHex {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Algorithm
    )

    $hashAlgorithm = [System.Security.Cryptography.HashAlgorithm]::Create($Algorithm)
    if ($null -eq $hashAlgorithm) {
        throw "Unsupported checksum algorithm: $Algorithm"
    }
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $hashBytes = $hashAlgorithm.ComputeHash($stream)
            return -join ($hashBytes | ForEach-Object { $_.ToString('x2') })
        } finally {
            $stream.Dispose()
        }
    } finally {
        $hashAlgorithm.Dispose()
    }
}

function Test-FileChecksum {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Checksum
    )

    $parsed = Split-PackerChecksum -Checksum $Checksum
    $actual = (Get-FileHashHex -Path $Path -Algorithm $parsed.Algorithm).ToUpperInvariant()
    return $actual -eq $parsed.Hash
}

function Resolve-PhotonSourceIso {
    param(
        [Parameter(Mandatory = $true)][string]$UrlOrPath,
        [Parameter(Mandatory = $true)][string]$Checksum,
        [Parameter(Mandatory = $true)][string]$BuildDirectory,
        [Parameter(Mandatory = $true)][string]$PackerDirectory
    )

    if (Test-Path -LiteralPath $UrlOrPath -PathType Leaf) {
        $local = (Resolve-Path -LiteralPath $UrlOrPath).Path
        if (-not (Test-FileChecksum -Path $local -Checksum $Checksum)) {
            throw "Local ISO checksum does not match IsoChecksum: $local"
        }
        return $local
    }

    $candidateDirs = @(
        (Join-Path $BuildDirectory 'source'),
        (Join-Path $PackerDirectory 'packer_cache')
    )
    foreach ($dir in $candidateDirs) {
        if (-not (Test-Path -LiteralPath $dir)) {
            continue
        }
        foreach ($candidate in Get-ChildItem -LiteralPath $dir -Filter '*.iso' -File -ErrorAction SilentlyContinue) {
            if (Test-FileChecksum -Path $candidate.FullName -Checksum $Checksum) {
                return $candidate.FullName
            }
        }
    }

    $sourceDir = Join-Path $BuildDirectory 'source'
    New-Item -ItemType Directory -Force -Path $sourceDir | Out-Null
    $fileName = 'photon-source.iso'
    try {
        $uri = [Uri]$UrlOrPath
        $leaf = Split-Path -Leaf $uri.AbsolutePath
        if (-not [string]::IsNullOrWhiteSpace($leaf)) {
            $fileName = $leaf
        }
    } catch {
        $fileName = 'photon-source.iso'
    }
    $downloadPath = Join-Path $sourceDir $fileName
    Write-Host "Downloading Photon ISO to $downloadPath"
    Invoke-WebRequest -Uri $UrlOrPath -OutFile $downloadPath
    if (-not (Test-FileChecksum -Path $downloadPath -Checksum $Checksum)) {
        throw "Downloaded ISO checksum does not match IsoChecksum: $downloadPath"
    }
    return $downloadPath
}

function New-RemasteredPhotonIso {
    param(
        [Parameter(Mandatory = $true)][string]$SourceIso,
        [Parameter(Mandatory = $true)][string]$KickstartJson,
        [Parameter(Mandatory = $true)][string]$OutputIso
    )

    $repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')
    $script = Join-Path $repoRoot 'scripts\interop\create_photon_kickstart_iso.py'
    if (-not (Test-Path -LiteralPath $script)) {
        throw "Photon ISO remaster helper not found: $script"
    }
    & python $script --source-iso $SourceIso --kickstart $KickstartJson --output $OutputIso
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create remastered Photon ISO."
    }
}

function ConvertTo-HclLiteral {
    param([AllowNull()]$Value)

    return ConvertTo-Json -InputObject $Value -Compress
}

function Write-PackerVarFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Variables
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $lines = foreach ($key in ($Variables.Keys | Sort-Object)) {
        "$key = $(ConvertTo-HclLiteral -Value $Variables[$key])"
    }
    [System.IO.File]::WriteAllLines($Path, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
}

if ([string]::IsNullOrWhiteSpace($PackerDirectory)) {
    $PackerDirectory = Join-Path $PSScriptRoot '..\..\image\hyperv'
}

if ($BuilderStaticIp -and $BuilderStaticDns.Count -eq 0) {
    throw "-BuilderStaticDns must contain at least one DNS server when -BuilderStaticIp is set."
}

$packerDir = Resolve-RepoPath $PackerDirectory
if ([string]::IsNullOrWhiteSpace($PreparedIsoPath)) {
    $PreparedIsoPath = Join-Path $packerDir 'build\kickstart\labfoundry-photon-with-kickstart.iso'
}

$buildDir = Join-Path $packerDir 'build'
$varFilePath = Join-Path $buildDir 'packer-vars\labfoundry-photon.auto.pkrvars.hcl'
$ksSourceDir = Join-Path $buildDir 'kickstart-src'
$kickstartJson = Join-Path $ksSourceDir 'photon-ks.json'
$resolvedPreparedIsoPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PreparedIsoPath)

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

$sourceIsoPath = Resolve-PhotonSourceIso -UrlOrPath $IsoUrl -Checksum $IsoChecksum -BuildDirectory $buildDir -PackerDirectory $packerDir
New-RemasteredPhotonIso -SourceIso $sourceIsoPath -KickstartJson $kickstartJson -OutputIso $resolvedPreparedIsoPath
$preparedIso = Get-Item -LiteralPath $resolvedPreparedIsoPath -ErrorAction Stop
if ($preparedIso.Length -le 0) {
    throw "Remastered Photon ISO was created but is empty: $resolvedPreparedIsoPath"
}
$preparedIsoChecksum = "sha512:$(Get-FileHashHex -Path $resolvedPreparedIsoPath -Algorithm SHA512)"
Write-Host "Using remastered Photon ISO: $resolvedPreparedIsoPath"
Write-Host "Packer will boot a single DVD with embedded photon-ks.json."

if ($PrepareIsoOnly) {
    Write-Host "Remastered Photon ISO prepared at $resolvedPreparedIsoPath"
    return
}

$packerVariables = @{
    iso_url                  = $resolvedPreparedIsoPath
    iso_checksum             = $preparedIsoChecksum
    iso_contains_kickstart   = $true
    ssh_password             = $SshPassword
    bootstrap_admin_password = $BootstrapAdminPassword
    vm_name                  = $VmName
    switch_name              = $SwitchName
    builder_static_ip        = $BuilderStaticIp
    builder_static_netmask   = $BuilderStaticNetmask
    builder_static_gateway   = $BuilderStaticGateway
    builder_static_dns       = $BuilderStaticDns
}

if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $packerVariables["output_directory"] = $OutputDirectory
}

if (-not [string]::IsNullOrWhiteSpace($SshHost)) {
    $packerVariables["ssh_host"] = $SshHost
}

Write-PackerVarFile -Path $varFilePath -Variables $packerVariables
Write-Host "Using Packer var-file: $varFilePath"

$packerArgs = @(
    $(if ($ValidateOnly) { 'validate' } else { 'build' })
)

if (-not $ValidateOnly -and -not $KeepExistingOutput) {
    Write-Host "Packer build will replace any existing output directory for this build."
    $packerArgs += '-force'
}

$packerArgs += @(
    '-var-file', $varFilePath,
    '.'
)

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

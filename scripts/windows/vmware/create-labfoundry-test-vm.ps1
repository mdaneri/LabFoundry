[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Name = 'LabFoundry-VMware',
    [string]$ApplianceVmxPath = '',
    [string]$OutputDirectory = '',
    [string]$VmrunPath = '',
    [string]$ManagementNetwork = 'VMnet8',
    [string]$SiteANetwork = 'VMnet2',
    [string]$SiteBNetwork = 'VMnet3',
    [string]$TrunkNetwork = 'VMnet4',
    [string]$VdiskManagerPath = '',
    [string]$DepotVmdkPath = '',
    [string]$BackupVmdkPath = '',
    [string]$DepotDiskSize = '500GB',
    [string]$BackupDiskSize = '500GB',
    [switch]$Redeploy,
    [switch]$SkipLabNetworkAdapters,
    [switch]$IncludeLabNetworkAdapters,
    [switch]$ResetDataDisks,
    [switch]$NoStart,
    [switch]$SkipNetworkPrepare,
    [switch]$WaitForIp,
    [switch]$TrustRootCa,
    [int]$IpTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

function Find-LatestApplianceVmx {
    param([string]$RepoRoot)

    $outputRoot = Join-Path $RepoRoot 'image\vmware-workstation\output'
    if (-not (Test-Path -LiteralPath $outputRoot)) {
        throw "VMware Workstation output directory not found: $outputRoot. Build the image first or pass -ApplianceVmxPath."
    }

    $selected = Get-ChildItem -LiteralPath $outputRoot -Recurse -Filter '*.vmx' -File |
        Sort-Object -Property LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $selected) {
        throw "No appliance VMX found under $outputRoot. Build the Workstation image first or pass -ApplianceVmxPath."
    }
    return $selected.FullName
}

function Install-ApplianceRootCa {
    param(
        [Parameter(Mandatory = $true)][string]$IpAddress,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $rootPemPath = Join-Path $env:TEMP "labfoundry-$Name-root-ca.pem"
    $rootCerPath = Join-Path $env:TEMP "labfoundry-$Name-root-ca.cer"
    $rootUrl = "https://$IpAddress/ca/downloads/root-ca.pem"
    Write-Host "Downloading LabFoundry root CA from $rootUrl"
    Invoke-WebRequest -Uri $rootUrl -SkipCertificateCheck -UseBasicParsing -TimeoutSec 30 -OutFile $rootPemPath

    $pem = Get-Content -LiteralPath $rootPemPath -Raw
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPem($pem)
    if ($certificate.Subject -ne $certificate.Issuer -or $certificate.Subject -notlike '*CN=LabFoundry Internal Root CA*') {
        throw "Downloaded certificate is not the expected self-signed LabFoundry root CA: $($certificate.Subject)"
    }

    [System.IO.File]::WriteAllBytes(
        $rootCerPath,
        $certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
    )
    $staleRoots = @(Get-ChildItem Cert:\CurrentUser\Root | Where-Object {
        $_.Subject -like '*CN=LabFoundry Internal Root CA*' -and $_.Thumbprint -ne $certificate.Thumbprint
    })
    foreach ($staleRoot in $staleRoots) {
        Write-Host "Removing stale LabFoundry root CA from current user: $($staleRoot.Thumbprint)"
        certutil.exe -user -delstore Root $staleRoot.Thumbprint | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to remove stale LabFoundry root CA from the current-user Trusted Root store: $($staleRoot.Thumbprint)"
        }
    }
    certutil.exe -f -user -addstore Root $rootCerPath | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to import LabFoundry root CA into the current-user Trusted Root store."
    }
    Write-Host "Trusted LabFoundry root CA for current user: $($certificate.Thumbprint)"
}

function Write-ConnectionSummary {
    param(
        [Parameter(Mandatory = $true)][string]$IpAddress,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$VmxPath,
        [Parameter(Mandatory = $true)][bool]$RootCaTrusted
    )

    function Write-SummaryRow {
        param(
            [Parameter(Mandatory = $true)][string]$Label,
            [Parameter(Mandatory = $true)][string]$Value,
            [System.ConsoleColor]$ValueColor = [System.ConsoleColor]::Green
        )
        Write-Host "  $($Label.PadRight(12))" -ForegroundColor DarkGray -NoNewline
        Write-Host $Value -ForegroundColor $ValueColor
    }

    Write-Host ""
    Write-Host "LabFoundry VMware appliance connection summary" -ForegroundColor Cyan
    Write-SummaryRow -Label "Name:" -Value $Name -ValueColor White
    Write-SummaryRow -Label "VMX:" -Value $VmxPath -ValueColor Gray
    Write-SummaryRow -Label "Console URL:" -Value "https://$IpAddress/"
    Write-SummaryRow -Label "API URL:" -Value "https://$IpAddress/openapi.json"
    Write-SummaryRow -Label "Swagger URL:" -Value "https://$IpAddress/api/docs"
    Write-SummaryRow -Label "Root CA URL:" -Value "https://$IpAddress/ca/downloads/root-ca.pem"
    Write-SummaryRow -Label "SSH:" -Value "ssh admin@$IpAddress"
    if ($RootCaTrusted) {
        Write-SummaryRow -Label "HTTPS trust:" -Value "LabFoundry root CA imported for current user" -ValueColor Green
    } else {
        Write-SummaryRow -Label "HTTPS trust:" -Value "pass -TrustRootCa to trust this appliance root CA" -ValueColor Yellow
    }
    Write-Host ""
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path

if ($SkipLabNetworkAdapters -and $IncludeLabNetworkAdapters) {
    throw "Pass either -SkipLabNetworkAdapters or -IncludeLabNetworkAdapters, not both."
}
if ($TrustRootCa -and $NoStart) {
    throw "Pass -TrustRootCa only when the VM will be started, because the script must fetch the appliance root CA over HTTPS."
}

$effectiveSkipLabNetworkAdapters = -not $IncludeLabNetworkAdapters
if ($SkipLabNetworkAdapters) {
    $effectiveSkipLabNetworkAdapters = $true
}

if (-not $ApplianceVmxPath) {
    $ApplianceVmxPath = Find-LatestApplianceVmx -RepoRoot $repoRoot
}
$resolvedSourceVmx = (Resolve-Path -LiteralPath $ApplianceVmxPath).Path

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "image\vmware-workstation\test-vms\$Name"
}
$resolvedOutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$targetVmx = Join-Path $resolvedOutputDirectory "$Name.vmx"
$resolvedDepotVmdkPath = if ($DepotVmdkPath) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DepotVmdkPath)
} else {
    Join-Path $resolvedOutputDirectory 'LabFoundry-Depot.vmdk'
}
$resolvedBackupVmdkPath = if ($BackupVmdkPath) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BackupVmdkPath)
} else {
    Join-Path $resolvedOutputDirectory 'LabFoundry-Backups.vmdk'
}

if ((Test-Path -LiteralPath $targetVmx) -and -not $Redeploy) {
    throw "VM already exists: $targetVmx. Pass -Redeploy to remove and recreate it, or pass -Name/-OutputDirectory for a new test VM."
}

if (-not $SkipNetworkPrepare) {
    & (Join-Path $PSScriptRoot 'prepare-networks.ps1') `
        -VmrunPath $VmrunPath `
        -ManagementNetwork $ManagementNetwork `
        -SiteANetwork $SiteANetwork `
        -SiteBNetwork $SiteBNetwork `
        -TrunkNetwork $TrunkNetwork `
        -ManagementOnly:$effectiveSkipLabNetworkAdapters
    if (-not $?) {
        throw "VMware Workstation network validation failed. Plain test VM creation uses management only by default; pass -IncludeLabNetworkAdapters only after VMnet2, VMnet3, and VMnet4 exist."
    }
}

if ((Test-Path -LiteralPath $resolvedOutputDirectory) -and $Redeploy) {
    if ($PSCmdlet.ShouldProcess($targetVmx, 'Remove existing LabFoundry Workstation test VM')) {
        if (Test-Path -LiteralPath $targetVmx) {
            & (Join-Path $PSScriptRoot 'remove-labfoundry-vm.ps1') `
                -VmxPath $targetVmx `
                -VmrunPath $VmrunPath
        } else {
            Remove-Item -LiteralPath $resolvedOutputDirectory -Recurse -Force
        }
    }
}

if ($ResetDataDisks) {
    foreach ($diskPath in @($resolvedDepotVmdkPath, $resolvedBackupVmdkPath)) {
        if (-not (Test-Path -LiteralPath $diskPath)) {
            continue
        }
        $resolvedDiskPath = (Resolve-Path -LiteralPath $diskPath).Path
        if (-not $resolvedDiskPath.StartsWith($resolvedOutputDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to reset VMware data disk outside the VM output directory: $resolvedDiskPath"
        }
        if ($PSCmdlet.ShouldProcess($resolvedDiskPath, 'Remove existing LabFoundry VMware data disk')) {
            Remove-Item -LiteralPath $resolvedDiskPath -Force
            Write-Host "Removed existing data disk: $resolvedDiskPath"
        }
    }
}

if ($PSCmdlet.ShouldProcess($targetVmx, "Create LabFoundry Workstation test VM from $resolvedSourceVmx")) {
    & (Join-Path $PSScriptRoot 'create-labfoundry-vm.ps1') `
        -Name $Name `
        -ApplianceVmxPath $resolvedSourceVmx `
        -OutputDirectory $resolvedOutputDirectory `
        -VmrunPath $VmrunPath `
        -VdiskManagerPath $VdiskManagerPath `
        -DepotVmdkPath $resolvedDepotVmdkPath `
        -BackupVmdkPath $resolvedBackupVmdkPath `
        -DepotDiskSize $DepotDiskSize `
        -BackupDiskSize $BackupDiskSize `
        -ManagementNetwork $ManagementNetwork `
        -SiteANetwork $SiteANetwork `
        -SiteBNetwork $SiteBNetwork `
        -TrunkNetwork $TrunkNetwork `
        -SkipLabNetworkAdapters:$effectiveSkipLabNetworkAdapters
    if (-not $?) {
        throw "LabFoundry VMware Workstation VM creation failed."
    }
}

if (-not $NoStart -and -not $WhatIfPreference) {
    & (Join-Path $PSScriptRoot 'start-labfoundry-vm.ps1') `
        -VmxPath $targetVmx `
        -VmrunPath $VmrunPath `
        -Mode gui
    if (-not $?) {
        throw "LabFoundry VMware Workstation VM start failed."
    }
}

Write-Host "LabFoundry Workstation test VM ready: $Name"
Write-Host "Appliance VMX: $targetVmx"

if (-not $NoStart -and -not $WhatIfPreference) {
    $ip = & (Join-Path $PSScriptRoot 'get-labfoundry-vm-ip.ps1') `
        -VmxPath $targetVmx `
        -VmrunPath $VmrunPath `
        -TimeoutSeconds $IpTimeoutSeconds
    if ($WaitForIp) {
        Write-Host "Management IP: $ip"
    }
    if ($TrustRootCa) {
        Install-ApplianceRootCa -IpAddress $ip -Name $Name
    }
    Write-ConnectionSummary -IpAddress $ip -Name $Name -VmxPath $targetVmx -RootCaTrusted ([bool]$TrustRootCa)
}

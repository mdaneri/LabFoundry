[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [string]$IpAddress = '',
    [string]$VmxPath = '',
    [string]$VmrunPath = '',
    [string]$SshUser = 'admin',
    [string]$RemoteDirectory = '/tmp',
    [string]$Python = 'python',
    [switch]$SkipBuild,
    [string]$WheelPath = '',
    [switch]$SkipHostCheck
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
    throw 'vmrun.exe was not found. Install VMware Workstation Pro, pass -IpAddress, or pass -VmrunPath.'
}

function Resolve-RepoRoot {
    param([string]$Path)

    if ($Path) {
        return (Resolve-Path -LiteralPath $Path).Path
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = ''
    )

    $previousLocation = Get-Location
    try {
        if ($WorkingDirectory) {
            Set-Location -LiteralPath $WorkingDirectory
        }
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$FilePath $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
        }
    } finally {
        Set-Location $previousLocation
    }
}

function Get-LabFoundryRunningVmx {
    param([string]$ResolvedVmrun)

    $running = @(& $ResolvedVmrun -T ws list 2>$null | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) {
        throw "vmrun list failed with exit code $LASTEXITCODE."
    }
    $candidates = @($running | Where-Object { $_ -match '(?i)LabFoundry' })
    if ($candidates.Count -eq 1) {
        return $candidates[0]
    }
    if ($candidates.Count -gt 1) {
        throw "Multiple running LabFoundry VMware VMs found. Pass -VmxPath explicitly: $($candidates -join '; ')"
    }
    if ($running.Count -eq 1) {
        return $running[0]
    }
    throw 'No running LabFoundry VMware VM was found. Pass -IpAddress or -VmxPath.'
}

function Get-GuestIpAddress {
    param(
        [string]$ResolvedVmrun,
        [string]$ResolvedVmxPath
    )

    $ip = (& $ResolvedVmrun -T ws getGuestIPAddress $ResolvedVmxPath -wait 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or $ip -notmatch '^\d+\.\d+\.\d+\.\d+$' -or $ip -like '169.254.*') {
        throw "No usable IPv4 address reported for VM '$ResolvedVmxPath'. Confirm open-vm-tools is running in the guest."
    }
    return $ip
}

function Get-WheelPath {
    param(
        [string]$Path,
        [string]$Root
    )

    if ($Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "Wheel not found: $Path"
        }
        return (Resolve-Path -LiteralPath $Path).Path
    }
    $wheel = Get-ChildItem -LiteralPath (Join-Path $Root 'dist') -Filter 'labfoundry-*.whl' -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $wheel) {
        throw "No LabFoundry wheel found under $(Join-Path $Root 'dist'). Run without -SkipBuild or pass -WheelPath."
    }
    return $wheel.FullName
}

function Test-RequiredCommand {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$Name was not found on PATH. Install Windows OpenSSH Client or run from a shell where $Name is available."
    }
    return $command.Source
}

function Invoke-HostOpenApiCheck {
    param([string]$HostAddress)

    $url = "https://$HostAddress/openapi.json"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -k -f -sS $url | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Host OpenAPI check failed: $url"
        }
        return
    }

    try {
        Invoke-WebRequest -Uri $url -UseBasicParsing -SkipCertificateCheck | Out-Null
    } catch [System.Management.Automation.ParameterBindingException] {
        $previousCallback = [System.Net.ServicePointManager]::ServerCertificateValidationCallback
        try {
            [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
            Invoke-WebRequest -Uri $url -UseBasicParsing | Out-Null
        } finally {
            [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $previousCallback
        }
    }
}

$resolvedRepoRoot = Resolve-RepoRoot -Path $RepoRoot

if (-not $SkipBuild) {
    New-Item -ItemType Directory -Force -Path (Join-Path $resolvedRepoRoot 'dist') | Out-Null
    Write-Host "Building LabFoundry wheel..."
    Invoke-CheckedCommand -FilePath $Python -Arguments @('-m', 'pip', 'wheel', '.', '-w', 'dist') -WorkingDirectory $resolvedRepoRoot
}

$resolvedWheelPath = Get-WheelPath -Path $WheelPath -Root $resolvedRepoRoot
$wheelName = Split-Path -Leaf $resolvedWheelPath
$remoteWheelPath = "$($RemoteDirectory.TrimEnd('/'))/$wheelName"
$remoteScriptPath = "$($RemoteDirectory.TrimEnd('/'))/labfoundry-deploy-wheel.sh"

if (-not $IpAddress) {
    $resolvedVmrun = Resolve-VmrunPath -Path $VmrunPath
    if (-not $VmxPath) {
        $VmxPath = Get-LabFoundryRunningVmx -ResolvedVmrun $resolvedVmrun
    }
    $resolvedVmxPath = (Resolve-Path -LiteralPath $VmxPath).Path
    $IpAddress = Get-GuestIpAddress -ResolvedVmrun $resolvedVmrun -ResolvedVmxPath $resolvedVmxPath
}

Test-RequiredCommand -Name 'scp' | Out-Null
Test-RequiredCommand -Name 'ssh' | Out-Null

$deployScript = @'
#!/bin/sh
set -eu

wheel="${1:?wheel path required}"
venv="/opt/labfoundry/.venv"
python="$venv/bin/python"

if [ ! -x "$python" ]; then
    echo "LabFoundry venv python not found or not executable: $python" >&2
    exit 2
fi

"$python" -m pip install --force-reinstall --no-deps "$wheel"
find "$venv" -type d -exec chmod 755 {} \;
find "$venv" -type f -exec chmod 644 {} \;
find "$venv/bin" -type f -exec chmod 755 {} \;
systemctl restart labfoundry
systemctl is-active labfoundry
curl -f http://127.0.0.1:8000/openapi.json >/dev/null
echo "LabFoundry service restarted and loopback OpenAPI is reachable."
'@

$tempScript = Join-Path ([System.IO.Path]::GetTempPath()) "labfoundry-deploy-wheel-$([guid]::NewGuid().ToString('N')).sh"
[System.IO.File]::WriteAllText($tempScript, ($deployScript -replace "`r?`n", "`n"), [System.Text.UTF8Encoding]::new($false))

try {
    Write-Host "Uploading wheel to $SshUser@$IpAddress`:$remoteWheelPath"
    Invoke-CheckedCommand -FilePath 'scp' -Arguments @($resolvedWheelPath, "${SshUser}@${IpAddress}:$remoteWheelPath")
    Write-Host "Uploading remote installer to $SshUser@$IpAddress`:$remoteScriptPath"
    Invoke-CheckedCommand -FilePath 'scp' -Arguments @($tempScript, "${SshUser}@${IpAddress}:$remoteScriptPath")

    Write-Host "Installing wheel and restarting labfoundry.service..."
    Invoke-CheckedCommand -FilePath 'ssh' -Arguments @('-t', "${SshUser}@${IpAddress}", "sudo sh '$remoteScriptPath' '$remoteWheelPath'")

    if (-not $SkipHostCheck) {
        Write-Host "Checking host-facing OpenAPI..."
        Invoke-HostOpenApiCheck -HostAddress $IpAddress
    }

    Write-Host "Deployed $wheelName to $IpAddress and verified labfoundry.service."
} finally {
    Remove-Item -LiteralPath $tempScript -Force -ErrorAction SilentlyContinue
}

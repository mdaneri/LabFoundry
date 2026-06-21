[CmdletBinding()]
param(
    [string]$Name = 'LabFoundry',
    [string]$ManagementUrl = 'https://labfoundry.internal/'
)

$ErrorActionPreference = 'Stop'

$vm = Get-VM -Name $Name -ErrorAction Stop
if ($vm.State -ne 'Running') {
    throw "LabFoundry VM is not running. Current state: $($vm.State)"
}

Write-Host "Smoke test scaffold for $Name"
Write-Host "Expected management URL: $ManagementUrl"
Write-Host "TODO: verify boot, management reachability, setup, SiteA-to-SiteB routing, WAN latency/loss/bandwidth, repository, SFTP, and reboot persistence."

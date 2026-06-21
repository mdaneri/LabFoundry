[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'

$switches = @(
    @{ Name = 'LabFoundry-Mgmt'; Type = 'Internal' },
    @{ Name = 'LabFoundry-SiteA'; Type = 'Private' },
    @{ Name = 'LabFoundry-SiteB'; Type = 'Private' },
    @{ Name = 'LabFoundry-Trunk'; Type = 'Private' }
)

foreach ($switch in $switches) {
    $existing = Get-VMSwitch -Name $switch.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Switch already exists: $($switch.Name)"
        continue
    }

    if ($PSCmdlet.ShouldProcess($switch.Name, "Create $($switch.Type) Hyper-V switch")) {
        New-VMSwitch -Name $switch.Name -SwitchType $switch.Type | Out-Null
        Write-Host "Created switch: $($switch.Name)"
    }
}

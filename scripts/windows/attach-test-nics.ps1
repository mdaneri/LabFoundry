[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Name = 'LabFoundry'
)

$ErrorActionPreference = 'Stop'

$adapters = @(
    @{ Name = 'SiteA'; SwitchName = 'LabFoundry-SiteA' },
    @{ Name = 'SiteB'; SwitchName = 'LabFoundry-SiteB' },
    @{ Name = 'Trunk'; SwitchName = 'LabFoundry-Trunk' }
)

foreach ($adapter in $adapters) {
    if ($PSCmdlet.ShouldProcess($Name, "Attach $($adapter.Name) NIC")) {
        Add-VMNetworkAdapter -VMName $Name -Name $adapter.Name -SwitchName $adapter.SwitchName
        Write-Host "Attached $($adapter.Name) to $($adapter.SwitchName)"
    }
}

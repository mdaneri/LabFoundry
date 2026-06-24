[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$LabName = 'LabFoundryLifecycle'
)

$ErrorActionPreference = 'Stop'

if (-not $LabName.StartsWith('LabFoundryLifecycle')) {
    throw "Refusing VM cleanup for prefix '$LabName'. Cleanup is limited to LabFoundryLifecycle* VM names."
}

$reserved = @('LabFoundry', 'LabFoundry-Photon-Builder')
$vms = Get-VM -ErrorAction SilentlyContinue |
    Where-Object { $_.Name.StartsWith($LabName) -and $reserved -notcontains $_.Name } |
    Sort-Object -Property Name

if (-not $vms) {
    Write-Host "No lifecycle VMs found for prefix: $LabName"
    return
}

foreach ($vm in $vms) {
    if ($reserved -contains $vm.Name) {
        throw "Refusing to remove reserved VM '$($vm.Name)'."
    }
    if (-not $vm.Name.StartsWith($LabName)) {
        throw "Refusing to remove VM '$($vm.Name)' because it does not start with '$LabName'."
    }
    if ($PSCmdlet.ShouldProcess($vm.Name, 'Stop and remove lifecycle VM')) {
        Stop-VM -Name $vm.Name -Force -TurnOff -ErrorAction SilentlyContinue
        Remove-VM -Name $vm.Name -Force
        Write-Host "Removed lifecycle VM: $($vm.Name)"
    }
}

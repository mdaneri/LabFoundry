# VMware Workstation Lifecycle Testing

LabFoundry can run a VMware Workstation lifecycle lab alongside the Hyper-V
lab. The Workstation path uses VMX/VMDK artifacts and `vmrun.exe`, then
delegates appliance behavior checks to the shared Python lifecycle runner.

## Topology

The default lifecycle lab creates isolated VM directories under:

```text
test-results/vmware-workstation-lifecycle/<timestamp>/vms
```

The appliance VMX is copied from the selected Workstation image output. Client
VMs use an Alpine cloud VMDK prepared from the same upstream QCOW2 source as the
Hyper-V lifecycle client.

Default vmnets:

- `vmnet8` for management, defaulting to `192.168.167.0/24`
- `vmnet2` for SiteA
- `vmnet3` for WAN/SiteB
- `vmnet4` for trunk-like validation

Check the current Workstation host network inventory with:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/prepare-vmware-networks.ps1 `
  -PlanOnly
```

If vmnets are missing or the management subnet does not match the appliance
address plan, adjust them in VMware Virtual Network Editor before running the
interop test.

The Workstation management subnet must remain separate from the Hyper-V
management subnet. The default appliance address is `192.168.167.10`, with
`192.168.167.2` as the VMware NAT gateway.

## Build The Appliance

Build the Workstation appliance with:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/build-photon-vmware-image.ps1 `
  -IsoUrl "<photon-iso-url-or-path>" `
  -IsoChecksum "<packer-checksum>" `
  -EnableRealSystemAdapters
```

The wrapper shares Photon ISO remastering, kickstart rendering, checksum
validation, and Packer var-file generation with the Hyper-V build wrapper.
Both wrappers use `image/common/source` for the original Photon ISO download
cache so the source ISO is not duplicated under each target.
The Workstation image installs `open-vm-tools`; the Hyper-V image keeps the
`hyper-v` package and Hyper-V guest daemons.
The Workstation build wrapper opens a visible VMware console by default. Use
`-Headless` only when an unattended build is preferred.

## Single-Command Run

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/invoke-vmware-lifecycle-test.ps1
```

The wrapper selects the newest appliance VMX under
`image/vmware-workstation/output`, prepares the tiny Alpine client VMDK when
needed, creates a unique `LabFoundryWorkstationLifecycle-*` lab, runs the
initial lifecycle scenario, and by default runs the restored backup/restore
pass. Pass `-SkipBackupRestoreTest` only when the older single-pass behavior is
intended.

Useful commands:

```powershell
# Print selected paths and topology without creating VMs.
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/invoke-vmware-lifecycle-test.ps1 `
  -PlanOnly

# Validate Workstation vmnet inventory.
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/invoke-vmware-lifecycle-test.ps1 `
  -PrepareNetworksOnly
```

## Normal Test VM

For a normal Workstation appliance VM, separate from the lifecycle lab, use:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/create-labfoundry-vmware-test-vm.ps1 `
  -Redeploy `
  -ResetDataDisks `
  -WaitForIp
```

That is the Workstation counterpart to
`scripts/windows/create-labfoundry-hyperv-test-vm.ps1`.
It defaults to the management vmnet only; pass `-IncludeLabNetworkAdapters`
after creating the SiteA, WAN/SiteB, and trunk-like vmnets.

## Fidelity Boundary

VMware Workstation vmnets provide isolated layer-2 segments, but they do not
match Hyper-V's explicit access/trunk VLAN port model. The Workstation lifecycle
therefore validates the appliance workflow, management reachability, service
apply behavior, backup/restore portability, and host/client integration where
separate vmnets are equivalent. Keep Hyper-V lifecycle results as the
authoritative acceptance evidence for VLAN access/trunk behavior until a
Workstation-specific tagged-client strategy is added and proven.

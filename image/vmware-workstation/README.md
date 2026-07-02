# LabFoundry Photon OS VMware Workstation Image

This target builds a Photon OS 5.0 VMware Workstation VMX/VMDK appliance with
the same LabFoundry control plane provisioning used by the Hyper-V image.

## Prerequisites

- VMware Workstation Pro with `vmrun.exe` available under
  `C:\Program Files\VMware\VMware Workstation`.
- Packer `>= 1.10`.
- `qemu-img` when preparing the tiny Alpine lifecycle client VMDK.
- Photon OS 5.0 ISO URL and checksum.

The template uses the Packer VMware Desktop plugin:

```hcl
source = "github.com/vmware/vmware"
```

Run `packer init` from this directory before validating or building.

## Build

Use the wrapper instead of raw `packer build`; it creates the remastered Photon
ISO with `photon-ks.json` and the LabFoundry GRUB auto-install entry.
The original Photon source ISO is shared with the Hyper-V image path under
`image/common/source`; only the target-specific remastered kickstart ISO is
written under this image directory.
Workstation builds show the VMware console by default so boot/install progress
is visible; pass `-Headless` for unattended runs.

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/build-photon-image.ps1 `
  -IsoUrl "https://packages.vmware.com/photon/5.0/GA/iso/photon-5.0-dde71ec57.x86_64.iso" `
  -IsoChecksum "sha512:<checksum>"
```

For lifecycle/demo images that should use real appliance adapters:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/build-photon-image.ps1 `
  -IsoUrl "<photon-iso-url-or-path>" `
  -IsoChecksum "<packer-checksum>" `
  -EnableRealSystemAdapters
```

## Networking

The default Workstation builder and lifecycle scripts expect:

- management: `vmnet8`, with the LabFoundry appliance address derived from the
  selected vmnet subnet by default
- SiteA: `vmnet2`
- WAN/SiteB: `vmnet3`
- trunk-like validation segment: `vmnet4`

Validate the current Workstation network inventory with:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/prepare-networks.ps1 `
  -PlanOnly
```

The Workstation management subnet intentionally stays separate from the Hyper-V
lab subnet. The build wrapper reads the selected VMware network before
rendering Packer variables. For NAT/host-only vmnets it uses
`vmrun -T ws listHostNetworks`; for bridged `vmnet0` it falls back to the active
Windows IPv4 interface, or the interface named by `-BridgedInterfaceAlias`.
Unless overridden, it chooses host offsets `.30` for the temporary Photon
builder SSH address, `.10` for the final appliance management address, and the
VMware/host gateway for routing. Pass `-BuilderStaticIp`,
`-BuilderStaticGateway`, `-FinalMgmtAddress`, and `-FinalMgmtGateway` together
only when a different address plan is intentional.

Create or adjust missing lifecycle vmnets in VMware Virtual Network Editor. The
scripts intentionally do not rewrite global Workstation vmnet configuration
because `vnetlib.exe` behavior is version-sensitive and can affect unrelated
VMs.

## Lifecycle

Run the Workstation lifecycle wrapper after building an appliance VM:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/invoke-lifecycle-test.ps1
```

The wrapper writes evidence under
`test-results/vmware-workstation-lifecycle/<timestamp>`. It keeps the Python
appliance assertions shared with the Hyper-V lifecycle runner.

Pass `-PlanOnly` to print the selected VMX, client VMDK, vmnets, and result path
without creating VMs.

## Boot A Test Appliance

Create and start a normal Workstation test appliance from the latest built VMX:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/create-labfoundry-test-vm.ps1 `
  -Redeploy `
  -ResetDataDisks `
  -WaitForIp
```

The wrapper creates fresh Depot and Backups data VMDKs when needed, and
`-ResetDataDisks` removes those data VMDKs before recreating them. Pass
`-IncludeLabNetworkAdapters` only after `vmnet2`, `vmnet3`, and `vmnet4` exist
for the SiteA, WAN/SiteB, and trunk-like lifecycle networks.
On first boot, `labfoundry-data-disks.service` formats blank attached data
VMDKs, labels them as `LABFOUNDRY_DEPOT` and `LABFOUNDRY_BKUP`, writes
`/etc/fstab`, and mounts them at `/mnt/labfoundry-vcf-offline-depot` and
`/mnt/labfoundry-vcf-backups` before the LabFoundry control plane starts.

## Fidelity Notes

Workstation vmnets are isolated layer-2 segments. They are useful for appliance
management, SiteA, WAN, and trunk-like separation, but they do not expose the
same explicit Hyper-V access/trunk port VLAN controls. Treat the Workstation
lifecycle as parity for appliance behavior and host/client integration where the
vmnet topology can represent it; keep Hyper-V as the authoritative VLAN
access/trunk acceptance path until a Workstation VLAN-specific client strategy
is validated.

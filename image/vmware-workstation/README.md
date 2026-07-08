# LabFoundry Photon OS VMware Workstation Image

This target builds a Photon OS 5.0 VMware Workstation VMX/VMDK appliance with
the same LabFoundry control plane provisioning used by the Hyper-V image.
Fresh appliances enable the integrated CA on deployed-VM first boot, serve the
management console/API over CA-backed HTTPS/443, and keep management HTTP/80
redirect-only. ESXi PXE remains the only served HTTP payload.

## Prerequisites

- VMware Workstation Pro with `vmrun.exe` available under
  `C:\Program Files\VMware\VMware Workstation`.
- VMware Workstation's bundled OVF Tool with `ovftool.exe` available under
  `C:\Program Files\VMware\VMware Workstation\OVFTool` when exporting OVF/OVA artifacts.
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

Before `packer build -force` replaces the Workstation output directory, the
wrapper checks for an existing output VMX and unregisters it with
`vmrun -T ws unregister`. The `vmrun.exe` path is resolved through the same
Workstation discovery path used by the rest of the VMware scripts, and the
cleanup is scoped to this image target's configured output directory.

For lifecycle/demo images that should use real appliance adapters:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/build-photon-image.ps1 `
  -IsoUrl "<photon-iso-url-or-path>" `
  -IsoChecksum "<packer-checksum>" `
  -EnableRealSystemAdapters
```

The built VMX keeps the first adapter on `-VmnetName` as management-only and
adds a second `vmxnet3` adapter on `-ServiceVmnetName` for service traffic. The
service network defaults to Workstation's built-in host-only `VMnet1`.

## Networking

The default Workstation builder and lifecycle scripts expect:

- management: `VMnet8`, with the LabFoundry appliance address assigned by
  DHCP by default
- services: `VMnet1`
- SiteA: `VMnet2`
- WAN/SiteB: `VMnet3`
- trunk-like validation segment: `VMnet4`

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
Unless overridden, it chooses host offset `.30` for the temporary Photon
builder SSH address and uses DHCP for the final appliance management address.
For NAT vmnets, the wrapper points the temporary builder at the VMware NAT
gateway DNS proxy, normally host offset `.2`, instead of copying unrelated host
DNS servers into the Photon kickstart. Pass `-BuilderStaticIp`,
`-BuilderStaticGateway`, and `-BuilderStaticDns` together only when a different
builder address plan is intentional. Pass `-FinalMgmtAddress` and
`-FinalMgmtGateway` only when a static final management address is intentional.
Pass `-ServiceVmnetName` only when the second appliance NIC should attach to a
different Workstation network.

Create or adjust missing lifecycle vmnets in VMware Virtual Network Editor. The
scripts intentionally do not rewrite global Workstation vmnet configuration
because `vnetlib.exe` behavior is version-sensitive and can affect unrelated
VMs.

## OVF / OVA Export

After a VMware image build, export a deployable OVF folder and OVA archive:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/export-ovf.ps1 `
  -SourceVmxPath image/vmware-workstation/output/labfoundry-photon-vmware-workstation/LabFoundry-Photon-Builder-VMware.vmx `
  -Name LabFoundry-Photon `
  -Force
```

The export script runs OVF Tool, adds LabFoundry vApp properties and appliance
network mappings to the OVF descriptor, regenerates the manifest, and packages
the folder as an OVA unless `-NoOva` is passed. The descriptor exposes two
network mappings for vSphere/ESXi import: `LabFoundry Management Network` for
the first adapter, which remains management-only as `eth0`, and
`LabFoundry Services Network` for the second adapter used by DNS, DHCP, CA,
depot, PXE, KMS, and other LabFoundry-managed services. The OVF properties are
intended for vSphere/ESXi import:

| Property | Required | Description |
| --- | --- | --- |
| `labfoundry.management_mode` | no | `dhcp` by default. Use `static` only when `labfoundry.cidr` and `labfoundry.gateway` should be enforced on first boot. |
| `labfoundry.cidr` | no | Static management IPv4 CIDR for `eth0`, for example `192.168.10.10/24`; required only when management mode is `static`. |
| `labfoundry.gateway` | no | IPv4 gateway for the management network when management mode is `static`. |
| `labfoundry.fqdn` | yes | Appliance FQDN applied to Photon OS and LabFoundry desired state. |
| `labfoundry.dns_servers` | no | Optional resolver IPs separated by commas, spaces, or new lines. Blank DHCP deployments keep lease-provided DNS. |
| `labfoundry.ntp_servers` | no | Optional NTP names or IPs. Blank keeps the image defaults. |
| `labfoundry.admin_password` | yes | Initial LabFoundry web `admin` password. |
| `labfoundry.root_password` | yes | Photon root console password. Root SSH remains disabled by default. |

On first boot from an OVF/OVA deployment, `labfoundry-vmware-ovf-customize`
reads those properties through VMware Tools before LabFoundry starts. DHCP
management writes `DHCP=ipv4` for `eth0`; static management writes the supplied
CIDR and gateway. It also writes resolver overrides when supplied, hostname,
root password, and bootstrap admin password once, then records a redacted marker
under `/var/lib/labfoundry`.
Passwords are consumed as deployment inputs and are not printed in the marker or
customization log.

## Lifecycle

Run the Workstation lifecycle wrapper after building an appliance VM:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/invoke-lifecycle-test.ps1
```

The wrapper writes evidence under
`test-results/vmware-workstation-lifecycle/<timestamp>`. Unless
`-ApplianceIPAddress` is passed, it waits for VMware Tools to report the DHCP
management address and records it in `discovered-appliance.json` before running
HTTP and SSH probes. It keeps the Python appliance assertions shared with the
Hyper-V lifecycle runner.

Pass `-PlanOnly` to print the selected VMX, client VMDK, vmnets, and result path
without creating VMs.

## Boot A Test Appliance

Create and start a normal Workstation test appliance from the latest built VMX:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/vmware/create-labfoundry-test-vm.ps1 `
  -Redeploy `
  -ResetDataDisks `
  -WaitForIp `
  -TrustRootCa
```

The wrapper creates fresh Depot and Backups data VMDKs when needed, and
`-ResetDataDisks` removes those data VMDKs before recreating them. Pass
`-IncludeLabNetworkAdapters` only after `VMnet2`, `VMnet3`, and `VMnet4` exist
for the SiteA, WAN/SiteB, and trunk-like lifecycle networks.
`-TrustRootCa` downloads the freshly deployed appliance root CA, removes stale
LabFoundry root CAs from the current-user Trusted Root store, and trusts the new
root so Edge and the Codex integrated browser accept the first-boot HTTPS cert.
The root CA is generated by `labfoundry-bootstrap-https.service` on each
deployed VM's first boot, not baked into the reusable Packer-built VMX.
After the VM starts, the wrapper prints a connection summary with the HTTPS
console URL, Swagger URL, OpenAPI URL, root certificate URL, and
`ssh admin@<appliance-ip>` command.
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

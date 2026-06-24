# LabFoundry Photon OS Hyper-V Image

This directory contains the first real-OS appliance image path for LabFoundry.
It builds a Photon OS 5.0 Hyper-V VHDX and provisions the FastAPI control plane
as a systemd service.

## Host Prerequisites

- Windows host with Hyper-V enabled.
- Run Packer from an elevated PowerShell session or as a user in the
  `Hyper-V Administrators` group.
- Packer `>= 1.10`.
- Hyper-V `Default Switch` available for the Packer build VM. The build uses
  this switch by default because it provides a host-side IP, DHCP, and outbound
  internet for the kickstart file and `tdnf update`.
- LabFoundry lab switches created for the finished appliance VM:

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/windows/create-hyperv-switches.ps1
```

## Build Inputs

Photon publishes the ISO and checksum from the Photon OS download page. The
current LabFoundry build target uses the Photon OS 5.0 GA full ISO:

```powershell
cd image/hyperv
packer init .
packer build `
  -var "iso_url=https://packages.vmware.com/photon/5.0/GA/iso/photon-5.0-dde71ec57.x86_64.iso" `
  -var "iso_checksum=sha512:6a7a258399a258da742032987c043ab25503698d35edafaf1ae000f12127da1a161d8b84caa17fd8f23d129e81e1faa7ab087c20ab9229772a643f8f9475305f" `
  -var "builder_static_ip=<photon-builder-ip>" `
  -var "builder_static_netmask=<photon-builder-netmask>" `
  -var "builder_static_gateway=<default-switch-gateway>" `
  -var "ssh_password=<one-time-build-root-password>" `
  -var "bootstrap_admin_password=<initial-labfoundry-admin-password>" `
  .
```

For Hyper-V Default Switch, `builder_static_gateway` is the Windows host-side
vEthernet address that Packer logs as `Host IP for the HyperV machine`, such as
`172.30.0.1`. Choose `builder_static_ip` from the same subnet, for example
`172.30.14.160`, and use the matching netmask, for example `255.255.240.0` for
a `/20` Default Switch network. When `builder_static_ip` is set, the template
automatically uses it as Packer's SSH target.

Use single quotes around passwords that contain PowerShell metacharacters:

```powershell
-var 'ssh_password=<one-time-build-root-password>'
-var 'bootstrap_admin_password=<initial-labfoundry-admin-password>'
```

`ssh_password` is for the temporary installer/root credentials used during the
image build. `bootstrap_admin_password` is the initial LabFoundry web login
password for `admin`. If `bootstrap_admin_password` is omitted, the build falls
back to `ssh_password` for compatibility with early appliance images.

If Packer prints `Using SSH communicator to connect: <ip>` and waits even
though the VM is reachable, test the exact same credentials from the Windows
host:

```powershell
ssh root@<photon-builder-ip>
```

The Packer communicator uses the temporary `labfoundry-build` user, port `22`,
password authentication, and a longer SSH timeout to allow Photon installation
and reboot to finish. Provisioning removes the temporary sudoers entry before
the image is finalized.

Use `-var "switch_name=<switch>"` only if the replacement switch has a host
adapter IP, DHCP for the builder VM, and internet access. The LabFoundry
internal/private lab switches are intended for the finished appliance VM, not
for the Packer installer VM.

Packer logs a line like `Host IP for the HyperV machine: 172.30.0.1`. That is
the Windows host-side Default Switch address used for the kickstart HTTP URL;
it is not the Photon guest SSH address. The Photon guest address is the IPv4
shown by Hyper-V Manager for `LabFoundry-Photon-Builder`, for example
`172.30.14.160`.

The build updates Photon packages during provisioning. On June 21, 2026, the
Photon 5.0 updates repo exposed `python3` as `3.14.5-2.ph5`; keep the image
builder on the updated repo stream rather than relying only on the GA ISO
package set.

If the VM stops at the Photon license agreement or disk selection screen, the
builder did not load the kickstart file. Stop the build, make sure this
directory is current, and rerun `packer build .`; the template should boot
Photon through the GRUB command line with `ks=http://...
insecure_installation=1 photon.media=cdrom`. The built-in Packer HTTP server is
pinned to port `8591` to make troubleshooting simpler.

If Photon installs and SSH works from the Windows host but Packer remains at
`Waiting for SSH to become available`, query the IPv4 reported by Hyper-V:

```powershell
powershell.exe -ExecutionPolicy Bypass -File ..\..\scripts\windows\get-labfoundry-vm-ip.ps1 `
  -Name LabFoundry-Photon-Builder `
  -SwitchName "Default Switch"
```

Then verify SSH:

```powershell
ssh root@<photon-vm-ip>
```

The Packer template leaves `ssh_host` unset unless `builder_static_ip` is set,
so the Hyper-V builder can discover the Photon guest IP through KVP by default.
If SSH is reachable but Packer still does not detect the guest IP, stop the
build and rerun with either `builder_static_ip` or a queried `ssh_host`:

```powershell
$photonVmIp = powershell.exe -ExecutionPolicy Bypass -File ..\..\scripts\windows\get-labfoundry-vm-ip.ps1 `
  -Name LabFoundry-Photon-Builder `
  -SwitchName "Default Switch"

packer build `
  -var "ssh_host=$photonVmIp" `
  -var "iso_url=https://packages.vmware.com/photon/5.0/GA/iso/photon-5.0-dde71ec57.x86_64.iso" `
  -var "iso_checksum=sha512:6a7a258399a258da742032987c043ab25503698d35edafaf1ae000f12127da1a161d8b84caa17fd8f23d129e81e1faa7ab087c20ab9229772a643f8f9475305f" `
  -var "ssh_password=<one-time-build-root-password>" `
  -var "bootstrap_admin_password=<initial-labfoundry-admin-password>" `
  .
```

The helper reads the current Photon guest IPv4 from Hyper-V and filters out the
host-side Default Switch address.

Photon's Hyper-V guest integration package is `hyper-v`. The kickstart and
provisioning scripts install it and enable `hv_kvp_daemon`, `hv_fcopy_daemon`,
and `hv_vss_daemon` so Hyper-V can report guest metadata such as IP addresses.
Do not install `open-vm-tools` in this Hyper-V image; reserve VMware Tools for a
future vSphere/ESXi image path. Keep the `ssh_host` override as a fallback for
early build runs where the guest IP is visible manually before Hyper-V reports
it to Packer.

## What Provisioning Installs

- Photon packages updated from the configured Photon 5.0 repositories, with a
  second `tdnf -y update` pass after required appliance packages are installed.
- `labfoundry` system user.
- `/opt/labfoundry` application install.
- `/etc/labfoundry/labfoundry.env` production environment file.
- `/etc/labfoundry/build-info` recording build time, Photon release, kernel,
  Python, and the package update marker.
- A masked `systemd-ssh-generator` so Photon does not try to advertise or bind
  automatic SSH-over-AF_VSOCK sockets on Hyper-V. Normal TCP SSH remains
  provided by `sshd`.
- `/var/lib/labfoundry` durable SQLite state.
- `/var/log/labfoundry` local service logs.
- Fixed appliance mounts under `/mnt/labfoundry-vcf-*`.
- `/etc/systemd/system/labfoundry.service`.
- `/etc/systemd/system/labfoundry-firewall.service` loading the nftables
  management firewall.
- `dnsmasq` for the shared DNS/DHCP appliance service.
- `/opt/labfoundry/bin/labfoundry-helper` constrained appliance helper.
- `/etc/sudoers.d/labfoundry-helper` constrained helper allowlist.

The generated appliance keeps `LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS=true` until
each helper-backed apply unit is reviewed and promoted.
Provisioning writes both `LABFOUNDRY_SECRET_KEY` and
`LABFOUNDRY_SECRETS_KEY`; the latter encrypts CA root and leaf private-key
material stored in the LabFoundry database and must be preserved for settings
backup portability.
Firewall desired state is nftables-backed. Provisioning installs nftables,
loads `/etc/labfoundry/nftables.d/labfoundry.nft`, and disables the older
Photon iptables service so LabFoundry has a single firewall owner.

DNS/DHCP desired state is dnsmasq-backed. Real `/appliance-apply` stages the
rendered config under `/var/lib/labfoundry/apply/dnsmasq/`, validates it with
`dnsmasq --test`, installs `/etc/labfoundry/dnsmasq.d/labfoundry.conf`, and
reloads or restarts `dnsmasq` through `labfoundry-helper`.
The rendered config uses `/var/lib/labfoundry/dnsmasq/dhcp.leases` for DHCP
leases, and the helper exposes only that allowlisted lease readback path.
DHCP scopes should bind to access physical interfaces with IP CIDR or enabled
VLAN interfaces with IP CIDR, not trunk or addressless physical interfaces.

Certificate Authority desired state is LabFoundry CA-backed. Real
`/appliance-apply` stages `/var/lib/labfoundry/apply/ca/labfoundry-ca.json`,
validates the staged CA/certificate payload through `labfoundry-helper`, and
writes public CA bundles plus service certificate/key files under
`/etc/labfoundry`. Private keys are encrypted in the database with
`LABFOUNDRY_SECRETS_KEY`; previews, jobs, and logs must remain redacted.

Local Users desired state is Photon OS account-backed. Real `/appliance-apply`
stages `/var/lib/labfoundry/apply/local-users/labfoundry-users.json`, validates
LabFoundry-owned local usernames, creates or updates enabled users under
`/var/lib/labfoundry/users` with the per-user desired shell, removes disabled or
removed managed users with `userdel -r`, applies staged unlock requests with
`passwd -u` and `faillock --reset`, writes the desired PAM/pwquality password
policy, and sends in-memory pending passwords to `chpasswd` over stdin. Password
previews, job results, and logs should show only status and counts.
`labfoundry.service` preserves
`LABFOUNDRY_HELPER_USE_SYSTEMD_RUN=1` through sudo so account-mutating helper
commands can run as transient systemd units outside the control-plane service
sandbox while still using the constrained helper allowlist.
Nginx owns the public management front door. Appliance Settings apply writes
`/etc/nginx/conf.d/labfoundry.conf`,
`/etc/labfoundry/nginx/sites.d/management.conf`, and a loopback-only
`labfoundry.service` override. When CA-backed management UI HTTPS is enabled,
nginx redirects public HTTP/80 to HTTPS/443 and reverse-proxies HTTPS to uvicorn
on `127.0.0.1:8000`. When HTTPS is disabled, including after factory reset plus
apply, nginx serves public HTTP/80 as a plain reverse proxy to the same loopback
upstream and does not expose a management HTTPS listener. The helper disables
the retired `labfoundry-http-redirect.service` if present, reloads
nginx/systemd, and schedules a short delayed `labfoundry.service` restart so the
global apply job can finish before uvicorn moves behind nginx.

Provisioning creates the bootstrap admin OS account under
`/var/lib/labfoundry/users/<admin>` with `/sbin/nologin` and sets the same
bootstrap password used for the initial web login, so the admin account exists
on Photon before first appliance apply.

VCF Backups desired state is OpenSSH-backed. Provisioning leaves the default
`vcf-backup` account absent from Photon OS until Local Users apply creates it.
When VCF Backup desired state is off, LabFoundry keeps the default `vcf-backup`
user disabled so the next Local Users apply removes the Photon OS account.
Real `/appliance-apply` stages the rendered drop-in under
`/var/lib/labfoundry/apply/vcf-backups/`, validates that it is a
LabFoundry-rendered `Match User` config for an existing OS account, installs
`/etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf`, prepares the fixed
`/mnt/labfoundry-vcf-backups` chroot and `/backups` upload directory, and
restarts `sshd` through `labfoundry-helper`. Firewall apply still owns the
selected interface and port allow rule. Apply Local Users first when the
selected SFTP user is new, disabled/enabled, has a pending password, changes shell, or has an unlock request.

The firewall preview derives LabFoundry-managed service allow rules from
enabled service listener desired state, including management, DNS, DHCP, KMS,
VCF Backup, VCF Offline Depot, and VCF Private Registry listeners. DHCP VLAN
moves or service listener moves should be applied with the changed Firewall
unit when `/appliance-apply` shows it pending.

Before shutdown, provisioning resets the exported appliance image from the
temporary Packer builder network to the LabFoundry management network:

- appliance address: `192.168.49.1/24`;
- appliance interface: `eth0`;
- host-side `LabFoundry-Mgmt` address: `192.168.49.254/24`;
- default gateway: `192.168.49.254`.

The generated `00-labfoundry-mgmt.network` matches only `eth0`. Provisioning
removes the Photon installer's broad `50-static-en.network` and
`99-dhcp-en.network` defaults so non-management NICs remain opt-in through
LabFoundry desired state and global appliance apply.

The Hyper-V switch script configures the host-side management address and NAT so
the final appliance can reach Photon repositories when the Windows host has
internet access.

Windows NAT for the management switch is configured with:

```powershell
New-NetIPAddress -InterfaceAlias "vEthernet (LabFoundry-Mgmt)" -IPAddress 192.168.49.254 -PrefixLength 24
New-NetNat -Name LabFoundry-Mgmt-NAT -InternalIPInterfaceAddressPrefix 192.168.49.0/24
```

Use `scripts/windows/create-hyperv-switches.ps1` instead of running those by
hand; it creates or repairs the address/NAT and prints the resulting summary.

Packer uploads only the files required for appliance installation: the
`labfoundry` package, packaging metadata, appliance helper scripts, the Photon
compatibility check, systemd unit, and sudoers template. It intentionally does
not upload `.git`, test artifacts, caches, or development virtual environments
into the builder VM.

## Boot The VHDX

After Packer completes, use the existing Hyper-V scripts. The finished
appliance VM gets two additional dynamic VHDX data disks by default:

- `LabFoundry-Depot.vhdx`, intended for `/mnt/labfoundry-vcf-offline-depot`;
- `LabFoundry-Backups.vhdx`, intended for `/mnt/labfoundry-vcf-backups`.

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/windows/create-hyperv-switches.ps1
powershell.exe -ExecutionPolicy Bypass -File scripts/windows/create-labfoundry-vm.ps1 `
  -VhdxPath 'image/hyperv/output/labfoundry-photon-hyperv/Virtual Hard Disks/LabFoundry-Photon-Builder.vhdx'
powershell.exe -ExecutionPolicy Bypass -File scripts/windows/start-labfoundry-vm.ps1
```

The default data disks are dynamic 500 GB VHDX files stored next to the OS
VHDX. Override them with `-DepotVhdxPath`, `-BackupVhdxPath`,
`-DepotDiskSizeBytes`, or `-BackupDiskSizeBytes` when needed. Format and mount
them inside Photon before enabling real Depot or Backup apply actions.

## Appliance Smoke Checks

Inside the Photon VM:

```bash
python3 --version
cat /etc/labfoundry/build-info
ip addr show
tdnf check-update || true
systemctl status labfoundry --no-pager
journalctl -u labfoundry -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null
curl -fsS http://127.0.0.1:8000/api/v1/dashboard >/dev/null || true
```

From the host, verify the management URL, login, reboot persistence, and that
`/appliance-apply` still records dry-run command intent before any real adapter
execution is enabled.

If the VM console prints
`systemd-ssh-generator: Failed to query local AF_VSOCK CID: Cannot assign requested address`,
the appliance is hitting systemd's automatic SSH-over-vsock discovery path.
LabFoundry does not use SSH-over-vsock, and current image provisioning masks
that generator while keeping regular TCP SSH available. On an already-built VM,
apply the same cleanup as root and reboot:

```bash
install -d -o root -g root -m 0755 /etc/systemd/system-generators
ln -sfn /dev/null /etc/systemd/system-generators/systemd-ssh-generator
systemctl daemon-reload
reboot
```

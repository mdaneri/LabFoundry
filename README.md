# LabFoundry

![LabFoundry appliance graphic](labfoundry/app/static/brand/labfoundry-appliance-graphic.svg)

LabFoundry is a Linux-based, web-managed infrastructure appliance for homelabs, VMware Cloud Foundation labs, POCs, training environments, isolated network labs, and WAN simulation testing.

The MVP is a safe runnable scaffold. It provides the FastAPI control plane, appliance-style web UI, local authentication, JWT bearer API tokens, audit logging, OpenAPI 3.1, dry-run system adapters, and Windows/Hyper-V script scaffolding. It does not apply real host networking, firewall, service, SFTP, registry, repository, DNS, DHCP, CA, or KMS changes by default.

## Photon OS Appliance Image

The first real OS appliance target is Photon OS 5.0 on Hyper-V. The image
builder lives in [`image/hyperv/`](image/hyperv/) and provisions:

- a Photon OS 5.0 Generation 2 Hyper-V VM with Secure Boot off;
- updated Photon packages from the configured Photon 5.0 repositories, with a
  second update pass after appliance packages are installed;
- the `labfoundry` system user;
- `/opt/labfoundry` for the installed application;
- `/etc/labfoundry/labfoundry.env` for appliance environment settings;
- `/etc/labfoundry/build-info` for build/update provenance;
- masked `systemd-ssh-generator` so Photon does not attempt automatic
  SSH-over-AF_VSOCK sockets on Hyper-V while normal TCP SSH remains available;
- `/var/lib/labfoundry` for durable state;
- `/var/log/labfoundry` for local logs;
- fixed appliance mount points under `/mnt/labfoundry-vcf-*`;
- `labfoundry.service` running uvicorn from a Python virtual environment;
- nginx enabled as the default management front door, proxying HTTP/80 to
  uvicorn on `127.0.0.1:8000`;
- `labfoundry-firewall.service` loading the appliance nftables firewall;
- `/opt/labfoundry/bin/labfoundry-helper` and a constrained sudoers template.

The finished Hyper-V appliance VM also attaches two durable dynamic data disks:
one for the VCF Offline Depot at `/mnt/labfoundry-vcf-offline-depot` and one
for VCF Backups at `/mnt/labfoundry-vcf-backups`. Keep those workloads off the
OS VHDX.

LabFoundry writes operational events to `/var/log/labfoundry/labfoundry.log`.
Audit events, desired-state edits, and appliance apply submissions are mirrored
there with sensitive values redacted. The Settings page controls local file
verbosity and can also forward the same operational events to an external
syslog receiver.

Photon OS 5.0 GA shipped with Python 3.11, but the current Photon 5.0 updates
stream has moved beyond that baseline. On June 21, 2026, live repository
metadata showed `python3` as `3.14.5-2.ph5`. LabFoundry keeps
`requires-python >=3.12`; verify the appliance stream with:

```bash
python3 scripts/check_photon_compatibility.py
```

Build inputs are the current Photon OS 5.0 ISO URL and checksum. On Hyper-V,
use the Windows wrapper so the Photon kickstart is attached as a local
single remastered ISO instead of depending on early installer networking:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/build-photon-hyperv-image.ps1 `
  -IsoUrl "https://packages.broadcom.com/photon/5.0/GA/iso/photon-5.0-dde71ec57.x86_64.iso" `
  -IsoChecksum "sha512:6a7a258399a258da742032987c043ab25503698d35edafaf1ae000f12127da1a161d8b84caa17fd8f23d129e81e1faa7ab087c20ab9229772a643f8f9475305f" `
  -SshPassword "<one-time-build-root-password>" `
  -BootstrapAdminPassword "<initial-labfoundry-admin-password>"
```

Run Packer from an elevated PowerShell session or as a user in the
`Hyper-V Administrators` group. Prepare the LabFoundry Hyper-V management
network before building:

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/windows/create-hyperv-switches.ps1
```

The Packer build VM uses the `LabFoundry-Mgmt` switch by default with temporary
static address `192.168.49.30/24` and gateway `192.168.49.254`. This avoids
fragile `Default Switch` host-IP detection while still giving the builder NAT
internet access for `tdnf update`. Unless `-BuilderStaticDns` is supplied, the
wrapper discovers the host's active IPv4 DNS servers and uses them for both the
temporary Photon builder and the finished appliance management interface, with
public DNS only as a fallback. The wrapper writes `photon-ks.json`, embeds it
into a remastered Photon ISO, replaces the ISO's UEFI GRUB config with a
LabFoundry auto-install entry, and passes that single ISO to Packer. Photon then
boots with `ks=cdrom:/photon-ks.json` without Packer typing boot commands. Raw
`packer build .` is intentionally blocked unless the ISO is marked as
wrapper-prepared; the wrapper is the tested Windows Server 2025 path. Build
runs pass Packer's `-force` flag by default so the fixed output directory can be
rebuilt in one command. Use
`-OutputDirectory <path>` to keep multiple artifacts or `-KeepExistingOutput`
when you want Packer to fail instead of replacing an existing output directory.
Use `-PackerOnError abort` to keep a failed builder VM for debugging, or
`-PackerOnError ask` to choose the failure action interactively.

The image builder does not configure a custom pip package index by default. If
your build network requires an internal PyPI mirror, pass `-PipGlobalIndex` or
`-PipGlobalIndexUrl` to set Photon site-level pip configuration before the
LabFoundry virtual environment is created. The provisioner does not upgrade pip
as a separate bootstrap step; it uses the Photon-packaged pip to install
LabFoundry so a transient public PyPI pip release download cannot fail the image
before the application install starts. Leave both options empty to keep standard pip behavior:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/build-photon-hyperv-image.ps1 `
  -IsoUrl "<photon-5.0-iso-url>" `
  -IsoChecksum "<packer-checksum>" `
  -PipGlobalIndex "https://packages.vcfd.broadcom.net/artifactory/api/pypi/upstream-pypi-virtual/pypi" `
  -PipGlobalIndexUrl "https://packages.vcfd.broadcom.net/artifactory/api/pypi/upstream-pypi-virtual/simple"
```

The generated appliance intentionally keeps
`LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS=true`. Real host mutation is staged per
apply unit after the helper-backed command path is reviewed. Build disposable
demo or lifecycle images with `-EnableRealSystemAdapters` when the VM should
actually mutate Photon services through the reviewed helper paths.
Firewall desired state is nftables-backed. The image installs nftables and
boots with management access to SSH, HTTPS, and the LabFoundry web UI.

Appliance Update is a separate runtime-maintenance workflow from global
`/appliance-apply`. It can check or run Photon OS package updates, Python
library updates, and LabFoundry wheel updates through
`labfoundry-helper appliance-update`. The LabFoundry wheel source defaults to
`http://localhost:18080/update/manifest.json` for development, but Photon VM
tests should point it at a URL reachable from the appliance when the update
server runs on the Windows host. Build a versioned wheel and manifest with:

```bash
python scripts/build_update_wheel.py
```

The generated LabFoundry version uses the project version plus git provenance,
such as `0.1.0+gabcdef123456`; the update manifest records the full git commit,
build time, wheel name, and SHA256. See
[`docs/appliance-update.md`](docs/appliance-update.md).

The exported Hyper-V appliance resets to `192.168.49.1/24` on
`LabFoundry-Mgmt`; the Windows host side should be `192.168.49.254/24`.
`scripts/windows/create-hyperv-switches.ps1` configures that address and a NAT
for the management network so Photon package checks work when the host has
internet access.

## Development

Primary workflow:

1. Develop inside WSL2 on Windows 11.
2. Run unit and API tests in WSL2.
3. Build the Photon OS Hyper-V appliance image with Packer.
4. Test the appliance in Hyper-V with PowerShell automation.

Install and run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn labfoundry.app.main:app --reload --host 127.0.0.1 --port 8000
```

Run from Windows PowerShell using the WSL development virtualenv:

```powershell
wsl -e sh -lc "cd /mnt/c/Users/m_dan/Documents/LabFoundry && /home/m_dan/.venvs/labfoundry/bin/python -m uvicorn labfoundry.app.main:app --host 127.0.0.1 --port 8000"
```

Run in the background from Windows PowerShell:

```powershell
wsl -e sh -lc "cd /mnt/c/Users/m_dan/Documents/LabFoundry && setsid -f /home/m_dan/.venvs/labfoundry/bin/python -m uvicorn labfoundry.app.main:app --host 127.0.0.1 --port 8000 >/tmp/labfoundry-uvicorn.log 2>&1"
```

View the background server log:

```powershell
wsl -e sh -lc "tail -f /tmp/labfoundry-uvicorn.log"
```

Stop the background server:

```powershell
wsl -e sh -lc "pkill -f 'uvicorn labfoundry.app.main:app'"
```

Development URL:

```text
http://127.0.0.1:8000
```

Bootstrap local login:

```text
username: admin
password: labfoundry-admin
```

For a real appliance image, pass `-var "bootstrap_admin_password=<initial-labfoundry-admin-password>"`
to Packer. If omitted, the image build falls back to `ssh_password` for
compatibility with early appliance builds. On Photon appliances, the bootstrap
admin also exists as a password-backed sudo OS account for local recovery and
debugging.

Default local VCF backup SFTP user:

```text
username: vcf-backup
status: disabled until VCF Backups is enabled and Local Users apply creates the Photon OS account
```

Set/reset this account from `Users`, then apply Local Users before exposing the SFTP endpoint beyond a development lab.

VCF Offline Depot uses the proprietary VCF Download Tool to stage disconnected VCF 9 depot content. Upload the VCF Download Tool file (`vcf-download-tool-*.tar.gz`) and LabFoundry extracts it before running `vcf-download-tool configuration generate --software-depot-id` to capture the activation ID operators use with Broadcom. Upload Broadcom token or activation-code files through the UI, or paste a download token when Broadcom does not provide a token file; LabFoundry stores the token as the runtime download-token file used by the VCFDT `--depot-download-token-file` parameter. Global appliance apply records show only sanitized filenames, presence flags, generated activation ID, and generated command intent. The generated VCFDT script uses the extracted `/var/lib/labfoundry/vcfDownloadTool/active-tool/bin/vcf-download-tool` binary plus runtime secret file paths, writes the telemetry flag, supports install, upgrade, upgrade-only, patch-only, Day-N component, and ESX activation-code workflows, and writes ESX disabled-platform selections to `conf/esxUserConfig.json`. Operators can manually start an individual download profile from the Download Profiles grid; Start creates a `vcf-depot-download` background job, prepares runtime credential files under the VCFDT working tree, and runs the selected VCFDT commands as the LabFoundry service user. Scheduling is not exposed yet. The Logs page exposes fixed-source tabs, including redacted VCFDT output from `/var/lib/labfoundry/vcfDownloadTool/active-tool/log/vdt.log`. When enabled, the depot apply unit stages nginx config under `/var/lib/labfoundry/apply/vcf-offline-depot/`, serves the fixed depot store as an HTTPS static document root, and uses the CA-managed `vcf_offline_depot:https` certificate/key file paths.

## Appliance Apply Workflow

LabFoundry treats service pages as desired-state editors. Routine setting and grid edits save into the control-plane database, but they do not mutate host services on each field change.

Use `Appliance Apply` to review and submit appliance changes. The page:

- lists changed apply units such as Local Users, Appliance Settings, Network, Routes & WAN Simulation, DNS/DHCP, ESXi PXE, Firewall, Certificate Authority, KMS, VCF Backups, VCF Offline Depot, and VCF Private Registry;
- checks changed valid units by default;
- shows compact summaries and rendered config previews or diffs when a last-applied baseline exists;
- lets operators unselect changed units that should stay pending;
- creates one `appliance-apply` job that records selected units, skipped changed units, validation results, rendered previews/diffs, adapter command intent, dry-run state, and the audit event.

Within each selected unit, helper commands run sequentially and stop on the first failure. A failed `validate` step is recorded in the job and prevents the matching `apply` or follow-on reload/sync step from running.

Fresh Photon appliance startup records a factory desired-state baseline when no baseline, appliance-apply job, or non-auth operator audit event exists. This only initializes comparison state and marks the provisioned bootstrap admin OS account as synced; it does not run helper commands or mutate host services.

Local Users stages `/var/lib/labfoundry/apply/local-users/labfoundry-users.json` and synchronizes LabFoundry local users to Photon OS accounts. Each user has a desired default shell, defaulting to `/sbin/nologin`, and enabled users are created or updated with that shell. New or reset passwords are held only in process memory until a successful real global apply sends them to `chpasswd`; LabFoundry does not store local user password hashes or encrypted pending OS passwords in the database, and previews and job results show counts/status only. Disabled or removed managed users are removed from Photon OS with `userdel -r`, unlock requests reset `passwd` and `faillock`, and the desired password policy is written to Photon PAM/pwquality during Local Users apply.

Appliance Settings owns the appliance FQDN, OS hostname, resolver mode, resolver servers, management UI HTTPS preference, root SSH login preference, and appliance NTP client. The helper installs nginx LabFoundry site config, writes a loopback-only `labfoundry.service` override, applies the LabFoundry-owned root SSH sshd drop-in, disables the retired Python redirect service if present, and schedules a short delayed restart so the apply job can finish recording before uvicorn moves behind nginx. Root SSH is disabled by default and enabled only when the Appliance Settings switch is applied. When management UI HTTPS is enabled, it uses the CA-managed `appliance:https` certificate, redirects public HTTP/80 to HTTPS/443, and reverse-proxies HTTPS to uvicorn on `127.0.0.1:8000`. When management UI HTTPS is disabled, including after factory reset plus apply, nginx serves public HTTP/80 as a plain reverse proxy to the same loopback upstream and does not expose a management HTTPS listener.

Routes & WAN Simulation stages `/var/lib/labfoundry/apply/wan/labfoundry-wan.conf` and owns static route desired state, IPv4 masquerade NAT rules, and interface/VLAN-level `tc/netem` WAN impairment. NAT v1 is explicit outbound masquerade only; there is no destination NAT or port forwarding, and the outbound interface can be any access physical interface or enabled VLAN with an IP CIDR. Route-specific WAN impairment is roadmap work tracked in `docs/routing-wan-roadmap.md`; v1 exposes only interface/VLAN-level impairment.

DNS and DHCP share one `DNS/DHCP (dnsmasq)` apply unit because they render and reload the same dnsmasq config. DHCP scopes bind only to access physical interfaces with IP CIDR or enabled VLAN interfaces with IP CIDR, and live lease readback uses the LabFoundry-owned dnsmasq lease file under `/var/lib/labfoundry/dnsmasq/`. Certificate Authority stores CA and leaf private keys encrypted in the database with `LABFOUNDRY_SECRETS_KEY`, auto-ensures VCF/KMS/service certificates when enabled, and stages `/var/lib/labfoundry/apply/ca/labfoundry-ca.json`; the helper writes public bundles and service certificate/key files under `/etc/labfoundry`.

ESXi PXE stores Kickstart source files in the LabFoundry database. The database is the source of truth; generated files under `/var/lib/labfoundry/pxe/http/esxi/ks/<id>.cfg` are runtime copies for PXE boot only. Saving in the CodeMirror editor updates desired state and marks the `esxi_pxe` apply unit changed. ESXi PXE boot settings select one or more DHCP IP zones instead of a freeform interface/IP pair; LabFoundry derives the PXE interfaces, TFTP server addresses, DNS records, firewall bind targets, and dnsmasq scope tags from those zones. Native UEFI HTTP URLs are generated per selected zone unless an operator supplies a manual absolute URL. Installer ISO choices are discovered from `/mnt/labfoundry-vcf-offline-depot/PROD/COMP/ESX_HOST`, the VCFDT ESX host component folder; LabFoundry creates that folder when needed, marks VCFDT-discovered images separately from user-uploaded images with dates, and lets operators upload or delete `.iso` files from the Installer ISOs tab. Deleting an ISO clears host/default PXE references to that image; generated runtime files are reconciled on the next global apply. Host PXE definitions are edited in the Host References grid, can reference both a database Kickstart and a selected installer ISO path, may include an optional IP address that creates an ESXi-managed DHCP reservation plus DNS A/AAAA record, and include a DHCP-only default profile for undefined MAC addresses. Global appliance apply stages schema-v2 `/var/lib/labfoundry/apply/esxi-pxe/labfoundry-esxi-pxe.json`, validates selected ISO paths stay under the ESX_HOST folder, extracts selected installers to `/var/lib/labfoundry/pxe/http/esxi/images/<image-key>/`, generates default and host-specific `boot.cfg` plus PXELINUX configs, stages `undionly.kpxe`, `snponly.efi`, `pxelinux.0`, `mboot.efi`, and `mboot.c32`, installs a dedicated static ESXi PXE HTTP listener on port `8080` by default, records render/apply timestamps, and redacts sensitive Kickstart values from previews, diffs, jobs, logs, and audit events. The helper searches Photon package paths plus `/var/lib/labfoundry/pxe/bootloaders` for the iPXE/SYSLINUX first-stage files; Photon image provisioning stages LabFoundry's bundled iPXE `undionly.kpxe` and `snponly.efi` artifacts there because the appliance package stream may not ship those filenames. When ESXi PXE boot settings change, review and apply the changed DNS/DHCP, ESXi PXE, and Firewall units together so dnsmasq, generated boot artifacts, and UDP/69 plus TCP/8080 allow rules stay aligned.

VCF Offline Depot stages nginx HTTPS static-site config under `/var/lib/labfoundry/apply/vcf-offline-depot/`, validates the CA-managed `vcf_offline_depot:https` certificate/key paths, and installs `/etc/labfoundry/nginx/sites.d/vcf-offline-depot.conf` through `labfoundry-helper`. VCF Backups stages an OpenSSH `Match User` drop-in under `/var/lib/labfoundry/apply/vcf-backups/`; when the service desired state is off, the default `vcf-backup` user is disabled so the next Local Users apply removes that OS account. Real VCF Backup apply validates the selected OS backup user, installs `/etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf`, prepares the fixed chroot volume and `/backups` upload directory, and restarts `sshd` through `labfoundry-helper`. Apply Local Users before VCF Backups when the selected SFTP user is new, disabled/enabled, removed, has a pending password, changes shell, or has an unlock request.

The firewall preview derives LabFoundry-managed service allow rules from service desired state, including management, DNS, DHCP, KMS, VCF Backup, VCF Offline Depot, and VCF Private Registry listeners. Managed listener rules default to the built-in `Any` group; operators can create, rename, remove, and assign firewall groups containing `any`, CIDRs, addresses, or other groups when rule sources or destinations need narrower access. DHCP bootstrap remains interface-bound on UDP/67 because clients may not have an address yet. Moving a DHCP scope or service listener to a VLAN such as `eth2.50` also changes the Firewall apply unit. In development, system adapters remain dry-run by default and record command intent instead of mutating host services directly.

KMS / KMIP is PyKMIP-backed and intended for lab compatibility testing, not production HSM use. The KMS page derives its listen address from the selected service interface, manages an app-owned DNS record for the KMS hostname, and requires an enabled healthy CA before activation. Real KMS apply stages `/var/lib/labfoundry/apply/kms/pykmip.conf`, installs `/etc/labfoundry/kms/pykmip.conf` and `/etc/pykmip/server.conf`, and manages `labfoundry-kms.service`; the service launches PyKMIP through LabFoundry's compatibility wrapper so current Photon Python streams remain supported. Disabling KMS stops the service while preserving `/var/lib/labfoundry/kms/pykmip.db`.

On the Photon appliance, real mutating helper actions re-enter through a transient `systemd-run` service when `LABFOUNDRY_HELPER_USE_SYSTEMD_RUN=1` is set. This keeps the web control plane inside its restricted `labfoundry.service` sandbox while allowing the reviewed root helper to write approved `/etc` configuration files from outside the service's read-only mount namespace.

More detail lives in [`docs/appliance-apply.md`](docs/appliance-apply.md).

## Backup, Restore, And Factory Reset

`Backup / Restore` exports LabFoundry desired-state settings as a JSON archive. The archive includes appliance, network, DNS/DHCP, ESXi PXE Kickstarts and host references, firewall, CA, KMS, VCF service, safe generic desired-state settings, and encrypted CA private-key material. It does not include audit events, jobs, API tokens, password hashes, uploaded secret bodies, generated PXE runtime files, or other runtime history. Restoring usable CA private material requires the same `LABFOUNDRY_SECRETS_KEY`.

Restoring a settings archive replaces desired-state configuration in the control-plane database only. Factory reset removes current desired-state configuration and reseeds only core LabFoundry defaults. It does not recreate demo VLANs, routes, NAT rules, WAN policies, trunk-only parent NIC posture, DHCP scopes or reservations, firewall rules, CA requests, KMS clients or keys, depot download profiles, or service listener bindings, including after a service restart. The core reset keeps only the appliance DNS zone derived from the appliance FQDN and an app-owned appliance A/AAAA record pointing at the management IP. The core reset leaves only `eth0` desired up for management; other physical NICs are desired admin down until an operator enables them. Disabled service settings reset with blank listen interfaces and addresses so `Appliance Apply` can submit a clean disabled baseline. Both restore and factory reset force service status rows to stopped, disabled, and `unconfigured`; host services are not mutated until the operator reviews and submits selected units through the global `Appliance Apply` workflow.

## Brand Assets

Reusable SVG assets live in `labfoundry/app/static/brand/` and are documented in `docs/branding.md`.

## Safety Boundary

Python is the control plane and desired-state owner. LabFoundry does not reimplement routing, firewalling, DNS, DHCP, SFTP, or HTTPS serving in Python; CA v1 is the exception for local trust custody, where Python generates and encrypts CA/certificate material while host file writes still go through `labfoundry-helper`.

The MVP follows these boundaries:

- App package: `labfoundry`
- Service user: `labfoundry`
- Default database: `data/labfoundry.db`
- VCF Offline Depot store and HTTPS document root: `/mnt/labfoundry-vcf-offline-depot`
- VCF private registry volume mount: `/mnt/labfoundry-vcf-registry`
- VCF backup volume mount: `/mnt/labfoundry-vcf-backups`
- VCF backup SFTP remote directory: `/backups`
- System adapters default to dry-run mode.
- On appliance startup, Physical Interfaces automatically refresh read-only Linux NIC inventory from Photon/Hyper-V and persist the observed host facts. Operators can also refresh inventory manually; observed host facts are separate from desired interface state and do not create an appliance apply job. Host NIC reconciliation matches by MAC address before Linux interface name so removing a NIC and rebooting cannot move desired state to a different adapter; removed host NICs are made inert, dependent VLANs are disabled, service listener interfaces and listener addresses are pruned or disabled when no listener remains, and the cleanup is written to the app log and audit events.
- Real network apply is Photon `systemd-networkd` backed: it stages LabFoundry's desired network state, installs LabFoundry-owned `.network`/`.netdev` files under `/etc/systemd/network/`, reloads networkd, reconfigures non-management links, and reconciles VLAN links. The appliance image's default `00-labfoundry-mgmt.network` matches only `eth0`, LabFoundry retires Photon catchall network defaults, and apply keeps management explicit while avoiding blind management-link reconfiguration.
- Photon image provisioning installs Photon's `powershell` package and creates the bootstrap admin OS account under `/var/lib/labfoundry/users` with `/usr/bin/pwsh`, using the same bootstrap admin password as the initial web login.
- Local Users apply stages `/var/lib/labfoundry/apply/local-users/labfoundry-users.json`, creates or updates enabled local users under `/var/lib/labfoundry/users` with their desired shell, removes disabled or removed managed users with `userdel -r`, handles staged unlock requests with `passwd -u` and `faillock --reset`, writes the desired PAM/pwquality password policy, and clears in-memory pending OS passwords only after a successful real apply.
- Appliance Settings apply stages `/var/lib/labfoundry/apply/appliance-settings/labfoundry-settings.json`, sets the OS hostname to the appliance FQDN, configures the management resolver for local or external DNS mode, writes the Photon `systemd-timesyncd` drop-in for the appliance NTP client, manages root SSH login through `/etc/ssh/sshd_config.d/labfoundry-root-login.conf`, and can switch the management UI to CA-backed HTTPS through nginx plus a loopback-only `labfoundry.service` override.
- Certificate Authority apply stages `/var/lib/labfoundry/apply/ca/labfoundry-ca.json`, validates CA/certificate material, writes public CA bundles and service certificates under `/etc/labfoundry`, and keeps private keys out of previews, logs, and job results.
- VCF Backups apply stages `/var/lib/labfoundry/apply/vcf-backups/labfoundry-vcf-backups-sshd.conf`, validates the LabFoundry-rendered OpenSSH drop-in and selected OS backup user, installs `/etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf`, prepares `/mnt/labfoundry-vcf-backups/backups`, and restarts `sshd`. Firewall apply owns the listener allow rule for the selected interface and port.
- Privileged changes must use reviewed `labfoundry-helper` commands and sudo allowlists. On the Photon appliance, real mutating helper actions run through `systemd-run` from inside the helper so they are not trapped in the web service's read-only `/etc` mount namespace.
- Subprocess calls must use argument arrays, not arbitrary shell strings.
- The global `/appliance-apply` workflow is the only appliance enforcement path.

## REST API

API prefix:

```text
/api/v1
```

OpenAPI and docs:

```text
http://127.0.0.1:8000/openapi.json
http://127.0.0.1:8000/api/docs
```

The OpenAPI document uses OpenAPI 3.1 and includes a JWT bearer security scheme.

Initial resource areas:

- Auth
- API Tokens
- Dashboard
- Interfaces
- VLANs
- Routes
- NAT
- WAN
- VCF Offline Depot
- Services
- Logs
- Audit
- Jobs
- Settings

Several future appliance resources are intentionally scaffolded as dry-run or status-only surfaces until their native Linux adapters are implemented.

## API Token Example

Create a bearer token from the bootstrap admin account:

```bash
curl -s \
  -X POST \
  "http://127.0.0.1:8000/api/v1/auth/login?username=admin&password=labfoundry-admin" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "development token",
    "scopes": [
      "read:dashboard",
      "read:routes",
      "read:wan",
      "write:wan",
      "read:services",
      "read:audit"
    ]
  }'
```

Call the dashboard API:

```bash
curl -s \
  -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8000/api/v1/dashboard
```

Create a WAN policy:

```bash
curl -s \
  -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8000/api/v1/wan/policies \
  -d '{
    "name": "Slow WAN",
    "latency_ms": 100,
    "jitter_ms": 10,
    "packet_loss_percent": 0.5,
    "bandwidth_mbit": 100
  }'
```

Create an outbound NAT rule:

```bash
curl -s \
  -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8000/api/v1/nat/rules \
  -d '{
    "name": "SiteA outbound WAN",
    "source": "192.168.50.0/24",
    "outbound_interface": "eth1.20",
    "masquerade": true,
    "priority": 100
  }'
```

Problem-details errors use this shape:

```json
{
  "type": "https://labfoundry.internal/errors/validation-error",
  "title": "Validation error",
  "status": 422,
  "detail": "Invalid request payload",
  "instance": "/api/v1/wan/policies",
  "error_code": "VALIDATION_ERROR",
  "request_id": "req_123"
}
```

## API Scopes

Supported initial scopes:

```text
read:dashboard
read:interfaces
write:interfaces
read:vlans
write:vlans
read:routes
write:routes
read:wan
write:wan
read:firewall
write:firewall
read:dns
write:dns
read:dhcp
write:dhcp
read:ca
write:ca
read:kms
write:kms
read:repository
write:repository
read:vcf-registry
write:vcf-registry
read:vcf-backups
write:vcf-backups
read:services
write:services
read:logs
read:audit
write:backup
admin:all
```

Role checks and scope checks are both enforced. A viewer cannot mint admin scopes, and a network-admin cannot mint CA or repository administration scopes.

## Hyper-V Workflow

Windows-side automation lives in `scripts/windows/`.

From WSL2:

```bash
powershell.exe -ExecutionPolicy Bypass -File scripts/windows/create-hyperv-switches.ps1
```

The scaffold uses these switch names:

- `LabFoundry-Mgmt`
- `LabFoundry-SiteA`
- `LabFoundry-SiteB`
- `LabFoundry-Trunk`

The primary appliance image target is Hyper-V VHDX. ESXi/vSphere OVA and KVM/Proxmox QCOW2 are future packaging targets.

The Photon image build scaffold lives in:

```text
image/hyperv/
```

Use the existing scripts to create switches, create a VM from the Packer VHDX,
start the VM, attach test NICs, and run smoke checks. The first appliance smoke
pass should verify SSH, `systemctl status labfoundry`, web UI login,
`/openapi.json`, `/api/v1/dashboard`, reboot persistence, and dry-run
`/appliance-apply` job output.

Lifecycle interop testing uses a separate Hyper-V VM set and must not reuse or
destroy the normal `LabFoundry` test VM. The simple entry point is:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File scripts/windows/invoke-hyperv-lifecycle-test.ps1
```

The wrapper prepares the tiny Alpine client VHDX, selects the newest appliance
VHDX under `image/hyperv/output`, creates a unique `LabFoundryLifecycle-*` lab,
validates DNS, DHCP, firewall, routing, NAT, WAN netem simulation, CA apply
with deterministic packet-loss/recovery proof, CA apply with a ClientA CSR
request and issued-certificate verification, VCF Backup SFTP with the
`vcf-backup` OS user, client-side connectivity, and by default a
backup/restore redeploy pass that confirms the restored ClientA certificate has
the same serial number and SHA-256 fingerprint as the pre-restore certificate
and that the restored CA archive fingerprints match the original settings
backup.
It prints a human-readable console summary, writes `result.json`, then removes
the VMs it created. It defaults to the local Hyper-V lab password for admin and
appliance/client SSH access; appliance host-state probes log in as `admin`
because root SSH is disabled by default, then run checks through sudo. It uses a
separate policy-compliant default for VCF Backup SFTP test access; pass
`-AdminPassword`, `-SshPassword`, and `-VcfBackupPassword` to override those
defaults. Pass `-SkipBackupRestoreTest` only when you need the older single-pass
run, and pass `-KeepVms` only when preserving a failed lab for inspection. Use
`-PrepareNetworksOnly` to set up the Hyper-V switches/NAT, `-CleanupVmsOnly` to
remove only lifecycle VMs, and `-CleanupNetworksOnly` to remove LabFoundry
switches/NAT after all attached VMs are gone. Details live in
[`docs/hyperv-lifecycle-testing.md`](docs/hyperv-lifecycle-testing.md).

When troubleshooting a Hyper-V builder VM, use
`scripts/windows/get-labfoundry-vm-ip.ps1` from an elevated PowerShell session
to read the current IPv4 address reported by Hyper-V.

## PowerShell Roadmap

The future PowerShell module scaffold lives in:

```text
clients/powershell/LabFoundry/
```

The first generated or hand-wrapped cmdlets should map cleanly to the OpenAPI operation IDs. Token authentication should be preferred for automation. `-SkipCertificateCheck` may be added for lab testing only and must not be the default.

## Tests

Run:

```bash
pytest
python -m compileall labfoundry
python scripts/check_photon_compatibility.py
```

The MVP test suite covers auth, token revocation, scope enforcement, audit records, UI smoke rendering, and OpenAPI contract checks.

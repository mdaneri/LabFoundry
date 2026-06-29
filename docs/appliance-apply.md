# Appliance Apply

LabFoundry separates desired-state editing from appliance enforcement.

Service pages edit desired state. They autosave routine settings and grids, show local validation and rendered config previews, and link to the global apply review. They should not own service-specific apply buttons or service-specific apply submit routes.

`Appliance Apply` is the global review and submit surface. It lists changed apply units, checks valid changed units by default, and lets an operator unselect any unit that should remain pending.

On Photon appliances, real mutating helper actions run through `labfoundry-helper` and then re-enter via a transient `systemd-run` service when `LABFOUNDRY_HELPER_USE_SYSTEMD_RUN=1` is set. The web control plane remains inside the `labfoundry.service` sandbox, while the reviewed root helper writes approved `/etc` files from outside that service's read-only mount namespace.

## Apply Units

Current apply units are:

- Local Users
- Network
- Appliance Settings
- Routes & WAN Simulation
- Firewall
- DNS/DHCP (dnsmasq)
- ESXi PXE
- Certificate Authority
- KMS / KMIP
- VCF Backups
- VCF Offline Depot
- VCF Private Registry

DNS and DHCP are one unit because they share the rendered dnsmasq config and reload boundary.

Appliance Settings owns appliance identity, OS hostname, resolver mode, resolver servers, management UI HTTPS preference, root SSH login preference, and the appliance NTP client. The app-owned appliance DNS record is derived from that identity, but DNS/DHCP still owns the rendered dnsmasq record and service reload boundary.

## Local Users Apply

The real Local Users apply path stages JSON at `/var/lib/labfoundry/apply/local-users/labfoundry-users.json`. The `local_users` unit synchronizes LabFoundry local users to Photon OS users through `labfoundry-helper local-users validate|apply`. Enabled LabFoundry users are created under `/var/lib/labfoundry/users/<username>` with the per-user desired shell, defaulting to `/sbin/nologin`; disabled and removed managed users are removed from Photon OS with `userdel -r`. Photon image provisioning creates the bootstrap admin OS account before first apply. When VCF Backup desired state is off, LabFoundry keeps the default `vcf-backup` user disabled so Local Users apply removes that OS account.

Passwords are available for OS sync only when an administrator creates or resets a local user password. LabFoundry does not store local user password hashes or encrypted pending OS passwords in the database; the pending value is held only in process memory until a real global apply sends it to `chpasswd` over stdin and then clears it. If the service restarts before apply, the operator must set/reset the password again. Dry-run apply records command intent but keeps the in-memory pending password staged. Unlock requests are staged as desired state and applied later with `passwd -u` plus `faillock --user <name> --reset`. Password policy edits are also desired state; Local Users apply writes a LabFoundry-managed block in `/etc/security/pwquality.conf` and ensures `/etc/pam.d/system-password` runs `pam_pwquality.so` before `pam_unix.so`. Rendered previews, diffs, job results, logs, and audit details must show only counts and status such as `password staged` or `password not staged; reset to sync`.

## Physical Interface Inventory

Refreshing Physical Interfaces is inventory only. Appliance startup refreshes observed Linux NIC facts automatically, and operators can also run the same refresh manually from the page. Both paths update LabFoundry's observed model, but neither runs the network adapter nor applies desired state to the host.

## Network Apply

The real network apply path is Photon `systemd-networkd` backed. The `network` apply unit stages LabFoundry's rendered network config at `/var/lib/labfoundry/apply/network/labfoundry-network.conf`, validates management, physical, VLAN, and CIDR intent, installs LabFoundry-owned `.network` and `.netdev` files under `/etc/systemd/network/`, reloads networkd, and reconfigures non-management links. Management remains explicit on `eth0`; the helper does not blindly reconfigure the management link during this first pass. When a VLAN was present in successful LabFoundry network apply history and is no longer desired, the staged config includes an explicit removal target and the helper deletes that VLAN link after verifying it is a VLAN device.

## Routes And WAN Apply

The real Routes & WAN Simulation apply path stages config at `/var/lib/labfoundry/apply/wan/labfoundry-wan.conf`. The `wan` unit owns static route desired state, IPv4 outbound masquerade NAT rules, and interface/VLAN-level WAN simulation through `tc/netem`. NAT v1 is explicit masquerade only: no destination NAT, port forwarding, or automatic broad NAT is created from interface roles. Operators edit NAT rules on `/routes-wan`, choose an access physical interface or enabled VLAN with an IP CIDR as the outbound interface, and review the rendered nftables table and command intent on the global apply page. Route-specific WAN impairment is planned but not exposed in v1; the design notes live in `docs/routing-wan-roadmap.md`.

Through `labfoundry-helper wan validate|apply`, the helper validates staged routes, NAT rules, WAN targets, and netem policy values. Apply installs `/etc/labfoundry/nftables.d/labfoundry-nat.nft`, enables `net.ipv4.ip_forward=1` only when enabled NAT rules exist, applies the NAT table with `nft`, applies static routes with `ip route replace`, and applies or clears `tc qdisc` netem state on route targets with assigned policies. Removed route deletion is staged only when a route existed in the selected unit's last-applied baseline and is absent from current desired state.

## DNS/DHCP Apply

The real DNS/DHCP apply path is dnsmasq-backed. The `dnsmasq` apply unit stages LabFoundry's rendered dnsmasq config at `/var/lib/labfoundry/apply/dnsmasq/labfoundry.conf`, validates it with `dnsmasq --test`, installs `/etc/labfoundry/dnsmasq.d/labfoundry.conf`, enables `dnsmasq`, and reloads or restarts the service through `labfoundry-helper`. DNS and DHCP remain one global apply unit because they share one dnsmasq config and service reload boundary.

DHCP IP zones can bind only to valid service targets: access physical interfaces with an IP CIDR or enabled VLAN interfaces with an IP CIDR. Trunk physical interfaces and addressless interfaces are rejected before apply. The rendered dnsmasq config owns DHCP ranges, options, reservations, and the lease file at `/var/lib/labfoundry/dnsmasq/dhcp.leases`; live lease readback goes through the allowlisted `labfoundry-helper dnsmasq leases --real` path.

## ESXi PXE Apply

The ESXi PXE apply unit owns generated installer boot artifacts. Operators edit Kickstart source in the database through the built-in CodeMirror editor; filesystem copies are derived artifacts, not desired state. Saving a Kickstart updates the database source hash and marks `esxi_pxe` changed, but does not write `/var/lib/labfoundry/pxe/http/esxi/ks/<id>.cfg`.

The ESXi PXE page also discovers installer ISOs under `/mnt/labfoundry-vcf-offline-depot/PROD/COMP/ESX_HOST`, the VCFDT ESX host component folder, and creates that folder when needed. Operators can upload additional `.iso` files into the same folder and select an installer ISO on each host reference.

The real apply path stages schema-v2 `/var/lib/labfoundry/apply/esxi-pxe/labfoundry-esxi-pxe.json`. Through `labfoundry-helper esxi-pxe validate|apply`, the helper validates the manifest, writes enabled Kickstarts to the PXE HTTP root, validates selected installer ISO paths remain under the ESX_HOST folder, extracts selected installers to `/var/lib/labfoundry/pxe/http/esxi/images/<image-key>/`, stages the iPXE first-stage boot files `undionly.kpxe` and `snponly.efi`, stages the second-stage boot files `pxelinux.0`, `mboot.efi`, and `mboot.c32`, and generates host-specific `boot.cfg` plus PXELINUX configs. The helper searches Photon package paths plus `/var/lib/labfoundry/pxe/bootloaders` for `undionly.kpxe`, `snponly.efi`, and `pxelinux.0`; operators can stage missing first-stage files in that directory without changing desired state. The helper also installs a dedicated static nginx listener that serves only `/pxe/esxi/` from the generated PXE HTTP root on port `8080` by default. LabFoundry redacts Kickstart secrets from previews, diffs, job output, logs, and audit events. Drift detection compares the generated filesystem copy to the database source hash and never imports filesystem changes without an explicit admin action.

ESXi PXE boot settings also affect DNS/DHCP and Firewall desired state. Apply the changed DNS/DHCP, ESXi PXE, and Firewall units together when the PXE bind target, boot address, or HTTP port changes so dnsmasq returns the guide-aligned first-stage and second-stage boot files and the appliance exposes UDP/69 plus the PXE HTTP port on the selected bind targets.

## Firewall Apply

The Firewall apply unit derives LabFoundry-managed service allow rules from enabled service listener desired state. Management, DNS, DHCP, KMS, VCF Backup, VCF Offline Depot, and VCF Private Registry listeners appear in the managed service rules grid on the Firewall page, while custom firewall rules remain editable in the main grid. Managed DNS and service listener rules default to the built-in `Any` group. Operators can create, rename, remove, and assign firewall groups containing `any`, CIDRs, addresses, or other groups when rule sources or destinations need narrower access than the default. DHCP bootstrap rules are the exception: they remain interface-bound UDP/67 input rules without group filtering because clients and relay paths may arrive before a client address is assigned. If a DHCP zone or service listener moves from a physical interface to a VLAN such as `eth2.50`, the firewall preview and apply diff should move the generated rule to that same interface. Apply the changed Firewall unit with the service unit that changed when the global apply page shows both as pending.

## VCF Backups Apply

The real VCF Backups apply path is OpenSSH-backed. The `vcf_backups` unit stages LabFoundry's rendered `Match User` drop-in at `/var/lib/labfoundry/apply/vcf-backups/labfoundry-vcf-backups-sshd.conf`, validates that it is LabFoundry-rendered and scoped to the selected backup user, verifies the selected OS account exists, installs `/etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf`, prepares the fixed chroot storage mount and `/backups` upload directory, validates `sshd`, and restarts `sshd` through `labfoundry-helper`. The selected backup user should be synchronized through the Local Users apply unit before VCF Backups is applied. Firewall apply owns the selected interface and port allow rule.

## Certificate Authority Apply

The real Certificate Authority apply path stages JSON at `/var/lib/labfoundry/apply/ca/labfoundry-ca.json`. When enabled, LabFoundry generates and stores the local root CA and issued leaf private keys encrypted in the database with `LABFOUNDRY_SECRETS_KEY`, auto-ensures certificates for LabFoundry HTTPS, KMS/KMIP, VCF Offline Depot, and VCF Private Registry, and renders a redacted apply preview. Through `labfoundry-helper ca validate|apply`, the helper validates the staged JSON, rejects certificate/key mismatches when OpenSSL can check them, writes `root-ca.pem`, `root.crt`, `ca-bundle.pem`, and service certificate/key/chain files under `/etc/labfoundry`, and keeps private keys out of job output.

Settings backups include encrypted CA private-key material. Restoring usable CA custody requires the same `LABFOUNDRY_SECRETS_KEY`; otherwise operators should reissue the CA/certificates.

## KMS / KMIP Apply

The real KMS apply path is PyKMIP-backed and lab-only. The KMS page derives its listen address from the selected access physical interface or enabled VLAN, creates an app-owned DNS record for the KMIP hostname, and requires an enabled healthy CA before KMS can be activated. When KMS is enabled, CA desired state auto-ensures the `kms:server` certificate and enabled KMIP client certificates; apply remains invalid until the issued server certificate and key are available.

The `kms` unit stages `/var/lib/labfoundry/apply/kms/pykmip.conf`. Through `labfoundry-helper kms validate|apply`, the helper validates that the staged config stays under the KMS apply directory, checks the derived listen address, certificate/key/CA file paths, PyKMIP availability, and SQLite database path, then installs `/etc/labfoundry/kms/pykmip.conf`, writes `/etc/pykmip/server.conf`, installs `labfoundry-kms.service`, and enables/restarts the listener. The service launches PyKMIP through LabFoundry's compatibility wrapper so Photon Python streams where legacy `ssl.wrap_socket` was removed can still run the lab KMIP server. Disabling KMS stops and disables `labfoundry-kms.service` while preserving `/var/lib/labfoundry/kms/pykmip.db`. Firewall apply owns TCP/5696 access to the selected interface.

## Appliance Settings Apply

The real Appliance Settings apply path stages JSON at `/var/lib/labfoundry/apply/appliance-settings/labfoundry-settings.json`. The `appliance_settings` unit records the appliance FQDN, resolver mode, resolver servers, local DNS desired-state flag, management interface/IP, management UI HTTPS preference, root SSH login preference, derived nginx public ports `80` and `443`, the uvicorn loopback upstream, and appliance NTP servers. Through `labfoundry-helper appliance-settings validate|apply`, the helper sets the OS hostname to the appliance FQDN, local DNS mode configures the management resolver to `127.0.0.1` with `Domains=~.`, external DNS mode configures the management resolver to the selected external DNS servers and removes the local catch-all domain, and the appliance NTP client is rendered to `/etc/systemd/timesyncd.conf.d/labfoundry.conf` for `systemd-timesyncd`. The helper always installs `/etc/nginx/conf.d/labfoundry.conf` plus `/etc/labfoundry/nginx/sites.d/management.conf`, writes a loopback-only `labfoundry.service` override, disables the retired `labfoundry-http-redirect.service` if present, reloads nginx/systemd, and schedules a short delayed restart of `labfoundry.service` so the apply job can be recorded before uvicorn moves behind nginx. It also writes `/etc/ssh/sshd_config.d/labfoundry-root-login.conf`, validates `sshd`, and restarts `sshd`; root SSH is disabled by default and enabled only when the Appliance Settings switch is applied. When management UI HTTPS is enabled, the helper requires the CA-managed `appliance:https` cert/key files, redirects public HTTP/80 to HTTPS/443, and reverse-proxies HTTPS traffic to uvicorn on `127.0.0.1:8000`. When management UI HTTPS is disabled, including after factory reset plus apply, nginx serves public HTTP/80 as a plain reverse proxy to uvicorn on `127.0.0.1:8000` and does not expose a management HTTPS listener.

## VCF Offline Depot Apply

The real VCF Offline Depot apply path stages nginx config at `/var/lib/labfoundry/apply/vcf-offline-depot/labfoundry-vcf-offline-depot.conf`. The preview and staged config use the CA-managed `vcf_offline_depot:https` certificate/key file paths, serve `settings.depot_store_path` as the HTTPS document root, and enable range-friendly static-file behavior for large depot artifacts. After VCFDT upload, LabFoundry extracts the archive and runs `vcf-download-tool configuration generate --software-depot-id` to capture the Broadcom activation ID; the activation-code file itself remains uploaded credential material and is never rendered. Download tokens can be uploaded as files or pasted as text, but both paths feed the runtime download-token file used by `--depot-download-token-file`. Global apply `stage-tool` extracts the uploaded archive under `/opt/labfoundry/vcf-download-tool/extracted` and writes a stable `/opt/labfoundry/vcf-download-tool/vcf-download-tool` wrapper for helper-owned apply work. The VCFDT preview is generated as a bash script with `/var/lib/labfoundry/vcfDownloadTool/active-tool` runtime token and activation-code file paths, telemetry flag setup, `conf/esxUserConfig.json` for disabled ESX platforms, and command intent for install, upgrade, upgrade-only, patch-only, Day-N component, metadata, and ESX downloads. Operators can manually start one profile from the Download Profiles grid; Start creates a `vcf-depot-download` background job, writes runtime token or activation-code files under `/var/lib/labfoundry/vcfDownloadTool/active-tool/secrets`, runs VCFDT as the LabFoundry service user, and keeps credential bodies out of job output. The Logs page includes a fixed VCFDT tab for `/var/lib/labfoundry/vcfDownloadTool/active-tool/log/vdt.log` and redacts sensitive-looking lines plus tokenized Broadcom URL segments before rendering. Through `labfoundry-helper vcf-offline-depot validate|apply-https`, the helper validates the staged site, rejects duplicate hostname/listener combinations, installs or removes `/etc/labfoundry/nginx/sites.d/vcf-offline-depot.conf`, validates with `nginx -t`, and reloads nginx. Disabling the depot removes only the nginx site; depot files remain intact.

## Baselines And Diffs

After a successful selected apply, LabFoundry stores the selected units' last-applied baseline in the existing `settings` table. The baseline includes the normalized snapshot hash, compact summary, rendered config preview, config path, and apply timestamp.

On fresh Photon appliance startup, LabFoundry records the factory desired-state baseline automatically when there is no existing baseline, no appliance-apply job, and no non-auth operator audit event. This startup baseline is comparison metadata only: it does not submit an apply job, run helper commands, or mutate host services. It also records the provisioned bootstrap admin OS account as synced because image provisioning already created that Photon account and set its password.

When desired state changes later, the global apply page compares the current rendered config preview to the last-applied preview and shows a unified config diff when available. On first apply, no baseline exists yet, so the page shows the current preview instead.

Rendered previews and job results must redact sensitive-looking values such as passwords, tokens, credentials, private keys, robot accounts, activation codes, encrypted CA private material, and uploaded secret contents.

## Job Result

Submitting creates one `appliance-apply` job. The job result records:

- selected apply units;
- skipped changed units;
- validation errors and warnings;
- compact summaries;
- rendered config previews and diffs;
- adapter commands and dry-run status;
- per-unit success state.

In development, adapter commands are dry-run records. They capture command intent without changing host services.

## UI Expectations

Service right rails should show:

- `Pending Appliance Changes`, with status and a link to `/appliance-apply`;
- `Validation`, with errors, warnings, and rendered config preview.

The global submit button should be labeled `Submit appliance changes`. Avoid reintroducing labels such as `Create appliance apply task`, `DNS Apply`, `DHCP Apply`, `SFTP Apply`, or other service-scoped apply actions.

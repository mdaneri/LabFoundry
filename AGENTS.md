# LabFoundry Agent Notes

## UI Defaults

- Every configurable setting should include an adjacent `i` help control using the `.field-label` and `.help-icon` pattern.
- The help text should explain what the setting changes, where it applies, and any safety boundary such as dry-run or interface binding.
- Keep the help inline and compact: use hover/focus tooltips for short explanations instead of adding persistent instructional text to the page.
- Prefer consistent control types: switch controls for binary settings, selects/list editors for short enumerations, inputs for exact free-form values, textareas for multiline config, tabs for mutually exclusive editing modes, and Tabulator for editable data grids.

## Appliance Configuration UX

- Use the DNS page as the default pattern for configurable appliance services where applicable.
- Treat forms as desired-state editors. Settings should autosave on change with `data-autosave-form`, a small `.autosave-status` message, and the existing CSRF/session protections. Avoid visible "Save" buttons for routine desired-state settings when autosave is safe.
- Keep enforcement separate from editing. Applying changes to the appliance should be a deliberate task action after the user is done, not part of every field change.
- Do not add service-specific apply cards or service-specific apply submit routes. Applying is a global appliance workflow owned by `/appliance-apply`.
- In service right-side rails, show a compact `Pending Appliance Changes` card first, then the service-specific `Validation` card. The pending card links to `/appliance-apply`; the validation card owns only valid/needs-attention state, validation messages, warnings, and rendered config preview.
- The global appliance apply page should list changed apply units, check changed valid units by default, show compact summaries, show rendered config diffs/previews, allow users to unselect units, and submit one `appliance-apply` job.
- Apply actions should create one global job/task that captures selected units, skipped changed units, current desired state summaries, rendered config previews/diffs, validation results, adapter commands, dry-run status, and audit event.
- Label the global submit action around the user's intent, such as `Submit appliance changes`, and explain that the task validates and applies selected desired state through LabFoundry adapters.
- Keep dry-run boundaries visible. In development, applying should record command intent through adapters instead of mutating host services directly.
- Use validation panels to show whether desired state is ready to apply, including warnings and rendered config previews.
- When autosave changes affect validation or preview output, update the validation card in-place without shifting the page with large `Saved` alerts. Use compact autosave status text near the edited form.
- Use compact Tabulator grids for editable record sets. Rows should autosave on edit, place new-record rows at the bottom, include a clear `+ Add record here` affordance, and expose destructive actions through a context/menu action rather than inline clutter.
- Use tab groups when two editing modes solve the same job. Do not show single-record forms, bulk import, and raw/config editors all at once if tabs can make the workflow clearer.
- Use tag editors for one-or-more selections such as interfaces, addresses, networks, domains, or labels. Tag editors should allow typed custom values and a `+` menu for known existing options.
- Use domain- or scope-specific tabs for resources that naturally belong under a parent, such as DNS records under zones. Each tab should keep edits scoped to that parent.
- Preserve active tab context after autosave, record creation, deletion, or import whenever possible.
- Prefer explicit status language over generic button text. Avoid labels such as `Save DNS` or `Apply` when the action really means "save desired state", "review appliance changes", "submit appliance changes", "import into this domain", or "apply zone file".
- Destructive UI actions such as deleting a domain, scope, record set, backup, token, or appliance-owned config should require the shared modal confirmation pattern (`data-confirm-modal`) instead of a browser confirm or immediate submit. The modal copy should name the object, explain what will be removed, and mention whether the appliance is affected immediately or only after global appliance apply.

## Photon OS Appliance Deployment

- The first real OS appliance target is Photon OS 5.0 on Hyper-V. Keep image-build work under `image/hyperv/`.
- The Hyper-V image is automated with Packer, Photon kickstart JSON, and provisioning scripts. Do not replace it with manual-only install steps unless the automation path is also kept current.
- Photon appliance provisioning should run `tdnf -y makecache` and `tdnf -y update` before installing LabFoundry so the image lands on the current Photon 5.0 package stream.
- Photon 5.0 GA started at Python 3.11, but the updated Photon 5.0 package stream may be newer; on June 21, 2026 live repo metadata showed `python3` as `3.14.5-2.ph5`. Keep LabFoundry at `requires-python >=3.12` and run `python scripts/check_photon_compatibility.py` before treating Photon compatibility as healthy.
- The appliance installs LabFoundry under `/opt/labfoundry`, stores environment in `/etc/labfoundry/labfoundry.env`, stores durable state in `/var/lib/labfoundry`, writes local logs under `/var/log/labfoundry`, and preserves fixed service mounts under `/mnt/labfoundry-vcf-*`.
- Keep the image-build OS/root password separate from the LabFoundry web bootstrap password. Packer exposes `ssh_password` for build-time SSH/root use and `bootstrap_admin_password` for the initial `admin` web login; if the latter is omitted, early-image compatibility falls back to `ssh_password`.
- Product-owned helper binaries should live under `/opt/labfoundry/bin`; do not put LabFoundry-owned helpers in `/usr/local/sbin` for Photon appliance images.
- The appliance systemd unit is `labfoundry.service` and should run uvicorn from the provisioned virtual environment as the `labfoundry` service user.
- Photon appliance firewall ownership is nftables-first. Provisioning installs nftables and loads `labfoundry-firewall.service`; do not add a LabFoundry iptables apply path.
- Keep `LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS=true` for first-boot appliance images. Promote real host mutation one apply unit at a time after validation, preview, job capture, and rollback behavior are reviewed.
- Privileged appliance enforcement must go through `labfoundry-helper` and constrained sudoers entries. Do not give the control plane broad shell, root, or package-manager access.
- The global `/appliance-apply` workflow remains the only host-mutation workflow. Do not add service-specific apply routes, service-specific apply jobs, or direct helper calls from desired-state edit forms.
- Packer is a Windows-host prerequisite for the Photon image path; Hyper-V and `qemu-img` may already be available locally but should still be checked in handoff notes.

## Photon VM Debugging Notes

- The Hyper-V management NAT appliance address used during the first Photon bring-up was `192.168.49.1`; verify the actual current address before assuming it with `scripts/windows/get-labfoundry-vm-ip.ps1`, Hyper-V Manager, or SSH.
- When the appliance web UI is unreachable, separate network reachability from service reachability: use host-side `Test-Connection <ip>` for ICMP, `Test-NetConnection <ip> -Port 8000` for the web service, and in-guest `systemctl status labfoundry --no-pager` plus `journalctl -u labfoundry -n 120 --no-pager`.
- ICMP can be intentionally blocked by nftables while SSH and TCP/8000 still work. Do not treat failed ping as proof that the VM is down; check TCP ports and Hyper-V console before changing networking.
- For live appliance patching, build a local wheel with `python -m pip wheel . -w dist`, copy only the LabFoundry wheel to the VM, install it with `/opt/labfoundry/.venv/bin/python -m pip install --force-reinstall --no-deps`, then restore venv readability for the `labfoundry` service user with directory `0755`, file `0644`, and executable bits under `.venv/bin`.
- After installing a live wheel, restart with `systemctl restart labfoundry` and verify both `systemctl is-active labfoundry` and `curl http://127.0.0.1:8000/openapi.json` from inside the guest or `Invoke-WebRequest http://<ip>:8000/openapi.json` from Windows.
- If `labfoundry.service` fails with `status=203/EXEC`, check execute permissions on `/opt/labfoundry/.venv/bin/python` for the `labfoundry` user. If it fails importing static/templates, confirm package assets are included in the wheel and that `base.html` static query strings changed after JS/CSS edits.
- Real firewall apply stages rendered nftables config under `/var/lib/labfoundry/apply/firewall/labfoundry.nft` as the `labfoundry` service user before invoking the root helper. Keep `/var/lib/labfoundry/apply` and its firewall child owned by `labfoundry:labfoundry`; root-owned staging files cause `/appliance-apply` to fail before a job is recorded.
- Validate actual firewall state with `nft list ruleset`, not only the UI preview. The helper should run `nft -c -f <staged file>` before apply; syntax errors such as placing `tcp` before `ip saddr` must fail validation and be fixed in the renderer.
- `labfoundry-firewall.service` is a oneshot persistence service. It should be installed with `systemctl enable --now labfoundry-firewall.service`; `enabled` plus `inactive` means it was not started after writing/enabling.
- When testing real apply from the UI, select only the intended apply unit. The current page lists units without a last-applied baseline as changed; unselect unrelated units before submitting until baseline UX is improved.
- Check the latest appliance apply job directly when behavior is unclear: query `Job` rows in the appliance SQLite database or inspect the rendered job JSON in the UI. A failed job can still leave host state unchanged if helper validation failed before apply.

## Network And Service Binding

- Physical Interfaces are for untagged/access networks. VLAN Interfaces are only for tagged VLAN networks on physical parent interfaces marked as trunk.
- Physical Interfaces may refresh observed Photon/Hyper-V NIC inventory from the host, but host inventory is read-only context; desired-state edits remain separate and enforcement still goes through `/appliance-apply`.
- Real network apply is Photon `systemd-networkd` backed. It may install LabFoundry-owned `.network`/`.netdev` files under `/etc/systemd/network/`, reload networkd, reconfigure non-management links, create/update desired VLAN links, and delete VLAN links explicitly derived from successful LabFoundry network apply history. Keep management on `eth0` explicit and do not blindly reconfigure the management link without reachability safeguards.
- Do not offer trunk physical interfaces as direct service bind targets. Service bind selectors should include access physical interfaces with an IP CIDR and enabled VLAN interfaces with an IP CIDR.
- When a service bind target is selected, derive the listen IP from the selected interface or VLAN IP CIDR. Do not ask the user to enter a separate bind IP unless the service genuinely supports multiple explicit listen addresses, such as DNS.
- If a VLAN has dependent state, protect parent interface mode changes that would invalidate it. A physical interface with VLAN children should not be silently changed from trunk to access.
- Validate required network creation fields before saving. For VLANs, do not persist a new VLAN row unless the parent, VLAN ID, and IP CIDR are present and valid.
- Keep the validation/config preview current after any network or service change that affects rendered appliance state.

## DNS And DHCP

- DNS domains are first-class zones. Represent domains as tabs, include a `+ Domain` tab/action, and keep records, hosts import, and zone-file editing inside the selected domain.
- DNS records belong under their domain. Store and edit relative hostnames inside a zone; render fully qualified names only where useful for preview, API output, or validation context.
- Always consider reverse zones for A and AAAA records. DNS record grids should expose reverse/PTR status so missing reverse coverage is visible.
- Support at least A, AAAA, and CNAME records in DNS record editing. A is IPv4, AAAA is IPv6, and CNAME is an alias target; use selects instead of free-text inputs for short record-type enumerations.
- Avoid `.local` for VMware Cloud Foundation labs. Warn when a user enters `.local`, recommend `.internal`, and mention that `.local` is reserved for multicast DNS/link-local naming by RFC 6762 and listed as a special-use domain by RFC 6761. Treat `.internal` as LabFoundry's recommended private-use internal suffix; do not claim an IETF RFC reserves it unless the app copy cites a current authoritative source.
- Use `labfoundry.internal` as the sample/default internal domain.
- DHCP should be modeled as IP zones/scopes, not one global range. Each IP zone owns its interface, gateway, prefix, lease range, DNS servers, NTP servers, domain suffix, and per-zone options.
- DHCP also needs global options. Keep global options and per-zone options distinct in the UI.
- DHCP reservations should use DNS names. If a matching A or AAAA record is missing, ask for the FQDN and create the DNS record from the reservation IP rather than storing a disconnected hostname.
- DHCP domain fields should suggest current managed DNS domains.
- DHCP should expose actual leases in a separate tab or panel from desired state.

## Users, Auth, And Roles

- Keep local Users separate from authentication provider settings. LDAP is an authentication source, not the local user list.
- Users need roles because LabFoundry is expected to support OIDC. LDAP/OIDC integrations should support group-to-role mapping.
- Default local users should be created by seed logic when needed. The VCF Backup SFTP service has a default local user named `vcf-backup`; keep it visible under Users and selectable by the VCF Backup service.
- Never expose secrets in final responses, logs, widgets, or rendered previews beyond intentionally generated one-time credentials already displayed by the app.

## VCF Backups

- VCF Backups is an SFTP endpoint backed by local LabFoundry users. The selected SFTP user must come from Users.
- The default VCF Backup user is `vcf-backup`. Development seed credentials may exist for bootstrapping, but production workflows should prompt/reset credentials before exposure.
- VCF Backup listen targets must include access physical interfaces with IPs and VLAN interfaces with IPs; exclude trunk physical interfaces.
- The VCF-facing remote directory should be short and stable: `/backups`.
- The appliance backup storage is a fixed appliance volume mount, currently `/mnt/labfoundry-vcf-backups`; do not make this a routine UI-configurable field.
- The VCF Backup config preview should make the host-side volume and VCF remote directory clear, and OpenSSH should use `ForceCommand internal-sftp -d /backups` when chroot is enabled.

## VCF Private Registry

- VCF Private Registry is a Harbor-backed appliance service for staging VCF Supervisor Service bundles in a private OCI registry.
- The registry listen targets must follow the same service binding rule as VCF Backups: access physical interfaces with IPs and VLAN interfaces with IPs; exclude trunk physical interfaces.
- The default registry hostname is `registry.labfoundry.internal`, and the default Harbor project is `vcf-supervisor-services`.
- The registry storage path is a fixed appliance volume mount, currently `/mnt/labfoundry-vcf-registry`; do not make this a routine UI-configurable field.
- The registry CA bundle should come from the local LabFoundry CA when CA is enabled. When the local CA is disabled, require an uploaded PEM CA bundle and stage it through global appliance apply; do not expose a routine free-form CA bundle path editor.
- Bundle relocation should be modeled as desired state and previewed as `imgpkg copy` command intent. Development appliance apply jobs must record Harbor and relocation command intent through adapters instead of pushing images or mutating host services directly.
- Do not render Harbor admin passwords, robot account tokens, or registry credentials in config previews, job results, logs, widgets, or final responses.

## Database And Verification

- This project is still in MVP scaffold mode. When model/schema changes make the development SQLite database stale, prefer deleting/reseeding `data/labfoundry.db` over adding migrations, unless the user explicitly asks for migrations.
- Do not delete the DB for data-only seed/default updates if a focused in-place update is safer and the schema did not change.
- Any major product, architecture, workflow, safety-boundary, or operator-experience change must include a same-change documentation sweep for `README.md` and `AGENTS.md`.
- Before finalizing UI/backend changes, run focused tests for the touched area when available, then `pytest -q` for broader confidence. Also run `python -m compileall labfoundry` after broad Python/template-adjacent changes.
- Before finalizing appliance deployment changes, also run `python scripts/check_photon_compatibility.py`. If image build files changed and Packer is available, run `packer fmt` and `packer validate` from `image/hyperv/`.
- Restart the local uvicorn server after template/static/route changes so the in-app browser sees the new code. Bump the static asset query string in `base.html` after CSS or JS changes.

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

## Network And Service Binding

- Physical Interfaces are for untagged/access networks. VLAN Interfaces are only for tagged VLAN networks on physical parent interfaces marked as trunk.
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
- Restart the local uvicorn server after template/static/route changes so the in-app browser sees the new code. Bump the static asset query string in `base.html` after CSS or JS changes.

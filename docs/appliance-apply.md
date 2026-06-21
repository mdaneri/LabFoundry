# Appliance Apply

LabFoundry separates desired-state editing from appliance enforcement.

Service pages edit desired state. They autosave routine settings and grids, show local validation and rendered config previews, and link to the global apply review. They should not own service-specific apply buttons or service-specific apply submit routes.

`Appliance Apply` is the global review and submit surface. It lists changed apply units, checks valid changed units by default, and lets an operator unselect any unit that should remain pending.

## Apply Units

Current apply units are:

- Network
- Routes & WAN Simulation
- Firewall
- DNS/DHCP (dnsmasq)
- Certificate Authority
- KMS / KMIP
- VCF Backups
- VCF Offline Depot
- VCF Private Registry

DNS and DHCP are one unit because they share the rendered dnsmasq config and reload boundary.

## Baselines And Diffs

After a successful selected apply, LabFoundry stores the selected units' last-applied baseline in the existing `settings` table. The baseline includes the normalized snapshot hash, compact summary, rendered config preview, config path, and apply timestamp.

When desired state changes later, the global apply page compares the current rendered config preview to the last-applied preview and shows a unified config diff when available. On first apply, no baseline exists yet, so the page shows the current preview instead.

Rendered previews and job results must redact sensitive-looking values such as passwords, tokens, credentials, private keys, robot accounts, activation codes, and uploaded secret contents.

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

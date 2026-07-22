# ESX Storage over NFS

LabFoundry ESX Storage publishes ESX 9.x datastores over kernel NFS 3 and NFS 4.1. IPv4 and IPv6 have equal status: a share may enable IPv4, IPv6, or both, and LabFoundry never treats either family as preferred, secondary, fallback, or future work.

## Architecture

One `EsxNfsShare` is one datastore name, one backing directory, one selected storage interface or VLAN, and one preferred NFS version. A dual-stack share remains one database object and one data directory. LabFoundry derives two equivalent connection paths when both families are enabled:

- an IPv4 listener, A target record, IPv4 VMkernel allowlist, nftables `ip saddr` rule, and IPv4 ESX command;
- an IPv6 listener, AAAA target record, IPv6 VMkernel allowlist, nftables `ip6 saddr` rule, and IPv6 ESX command.

Every enabled family must have an address on the selected interface/VLAN and at least one matching VMkernel client IP or CIDR. There is no automatic family preference or failover. Every ESX host mounting the same datastore must use the same NFS version and the same generated hostname for the selected family, consistent with [Broadcom datastore identity guidance](https://knowledge.broadcom.com/external/article/422999/mounting-the-same-nfs-volume-in-differen.html).

## Storage VMkernel networks

Configure IPv4, IPv6, or both on the ESX VMkernel adapter that reaches the selected LabFoundry storage interface/VLAN. Put the exact VMkernel addresses or the smallest appropriate CIDRs into the matching share allowlists. Do not place an IPv6 client in the IPv4 list or an IPv4 client in the IPv6 list.

Before mounting, verify the selected path from every ESX host:

```text
vmkping -I vmk2 nfs-192-168-87-254.labfoundry.internal
vmkping -6 -I vmk2 nfs-2001-db8-87-0-0-0-0-fe.labfoundry.internal
```

Also query LabFoundry DNS directly and confirm the IPv4 target returns A and the IPv6 target returns AAAA. App-owned A/AAAA records use the existing dnsmasq `host-record` renderer and therefore receive normal generated PTR answers.

## DNS names

The normal canonical alias is `nfs.<domain>`. LabFoundry follows Appliance Settings’ global target-naming mode:

- IP mode generates names such as `nfs-192-168-87-254.<domain>` and `nfs-2001-db8-87-0-0-0-0-fe.<domain>`;
- interface mode generates an interface-derived target name with A and/or AAAA records.

The canonical alias publishes the same listener set as A and AAAA records for ordinary discovery. Dual-stack mount instructions deliberately do not use the canonical alias: the IPv4 command uses the generated A target and the IPv6 command uses the generated AAAA target. Resolver preference therefore cannot choose a family implicitly.

LabFoundry never replaces an operator-owned A or AAAA record. A collision blocks ESX Storage apply. Changing the interface, addresses, enabled families, service hostname, or enabled state removes stale app-owned records and marks `esx_storage`, `dnsmasq`, and `firewall` pending together. DNS desired state must be enabled and valid before ESX Storage can be applied.

## Volumes and disk initialization

Storage Volumes supports two sources:

1. An approved blank whole disk. Inventory accepts only a disk with a stable `/dev/disk/by-id` identity and no filesystem, partition, mount, swap use, LVM or RAID membership, holders, existing ESX Storage claim, read-only state, or relationship to the operating-system disk.
2. An eligible mounted ext4 filesystem. The mount must already exist, must not be an operating-system filesystem, and is revalidated during apply.

A newly initialized disk becomes a whole-device ext4 filesystem mounted by filesystem UUID at `/mnt/labfoundry-esx-storage/<volume-slug>`. `/dev/sdX` names are never persisted. The global review displays the complete model, serial, WWN, size, and stable identity and requires the exact text `FORMAT <volume-name>`. The resulting authorization belongs only to that appliance-apply job and the exact manifest hash/device identity; it is not desired state and is not placed in baselines or settings backups.

When a virtual SCSI controller does not expose a serial or WWN, the appliance udev policy creates a stable topology-derived `labfoundry-path-*` link under `/dev/disk/by-id`. The complete topology identity and fingerprint are still reviewed and revalidated; `/dev/sdX` is never accepted.

The helper inventories the disk again immediately before `mkfs.ext4`. If any safety property changed, apply stops before formatting. Formatting is deliberately not rolled back. If a later mount, export, service, DNS, or firewall step fails, the successfully created ext4 filesystem remains intact and an idempotent retry continues from it. V1 has no wipe, reformat, or delete-data action.

## Share paths and exports

Share paths are relative to a selected volume. LabFoundry rejects an empty/root path, `..` traversal, symlink escape, duplicate datastore names, overlapping exports, and root-plus-child exports. Multiple sibling directories on one volume are supported. Runtime bind mounts live under `/srv/labfoundry/esx-storage/<share-slug>`.

NFS 3 and NFS 4.1 are enabled globally over TCP; NFS 2, NFS 4.0, and UDP transport are disabled. `rpcbind.service` and `nfs-server.service` remain disabled until at least one valid share is active. Mountd uses fixed TCP port 20048. Exports use `rw,sync,no_subtree_check,no_root_squash` with AUTH_SYS and are restricted to the IPv4 and IPv6 VMkernel allowlists. `no_root_squash` follows [Broadcom’s ESX NFS access guidance](https://knowledge.broadcom.com/external/article/433826/esxi-host-fails-to-mount-nfs-datastore-w.html), so narrow client allowlists and the dedicated storage network are security requirements.

The preferred version controls the generated command and remote path:

- NFS 3: `/srv/labfoundry/esx-storage/<share-slug>` and TCP 111, 20048, and 2049;
- NFS 4.1: `/<share-slug>` and TCP 2049.

The page shows separate IPv4 and IPv6 `esxcli` commands. Use the command for the family configured on that ESX VMkernel path. After mounting, perform create/read/delete probes on every protocol/family combination in the acceptance topology.

## Apply, persistence, backup, and reset

ESX Storage stages `/var/lib/labfoundry/apply/esx-storage/labfoundry-esx-storage.json`. The constrained helper supports:

```text
labfoundry-helper esx-storage inventory
labfoundry-helper esx-storage validate <manifest>
labfoundry-helper esx-storage apply <manifest>
labfoundry-helper esx-storage status
labfoundry-helper esx-storage logs
```

Global appliance apply is the only mutation path. Dry-run records intended validation, format, UUID mount, bind mount, export, DNS, firewall, and service commands without changing the host. Real apply writes a managed `/etc/fstab` block, `/etc/exports.d/labfoundry-esx-storage.exports`, and `/etc/nfs.conf.d/labfoundry-esx-storage.conf`, then refreshes exports and services.

Settings backups include the service, volume fingerprints/UUIDs/mounts, and shares but never a format authorization. Restore marks volumes for runtime verification before reapply. Factory reset removes LabFoundry desired state, exports, and service enablement after apply; it does not erase, reformat, or delete files on storage disks. Reattach preserved ext4 data as an existing mounted volume.

## Troubleshooting

Check both address families independently:

```text
dig @<labfoundry-dns-ip> nfs-192-168-87-254.<domain> A
dig @<labfoundry-dns-ip> nfs-2001-db8-87-0-0-0-0-fe.<domain> AAAA
exportfs -v
ss -lntp | grep -E ':(111|2049|20048)\b'
nft list ruleset
systemctl status rpcbind.service nfs-server.service --no-pager
journalctl -u rpcbind.service -u nfs-server.service -n 200 --no-pager
```

If one family fails, verify its VMkernel address, route/VLAN, generated DNS record, allowlist family, and family-specific nftables source expression. Do not work around a family mismatch by switching to the canonical alias.

## iSCSI boundary

iSCSI is not part of this feature. The current Photon appliance kernel does not provide the maintained target modules/management stack required for a supportable implementation. iSCSI requires a separate feasibility issue and architecture review covering the target kernel, target management implementation, authentication, LUN lifecycle, persistence, firewalling, upgrade compatibility, and lifecycle acceptance. ESX Storage does not emulate iSCSI with an unsupported userspace target.

# DNS

LabFoundry manages DNS desired state through dnsmasq. Editing settings, zones, or records changes the control-plane database and the global DNS/DHCP preview; it does not mutate the appliance until an operator submits the `DNS/DHCP (dnsmasq)` unit through Appliance Apply.

## Local and authoritative modes

With **Authoritative** off, each managed domain renders as `local=/domain/`. LabFoundry answers known local records and forwards other queries to the configured upstream or conditional forwarders. Existing newline-delimited `domain` API values remain supported.

With **Authoritative** on, every managed forward domain renders as `auth-zone=domain`. dnsmasq has service-level authoritative settings, so all managed zones share one primary nameserver, SOA administrator, TTL, refresh, retry, expiry, and serial. v1 does not configure secondary nameservers, AXFR, or a separate DNS server. Generated reverse zones remain normal dnsmasq PTR behavior rather than authoritative reverse zones.

The authoritative renderer emits:

- `auth-server` for the configured primary nameserver, with the ordinary `interface` and `listen-address` directives retaining the selected DNS bind targets;
- one `auth-zone` per managed forward domain;
- shared `auth-soa` and `auth-ttl` values;
- A/AAAA `host-record` glue mapping the primary nameserver to every selected DNS listen address.

The primary nameserver must belong to a managed domain. Its glue identity is generated and cannot conflict with operator CNAME or A/AAAA data. SOA expiry must be greater than refresh and retry, and all timer values must be positive 32-bit seconds.

LabFoundry intentionally does not append interface or address arguments to `auth-server`. dnsmasq makes those destinations authoritative-only and returns `REFUSED` for recursive queries. Binding remains controlled by the selected `interface` and `listen-address` directives, allowing authoritative forward-zone answers, existing local PTR answers, and upstream forwarding to coexist on the lab listener.

## Generated zone records and serial

Each forward-zone summary and zone-file export shows the generated apex SOA and NS records plus nameserver glue. These records are structural and read-only; they are not available in the ordinary record-type selector or stored as duplicate DNS record rows.

LabFoundry owns one shared monotonic SOA serial. Zone creation/deletion, record create/update/delete/import, generated-record changes, and authoritative settings or listen-target changes advance it. The serial is exposed read-only in the UI and public DNS settings response and is included in backup/restore and desired-state comparisons.

## Zone-file import and export

Zone-file export includes `$ORIGIN`, `$TTL`, generated SOA/NS/glue, and all enabled operator records. Import supports A, AAAA, CNAME, TXT, SRV, MX, CAA, and PTR records. Matching SOA, NS, and glue are accepted and ignored rather than persisted. Conflicting structural metadata is rejected with guidance to change Authoritative DNS settings. Import remains scoped to the selected managed domain.

## Apply and verification

Review the DNS validation card and rendered config, then submit only the global DNS/DHCP unit when that is the intended changed unit. The helper stages and validates `/var/lib/labfoundry/apply/dnsmasq/labfoundry.conf`, installs `/etc/labfoundry/dnsmasq.d/labfoundry.conf`, and reloads or restarts `dnsmasq.service`.

On an applied appliance, verify the installed directives and query behavior:

```sh
sudo grep -E '^(auth-zone|auth-server|auth-soa|auth-ttl|host-record=ns)' /etc/labfoundry/dnsmasq.d/labfoundry.conf
systemctl is-active dnsmasq
dig @192.168.50.1 labfoundry.internal SOA
dig @192.168.50.1 labfoundry.internal NS
dig @192.168.50.1 ns1.labfoundry.internal A
dig @192.168.50.1 app.labfoundry.internal A
dig @192.168.50.1 -x 192.168.50.20
dig @192.168.50.1 missing.labfoundry.internal A
dig @192.168.50.1 example.com A
```

The missing managed name should return authoritative NXDOMAIN with SOA authority. The unrelated name verifies that configured upstream recursion still works. Replace addresses and names with the appliance's selected listener and managed data.

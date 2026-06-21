from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from pathlib import PurePosixPath

from labfoundry.app.models import DhcpOption, DhcpReservation, DhcpScope, DhcpSettings, DnsRecord, DnsSettings

DNS_CONDITIONAL_FORWARDERS_SETTING_KEY = "dns.conditional_forwarders"


def split_servers(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.replace(",", "\n").splitlines() if item.strip()]


def join_servers(servers: list[str]) -> str:
    return "\n".join(server.strip() for server in servers if server.strip())


def split_conditional_forwarders(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    forwarders: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            domain, server_text = line.split("=", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            domain, server_text = parts
        normalized_domain = domain.strip().strip(".").lower()
        for server in server_text.split(","):
            normalized_server = server.strip()
            key = (normalized_domain, normalized_server)
            if normalized_domain and normalized_server and key not in seen:
                seen.add(key)
                forwarders.append({"domain": normalized_domain, "server": normalized_server})
    return forwarders


def join_conditional_forwarders(forwarders: list[dict[str, str]]) -> str:
    grouped: dict[str, list[str]] = {}
    for forwarder in forwarders:
        domain = str(forwarder.get("domain", "")).strip().strip(".").lower()
        server = str(forwarder.get("server", "")).strip()
        if domain and server:
            grouped.setdefault(domain, [])
            if server not in grouped[domain]:
                grouped[domain].append(server)
    return "\n".join(f"{domain}={','.join(servers)}" for domain, servers in grouped.items())


def split_interfaces(raw: str | None) -> list[str]:
    if not raw:
        return []
    return _ordered_unique([item.strip() for item in raw.replace(",", "\n").splitlines() if item.strip()])


def join_interfaces(interfaces: list[str]) -> str:
    return "\n".join(split_interfaces("\n".join(interfaces)))


def split_addresses(raw: str | None) -> list[str]:
    if not raw:
        return []
    return _ordered_unique([item.strip() for item in raw.replace(",", "\n").splitlines() if item.strip()])


def join_addresses(addresses: list[str]) -> str:
    return "\n".join(split_addresses("\n".join(addresses)))


def split_domains(raw: str | None) -> list[str]:
    if not raw:
        return []
    return _ordered_unique([item.strip().strip(".").lower() for item in raw.replace(",", "\n").splitlines() if item.strip()])


def join_domains(domains: list[str]) -> str:
    return "\n".join(split_domains("\n".join(domains)))


def parse_hosts_records(hosts_text: str) -> tuple[list[dict[str, str | bool | None]], list[str]]:
    records: list[dict[str, str | bool | None]] = []
    errors: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for line_number, raw_line in enumerate(hosts_text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            errors.append(f"Line {line_number}: expected an IP address followed by at least one hostname.")
            continue
        address = parts[0]
        parsed_address = _validate_ip(address, f"Line {line_number} address", errors)
        if not parsed_address:
            continue
        record_type = "AAAA" if parsed_address.version == 6 else "A"
        for hostname in parts[1:]:
            normalized = hostname.strip().lower()
            key = (normalized, record_type)
            existing_address = seen.get(key)
            if existing_address == address:
                continue
            if existing_address:
                errors.append(f"Line {line_number}: {normalized} already maps to {existing_address}.")
                continue
            seen[key] = address
            records.append(
                {
                    "hostname": normalized,
                    "record_type": record_type,
                    "address": address,
                    "description": "Imported from hosts editor",
                    "enabled": True,
                }
            )
    return records, errors


def render_hosts_records(records: list[DnsRecord]) -> str:
    lines = [
        "# LabFoundry hosts editor",
        "# Format: <ip-address> <hostname> [alias ...]",
    ]
    for record in records:
        if record.enabled is not False and record.record_type.upper() in {"A", "AAAA"}:
            lines.append(f"{record.address} {record.hostname}")
    return "\n".join(lines) + "\n"


def render_zone_hosts_records(records: list[dict]) -> str:
    lines = [
        "# LabFoundry hosts import",
        "# Format: <ip-address> <hostname> [alias ...]",
    ]
    for record in records:
        if record["enabled"] is not False and record["record_type"].upper() in {"A", "AAAA"}:
            lines.append(f"{record['address']} {record['host_label']}")
    return "\n".join(lines) + "\n"


def render_zone_file(domain: str, records: list[dict]) -> str:
    lines = [
        f"$ORIGIN {domain}.",
        "$TTL 3600",
        "",
    ]
    for record in records:
        if record["enabled"] is False:
            continue
        record_type = record["record_type"].upper()
        if record_type not in {"A", "AAAA", "CNAME"}:
            continue
        lines.append(f"{record['host_label']:<24} IN {record_type:<5} {record['address']}")
    return "\n".join(lines) + "\n"


def parse_zone_records(zone_text: str, domain: str) -> tuple[list[dict[str, str | bool | None]], list[str]]:
    records: list[dict[str, str | bool | None]] = []
    errors: list[str] = []
    origin = domain.strip().strip(".").lower()
    for line_number, raw_line in enumerate(zone_text.splitlines(), start=1):
        line = raw_line.split(";", 1)[0].strip()
        if not line or line.startswith("$TTL"):
            continue
        if line.upper().startswith("$ORIGIN"):
            parts = line.split()
            if len(parts) >= 2:
                origin = parts[1].strip().strip(".").lower()
            continue
        parts = line.split()
        if len(parts) < 3:
            errors.append(f"Line {line_number}: expected <host> [ttl] [IN] <type> <value>.")
            continue
        host = parts[0]
        tokens = parts[1:]
        if tokens and tokens[0].isdigit():
            tokens = tokens[1:]
        if tokens and tokens[0].upper() == "IN":
            tokens = tokens[1:]
        if len(tokens) < 2:
            errors.append(f"Line {line_number}: expected a record type and value.")
            continue
        record_type = tokens[0].upper()
        value = tokens[1].strip().strip(".").lower()
        hostname = _zone_hostname(host, origin)
        record_errors = validate_dns_record(hostname, record_type, value)
        if record_errors:
            errors.extend(f"Line {line_number}: {error}" for error in record_errors)
            continue
        records.append(
            {
                "hostname": hostname,
                "record_type": record_type,
                "address": value,
                "description": "Imported from zone editor",
                "enabled": True,
            }
        )
    return records, errors


def dns_reverse_records(records: list[DnsRecord]) -> list[dict[str, str]]:
    reverse_records: list[dict[str, str]] = []
    for record in records:
        record_type = record.record_type.upper()
        if record.enabled is False or record_type not in {"A", "AAAA"}:
            continue
        try:
            parsed_address = ip_address(record.address)
        except ValueError:
            continue
        ptr_name = parsed_address.reverse_pointer
        zone = _reverse_zone_for_address(parsed_address)
        owner = ptr_name.removesuffix(f".{zone}") if ptr_name.endswith(f".{zone}") else ptr_name
        reverse_records.append(
            {
                "owner": owner,
                "zone": zone,
                "ptr_name": ptr_name,
                "target": record.hostname.strip().strip(".").lower(),
                "address": record.address,
                "record_type": record_type,
            }
        )
    return reverse_records


def dns_settings_to_dict(settings: DnsSettings, conditional_forwarders: str | None = None) -> dict:
    return {
        "id": settings.id,
        "enabled": settings.enabled,
        "listen_interface": settings.listen_interface,
        "listen_address": settings.listen_address,
        "domain": settings.domain,
        "upstream_servers": split_servers(settings.upstream_servers),
        "conditional_forwarders": split_conditional_forwarders(conditional_forwarders),
        "cache_size": settings.cache_size,
        "expand_hosts": settings.expand_hosts,
        "authoritative": settings.authoritative,
        "config_path": settings.config_path,
        "updated_at": settings.updated_at,
    }


def dhcp_settings_to_scope(settings: DhcpSettings) -> dict:
    return {
        "id": 0,
        "name": "SiteA",
        "interface_name": settings.interface_name,
        "site_address": settings.site_address,
        "prefix_length": settings.prefix_length,
        "range_start": settings.range_start,
        "range_end": settings.range_end,
        "lease_time": settings.lease_time,
        "domain_name": settings.domain_name,
        "dns_server": settings.dns_server,
        "ntp_server": "",
        "enabled": settings.enabled,
        "description": "Compatibility scope from DHCP settings.",
    }


def dhcp_scope_to_dict(scope: DhcpScope) -> dict:
    return {
        "id": scope.id,
        "name": scope.name,
        "interface_name": scope.interface_name,
        "site_address": scope.site_address,
        "prefix_length": scope.prefix_length,
        "range_start": scope.range_start,
        "range_end": scope.range_end,
        "lease_time": scope.lease_time,
        "domain_name": scope.domain_name,
        "dns_server": scope.dns_server,
        "ntp_server": scope.ntp_server,
        "enabled": scope.enabled,
        "description": scope.description or "",
    }


def dhcp_option_to_dict(option: DhcpOption) -> dict:
    return {
        "id": option.id,
        "scope_id": option.scope_id if option.scope_id is not None else "__global__",
        "option_code": option.option_code,
        "value": option.value,
        "description": option.description or "",
        "enabled": option.enabled,
    }


def parse_dnsmasq_leases(raw_text: str, now: datetime | None = None) -> list[dict]:
    current_time = now or datetime.now(timezone.utc)
    leases: list[dict] = []
    for line in raw_text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        expires_raw, mac_address, ip, hostname = fields[:4]
        client_id = fields[4] if len(fields) > 4 else ""
        try:
            expires_epoch = int(expires_raw)
        except ValueError:
            continue
        expires_at = None if expires_epoch == 0 else datetime.fromtimestamp(expires_epoch, tz=timezone.utc)
        if expires_at is None:
            status = "reserved"
        elif expires_at > current_time:
            status = "active"
        else:
            status = "expired"
        leases.append(
            {
                "expires_at": expires_at,
                "mac_address": mac_address,
                "ip_address": ip,
                "hostname": hostname if hostname != "*" else "",
                "client_id": client_id if client_id != "*" else "",
                "status": status,
            }
        )
    return leases


def validate_dns_settings(settings: DnsSettings, records: list[DnsRecord], conditional_forwarders: str | None = None) -> list[str]:
    errors: list[str] = []
    if not split_interfaces(settings.listen_interface):
        errors.append("DNS must listen on at least one interface.")
    if not split_domains(settings.domain):
        errors.append("DNS must manage at least one domain.")
    for domain in split_domains(settings.domain):
        if any(character.isspace() for character in domain):
            errors.append(f"DNS domain {domain} must not contain whitespace.")
    for address in split_addresses(settings.listen_address):
        _validate_ip(address, f"DNS listen address {address}", errors)
    for server in split_servers(settings.upstream_servers):
        _validate_ip(server, f"upstream server {server}", errors)
    for forwarder in split_conditional_forwarders(conditional_forwarders):
        domain = forwarder["domain"]
        server = forwarder["server"]
        if any(character.isspace() for character in domain):
            errors.append(f"conditional forwarder domain {domain} must not contain whitespace.")
        if not domain or "." not in domain:
            errors.append(f"conditional forwarder domain {domain or '(blank)'} must be a DNS domain.")
        _validate_forwarder_server(server, f"conditional forwarder {domain} server", errors)
    for record in records:
        if record.enabled is not False:
            errors.extend(validate_dns_record(record.hostname, record.record_type, record.address))
    if (settings.cache_size or 0) < 0:
        errors.append("DNS cache size must be zero or greater.")
    return errors


def dns_domain_warnings(domains: list[str]) -> list[str]:
    warnings: list[str] = []
    for domain in split_domains("\n".join(domains)):
        if domain.endswith(".local"):
            warnings.append(
                f"DNS domain {domain} uses .local. RFC 6762 defines .local for multicast DNS/link-local naming, "
                "and RFC 6761 lists it as a special-use domain. Use .internal for VCF labs; ICANN/IANA selected "
                ".internal for private-use internal networks, and VMware Cloud Foundation does not work reliably with .local domains."
            )
    return warnings


def validate_dns_record(hostname: str, record_type: str, address: str) -> list[str]:
    errors: list[str] = []
    normalized_type = record_type.strip().upper()
    if normalized_type not in {"A", "AAAA", "CNAME"}:
        errors.append(f"record {hostname} type must be A, AAAA, or CNAME.")
        return errors
    if normalized_type == "CNAME":
        target = address.strip().strip(".").lower()
        if not target:
            errors.append(f"CNAME record {hostname} must point to a target hostname.")
            return errors
        if _is_ip_address(target):
            errors.append(f"CNAME record {hostname} must point to a hostname, not an IP address.")
        if any(character.isspace() for character in target):
            errors.append(f"CNAME record {hostname} target must not contain whitespace.")
        return errors
    parsed_address = _validate_ip(address, f"record {hostname}", errors)
    if not parsed_address:
        return errors
    if normalized_type == "A" and parsed_address.version != 4:
        errors.append(f"A record {hostname} must use an IPv4 address.")
    if normalized_type == "AAAA" and parsed_address.version != 6:
        errors.append(f"AAAA record {hostname} must use an IPv6 address.")
    return errors


def validate_dhcp_settings(
    settings: DhcpSettings,
    reservations: list[DhcpReservation],
    scopes: list[DhcpScope] | None = None,
    options: list[DhcpOption] | None = None,
) -> list[str]:
    errors: list[str] = []
    scope_rows = scopes if scopes else [_legacy_scope(settings)]
    enabled_scopes = [scope for scope in scope_rows if scope.enabled is not False]
    if settings.enabled and not enabled_scopes:
        errors.append("DHCP must have at least one enabled IP zone when the service is enabled.")
    networks = []
    for scope in scope_rows:
        scope_errors, network = validate_dhcp_scope(scope)
        errors.extend(scope_errors)
        if scope.enabled is not False and network:
            networks.append((scope.name, network))
    for reservation in reservations:
        if reservation.enabled is not False:
            reserved_ip = _validate_ip(reservation.ip_address, f"reservation {reservation.hostname}", errors)
            if networks and reserved_ip and not any(reserved_ip in network for _, network in networks):
                errors.append(f"reservation {reservation.hostname} must be inside an enabled DHCP IP zone.")
    scope_ids = {scope.id for scope in scope_rows if scope.id is not None}
    for option in options or []:
        if option.enabled is False:
            continue
        label = "global DHCP option" if option.scope_id is None else f"DHCP option for scope {option.scope_id}"
        if option.scope_id is not None and option.scope_id not in scope_ids:
            errors.append(f"{label} references a missing DHCP IP zone.")
        if not option.option_code.strip():
            errors.append(f"{label} code is required.")
        if not option.value.strip():
            errors.append(f"{label} value is required.")
    return errors


def validate_dhcp_scope(scope: DhcpScope) -> tuple[list[str], object | None]:
    errors: list[str] = []
    label = f"DHCP IP zone {scope.name}"
    if not scope.name.strip():
        errors.append("DHCP IP zone name is required.")
    site_address = _validate_ip(scope.site_address, f"{label} gateway", errors)
    range_start = _validate_ip(scope.range_start, f"{label} range start", errors)
    range_end = _validate_ip(scope.range_end, f"{label} range end", errors)
    dns_server = _validate_ip(scope.dns_server, f"{label} DNS server", errors)
    ntp_server = _validate_ip(scope.ntp_server, f"{label} NTP server", errors) if scope.ntp_server else None
    network = None
    if site_address:
        try:
            network = ip_network(f"{site_address}/{scope.prefix_length}", strict=False)
        except ValueError:
            errors.append(f"{label} prefix length is not valid.")
    if network and range_start and range_start not in network:
        errors.append(f"{label} range start must be inside the zone subnet.")
    if network and range_end and range_end not in network:
        errors.append(f"{label} range end must be inside the zone subnet.")
    if range_start and range_end and int(range_start) > int(range_end):
        errors.append(f"{label} range start must be less than or equal to range end.")
    if network and dns_server and dns_server not in network:
        errors.append(f"{label} DNS server should be inside the zone subnet.")
    if network and ntp_server and ntp_server not in network:
        errors.append(f"{label} NTP server should be inside the zone subnet.")
    return errors, network


def render_dnsmasq_config(
    *,
    dns_settings: DnsSettings,
    dns_records: list[DnsRecord],
    dhcp_settings: DhcpSettings,
    dhcp_reservations: list[DhcpReservation],
    dhcp_scopes: list[DhcpScope] | None = None,
    dhcp_options: list[DhcpOption] | None = None,
    conditional_forwarders: str | None = None,
) -> str:
    domains = split_domains(dns_settings.domain) or ["labfoundry.internal"]
    scopes = dhcp_scopes if dhcp_scopes else [_legacy_scope(dhcp_settings)]
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        "domain-needed",
        "bogus-priv",
        "no-resolv",
        "bind-interfaces",
        f"cache-size={dns_settings.cache_size if dns_settings.cache_size is not None else 1000}",
    ]
    for domain in domains:
        lines.append(f"domain={domain}")
    if dns_settings.expand_hosts:
        lines.append("expand-hosts")
    if dhcp_settings.enabled and dhcp_settings.authoritative:
        lines.append("dhcp-authoritative")
    dhcp_interfaces = [scope.interface_name for scope in scopes if scope.enabled is not False]
    for interface_name in _ordered_unique([*split_interfaces(dns_settings.listen_interface), *dhcp_interfaces]):
        lines.append(f"interface={interface_name}")
    for listen_address in split_addresses(dns_settings.listen_address):
        lines.append(f"listen-address={listen_address}")
    for server in split_servers(dns_settings.upstream_servers):
        lines.append(f"server={server}")
    for forwarder in split_conditional_forwarders(conditional_forwarders):
        lines.append(f"server=/{forwarder['domain']}/{forwarder['server']}")
    for record in dns_records:
        if record.enabled is False:
            continue
        if record.record_type.upper() in {"A", "AAAA"}:
            # dnsmasq host-record also creates the matching PTR record.
            lines.append(f"host-record={record.hostname},{record.address}")
        elif record.record_type.upper() == "CNAME":
            lines.append(f"cname={record.hostname},{record.address.strip().strip('.').lower()}")
    if dhcp_settings.enabled:
        scope_tags = {scope.id: _dnsmasq_tag(scope.name) for scope in scopes}
        for scope in scopes:
            if scope.enabled is False:
                continue
            tag = _dnsmasq_tag(scope.name)
            lines.extend(
                [
                    f"dhcp-range=set:{tag},{scope.range_start},{scope.range_end},{scope.lease_time or '12h'}",
                    f"dhcp-option=tag:{tag},option:router,{scope.site_address}",
                    f"dhcp-option=tag:{tag},option:dns-server,{scope.dns_server}",
                    f"dhcp-option=tag:{tag},option:domain-name,{scope.domain_name or domains[0]}",
                ]
            )
            if scope.ntp_server:
                lines.append(f"dhcp-option=tag:{tag},option:ntp-server,{scope.ntp_server}")
        for option in dhcp_options or []:
            if option.enabled is False:
                continue
            option_code = option.option_code.strip()
            option_value = option.value.strip()
            if not option_code or not option_value:
                continue
            if option.scope_id is None:
                lines.append(f"dhcp-option=option:{option_code},{option_value}")
            elif option.scope_id in scope_tags:
                lines.append(f"dhcp-option=tag:{scope_tags[option.scope_id]},option:{option_code},{option_value}")
        for reservation in dhcp_reservations:
            if reservation.enabled is not False:
                lines.append(f"dhcp-host={reservation.mac_address},{reservation.hostname},{reservation.ip_address}")
    return "\n".join(lines) + "\n"


def reservation_dns_record(reservation: DhcpReservation, scopes: list[DhcpScope]) -> tuple[str, str, str] | None:
    try:
        reserved_ip = ip_address(reservation.ip_address)
    except ValueError:
        return None
    matching_scope = _scope_for_ip(reserved_ip, scopes)
    if matching_scope is None:
        return None
    hostname = reservation.hostname.strip().strip(".").lower()
    domain = matching_scope.domain_name.strip().strip(".").lower()
    fqdn = hostname if "." in hostname else f"{hostname}.{domain}"
    record_type = "AAAA" if reserved_ip.version == 6 else "A"
    return fqdn, record_type, str(reserved_ip)


def dnsmasq_test_command(config_path: str) -> list[str]:
    normalized_path = str(PurePosixPath(config_path))
    return ["dnsmasq", "--test", f"--conf-file={normalized_path}"]


def _validate_ip(value: str, label: str, errors: list[str]):
    try:
        return ip_address(value)
    except ValueError:
        errors.append(f"{label} is not a valid IP address.")
        return None


def _is_ip_address(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _validate_forwarder_server(value: str, label: str, errors: list[str]) -> None:
    server = value.strip()
    if "#" in server:
        server, port = server.rsplit("#", 1)
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            errors.append(f"{label} port must be between 1 and 65535.")
    _validate_ip(server, label, errors)


def _zone_hostname(host: str, origin: str) -> str:
    normalized = host.strip().strip(".").lower()
    if normalized == "@":
        return origin
    if normalized.endswith(f".{origin}"):
        return normalized
    if "." in normalized:
        return normalized
    return f"{normalized}.{origin}"


def _reverse_zone_for_address(value) -> str:
    if value.version == 4:
        octets = str(value).split(".")
        return f"{octets[2]}.{octets[1]}.{octets[0]}.in-addr.arpa"
    labels = value.reverse_pointer.split(".")
    return ".".join(labels[16:])


def _legacy_scope(settings: DhcpSettings) -> DhcpScope:
    return DhcpScope(
        id=0,
        name="SiteA",
        interface_name=settings.interface_name,
        site_address=settings.site_address,
        prefix_length=settings.prefix_length,
        range_start=settings.range_start,
        range_end=settings.range_end,
        lease_time=settings.lease_time,
        domain_name=settings.domain_name,
        dns_server=settings.dns_server,
        ntp_server="",
        enabled=settings.enabled,
        description="Compatibility scope from DHCP settings.",
    )


def _dnsmasq_tag(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value.strip())
    return normalized.strip("-") or "scope"


def _scope_for_ip(value, scopes: list[DhcpScope]) -> DhcpScope | None:
    for scope in scopes:
        if scope.enabled is False:
            continue
        try:
            network = ip_network(f"{scope.site_address}/{scope.prefix_length}", strict=False)
        except ValueError:
            continue
        if value in network:
            return scope
    return None


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result

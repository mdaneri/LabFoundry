from ipaddress import ip_network
import re

from labfoundry.app.models import (
    DhcpScope,
    DhcpSettings,
    DnsSettings,
    FirewallRule,
    FirewallSettings,
    KmsSettings,
    PhysicalInterface,
    VcfBackupSettings,
    VcfOfflineDepotSettings,
    VcfPrivateRegistrySettings,
    VlanInterface,
)


FIREWALL_DIRECTIONS = ["input", "forward", "output"]
FIREWALL_ACTIONS = ["accept", "drop", "reject"]
FIREWALL_PROTOCOLS = ["any", "tcp", "udp", "icmp"]
FIREWALL_POLICIES = ["accept", "drop"]
FIREWALL_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/firewall/labfoundry.nft"
LABFOUNDRY_DHCP_FIREWALL_RULE_MARKER = "LabFoundry-managed DNS/DHCP access"
LABFOUNDRY_DNS_FIREWALL_RULE_MARKER = "LabFoundry-managed DNS access"
LABFOUNDRY_SERVICE_FIREWALL_RULE_MARKER = "LabFoundry-managed service access"
LABFOUNDRY_LEGACY_DHCP_FIREWALL_RULE_NAMES = {"sitea-dns-dhcp"}
LABFOUNDRY_LEGACY_SERVICE_FIREWALL_RULE_NAMES = {"mgmt-console"}


def firewall_settings_to_dict(settings: FirewallSettings) -> dict:
    return {
        "id": settings.id,
        "enabled": settings.enabled,
        "default_input_policy": settings.default_input_policy,
        "default_forward_policy": settings.default_forward_policy,
        "default_output_policy": settings.default_output_policy,
        "allow_established": settings.allow_established,
        "allow_loopback": settings.allow_loopback,
        "allow_icmp": settings.allow_icmp,
        "log_dropped": settings.log_dropped,
        "config_path": settings.config_path,
        "updated_at": settings.updated_at.isoformat() if settings.updated_at else "",
    }


def firewall_rule_to_dict(rule: FirewallRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "direction": rule.direction,
        "action": rule.action,
        "protocol": rule.protocol,
        "source": rule.source,
        "destination": rule.destination,
        "destination_port": rule.destination_port,
        "interface_name": rule.interface_name,
        "priority": rule.priority,
        "enabled": rule.enabled,
        "description": rule.description or "",
        "created_at": rule.created_at.isoformat() if rule.created_at else "",
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else "",
        "is_new": False,
    }


def validate_firewall_settings(settings: FirewallSettings) -> list[str]:
    errors: list[str] = []
    for field_name, value in [
        ("Default input policy", settings.default_input_policy),
        ("Default forward policy", settings.default_forward_policy),
        ("Default output policy", settings.default_output_policy),
    ]:
        if value not in FIREWALL_POLICIES:
            errors.append(f"{field_name} must be accept or drop.")
    return errors


def validate_firewall_rule(rule: FirewallRule) -> list[str]:
    errors: list[str] = []
    if not rule.name.strip():
        errors.append("Rule name is required.")
    if rule.direction not in FIREWALL_DIRECTIONS:
        errors.append("Direction must be input, forward, or output.")
    if rule.action not in FIREWALL_ACTIONS:
        errors.append("Action must be accept, drop, or reject.")
    if rule.protocol not in FIREWALL_PROTOCOLS:
        errors.append("Protocol must be any, tcp, udp, or icmp.")
    if rule.protocol in {"any", "icmp"} and rule.destination_port.strip():
        errors.append("Destination port is only valid for TCP or UDP rules.")
    for label, value in [("Source", rule.source), ("Destination", rule.destination)]:
        normalized = value.strip().lower()
        if normalized in {"", "any"}:
            continue
        try:
            ip_network(normalized, strict=False)
        except ValueError:
            errors.append(f"{label} must be 'any' or a valid IPv4/IPv6 address or CIDR.")
    if rule.destination_port.strip():
        errors.extend(_validate_ports(rule.destination_port))
    return errors


def dhcp_firewall_rules(dhcp_settings: DhcpSettings, scopes: list[DhcpScope]) -> list[FirewallRule]:
    if not dhcp_settings.enabled:
        return []
    generated_rules: list[FirewallRule] = []
    for index, scope in enumerate([item for item in scopes if item.enabled], start=1):
        generated_rules.append(
            FirewallRule(
                name=f"{_slug(scope.name)}-dns-dhcp",
                direction="input",
                action="accept",
                protocol="udp",
                source=_scope_network(scope),
                destination="any",
                destination_port="53,67",
                interface_name=scope.interface_name.strip(),
                priority=20 + index,
                enabled=True,
                description=f"{LABFOUNDRY_DHCP_FIREWALL_RULE_MARKER} for DHCP IP zone {scope.name}.",
            )
        )
    return generated_rules


def dns_firewall_rules(dns_settings: DnsSettings, interface_networks: dict[str, str]) -> list[FirewallRule]:
    if not dns_settings.enabled:
        return []
    generated_rules: list[FirewallRule] = []
    listen_interfaces = [item.strip() for item in dns_settings.listen_interface.replace(",", "\n").splitlines() if item.strip()]
    for index, interface_name in enumerate(listen_interfaces, start=1):
        for protocol_offset, protocol in enumerate(["tcp", "udp"]):
            generated_rules.append(
                FirewallRule(
                    name=f"{_slug(interface_name)}-dns-{protocol}",
                    direction="input",
                    action="accept",
                    protocol=protocol,
                    source=interface_networks.get(interface_name, "any"),
                    destination="any",
                    destination_port="53",
                    interface_name=interface_name,
                    priority=40 + (index * 2) + protocol_offset,
                    enabled=True,
                    description=f"{LABFOUNDRY_DNS_FIREWALL_RULE_MARKER} for DNS listener {interface_name}.",
                )
            )
    return generated_rules


def managed_service_firewall_rules(
    *,
    dns_settings: DnsSettings,
    dhcp_settings: DhcpSettings,
    dhcp_scopes: list[DhcpScope],
    kms_settings: KmsSettings,
    vcf_backup_settings: VcfBackupSettings,
    vcf_depot_settings: VcfOfflineDepotSettings,
    vcf_registry_settings: VcfPrivateRegistrySettings,
    interface_networks: dict[str, str],
) -> list[FirewallRule]:
    rules = [
        _service_firewall_rule(
            name="mgmt-console",
            service="Management console",
            interface_name="eth0",
            source=interface_networks.get("eth0", "192.168.49.0/24"),
            protocol="tcp",
            ports="22,443,8000",
            priority=10,
        )
    ]
    rules.extend(dns_firewall_rules(dns_settings, interface_networks))
    rules.extend(dhcp_firewall_rules(dhcp_settings, dhcp_scopes))
    if kms_settings.enabled:
        rules.append(
            _service_firewall_rule(
                name="kms-kmip",
                service="KMS / KMIP",
                interface_name=kms_settings.listen_interface,
                source=interface_networks.get(kms_settings.listen_interface, "any"),
                protocol="tcp",
                ports=str(kms_settings.port),
                priority=60,
            )
        )
    if vcf_backup_settings.enabled:
        rules.append(
            _service_firewall_rule(
                name="vcf-backups-sftp",
                service="VCF Backups SFTP",
                interface_name=vcf_backup_settings.listen_interface,
                source=interface_networks.get(vcf_backup_settings.listen_interface, "any"),
                protocol="tcp",
                ports=str(vcf_backup_settings.port),
                priority=70,
            )
        )
    if vcf_depot_settings.enabled:
        rules.append(
            _service_firewall_rule(
                name="vcf-offline-depot",
                service="VCF Offline Depot",
                interface_name=vcf_depot_settings.listen_interface,
                source=interface_networks.get(vcf_depot_settings.listen_interface, "any"),
                protocol="tcp",
                ports=str(vcf_depot_settings.port),
                priority=80,
            )
        )
    if vcf_registry_settings.enabled:
        rules.append(
            _service_firewall_rule(
                name="vcf-private-registry",
                service="VCF Private Registry",
                interface_name=vcf_registry_settings.listen_interface,
                source=interface_networks.get(vcf_registry_settings.listen_interface, "any"),
                protocol="tcp",
                ports=str(vcf_registry_settings.port),
                priority=90,
            )
        )
    return rules


def firewall_interface_networks(interfaces: list[PhysicalInterface], vlans: list[VlanInterface]) -> dict[str, str]:
    networks: dict[str, str] = {}
    for interface in interfaces:
        network = _network_from_cidr(interface.ip_cidr)
        if network:
            networks[interface.name] = network
    for vlan in vlans:
        if not vlan.enabled:
            continue
        network = _network_from_cidr(vlan.ip_cidr)
        if network:
            networks[vlan.name] = network
    return networks


def effective_firewall_rules(
    rules: list[FirewallRule],
    generated_rules: list[FirewallRule] | None = None,
    replace_labfoundry_dhcp_rules: bool = False,
    replace_labfoundry_dns_rules: bool = False,
    replace_labfoundry_service_rules: bool = False,
) -> list[FirewallRule]:
    generated_rules = generated_rules or []
    if not generated_rules and not replace_labfoundry_dhcp_rules and not replace_labfoundry_dns_rules and not replace_labfoundry_service_rules:
        return rules
    return [
        rule
        for rule in rules
        if not (
            (replace_labfoundry_dhcp_rules and is_labfoundry_dhcp_firewall_rule(rule))
            or (replace_labfoundry_dns_rules and is_labfoundry_dns_firewall_rule(rule))
            or (replace_labfoundry_service_rules and is_labfoundry_managed_firewall_rule(rule))
        )
    ] + generated_rules


def is_labfoundry_managed_firewall_rule(rule: FirewallRule) -> bool:
    normalized_name = rule.name.strip().lower()
    description = (rule.description or "").strip()
    return (
        normalized_name in LABFOUNDRY_LEGACY_SERVICE_FIREWALL_RULE_NAMES
        or LABFOUNDRY_SERVICE_FIREWALL_RULE_MARKER in description
        or is_labfoundry_dns_firewall_rule(rule)
        or is_labfoundry_dhcp_firewall_rule(rule)
    )


def is_labfoundry_dhcp_firewall_rule(rule: FirewallRule) -> bool:
    normalized_name = rule.name.strip().lower()
    description = (rule.description or "").strip()
    return normalized_name in LABFOUNDRY_LEGACY_DHCP_FIREWALL_RULE_NAMES or LABFOUNDRY_DHCP_FIREWALL_RULE_MARKER in description


def is_labfoundry_dns_firewall_rule(rule: FirewallRule) -> bool:
    normalized_name = rule.name.strip().lower()
    description = (rule.description or "").strip()
    protocol = rule.protocol.strip().lower()
    return (
        LABFOUNDRY_DNS_FIREWALL_RULE_MARKER in description
        or (
            normalized_name.startswith("allow-")
            and "-dns-" in normalized_name
            and protocol in {"tcp", "udp"}
            and rule.destination_port.strip() == "53"
            and description.startswith("Allow DNS from ")
        )
    )


def validate_firewall_state(
    settings: FirewallSettings,
    rules: list[FirewallRule],
    generated_rules: list[FirewallRule] | None = None,
    replace_labfoundry_dhcp_rules: bool = False,
    replace_labfoundry_dns_rules: bool = False,
    replace_labfoundry_service_rules: bool = False,
) -> list[str]:
    errors = validate_firewall_settings(settings)
    seen_names: set[str] = set()
    for rule in effective_firewall_rules(
        rules,
        generated_rules,
        replace_labfoundry_dhcp_rules,
        replace_labfoundry_dns_rules,
        replace_labfoundry_service_rules,
    ):
        normalized_name = rule.name.strip().lower()
        if normalized_name in seen_names:
            errors.append(f"Firewall rule {rule.name} is duplicated.")
        seen_names.add(normalized_name)
        errors.extend(validate_firewall_rule(rule))
    return errors


def render_nftables_config(
    settings: FirewallSettings,
    rules: list[FirewallRule],
    generated_rules: list[FirewallRule] | None = None,
    replace_labfoundry_dhcp_rules: bool = False,
    replace_labfoundry_dns_rules: bool = False,
    replace_labfoundry_service_rules: bool = False,
) -> str:
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        "# nftables firewall state for Photon OS appliance images.",
        "flush ruleset",
    ]
    if not settings.enabled:
        lines.extend(
            [
                "# LabFoundry firewall desired state is disabled.",
                "# Applying this state clears LabFoundry-managed nftables rules.",
            ]
        )
        return "\n".join(lines) + "\n"
    lines.append("table inet labfoundry {")
    for chain_name, policy in [
        ("input", settings.default_input_policy),
        ("forward", settings.default_forward_policy),
        ("output", settings.default_output_policy),
    ]:
        hook_priority = "filter"
        lines.append(f"  chain {chain_name} {{")
        lines.append(f"    type filter hook {chain_name} priority {hook_priority}; policy {policy};")
        if chain_name == "input" and settings.allow_loopback:
            lines.append('    iifname "lo" accept comment "LabFoundry loopback"')
        if settings.allow_established:
            lines.append('    ct state established,related accept comment "LabFoundry established traffic"')
        if chain_name == "input" and not replace_labfoundry_service_rules:
            lines.append('    ip saddr 192.168.49.0/24 tcp dport { 22, 443, 8000 } accept comment "LabFoundry management access"')
        if settings.allow_icmp:
            lines.append('    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"')
            lines.append('    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"')
        for rule in sorted(
            [
                item
                for item in effective_firewall_rules(
                    rules,
                    generated_rules,
                    replace_labfoundry_dhcp_rules,
                    replace_labfoundry_dns_rules,
                    replace_labfoundry_service_rules,
                )
                if item.enabled and item.direction == chain_name
            ],
            key=lambda item: item.priority,
        ):
            lines.append(f"    {_render_rule(rule)}")
        if settings.log_dropped and policy == "drop":
            lines.append(f'    log prefix "labfoundry {chain_name} drop: " flags all counter')
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_rule(rule: FirewallRule) -> str:
    parts: list[str] = []
    if rule.interface_name.strip():
        interface_key = "oifname" if rule.direction == "output" else "iifname"
        parts.append(f'{interface_key} "{rule.interface_name.strip()}"')
    protocol = rule.protocol.strip().lower()
    if protocol == "icmp":
        parts.append("meta l4proto icmp")
    source = rule.source.strip().lower()
    if source and source != "any":
        parts.append(_address_expr("saddr", source))
    destination = rule.destination.strip().lower()
    if destination and destination != "any":
        parts.append(_address_expr("daddr", destination))
    if protocol in {"tcp", "udp"} and rule.destination_port.strip():
        ports = _render_ports(rule.destination_port)
        parts.append(f"{protocol} dport {ports}")
    elif protocol in {"tcp", "udp"}:
        parts.append(f"meta l4proto {protocol}")
    parts.append(rule.action)
    parts.append(f'comment "{_safe_comment(rule.name)}"')
    return " ".join(parts)


def _validate_ports(raw_ports: str) -> list[str]:
    errors: list[str] = []
    for item in raw_ports.replace(",", "\n").splitlines():
        value = item.strip()
        if not value:
            continue
        if "-" in value:
            start, end = value.split("-", 1)
            if not _valid_port(start) or not _valid_port(end) or int(start) > int(end):
                errors.append(f"Port range {value} is invalid.")
        elif not _valid_port(value):
            errors.append(f"Port {value} is invalid.")
    return errors


def _valid_port(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 65535


def _address_expr(direction: str, value: str) -> str:
    family = "ip6" if ip_network(value, strict=False).version == 6 else "ip"
    return f"{family} {direction} {value}"


def _render_ports(raw_ports: str) -> str:
    ports = [item.strip().replace("-", "-") for item in raw_ports.replace(",", "\n").splitlines() if item.strip()]
    if not ports:
        return ""
    if len(ports) == 1:
        return ports[0].replace("-", "-")
    return "{ " + ", ".join(ports) + " }"


def _safe_comment(value: str) -> str:
    return value.replace('"', "'").strip()


def _service_firewall_rule(
    *,
    name: str,
    service: str,
    interface_name: str,
    source: str,
    protocol: str,
    ports: str,
    priority: int,
) -> FirewallRule:
    return FirewallRule(
        name=name,
        direction="input",
        action="accept",
        protocol=protocol,
        source=source,
        destination="any",
        destination_port=ports,
        interface_name=interface_name.strip(),
        priority=priority,
        enabled=True,
        description=f"{LABFOUNDRY_SERVICE_FIREWALL_RULE_MARKER} for {service}.",
    )


def _scope_network(scope: DhcpScope) -> str:
    try:
        return str(ip_network(f"{scope.site_address.strip()}/{scope.prefix_length}", strict=False))
    except ValueError:
        return scope.site_address.strip() or "any"


def _network_from_cidr(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(ip_network(value, strict=False))
    except ValueError:
        return ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "dhcp-scope"

from ipaddress import ip_network

from labfoundry.app.models import FirewallRule, FirewallSettings


FIREWALL_DIRECTIONS = ["input", "forward", "output"]
FIREWALL_ACTIONS = ["accept", "drop", "reject"]
FIREWALL_PROTOCOLS = ["any", "tcp", "udp", "icmp"]
FIREWALL_POLICIES = ["accept", "drop"]
FIREWALL_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/firewall/labfoundry.nft"


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


def validate_firewall_state(settings: FirewallSettings, rules: list[FirewallRule]) -> list[str]:
    errors = validate_firewall_settings(settings)
    seen_names: set[str] = set()
    for rule in rules:
        normalized_name = rule.name.strip().lower()
        if normalized_name in seen_names:
            errors.append(f"Firewall rule {rule.name} is duplicated.")
        seen_names.add(normalized_name)
        errors.extend(validate_firewall_rule(rule))
    return errors


def render_nftables_config(settings: FirewallSettings, rules: list[FirewallRule]) -> str:
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
        if chain_name == "input":
            lines.append('    ip saddr 192.168.49.0/24 tcp dport { 22, 443, 8000 } accept comment "LabFoundry management access"')
        if settings.allow_icmp:
            lines.append('    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"')
            lines.append('    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"')
        for rule in sorted([item for item in rules if item.enabled and item.direction == chain_name], key=lambda item: item.priority):
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

from ipaddress import ip_address, ip_network

from labfoundry.app.models import Route, WanPolicy


WAN_CONFIG_PATH = "/etc/labfoundry/network/labfoundry-wan.conf"
WAN_MODES = ["interface", "route"]


def wan_policy_to_dict(policy: WanPolicy) -> dict:
    return {
        "id": policy.id,
        "name": policy.name,
        "description": policy.description or "",
        "enabled": policy.enabled,
        "latency_ms": policy.latency_ms,
        "jitter_ms": policy.jitter_ms,
        "packet_loss_percent": policy.packet_loss_percent,
        "bandwidth_mbit": policy.bandwidth_mbit or "",
        "corrupt_percent": policy.corrupt_percent or 0.0,
        "duplicate_percent": policy.duplicate_percent or 0.0,
        "reorder_percent": policy.reorder_percent or 0.0,
    }


def route_to_dict(route: Route) -> dict:
    return {
        "id": route.id,
        "destination_cidr": route.destination_cidr,
        "gateway": route.gateway or "",
        "interface_name": route.interface_name,
        "metric": route.metric,
        "enabled": route.enabled,
        "wan_policy_id": route.wan_policy_id or "",
        "wan_policy_name": route.wan_policy.name if route.wan_policy else "",
        "wan_mode": route.wan_mode,
    }


def wan_policy_summary(policy: WanPolicy | None) -> str:
    if policy is None:
        return "none"
    parts = [f"delay {policy.latency_ms}ms"]
    if policy.jitter_ms:
        parts.append(f"{policy.jitter_ms}ms jitter")
    if policy.packet_loss_percent:
        parts.append(f"loss {policy.packet_loss_percent}%")
    if policy.bandwidth_mbit:
        parts.append(f"rate {policy.bandwidth_mbit}mbit")
    if policy.corrupt_percent:
        parts.append(f"corrupt {policy.corrupt_percent}%")
    if policy.duplicate_percent:
        parts.append(f"duplicate {policy.duplicate_percent}%")
    if policy.reorder_percent:
        parts.append(f"reorder {policy.reorder_percent}%")
    return ", ".join(parts)


def netem_args(policy: WanPolicy) -> list[str]:
    args = ["delay", f"{policy.latency_ms}ms"]
    if policy.jitter_ms:
        args.append(f"{policy.jitter_ms}ms")
    if policy.packet_loss_percent:
        args.extend(["loss", f"{policy.packet_loss_percent}%"])
    if policy.corrupt_percent:
        args.extend(["corrupt", f"{policy.corrupt_percent}%"])
    if policy.duplicate_percent:
        args.extend(["duplicate", f"{policy.duplicate_percent}%"])
    if policy.reorder_percent:
        args.extend(["reorder", f"{policy.reorder_percent}%"])
    if policy.bandwidth_mbit:
        args.extend(["rate", f"{policy.bandwidth_mbit}mbit"])
    return args


def validate_wan_state(routes: list[Route], policies: list[WanPolicy], target_names: set[str]) -> list[str]:
    errors: list[str] = []
    policy_ids = {policy.id for policy in policies}
    for route in routes:
        try:
            ip_network(route.destination_cidr, strict=False)
        except ValueError:
            errors.append(f"Route {route.destination_cidr} is not a valid destination CIDR.")
        if route.gateway:
            try:
                ip_address(route.gateway)
            except ValueError:
                errors.append(f"Gateway {route.gateway} for {route.destination_cidr} is not a valid IP address.")
        if route.interface_name not in target_names:
            errors.append(f"Route {route.destination_cidr} uses {route.interface_name}, which is not an access interface or VLAN target.")
        if route.metric < 0:
            errors.append(f"Route {route.destination_cidr} has a negative metric.")
        if route.wan_policy_id and route.wan_policy_id not in policy_ids:
            errors.append(f"Route {route.destination_cidr} references a missing WAN policy.")

    for policy in policies:
        if not policy.name.strip():
            errors.append("WAN policy name is required.")
        if policy.latency_ms < 0 or policy.jitter_ms < 0:
            errors.append(f"WAN policy {policy.name} cannot have negative latency or jitter.")
        for field_name, value in [
            ("packet loss", policy.packet_loss_percent),
            ("corruption", policy.corrupt_percent or 0.0),
            ("duplication", policy.duplicate_percent or 0.0),
            ("reordering", policy.reorder_percent or 0.0),
        ]:
            if value < 0 or value > 100:
                errors.append(f"WAN policy {policy.name} has invalid {field_name} percentage.")
        if policy.bandwidth_mbit is not None and policy.bandwidth_mbit < 1:
            errors.append(f"WAN policy {policy.name} bandwidth must be at least 1 Mbps when set.")
    return errors


def render_wan_config(routes: list[Route]) -> str:
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        "# Dry-run preview of desired route and WAN simulation state.",
        "",
        "[routes]",
    ]
    for route in routes:
        status = "enabled" if route.enabled else "disabled"
        gateway = route.gateway or "direct"
        policy = route.wan_policy.name if route.wan_policy else "none"
        lines.append(
            f"route={route.destination_cidr},gateway={gateway},interface={route.interface_name},metric={route.metric},wan_policy={policy},status={status}"
        )

    lines.extend(["", "[commands]"])
    for route in routes:
        if not route.enabled:
            lines.append(f"# disabled: {route.destination_cidr}")
            continue
        command = ["ip", "route", "replace", route.destination_cidr]
        if route.gateway:
            command.extend(["via", route.gateway])
        command.extend(["dev", route.interface_name, "metric", str(route.metric)])
        lines.append(" ".join(command))
        if route.wan_policy and route.wan_policy.enabled:
            lines.append(" ".join(["tc", "qdisc", "replace", "dev", route.interface_name, "root", "netem", *netem_args(route.wan_policy)]))
        else:
            lines.append(" ".join(["tc", "qdisc", "del", "dev", route.interface_name, "root"]))
    return "\n".join(lines).strip() + "\n"

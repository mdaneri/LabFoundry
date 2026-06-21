from __future__ import annotations

from ipaddress import ip_interface

from labfoundry.app.models import PhysicalInterface, VlanInterface


INTERFACE_ROLES = ["management", "access", "wan", "unused"]
INTERFACE_MODES = ["access", "trunk", "unused"]
VLAN_ROLES = ["access", "management", "services", "storage", "wan"]


def normalize_interface_mode(mode: str | None) -> str:
    value = (mode or "unused").strip().lower()
    if value == "routed":
        return "access"
    if value in INTERFACE_MODES:
        return value
    return "unused"


def physical_interface_to_dict(interface: PhysicalInterface, vlan_count: int = 0) -> dict:
    return {
        "id": interface.id,
        "name": interface.name,
        "mac_address": interface.mac_address,
        "driver": interface.driver or "",
        "speed": interface.speed or "",
        "ip_cidr": interface.ip_cidr or "",
        "mtu": interface.mtu,
        "admin_state": interface.admin_state,
        "oper_state": interface.oper_state,
        "role": interface.role,
        "mode": normalize_interface_mode(interface.mode),
        "vlan_count": vlan_count,
    }


def vlan_interface_to_dict(vlan: VlanInterface) -> dict:
    return {
        "id": vlan.id,
        "name": vlan.name,
        "parent_interface": vlan.parent_interface,
        "vlan_id": vlan.vlan_id,
        "ip_cidr": vlan.ip_cidr or "",
        "mtu": vlan.mtu,
        "role": vlan.role,
        "enabled": vlan.enabled,
    }


def render_network_config(
    *,
    interfaces: list[PhysicalInterface],
    vlans: list[VlanInterface],
) -> str:
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        "# Dry-run preview of desired Linux network state.",
        "",
        "[physical_interfaces]",
    ]
    for interface in interfaces:
        mode = normalize_interface_mode(interface.mode)
        lines.extend(
            [
                f"interface={interface.name}",
                f"  mode={mode}",
                f"  ip_cidr={interface.ip_cidr or ''}",
                f"  admin_state={interface.admin_state}",
                f"  mtu={interface.mtu}",
            ]
        )
    lines.extend(["", "[vlan_interfaces]"])
    for vlan in vlans:
        if not vlan.enabled:
            continue
        lines.extend(
            [
                f"vlan={vlan.name}",
                f"  parent={vlan.parent_interface}",
                f"  vlan_id={vlan.vlan_id}",
                f"  ip_cidr={vlan.ip_cidr or ''}",
                f"  mtu={vlan.mtu}",
                f"  role={vlan.role}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def validate_network_state(
    *,
    interfaces: list[PhysicalInterface],
    vlans: list[VlanInterface],
) -> list[str]:
    errors: list[str] = []
    interface_names = {interface.name for interface in interfaces}
    for interface in interfaces:
        if interface.role not in INTERFACE_ROLES:
            errors.append(f"Interface {interface.name} role {interface.role} is not supported.")
        mode = normalize_interface_mode(interface.mode)
        if mode not in INTERFACE_MODES:
            errors.append(f"Interface {interface.name} link type {interface.mode} is not supported.")
        if interface.mtu < 576 or interface.mtu > 9000:
            errors.append(f"Interface {interface.name} MTU must be between 576 and 9000.")
        if interface.admin_state not in {"up", "down"}:
            errors.append(f"Interface {interface.name} admin state must be up or down.")
        if interface.ip_cidr:
            try:
                ip_interface(interface.ip_cidr)
            except ValueError:
                errors.append(f"Interface {interface.name} IP CIDR {interface.ip_cidr} is invalid.")
    interface_modes = {interface.name: normalize_interface_mode(interface.mode) for interface in interfaces}
    for vlan in vlans:
        if vlan.parent_interface not in interface_names:
            errors.append(f"VLAN {vlan.name} parent interface {vlan.parent_interface} does not exist.")
        elif interface_modes.get(vlan.parent_interface) != "trunk":
            errors.append(
                f"VLAN {vlan.name} parent {vlan.parent_interface} has {interface_modes.get(vlan.parent_interface)} link type. "
                "Tagged VLAN interfaces require a trunk parent; use the physical interface IP CIDR for access-mode networks."
            )
        if vlan.vlan_id < 1 or vlan.vlan_id > 4094:
            errors.append(f"VLAN {vlan.name} ID must be between 1 and 4094.")
        if vlan.mtu < 576 or vlan.mtu > 9000:
            errors.append(f"VLAN {vlan.name} MTU must be between 576 and 9000.")
        if vlan.role not in VLAN_ROLES:
            errors.append(f"VLAN {vlan.name} role {vlan.role} is not supported.")
        if vlan.ip_cidr:
            try:
                ip_interface(vlan.ip_cidr)
            except ValueError:
                errors.append(f"VLAN {vlan.name} IP CIDR {vlan.ip_cidr} is invalid.")
    return errors

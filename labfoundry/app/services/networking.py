from __future__ import annotations

from dataclasses import dataclass
import json
from ipaddress import ip_interface
from pathlib import Path
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from labfoundry.app.models import PhysicalInterface, Route, VlanInterface
from labfoundry.app.models import utcnow


INTERFACE_ROLES = ["management", "access", "wan", "unused"]
INTERFACE_MODES = ["access", "trunk", "unused"]
VLAN_ROLES = ["access", "management", "services", "storage", "wan"]


@dataclass(frozen=True)
class HostPhysicalInterface:
    name: str
    mac_address: str
    driver: str | None
    speed: str | None
    host_ip_cidr: str | None
    host_mtu: int | None
    host_admin_state: str
    oper_state: str


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
        "host_ip_cidr": interface.host_ip_cidr or "",
        "host_mtu": interface.host_mtu,
        "host_admin_state": interface.host_admin_state or "",
        "ip_cidr": interface.ip_cidr or "",
        "mtu": interface.mtu,
        "admin_state": interface.admin_state,
        "oper_state": interface.oper_state,
        "role": interface.role,
        "mode": normalize_interface_mode(interface.mode),
        "inventory_source": interface.inventory_source,
        "desired_state_source": interface.desired_state_source,
        "last_seen_at": interface.last_seen_at.isoformat() if interface.last_seen_at else "",
        "missing_since": interface.missing_since.isoformat() if interface.missing_since else "",
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


def trunk_parent_option(interface: PhysicalInterface) -> dict[str, str]:
    label_parts = [interface.name, interface.role, "trunk"]
    if interface.inventory_source == "host":
        label_parts.append("host NIC")
    if interface.mac_address:
        label_parts.append(interface.mac_address)
    return {
        "name": interface.name,
        "label": " - ".join(label_parts),
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _interface_driver(sysfs_interface: Path) -> str | None:
    driver_path = sysfs_interface / "device" / "driver"
    try:
        return driver_path.resolve().name
    except OSError:
        return None


def _interface_speed(sysfs_interface: Path) -> str | None:
    speed = _read_text(sysfs_interface / "speed")
    if not speed or speed.startswith("-"):
        return None
    return f"{speed} Mbps"


def _host_ip_cidr(row: dict) -> str | None:
    candidates = row.get("addr_info") or []
    for family in ("inet", "inet6"):
        for address in candidates:
            if address.get("family") != family:
                continue
            if address.get("scope") in {"host", "link"}:
                continue
            local = address.get("local")
            prefixlen = address.get("prefixlen")
            if local and prefixlen is not None:
                return f"{local}/{prefixlen}"
    return None


def parse_linux_ip_interfaces(payload: str, *, sysfs_base: Path = Path("/sys/class/net")) -> list[HostPhysicalInterface]:
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError:
        return []
    interfaces: list[HostPhysicalInterface] = []
    for row in rows:
        name = str(row.get("ifname") or "").strip()
        if not name or name == "lo" or "." in name:
            continue
        if row.get("link_type") != "ether":
            continue
        linkinfo = row.get("linkinfo") or {}
        if linkinfo.get("info_kind") == "vlan":
            continue
        mac_address = str(row.get("address") or "").strip()
        if not mac_address or mac_address == "00:00:00:00:00:00":
            continue
        sysfs_interface = sysfs_base / name
        flags = {str(flag).upper() for flag in row.get("flags") or []}
        interfaces.append(
            HostPhysicalInterface(
                name=name,
                mac_address=mac_address,
                driver=_interface_driver(sysfs_interface),
                speed=_interface_speed(sysfs_interface),
                host_ip_cidr=_host_ip_cidr(row),
                host_mtu=int(row["mtu"]) if row.get("mtu") is not None else None,
                host_admin_state="up" if "UP" in flags else "down",
                oper_state=str(row.get("operstate") or "unknown").lower(),
            )
        )
    return interfaces


def discover_host_physical_interfaces() -> list[HostPhysicalInterface]:
    try:
        completed = subprocess.run(
            ["ip", "-j", "address", "show"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return parse_linux_ip_interfaces(completed.stdout)


def reconcile_host_physical_interfaces(
    interfaces: list[PhysicalInterface],
    discovered: list[HostPhysicalInterface],
) -> list[PhysicalInterface]:
    now = utcnow()
    by_name = {interface.name: interface for interface in interfaces}
    seen_names: set[str] = set()
    for host in discovered:
        seen_names.add(host.name)
        interface = by_name.get(host.name)
        seed_desired = interface is None or interface.desired_state_source == "seed"
        if interface is None:
            interface = PhysicalInterface(
                name=host.name,
                mac_address=host.mac_address,
                role="unused",
                mode="access",
                desired_state_source="seed",
            )
            interfaces.append(interface)
            by_name[host.name] = interface
        interface.mac_address = host.mac_address
        interface.driver = host.driver
        interface.speed = host.speed
        interface.host_ip_cidr = host.host_ip_cidr
        interface.host_mtu = host.host_mtu
        interface.host_admin_state = host.host_admin_state
        interface.oper_state = host.oper_state
        interface.inventory_source = "host"
        interface.last_seen_at = now
        interface.missing_since = None
        if seed_desired:
            interface.ip_cidr = host.host_ip_cidr if interface.name == "eth0" or interface.role == "management" else None
            interface.mtu = host.host_mtu or interface.mtu
            interface.admin_state = host.host_admin_state if interface.name == "eth0" or interface.role == "management" else "down"
    for interface in interfaces:
        if interface.inventory_source == "host" and interface.name not in seen_names and interface.missing_since is None:
            interface.missing_since = now
            interface.oper_state = "missing"
    return interfaces


def sync_host_physical_interfaces(db: Session) -> tuple[list[PhysicalInterface], int]:
    discovered = discover_host_physical_interfaces()
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    reconciled = reconcile_host_physical_interfaces(interfaces, discovered)
    if discovered:
        discovered_names = {interface.name for interface in discovered}
        seed_only_missing = [
            interface
            for interface in reconciled
            if interface.name not in discovered_names
            and interface.inventory_source == "seed"
            and interface.desired_state_source == "seed"
        ]
        if seed_only_missing:
            removed_names = {interface.name for interface in seed_only_missing}
            dependent_vlans = db.execute(select(VlanInterface).where(VlanInterface.parent_interface.in_(removed_names))).scalars().all()
            removed_targets = removed_names | {vlan.name for vlan in dependent_vlans}
            dependent_routes = db.execute(select(Route).where(Route.interface_name.in_(removed_targets))).scalars().all()
            for route in dependent_routes:
                db.delete(route)
            for vlan in dependent_vlans:
                db.delete(vlan)
            for interface in seed_only_missing:
                db.delete(interface)
            reconciled = [interface for interface in reconciled if interface.name not in removed_names]
    for interface in reconciled:
        db.add(interface)
    db.commit()
    return db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all(), len(discovered)


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
                f"  role={interface.role}",
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

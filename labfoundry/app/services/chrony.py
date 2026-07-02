from __future__ import annotations

import re
from ipaddress import ip_address, ip_network

from labfoundry.app.models import ChronySettings
from labfoundry.app.services.dnsmasq import split_addresses, split_interfaces, split_servers


CHRONY_DEFAULT_HOSTNAME = "ntp.labfoundry.internal"
CHRONY_DEFAULT_UPSTREAM_SERVERS = "time1.google.com\ntime2.google.com\ntime3.google.com\ntime4.google.com"
CHRONY_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/chronyd/labfoundry-chrony.conf"
CHRONY_EFFECTIVE_CONFIG_PATH = "/etc/chrony.conf"
CHRONY_DRIFT_PATH = "/var/lib/chrony/drift"

HOSTNAME_PATTERN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def chrony_settings_to_dict(settings: ChronySettings) -> dict:
    return {
        "id": settings.id,
        "enabled": settings.enabled,
        "hostname": settings.hostname,
        "listen_interface": settings.listen_interface,
        "listen_interfaces": split_interfaces(settings.listen_interface),
        "listen_address": settings.listen_address,
        "listen_addresses": split_addresses(settings.listen_address),
        "port": settings.port,
        "upstream_servers": split_servers(settings.upstream_servers),
        "allow_clients": settings.allow_clients,
        "allow_client_entries": split_allow_clients(settings.allow_clients),
        "config_path": settings.config_path,
        "updated_at": settings.updated_at.isoformat() if settings.updated_at else "",
    }


def split_allow_clients(value: str | None) -> list[str]:
    if not value:
        return []
    entries: list[str] = []
    for item in str(value).replace(",", "\n").splitlines():
        normalized = item.strip().lower()
        if normalized and normalized not in entries:
            entries.append(normalized)
    return entries


def join_allow_clients(values: list[str]) -> str:
    entries = split_allow_clients("\n".join(values))
    return "\n".join(entries) if entries else "any"


def normalize_hostname(value: str) -> str:
    return value.strip().strip(".").lower()


def validate_chrony_state(settings: ChronySettings, available_interfaces: set[str]) -> list[str]:
    errors: list[str] = []
    hostname = normalize_hostname(settings.hostname)
    if not hostname:
        errors.append("Chrony hostname is required.")
    elif not HOSTNAME_PATTERN.fullmatch(hostname):
        errors.append("Chrony hostname must be a valid fully qualified DNS name.")
    if settings.enabled:
        listen_interfaces = split_interfaces(settings.listen_interface)
        if not listen_interfaces:
            errors.append("Chrony listen interface is required when the service is enabled.")
        for interface_name in listen_interfaces:
            if interface_name not in available_interfaces:
                errors.append(f"Chrony listen interface {interface_name} is not an available access or VLAN interface.")
        listen_addresses = split_addresses(settings.listen_address)
        if not listen_addresses:
            errors.append("Chrony listen address is required when the service is enabled.")
        for address in listen_addresses:
            try:
                ip_address(address)
            except ValueError:
                errors.append(f"Chrony listen address {address} must be a valid IPv4 or IPv6 address.")
        upstream_servers = split_servers(settings.upstream_servers)
        if not upstream_servers:
            errors.append("At least one Chrony upstream server is required.")
        for server in upstream_servers:
            try:
                ip_address(server)
                continue
            except ValueError:
                pass
            if not HOSTNAME_PATTERN.fullmatch(normalize_hostname(server)):
                errors.append(f"Chrony upstream server {server} must be a valid DNS name or IP address.")
    if settings.port != 123:
        errors.append("Chrony port must be UDP 123.")
    allow_entries = split_allow_clients(settings.allow_clients)
    if not allow_entries:
        errors.append("Chrony client allow list must include 'any' or at least one IPv4/IPv6 CIDR.")
    elif "any" in allow_entries and len(allow_entries) > 1:
        errors.append("Chrony client allow list can use 'any' only by itself.")
    else:
        for entry in allow_entries:
            if entry == "any":
                continue
            try:
                ip_network(entry, strict=False)
            except ValueError:
                errors.append(f"Chrony client allow entry {entry} must be 'any' or a valid IPv4/IPv6 CIDR.")
    if not settings.config_path.startswith("/"):
        errors.append("Chrony config path must be absolute.")
    return errors


def render_chrony_config(settings: ChronySettings) -> str:
    upstream_servers = split_servers(settings.upstream_servers)
    listen_addresses = split_addresses(settings.listen_address)
    allow_entries = split_allow_clients(settings.allow_clients) or ["any"]
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        f"# LabFoundry Chrony enabled: {str(bool(settings.enabled)).lower()}",
        f"# LabFoundry Chrony hostname: {normalize_hostname(settings.hostname)}",
        f"# LabFoundry Chrony listen interfaces: {', '.join(split_interfaces(settings.listen_interface)) or 'none'}",
        f"# LabFoundry Chrony listen addresses: {', '.join(listen_addresses) or 'none'}",
        f"# LabFoundry Chrony client allow list: {', '.join(allow_entries)}",
        f"driftfile {CHRONY_DRIFT_PATH}",
        "makestep 1.0 3",
        "rtcsync",
        "",
    ]
    for server in upstream_servers:
        lines.append(f"server {server} iburst")
    if upstream_servers:
        lines.append("")
    for address in listen_addresses:
        lines.append(f"bindaddress {address}")
    if listen_addresses:
        lines.append("")
    if "any" in allow_entries:
        lines.append("allow all")
    else:
        for entry in allow_entries:
            network = ip_network(entry, strict=False)
            lines.append(f"allow {network.with_prefixlen}")
    lines.append("")
    return "\n".join(lines)

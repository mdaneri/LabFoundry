from __future__ import annotations

import json
import re
from ipaddress import ip_address, ip_network
from pathlib import PurePosixPath

from labfoundry.app.models import ChronySettings
from labfoundry.app.services.dnsmasq import split_addresses, split_interfaces, split_servers


CHRONY_DEFAULT_HOSTNAME = "ntp.labfoundry.internal"
CHRONY_LEGACY_GOOGLE_UPSTREAM_SERVERS = "time1.google.com\ntime2.google.com\ntime3.google.com\ntime4.google.com"
CHRONY_DEFAULT_UPSTREAM_SOURCE_ROWS: list[dict[str, object]] = [
    {
        "id": "cloudflare-nts",
        "source": "time.cloudflare.com",
        "enabled": True,
        "use_nts": True,
        "description": "Cloudflare public NTS",
        "maxdelay": "",
    },
    {
        "id": "netnod-nts",
        "source": "nts.netnod.se",
        "enabled": True,
        "use_nts": True,
        "description": "Netnod public NTS",
        "maxdelay": "",
    },
]
CHRONY_DEFAULT_UPSTREAM_SERVERS = "\n".join(str(source["source"]) for source in CHRONY_DEFAULT_UPSTREAM_SOURCE_ROWS)
CHRONY_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/chronyd/labfoundry-chrony.conf"
CHRONY_EFFECTIVE_CONFIG_PATH = "/etc/chrony.conf"
CHRONY_DRIFT_PATH = "/var/lib/chrony/drift"
CHRONY_NTS_DUMP_DIR = "/var/lib/chrony"
CHRONY_DEFAULT_NTS_KE_PORT = 4460

HOSTNAME_PATTERN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
AUTHSELECT_MODES = {"", "ignore", "require", "prefer", "mix"}


def chrony_upstream_sources(settings: ChronySettings) -> list[dict[str, object]]:
    raw_sources = (settings.upstream_sources_json or "").strip()
    sources: list[dict[str, object]] = []
    if raw_sources:
        try:
            parsed = json.loads(raw_sources)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            for index, item in enumerate(parsed, start=1):
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source") or "").strip()
                if not source:
                    continue
                sources.append(
                    {
                        "id": str(item.get("id") or f"source-{index}"),
                        "source": source,
                        "enabled": bool(item.get("enabled", True)),
                        "use_nts": bool(item.get("use_nts", False)),
                        "description": str(item.get("description") or "").strip(),
                        "maxdelay": str(item.get("maxdelay") or "").strip(),
                    }
                )
    if sources:
        return sources
    return [
        {
            "id": f"legacy-{index}",
            "source": server,
            "enabled": True,
            "use_nts": False,
            "description": "",
            "maxdelay": "",
        }
        for index, server in enumerate(split_servers(settings.upstream_servers), start=1)
    ]


def dump_chrony_upstream_sources(sources: list[dict[str, object]]) -> str:
    normalized: list[dict[str, object]] = []
    for index, item in enumerate(sources, start=1):
        source = str(item.get("source") or "").strip()
        if not source:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or f"source-{index}"),
                "source": source,
                "enabled": bool(item.get("enabled", True)),
                "use_nts": bool(item.get("use_nts", False)),
                "description": str(item.get("description") or "").strip(),
                "maxdelay": str(item.get("maxdelay") or "").strip(),
            }
        )
    return json.dumps(normalized, separators=(",", ":"), sort_keys=True)


CHRONY_DEFAULT_UPSTREAM_SOURCES_JSON = dump_chrony_upstream_sources(CHRONY_DEFAULT_UPSTREAM_SOURCE_ROWS)


def default_chrony_upstream_fields(raw_servers: str | None = None) -> dict[str, str]:
    servers = split_servers(raw_servers or "")
    legacy_servers = split_servers(CHRONY_LEGACY_GOOGLE_UPSTREAM_SERVERS)
    if not servers or servers == legacy_servers:
        return {
            "upstream_servers": CHRONY_DEFAULT_UPSTREAM_SERVERS,
            "upstream_sources_json": CHRONY_DEFAULT_UPSTREAM_SOURCES_JSON,
        }
    return {
        "upstream_servers": "\n".join(servers),
        "upstream_sources_json": "",
    }


def enabled_chrony_sources(settings: ChronySettings) -> list[dict[str, object]]:
    return [source for source in chrony_upstream_sources(settings) if bool(source.get("enabled", True))]


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
        "upstream_servers": [str(source["source"]) for source in enabled_chrony_sources(settings)],
        "upstream_sources": chrony_upstream_sources(settings),
        "allow_clients": settings.allow_clients,
        "allow_client_entries": split_allow_clients(settings.allow_clients),
        "nts_server_enabled": settings.nts_server_enabled,
        "nts_server_cert_path": settings.nts_server_cert_path,
        "nts_server_key_path": settings.nts_server_key_path,
        "nts_ke_port": settings.nts_ke_port,
        "command_port_disabled": settings.command_port_disabled,
        "minsources": settings.minsources,
        "maxchange_seconds": settings.maxchange_seconds,
        "authselectmode": settings.authselectmode,
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
        upstream_sources = enabled_chrony_sources(settings)
        if not upstream_sources:
            errors.append("At least one Chrony upstream server is required.")
        for source in upstream_sources:
            server = str(source.get("source") or "").strip()
            try:
                ip_address(server)
                continue
            except ValueError:
                pass
            if not HOSTNAME_PATTERN.fullmatch(normalize_hostname(server)):
                errors.append(f"Chrony upstream server {server} must be a valid DNS name or IP address.")
            maxdelay = str(source.get("maxdelay") or "").strip()
            if maxdelay:
                try:
                    if float(maxdelay) <= 0:
                        errors.append(f"Chrony upstream {server} maxdelay must be greater than zero.")
                except ValueError:
                    errors.append(f"Chrony upstream {server} maxdelay must be a number of seconds.")
    if settings.port != 123:
        errors.append("Chrony port must be UDP 123.")
    if settings.nts_server_enabled:
        if not settings.nts_server_cert_path.strip():
            errors.append("Chrony NTS server certificate path is required when NTS server mode is enabled.")
        if not settings.nts_server_key_path.strip():
            errors.append("Chrony NTS server key path is required when NTS server mode is enabled.")
        for label, raw_path in {"certificate": settings.nts_server_cert_path, "key": settings.nts_server_key_path}.items():
            path = raw_path.strip()
            if path and not PurePosixPath(path).is_absolute():
                errors.append(f"Chrony NTS server {label} path must be absolute.")
    if settings.nts_ke_port != CHRONY_DEFAULT_NTS_KE_PORT:
        errors.append("Chrony NTS-KE port must be TCP 4460.")
    if settings.minsources is not None and settings.minsources < 1:
        errors.append("Chrony minsources must be at least 1.")
    if settings.maxchange_seconds is not None and settings.maxchange_seconds < 1:
        errors.append("Chrony maxchange must be at least 1 second.")
    if (settings.authselectmode or "").strip() not in AUTHSELECT_MODES:
        errors.append("Chrony authselectmode must be blank, ignore, require, prefer, or mix.")
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
    upstream_sources = enabled_chrony_sources(settings)
    listen_addresses = split_addresses(settings.listen_address)
    allow_entries = split_allow_clients(settings.allow_clients) or ["any"]
    has_nts = settings.nts_server_enabled or any(bool(source.get("use_nts")) for source in upstream_sources)
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
    ]
    if has_nts:
        lines.append(f"ntsdumpdir {CHRONY_NTS_DUMP_DIR}")
    if settings.command_port_disabled:
        lines.append("cmdport 0")
    if settings.minsources is not None:
        lines.append(f"minsources {settings.minsources}")
    if settings.maxchange_seconds is not None:
        lines.append(f"maxchange {settings.maxchange_seconds} 1 1")
    if settings.authselectmode:
        lines.append(f"authselectmode {settings.authselectmode}")
    if settings.nts_server_enabled:
        lines.append(f"ntsservercert {settings.nts_server_cert_path.strip()}")
        lines.append(f"ntsserverkey {settings.nts_server_key_path.strip()}")
        if settings.nts_ke_port != CHRONY_DEFAULT_NTS_KE_PORT:
            lines.append(f"ntsport {settings.nts_ke_port}")
    lines.append("")
    for source in upstream_sources:
        server = str(source.get("source") or "").strip()
        parts = ["server", server, "iburst"]
        if source.get("use_nts"):
            parts.append("nts")
        maxdelay = str(source.get("maxdelay") or "").strip()
        if maxdelay:
            parts.extend(["maxdelay", maxdelay])
        lines.append(" ".join(parts))
    if upstream_sources:
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

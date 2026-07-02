#!/usr/bin/env python3
"""Apply LabFoundry VMware OVF deployment properties on first boot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Interface, ip_address
import xml.etree.ElementTree as ET


PROPERTY_PREFIX = "labfoundry."
PROPERTY_CIDR = f"{PROPERTY_PREFIX}cidr"
PROPERTY_GATEWAY = f"{PROPERTY_PREFIX}gateway"
PROPERTY_FQDN = f"{PROPERTY_PREFIX}fqdn"
PROPERTY_DNS = f"{PROPERTY_PREFIX}dns_servers"
PROPERTY_NTP = f"{PROPERTY_PREFIX}ntp_servers"
PROPERTY_ADMIN_PASSWORD = f"{PROPERTY_PREFIX}admin_password"
PROPERTY_ROOT_PASSWORD = f"{PROPERTY_PREFIX}root_password"
REQUIRED_PROPERTIES = {
    PROPERTY_CIDR,
    PROPERTY_GATEWAY,
    PROPERTY_FQDN,
    PROPERTY_DNS,
    PROPERTY_ADMIN_PASSWORD,
    PROPERTY_ROOT_PASSWORD,
}

ENV_PATH = Path("/etc/labfoundry/labfoundry.env")
NETWORKD_PATH = Path("/etc/systemd/network/00-labfoundry-mgmt.network")
RESOLV_CONF_PATH = Path("/etc/resolv.conf")
NGINX_MANAGEMENT_PATH = Path("/etc/labfoundry/nginx/sites.d/management.conf")
FIREWALL_CONFIG_PATH = Path("/etc/labfoundry/nftables.d/labfoundry.nft")
MARKER_PATH = Path("/var/lib/labfoundry/vmware-ovf-customization.applied")
LOG_PATH = Path("/var/log/labfoundry/vmware-ovf-customize.log")
DEFAULT_INTERFACE = "eth0"
FQDN_PATTERN = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")
NTP_NAME_PATTERN = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9_.-]+(?<!-)$")


class OvfCustomizationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    line = f"{utc_now()} {message}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
    print(message)


def attr_value(element: ET.Element, local_name: str) -> str:
    for key, value in element.attrib.items():
        if key == local_name or key.endswith(f"}}{local_name}"):
            return value
    return ""


def parse_ovf_environment(xml_text: str) -> dict[str, str]:
    if not xml_text.strip():
        return {}
    root = ET.fromstring(xml_text)
    properties: dict[str, str] = {}
    for element in root.iter():
        if not element.tag.endswith("Property"):
            continue
        key = attr_value(element, "key")
        if not key.startswith(PROPERTY_PREFIX):
            continue
        properties[key] = attr_value(element, "value").strip()
    return properties


def split_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\s,;]+", value or "") if item.strip()]


def validate_fqdn(value: str) -> str:
    fqdn = value.strip().lower().rstrip(".")
    if not fqdn or "." not in fqdn or not FQDN_PATTERN.match(fqdn):
        raise OvfCustomizationError("labfoundry.fqdn must be a fully qualified DNS name")
    if fqdn.endswith(".local"):
        raise OvfCustomizationError("labfoundry.fqdn must not use .local")
    return fqdn


def validate_dns_servers(value: str) -> list[str]:
    servers = split_list(value)
    if not servers:
        raise OvfCustomizationError("labfoundry.dns_servers must include at least one DNS server")
    for server in servers:
        try:
            ip_address(server)
        except ValueError as exc:
            raise OvfCustomizationError(f"DNS server must be an IP address: {server}") from exc
    return servers


def validate_ntp_servers(value: str) -> list[str]:
    servers = split_list(value)
    for server in servers:
        try:
            ip_address(server)
        except ValueError:
            if not NTP_NAME_PATTERN.match(server):
                raise OvfCustomizationError(f"NTP server must be a DNS name or IP address: {server}") from None
    return servers


def validate_properties(properties: dict[str, str]) -> dict[str, object]:
    missing = sorted(key for key in REQUIRED_PROPERTIES if not properties.get(key))
    if missing:
        raise OvfCustomizationError(f"Missing required OVF properties: {', '.join(missing)}")

    try:
        cidr = IPv4Interface(properties[PROPERTY_CIDR].strip())
    except ValueError as exc:
        raise OvfCustomizationError("labfoundry.cidr must be an IPv4 CIDR such as 192.168.10.10/24") from exc

    try:
        gateway = IPv4Address(properties[PROPERTY_GATEWAY].strip())
    except ValueError as exc:
        raise OvfCustomizationError("labfoundry.gateway must be an IPv4 address") from exc

    fqdn = validate_fqdn(properties[PROPERTY_FQDN])
    dns_servers = validate_dns_servers(properties[PROPERTY_DNS])
    ntp_servers = validate_ntp_servers(properties.get(PROPERTY_NTP, ""))
    return {
        "cidr": str(cidr),
        "gateway": str(gateway),
        "fqdn": fqdn,
        "dns_servers": dns_servers,
        "ntp_servers": ntp_servers,
        "admin_password": properties[PROPERTY_ADMIN_PASSWORD],
        "root_password": properties[PROPERTY_ROOT_PASSWORD],
        "management_source_cidr": str(cidr.network),
    }


def redacted_summary(config: dict[str, object]) -> dict[str, object]:
    return {
        "applied_at": utc_now(),
        "cidr": config["cidr"],
        "gateway": config["gateway"],
        "fqdn": config["fqdn"],
        "dns_server_count": len(config["dns_servers"]),
        "ntp_server_count": len(config["ntp_servers"]),
        "admin_password_set": bool(config["admin_password"]),
        "root_password_set": bool(config["root_password"]),
    }


def read_ovf_environment() -> str:
    commands = [
        ["vmware-rpctool", "info-get guestinfo.ovfEnv"],
        ["vmtoolsd", "--cmd", "info-get guestinfo.ovfEnv"],
    ]
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        result = subprocess.run(command, check=False, text=True, capture_output=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return ""


def quote_env_value(value: object) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def write_env_file(path: Path, updates: dict[str, object]) -> None:
    values = read_env_file(path)
    values.update({key: str(value) for key, value in updates.items() if str(value)})
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={quote_env_value(values[key])}" for key in sorted(values)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o640)
    try:
        shutil.chown(path, user="root", group="labfoundry")
    except (LookupError, PermissionError):
        pass


def write_networkd_config(config: dict[str, object]) -> None:
    dns_lines = "\n".join(f"DNS={server}" for server in config["dns_servers"])
    content = (
        "[Match]\n"
        f"Name={DEFAULT_INTERFACE}\n\n"
        "[Network]\n"
        f"Address={config['cidr']}\n"
        f"Gateway={config['gateway']}\n"
        f"{dns_lines}\n"
    )
    NETWORKD_PATH.parent.mkdir(parents=True, exist_ok=True)
    NETWORKD_PATH.write_text(content, encoding="utf-8")
    os.chmod(NETWORKD_PATH, 0o644)


def write_resolv_conf(config: dict[str, object]) -> None:
    RESOLV_CONF_PATH.write_text("".join(f"nameserver {server}\n" for server in config["dns_servers"]), encoding="utf-8")
    os.chmod(RESOLV_CONF_PATH, 0o644)


def write_nginx_management_server_name(config: dict[str, object]) -> None:
    if not NGINX_MANAGEMENT_PATH.exists():
        return
    text = NGINX_MANAGEMENT_PATH.read_text(encoding="utf-8")
    text = re.sub(r"server_name\s+[^;]+;", f"server_name {config['fqdn']} _;", text, count=1)
    NGINX_MANAGEMENT_PATH.write_text(text, encoding="utf-8")
    os.chmod(NGINX_MANAGEMENT_PATH, 0o644)


def write_initial_firewall_config(config: dict[str, object]) -> None:
    source_cidr = str(config["management_source_cidr"])
    content = f"""# Managed by LabFoundry. Local changes may be overwritten.
# nftables firewall state for Photon OS appliance images.
flush ruleset
table inet labfoundry {{
  chain input {{
    type filter hook input priority filter; policy drop;
    iifname "lo" accept comment "LabFoundry loopback"
    ct state established,related accept comment "LabFoundry established traffic"
    ip saddr {source_cidr} tcp dport {{ 22, 80, 443 }} accept comment "LabFoundry management access"
    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"
    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"
  }}
  chain forward {{
    type filter hook forward priority filter; policy drop;
    ct state established,related accept comment "LabFoundry established traffic"
    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"
    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"
  }}
  chain output {{
    type filter hook output priority filter; policy accept;
    ct state established,related accept comment "LabFoundry established traffic"
    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"
    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"
  }}
}}
"""
    FIREWALL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIREWALL_CONFIG_PATH.write_text(content, encoding="utf-8")
    os.chmod(FIREWALL_CONFIG_PATH, 0o644)


def set_password(username: str, password: str) -> None:
    subprocess.run(["chpasswd"], input=f"{username}:{password}\n", text=True, check=True)


def set_hostname(fqdn: str) -> None:
    hostnamectl = shutil.which("hostnamectl")
    if hostnamectl:
        subprocess.run([hostnamectl, "set-hostname", fqdn], check=True)
        return
    Path("/etc/hostname").write_text(f"{fqdn}\n", encoding="utf-8")
    hostname = shutil.which("hostname")
    if hostname:
        subprocess.run([hostname, fqdn], check=True)


def apply_customization(config: dict[str, object], *, dry_run: bool = False) -> dict[str, object]:
    summary = redacted_summary(config)
    if dry_run:
        return summary

    write_networkd_config(config)
    write_resolv_conf(config)
    write_nginx_management_server_name(config)
    write_initial_firewall_config(config)
    set_hostname(str(config["fqdn"]))
    set_password("root", str(config["root_password"]))
    bootstrap_user = read_env_file(ENV_PATH).get("LABFOUNDRY_BOOTSTRAP_ADMIN_USERNAME", "admin").strip('"') or "admin"
    set_password(bootstrap_user, str(config["admin_password"]))
    write_env_file(
        ENV_PATH,
        {
            "LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD": config["admin_password"],
            "LABFOUNDRY_APPLIANCE_FQDN": config["fqdn"],
            "LABFOUNDRY_APPLIANCE_MANAGEMENT_CIDR": config["cidr"],
            "LABFOUNDRY_APPLIANCE_EXTERNAL_DNS_SERVERS": ",".join(config["dns_servers"]),
            "LABFOUNDRY_APPLIANCE_NTP_SERVERS": ",".join(config["ntp_servers"]),
            "LABFOUNDRY_MANAGEMENT_SOURCE_CIDR": config["management_source_cidr"],
        },
    )
    MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKER_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(MARKER_PATH, 0o640)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply LabFoundry VMware OVF deployment properties.")
    parser.add_argument("--ovf-env-file", default="", help="Read OVF environment XML from a file instead of VMware Tools.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the redacted summary without changing the host.")
    args = parser.parse_args(argv)

    if MARKER_PATH.exists() and not args.dry_run:
        log("VMware OVF customization already applied; leaving appliance state unchanged.")
        return 0

    xml_text = Path(args.ovf_env_file).read_text(encoding="utf-8") if args.ovf_env_file else read_ovf_environment()
    properties = parse_ovf_environment(xml_text)
    if not properties:
        log("No LabFoundry VMware OVF properties found; using image defaults.")
        return 0

    try:
        config = validate_properties(properties)
        summary = apply_customization(config, dry_run=args.dry_run)
    except (OvfCustomizationError, ET.ParseError) as exc:
        log(f"VMware OVF customization failed validation: {exc}")
        return 2
    except subprocess.CalledProcessError as exc:
        log(f"VMware OVF customization command failed: {exc.cmd} exit_code={exc.returncode}")
        return exc.returncode or 1

    log("Applied LabFoundry VMware OVF customization: " + json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

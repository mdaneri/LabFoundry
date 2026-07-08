import json
import re
import subprocess
from ipaddress import ip_address, ip_interface
from typing import Any

from labfoundry.app.models import ApplianceSettings, PhysicalInterface
from labfoundry.app.services.dnsmasq import split_servers
from labfoundry.app.services.networking import normalize_ipv4_method


APPLIANCE_SETTINGS_DEFAULT_FQDN = "labfoundry.labfoundry.internal"
APPLIANCE_SETTINGS_DEFAULT_EXTERNAL_DNS_SERVERS = "1.1.1.1\n9.9.9.9"
APPLIANCE_SETTINGS_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/appliance-settings/labfoundry-settings.json"
APPLIANCE_DNS_RECORD_DESCRIPTION = "LabFoundry app-owned appliance FQDN record."
SERVICE_DNS_TARGET_NAMING_CHOICES = ("ip", "interface")
SERVICE_DNS_TARGET_NAMING_DEFAULT = "ip"
RESOLVER_MODE_LOCAL_DNS = "local_dns"
RESOLVER_MODE_EXTERNAL = "external"
RESOLVER_MODE_DHCP = "dhcp"
MANAGEMENT_UI_PORT = 8000
MANAGEMENT_UI_PUBLIC_HTTP_PORT = 80
MANAGEMENT_UI_PUBLIC_HTTPS_PORT = 443
MANAGEMENT_UI_UPSTREAM_HOST = "127.0.0.1"

HOSTNAME_PATTERN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def appliance_settings_to_dict(settings: ApplianceSettings) -> dict[str, Any]:
    return {
        "id": settings.id,
        "fqdn": settings.fqdn,
        "management_https_enabled": settings.management_https_enabled,
        "root_ssh_enabled": settings.root_ssh_enabled,
        "service_dns_target_naming": normalize_service_dns_target_naming(settings.service_dns_target_naming),
        "external_dns_servers": split_servers(settings.external_dns_servers),
        "config_path": settings.config_path,
        "updated_at": settings.updated_at.isoformat() if settings.updated_at else "",
    }


def normalize_fqdn(value: str) -> str:
    return value.strip().strip(".").lower()


def normalize_multiline_values(value: str) -> str:
    return "\n".join(split_servers(value))


def normalize_service_dns_target_naming(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized in SERVICE_DNS_TARGET_NAMING_CHOICES:
        return normalized
    return SERVICE_DNS_TARGET_NAMING_DEFAULT


def is_app_owned_appliance_dns_record(description: str | None) -> bool:
    return APPLIANCE_DNS_RECORD_DESCRIPTION in (description or "")


def resolver_mode_for_settings(
    *,
    local_dns_enabled: bool,
    management_interface: dict[str, str],
    external_servers: list[str],
) -> str:
    if local_dns_enabled:
        return RESOLVER_MODE_LOCAL_DNS
    if not external_servers and management_interface.get("ipv4_method") == "dhcp":
        return RESOLVER_MODE_DHCP
    return RESOLVER_MODE_EXTERNAL


def parse_resolvectl_dns_servers(output: str) -> list[str]:
    servers: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        for token in re.split(r"[\s,]+", line.strip()):
            candidate = token.strip("[](),;")
            if not candidate:
                continue
            candidate = candidate.split("#", 1)[0].split("%", 1)[0]
            try:
                parsed = ip_address(candidate)
            except ValueError:
                continue
            if parsed.is_loopback:
                continue
            server = str(parsed)
            if server in seen:
                continue
            seen.add(server)
            servers.append(server)
    return servers


def observed_management_dhcp_dns_servers(interface_name: str) -> list[str]:
    if not interface_name:
        return []
    try:
        result = subprocess.run(
            ["resolvectl", "dns", interface_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_resolvectl_dns_servers(result.stdout)


def management_dhcp_dns_context(interfaces: list[PhysicalInterface]) -> tuple[dict[str, str], list[str]]:
    management = management_interface_context(interfaces)
    if management.get("ipv4_method") != "dhcp":
        return management, []
    servers = []
    seen: set[str] = set()
    for server in observed_management_dhcp_dns_servers(management.get("name", "")):
        try:
            parsed = ip_address(server)
        except ValueError:
            continue
        if parsed.is_loopback:
            continue
        normalized = str(parsed)
        if normalized in seen:
            continue
        seen.add(normalized)
        servers.append(normalized)
    return management, servers


def management_interface_context(interfaces: list[PhysicalInterface]) -> dict[str, str]:
    candidates = [interface for interface in interfaces if interface.role == "management"] + [
        interface for interface in interfaces if interface.name == "eth0"
    ]
    seen: set[str] = set()
    for interface in candidates:
        if interface.name in seen:
            continue
        seen.add(interface.name)
        candidate_cidr = interface.host_ip_cidr if normalize_ipv4_method(interface.ipv4_method) == "dhcp" else interface.ip_cidr
        if not candidate_cidr:
            continue
        try:
            parsed = ip_interface(candidate_cidr)
        except ValueError:
            continue
        return {
            "name": interface.name,
            "ip": str(parsed.ip),
            "ip_cidr": candidate_cidr,
            "ipv4_method": normalize_ipv4_method(interface.ipv4_method),
        }
    return {"name": "", "ip": "", "ip_cidr": "", "ipv4_method": "static"}


def validate_appliance_settings(
    settings: ApplianceSettings,
    *,
    local_dns_enabled: bool,
    management_interface: dict[str, str],
    dns_record_conflict: bool = False,
    ca_enabled: bool = False,
    management_https_cert_available: bool = False,
    chrony_enabled: bool = True,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    fqdn = normalize_fqdn(settings.fqdn)
    if not fqdn:
        errors.append("Appliance FQDN is required.")
    elif not HOSTNAME_PATTERN.fullmatch(fqdn):
        errors.append("Appliance FQDN must be a valid fully qualified DNS name.")

    external_servers = split_servers(settings.external_dns_servers)
    resolver_mode = resolver_mode_for_settings(
        local_dns_enabled=local_dns_enabled,
        management_interface=management_interface,
        external_servers=external_servers,
    )
    if resolver_mode == RESOLVER_MODE_EXTERNAL and not external_servers:
        errors.append("External DNS servers are required when local DNS is disabled.")
    for server in external_servers:
        try:
            ip_address(server)
        except ValueError:
            errors.append(f"External DNS server {server} must be a valid IPv4 or IPv6 address.")

    if local_dns_enabled and not management_interface.get("ip"):
        errors.append("Local DNS registration requires a management interface or eth0 with a valid IP CIDR.")
    if dns_record_conflict:
        errors.append("The appliance FQDN already has a user-owned DNS A/AAAA record. Rename it or remove that record before autosave can manage the appliance record.")
    if settings.management_https_enabled:
        if not ca_enabled:
            errors.append("Management UI HTTPS requires the local LabFoundry CA to be enabled.")
        elif not management_https_cert_available:
            errors.append("Management UI HTTPS requires an issued CA-managed appliance HTTPS certificate. Apply the CA unit first.")
    if not settings.config_path.startswith("/"):
        errors.append("Appliance settings config path must be absolute.")
    raw_target_naming = (settings.service_dns_target_naming or "").strip().lower().replace("_", "-")
    if raw_target_naming and raw_target_naming not in SERVICE_DNS_TARGET_NAMING_CHOICES:
        errors.append("Service DNS target names must be generated from either IP addresses or interface names.")
    if local_dns_enabled:
        warnings.append("Local DNS is enabled, so appliance resolver apply will point management DNS at 127.0.0.1.")
    elif resolver_mode == RESOLVER_MODE_DHCP:
        warnings.append("Management IPv4 uses DHCP and no external DNS servers are set, so appliance resolver apply will keep DHCP-provided DNS.")
    return errors, warnings


def appliance_settings_preview_payload(
    settings: ApplianceSettings,
    *,
    local_dns_enabled: bool,
    management_interface: dict[str, str],
    management_https_cert_path: str = "",
    management_https_key_path: str = "",
) -> dict[str, Any]:
    external_servers = split_servers(settings.external_dns_servers)
    resolver_mode = resolver_mode_for_settings(
        local_dns_enabled=local_dns_enabled,
        management_interface=management_interface,
        external_servers=external_servers,
    )
    resolver_servers = ["127.0.0.1"] if local_dns_enabled else external_servers
    payload = {
        "fqdn": normalize_fqdn(settings.fqdn),
        "resolver_mode": resolver_mode,
        "resolver_servers": resolver_servers,
        "local_dns_enabled": local_dns_enabled,
        "management_interface": management_interface.get("name", ""),
        "management_ip": management_interface.get("ip", ""),
        "management_ip_cidr": management_interface.get("ip_cidr", ""),
        "management_https_enabled": bool(settings.management_https_enabled),
        "root_ssh_enabled": bool(settings.root_ssh_enabled),
        "service_dns_target_naming": normalize_service_dns_target_naming(settings.service_dns_target_naming),
        "management_http_port": MANAGEMENT_UI_PORT,
        "management_public_http_port": MANAGEMENT_UI_PUBLIC_HTTP_PORT,
        "management_public_https_port": MANAGEMENT_UI_PUBLIC_HTTPS_PORT,
        "management_upstream_host": MANAGEMENT_UI_UPSTREAM_HOST,
        "management_upstream_port": MANAGEMENT_UI_PORT,
        "management_https_cert_path": management_https_cert_path if settings.management_https_enabled else "",
        "management_https_key_path": management_https_key_path if settings.management_https_enabled else "",
    }
    return payload


def render_appliance_settings_config(
    settings: ApplianceSettings,
    *,
    local_dns_enabled: bool,
    management_interface: dict[str, str],
    management_https_cert_path: str = "",
    management_https_key_path: str = "",
) -> str:
    payload = appliance_settings_preview_payload(
        settings,
        local_dns_enabled=local_dns_enabled,
        management_interface=management_interface,
        management_https_cert_path=management_https_cert_path,
        management_https_key_path=management_https_key_path,
    )
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"

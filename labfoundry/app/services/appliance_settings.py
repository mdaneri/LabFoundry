import json
import re
from ipaddress import ip_address, ip_interface
from typing import Any

from labfoundry.app.models import ApplianceSettings, PhysicalInterface
from labfoundry.app.services.dnsmasq import split_servers


APPLIANCE_SETTINGS_DEFAULT_FQDN = "labfoundry.labfoundry.internal"
APPLIANCE_SETTINGS_DEFAULT_EXTERNAL_DNS_SERVERS = "1.1.1.1\n9.9.9.9"
APPLIANCE_SETTINGS_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/appliance-settings/labfoundry-settings.json"
APPLIANCE_DNS_RECORD_DESCRIPTION = "LabFoundry app-owned appliance FQDN record."
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
        "external_dns_servers": split_servers(settings.external_dns_servers),
        "config_path": settings.config_path,
        "updated_at": settings.updated_at.isoformat() if settings.updated_at else "",
    }


def normalize_fqdn(value: str) -> str:
    return value.strip().strip(".").lower()


def normalize_multiline_values(value: str) -> str:
    return "\n".join(split_servers(value))


def is_app_owned_appliance_dns_record(description: str | None) -> bool:
    return APPLIANCE_DNS_RECORD_DESCRIPTION in (description or "")


def management_interface_context(interfaces: list[PhysicalInterface]) -> dict[str, str]:
    candidates = [interface for interface in interfaces if interface.role == "management"] + [
        interface for interface in interfaces if interface.name == "eth0"
    ]
    seen: set[str] = set()
    for interface in candidates:
        if interface.name in seen:
            continue
        seen.add(interface.name)
        if not interface.ip_cidr:
            continue
        try:
            parsed = ip_interface(interface.ip_cidr)
        except ValueError:
            continue
        return {
            "name": interface.name,
            "ip": str(parsed.ip),
            "ip_cidr": interface.ip_cidr,
        }
    return {"name": "", "ip": "", "ip_cidr": ""}


def validate_appliance_settings(
    settings: ApplianceSettings,
    *,
    local_dns_enabled: bool,
    management_interface: dict[str, str],
    dns_record_conflict: bool = False,
    ca_enabled: bool = False,
    management_https_cert_available: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    fqdn = normalize_fqdn(settings.fqdn)
    if not fqdn:
        errors.append("Appliance FQDN is required.")
    elif not HOSTNAME_PATTERN.fullmatch(fqdn):
        errors.append("Appliance FQDN must be a valid fully qualified DNS name.")

    external_servers = split_servers(settings.external_dns_servers)
    if not local_dns_enabled and not external_servers:
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
    if local_dns_enabled:
        warnings.append("Local DNS is enabled, so appliance resolver apply will point management DNS at 127.0.0.1.")
    return errors, warnings


def appliance_settings_preview_payload(
    settings: ApplianceSettings,
    *,
    local_dns_enabled: bool,
    management_interface: dict[str, str],
    management_https_cert_path: str = "",
    management_https_key_path: str = "",
) -> dict[str, Any]:
    resolver_mode = "local_dns" if local_dns_enabled else "external"
    resolver_servers = ["127.0.0.1"] if local_dns_enabled else split_servers(settings.external_dns_servers)
    return {
        "fqdn": normalize_fqdn(settings.fqdn),
        "resolver_mode": resolver_mode,
        "resolver_servers": resolver_servers,
        "local_dns_enabled": local_dns_enabled,
        "management_interface": management_interface.get("name", ""),
        "management_ip": management_interface.get("ip", ""),
        "management_ip_cidr": management_interface.get("ip_cidr", ""),
        "management_https_enabled": bool(settings.management_https_enabled),
        "root_ssh_enabled": bool(settings.root_ssh_enabled),
        "management_http_port": MANAGEMENT_UI_PORT,
        "management_public_http_port": MANAGEMENT_UI_PUBLIC_HTTP_PORT,
        "management_public_https_port": MANAGEMENT_UI_PUBLIC_HTTPS_PORT,
        "management_upstream_host": MANAGEMENT_UI_UPSTREAM_HOST,
        "management_upstream_port": MANAGEMENT_UI_PORT,
        "management_https_cert_path": management_https_cert_path if settings.management_https_enabled else "",
        "management_https_key_path": management_https_key_path if settings.management_https_enabled else "",
    }


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

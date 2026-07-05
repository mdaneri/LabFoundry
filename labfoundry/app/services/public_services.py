from __future__ import annotations

from ipaddress import ip_address
from typing import Any

from labfoundry.app.models import CaSettings, PhysicalInterface, VcfOfflineDepotSettings, VcfPrivateRegistrySettings, VlanInterface
from labfoundry.app.services.ca import CA_DEFAULT_PORTAL_HOSTNAME
from labfoundry.app.services.dnsmasq import split_addresses
from labfoundry.app.services.esxi_pxe import ESXI_PXE_DEFAULT_HOSTNAME
from labfoundry.app.services.networking import normalize_interface_role
from labfoundry.app.services.vcf_offline_depot import VCF_DEPOT_DEFAULT_HOSTNAME, VCF_DEPOT_DEFAULT_STORE_PATH, vcf_depot_endpoint
from labfoundry.app.services.vcf_private_registry import VCF_REGISTRY_DEFAULT_HOSTNAME, vcf_registry_endpoint


PUBLIC_SERVICES_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/public-services/labfoundry-public-services.conf"
PUBLIC_SERVICES_NGINX_SITE_PATH = "/etc/labfoundry/nginx/sites.d/public-services.conf"
PUBLIC_SERVICES_HTTP_PORT = 80
PUBLIC_SERVICES_UPSTREAM_HOST = "127.0.0.1"
PUBLIC_SERVICES_UPSTREAM_PORT = 8000
ESXI_PXE_HTTP_BASE = "/var/lib/labfoundry/pxe/http/esxi"


def public_service_interface_entries(interfaces: list[PhysicalInterface], vlans: list[VlanInterface]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for interface in interfaces:
        if interface.oper_state == "missing":
            continue
        entries.extend(_entries_for_target(interface.name, interface.role, interface.ip_cidr, interface.ipv6_cidr))
    for vlan in vlans:
        if not vlan.enabled:
            continue
        entries.extend(_entries_for_target(vlan.name, vlan.role, vlan.ip_cidr, vlan.ipv6_cidr))
    return entries


def public_services_for_address(
    address: str,
    *,
    ca_settings: CaSettings,
    esxi_pxe_boot: dict[str, Any] | None,
    vcf_depot_settings: VcfOfflineDepotSettings,
    vcf_registry_settings: VcfPrivateRegistrySettings,
) -> list[dict[str, Any]]:
    normalized = _normalize_address(address)
    services: list[dict[str, Any]] = []
    if ca_settings.enabled and normalized in _normalized_addresses(ca_settings.listen_address):
        services.append(
            {
                "id": "ca",
                "name": "Certificate Authority",
                "summary": "Trust material and certificate requests",
                "href": "/ca",
                "secondary_href": "",
                "secondary_label": "",
                "status": "available" if ca_settings.root_certificate_pem else "configured",
                "pill": "good" if ca_settings.root_certificate_pem else "warn",
                "dns_names": _service_dns_names(ca_settings.portal_hostname or CA_DEFAULT_PORTAL_HOSTNAME),
            }
        )
    boot = esxi_pxe_boot or {}
    if boot.get("enabled") and normalized in _normalized_addresses(str(boot.get("listen_address") or "")):
        services.append(
            {
                "id": "esxi_pxe",
                "name": "ESXi PXE",
                "summary": "HTTP boot files and Kickstart content",
                "href": "/pxe/esxi/",
                "secondary_href": "",
                "secondary_label": "",
                "status": "enabled",
                "pill": "good",
                "allow_unauthenticated_access": vcf_depot_settings.allow_unauthenticated_access,
                "dns_names": _service_dns_names(str(boot.get("hostname") or ESXI_PXE_DEFAULT_HOSTNAME)),
            }
        )
    if vcf_depot_settings.enabled and normalized in _normalized_addresses(vcf_depot_settings.listen_address):
        services.append(
            {
                "id": "vcf_offline_depot",
                "name": "VCF Offline Depot",
                "summary": "Static Broadcom depot mirror",
                "href": "/PROD/",
                "secondary_href": "",
                "secondary_label": "",
                "status": "enabled",
                "pill": "good",
                "dns_names": _service_dns_names(vcf_depot_settings.hostname or VCF_DEPOT_DEFAULT_HOSTNAME),
            }
        )
    if vcf_registry_settings.enabled and normalized in _normalized_addresses(vcf_registry_settings.listen_address):
        services.append(
            {
                "id": "vcf_private_registry",
                "name": "VCF Private Registry",
                "summary": "Canonical Harbor registry endpoint",
                "href": f"https://{vcf_registry_endpoint(vcf_registry_settings)}",
                "secondary_href": "",
                "secondary_label": "",
                "status": "link only",
                "pill": "muted",
                "dns_names": _service_dns_names(vcf_registry_settings.hostname or VCF_REGISTRY_DEFAULT_HOSTNAME),
            }
        )
    return services


def public_service_entries(
    *,
    interfaces: list[PhysicalInterface],
    vlans: list[VlanInterface],
    ca_settings: CaSettings,
    esxi_pxe_boot: dict[str, Any] | None,
    vcf_depot_settings: VcfOfflineDepotSettings,
    vcf_registry_settings: VcfPrivateRegistrySettings,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in public_service_interface_entries(interfaces, vlans):
        if entry["role"] == "management":
            continue
        entries.append(
            {
                **entry,
                "services": public_services_for_address(
                    entry["address"],
                    ca_settings=ca_settings,
                    esxi_pxe_boot=esxi_pxe_boot,
                    vcf_depot_settings=vcf_depot_settings,
                    vcf_registry_settings=vcf_registry_settings,
                ),
            }
        )
    return entries


def render_public_services_nginx_config(
    entries: list[dict[str, Any]],
    *,
    upstream_host: str = PUBLIC_SERVICES_UPSTREAM_HOST,
    upstream_port: int = PUBLIC_SERVICES_UPSTREAM_PORT,
    http_port: int = PUBLIC_SERVICES_HTTP_PORT,
    depot_store_path: str = VCF_DEPOT_DEFAULT_STORE_PATH,
    esxi_http_base: str = ESXI_PXE_HTTP_BASE,
) -> str:
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        "# IP-scoped public service front door for non-management interfaces.",
    ]
    for entry in sorted(entries, key=lambda item: (str(item.get("interface") or ""), str(item.get("address") or ""))):
        address = str(entry.get("address") or "").strip()
        service_rows = entry.get("services") or []
        services = {str(service.get("id")) for service in service_rows}
        vcf_depot_service = next((service for service in service_rows if str(service.get("id")) == "vcf_offline_depot"), {})
        vcf_depot_auth_lines = (
            []
            if bool(vcf_depot_service.get("allow_unauthenticated_access"))
            else [
                "    auth_request /_labfoundry_depot_auth;",
                "    error_page 401 = @labfoundry_depot_login;",
            ]
        )
        if not address:
            continue
        lines.extend(
            [
                "",
                "server {",
                f"  listen {_nginx_listen(address, http_port)};",
                f"  server_name {_nginx_server_name(address)};",
                "  client_max_body_size 1g;",
                "",
                *_proxy_location("= /", upstream_host, upstream_port),
                "",
                *_proxy_location("^~ /static/", upstream_host, upstream_port),
                "",
                *_proxy_location("= /favicon.ico", upstream_host, upstream_port),
                "",
                *_proxy_location("= /manifest.webmanifest", upstream_host, upstream_port),
            ]
        )
        if "ca" in services:
            lines.extend(
                [
                    "",
                    *_proxy_location("= /requests/login", upstream_host, upstream_port),
                    "",
                    *_proxy_location("= /requests/logout", upstream_host, upstream_port),
                    "",
                    *_proxy_location("/ca", upstream_host, upstream_port),
                    "",
                    *_proxy_location("/requests", upstream_host, upstream_port),
                ]
            )
        if "esxi_pxe" in services:
            lines.extend(
                [
                    "",
                    *_proxy_location("/pxe/esxi/ks/", upstream_host, upstream_port),
                    "",
                    "  location /pxe/esxi/ {",
                    f"    alias {esxi_http_base.rstrip('/')}/;",
                    "    autoindex off;",
                    "  }",
                ]
            )
        if "vcf_offline_depot" in services:
            lines.extend(
                [
                    "",
                    "  location = /PROD {",
                    "    return 301 /PROD/;",
                    "  }",
                    "",
                    *_proxy_location("= /PROD/login", upstream_host, upstream_port),
                    "",
                    *_proxy_location("= /PROD/logout", upstream_host, upstream_port),
                    "",
                    *_proxy_location("= /PROD/", upstream_host, upstream_port),
                    "",
                    *_proxy_location("~ ^/PROD/.*/$", upstream_host, upstream_port),
                    "",
                    "  location ~ ^/PROD/(?!login$|logout$)(.+[^/])$ {",
                    *vcf_depot_auth_lines,
                    f"    alias {depot_store_path.rstrip('/')}/PROD/$1;",
                    "    sendfile on;",
                    "    tcp_nopush on;",
                    "    directio 8m;",
                    "    autoindex off;",
                    "    types { }",
                    "    default_type application/octet-stream;",
                    "  }",
                ]
            )
            if vcf_depot_auth_lines:
                lines.extend(["", *_depot_auth_location(upstream_host, upstream_port), "", *_depot_login_redirect_location()])
        lines.extend(
            [
                "",
                "  location / {",
                "    return 404;",
                "  }",
                "}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _entries_for_target(name: str, role: str, *cidrs: str | None) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    normalized_role = normalize_interface_role(role)
    for cidr in cidrs:
        address = _address_from_cidr(cidr)
        if not address:
            continue
        entries.append({"interface": name, "role": normalized_role, "address": address})
    return entries


def _address_from_cidr(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(ip_address(str(value).split("/", 1)[0].strip())).lower()
    except ValueError:
        return ""


def _normalize_address(value: str) -> str:
    try:
        return str(ip_address(value.strip().strip("[]"))).lower()
    except ValueError:
        return value.strip().strip("[]").lower()


def _normalized_addresses(value: str | None) -> set[str]:
    return {_normalize_address(address) for address in split_addresses(value)}


def _service_dns_names(*values: str | None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = (value or "").strip().strip(".").lower()
        if not candidate or candidate in seen:
            continue
        names.append(candidate)
        seen.add(candidate)
    return names


def _nginx_listen(address: str, port: int) -> str:
    normalized = _normalize_address(address)
    return f"[{normalized}]:{port}" if ":" in normalized else f"{normalized}:{port}"


def _nginx_server_name(address: str) -> str:
    normalized = _normalize_address(address)
    try:
        parsed = ip_address(normalized)
    except ValueError:
        return "_"
    return f"_ {normalized}" if parsed.version == 4 else "_"


def _proxy_location(path: str, upstream_host: str, upstream_port: int, *, extra_directives: list[str] | None = None) -> list[str]:
    return [
        f"  location {path} {{",
        *(extra_directives or []),
        f"    proxy_pass http://{upstream_host}:{upstream_port};",
        "    proxy_http_version 1.1;",
        "    proxy_set_header Host $host;",
        "    proxy_set_header X-Real-IP $remote_addr;",
        "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "    proxy_set_header X-Forwarded-Proto http;",
        "  }",
    ]


def _depot_auth_location(upstream_host: str, upstream_port: int) -> list[str]:
    return [
        "  location = /_labfoundry_depot_auth {",
        "    internal;",
        f"    proxy_pass http://{upstream_host}:{upstream_port}/PROD/auth-check;",
        "    proxy_pass_request_body off;",
        '    proxy_set_header Content-Length "";',
        "    proxy_set_header Host $host;",
        "    proxy_set_header X-Real-IP $remote_addr;",
        "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "    proxy_set_header X-Forwarded-Proto http;",
        "  }",
    ]


def _depot_login_redirect_location() -> list[str]:
    return [
        "  location @labfoundry_depot_login {",
        "    return 303 /PROD/login?next=$request_uri;",
        "  }",
    ]

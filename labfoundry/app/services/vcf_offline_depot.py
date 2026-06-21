from __future__ import annotations

import re
import tarfile
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path

from labfoundry.app.models import Setting, VcfDepotDownloadProfile, VcfOfflineDepotSettings


VCF_DEPOT_DEFAULT_HOSTNAME = "depot.labfoundry.internal"
VCF_DEPOT_LEGACY_STORE_PATH = "/srv/repository"
VCF_DEPOT_DEFAULT_STORE_PATH = "/mnt/labfoundry-vcf-offline-depot"
VCF_DEPOT_DEFAULT_CONFIG_PATH = "/etc/labfoundry/nginx/sites.d/vcf-offline-depot.conf"
VCF_DEPOT_UPLOAD_DIR = Path("vcfDownloadTool")
VCF_DEPOT_ARCHIVE_PATTERN = "vcf-download-tool-*.tar.gz"
VCF_DEPOT_TOKEN_NAME_KEY = "vcf_depot_download_token_name"
VCF_DEPOT_TOKEN_VALUE_KEY = "vcf_depot_download_token_value"
VCF_DEPOT_ACTIVATION_NAME_KEY = "vcf_depot_activation_code_name"
VCF_DEPOT_ACTIVATION_VALUE_KEY = "vcf_depot_activation_code_value"
VCF_DEPOT_STAGED_TOKEN_FILE = "/etc/labfoundry/vcf-offline-depot/secrets/download-token.txt"
VCF_DEPOT_STAGED_ACTIVATION_FILE = "/etc/labfoundry/vcf-offline-depot/secrets/activation-code.txt"
VCF_DEPOT_STATUS_VALUES = {"planned", "ready", "synced", "blocked"}
VCF_DEPOT_PROFILE_TYPES = {"binaries", "metadata", "esx"}
VCF_DEPOT_SKUS = {"VCF", "VVF"}
VCF_DEPOT_BINARY_TYPES = {"INSTALL", "UPGRADE"}
VCF_DEPOT_TELEMETRY_CHOICES = {"ENABLE", "DISABLE", "NOT_PROVIDED"}
VCF_DEPOT_ESX_DISABLED_PLATFORMS = (
    "esxio-9.1-INTL",
    "armEsx-9.1-INTL",
    "embeddedEsx-8.0-INTL",
    "embeddedEsx-7.0-INTL",
    "embeddedEsx-9.0-INTL",
    "embeddedEsx-9.1-INTL",
    "esxio-8.0-INTL",
    "esxio-9.0-INTL",
    "embeddedEsx-6.7-INT",
)
VCF_DEPOT_COMPONENTS: dict[str, str] = {
    "DEPOT_SERVICE": "Software depot",
    "ESX_HOST": "VMware ESX",
    "HCX": "VCF Operations HCX",
    "NSX_T_MANAGER": "NSX Manager",
    "SDDC_MANAGER_VCF": "SDDC Manager",
    "TELEMETRY_ACCEPTOR": "Telemetry",
    "VCF_CONSUMPTION_CLI": "VCF Consumption CLI",
    "VCF_CONSUMPTION_CLI_PLUGINS": "VCF Consumption CLI Plugins",
    "VCF_FLEET_LCM": "Fleet lifecycle",
    "VCF_LICENSE_SERVER": "License server",
    "VCF_OBSERVABILITY_DATA_PLATFORM": "Observability Data Platform",
    "VCF_OPS_CLOUD_PROXY": "Cloud proxy",
    "VCF_SALT": "Salt Master",
    "VCF_SALT_RAAS": "Salt RaaS",
    "VCF_SDDC_LCM": "SDDC lifecycle",
    "VCF_SERVICE_VCD_MIGRATION_BACKEND": "VCF Service VCD Migration Backend",
    "VCFDT": "VCF Download Tool",
    "VCFMS_METRICS_STORE": "VCF Management Services Metrics Store",
    "VCENTER": "VMware vCenter",
    "VIDB": "Identity broker",
    "VMRC": "VMware Remote Console",
    "VMTOOLS": "VMware Tools",
    "VRA": "VCF Automation",
    "VRLI": "Log management",
    "VRNI": "VCF Operations for networks",
    "VRO": "VCF Operations orchestrator",
    "VROPS": "VCF Operations",
    "VRSLCM": "VCF Operations fleet management (9.0 only)",
    "VSP": "vSphere Supervisor Platform",
    "VSAN_ESA_WITNESS": "vSAN ESA Witness",
    "VSAN_FILE_SERVICES": "vSAN File Services",
    "VSAN_OSA_WITNESS": "vSAN OSA Witness",
}
VCF_DEPOT_LIFECYCLE_MANAGERS: dict[str, str] = {
    "SDDC_MANAGER_VCF": "SDDC Manager",
    "VRSLCM": "VCF Operations Fleet Management",
    "VCF_FLEET_LCM": "Fleet Lifecycle",
    "SELF": "Self-managed",
}
HOSTNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{1,251}[A-Za-z0-9]$")


@dataclass(frozen=True)
class SecretState:
    present: bool
    filename: str = ""
    updated_at: str = ""


def find_local_vcf_download_tool_archive(upload_dir: Path = VCF_DEPOT_UPLOAD_DIR) -> Path | None:
    if not upload_dir.exists():
        return None
    archives = sorted(upload_dir.glob(VCF_DEPOT_ARCHIVE_PATTERN), key=lambda path: path.stat().st_mtime, reverse=True)
    return archives[0] if archives else None


def detect_vcf_download_tool_version(archive_path: str | Path) -> str:
    path = Path(archive_path)
    if not path.exists():
        return ""
    try:
        with tarfile.open(path, "r:gz") as archive:
            version_file = archive.extractfile("conf/tool-version.txt")
            if version_file is None:
                return ""
            return version_file.read(200).decode("utf-8", errors="replace").strip()
    except (tarfile.TarError, OSError):
        return ""


def safe_archive_upload_name(filename: str) -> str:
    name = Path(filename or "").name
    if not re.fullmatch(r"vcf-download-tool-[A-Za-z0-9._-]+\.tar\.gz", name):
        raise ValueError("Upload the VCF Download Tool file named vcf-download-tool-*.tar.gz.")
    return name


def vcf_depot_endpoint(settings: VcfOfflineDepotSettings) -> str:
    port = settings.port or 443
    host = settings.hostname.strip()
    return host if port == 443 else f"{host}:{port}"


def vcf_depot_settings_to_dict(settings: VcfOfflineDepotSettings) -> dict[str, object]:
    return {
        "id": settings.id,
        "enabled": settings.enabled,
        "hostname": settings.hostname,
        "endpoint": vcf_depot_endpoint(settings),
        "listen_interface": settings.listen_interface,
        "listen_address": settings.listen_address,
        "port": settings.port,
        "server_certificate": settings.server_certificate,
        "depot_store_path": settings.depot_store_path,
        "tool_archive_path": settings.tool_archive_path,
        "tool_archive_name": Path(settings.tool_archive_path).name if settings.tool_archive_path else "",
        "tool_version": settings.tool_version,
        "telemetry_choice": settings.telemetry_choice,
        "config_path": settings.config_path,
        "updated_at": settings.updated_at.isoformat() if settings.updated_at else "",
    }


def vcf_depot_profile_to_dict(profile: VcfDepotDownloadProfile) -> dict[str, object]:
    component = profile.component or ""
    return {
        "id": profile.id,
        "name": profile.name,
        "profile_type": profile.profile_type,
        "sku": profile.sku,
        "vcf_version": profile.vcf_version,
        "binary_type": profile.binary_type,
        "automated_install": profile.automated_install,
        "upgrades_only": profile.upgrades_only,
        "component": component,
        "component_label": VCF_DEPOT_COMPONENTS.get(component, component),
        "component_version": profile.component_version,
        "disabled_platforms": profile.disabled_platforms,
        "enabled": profile.enabled,
        "status": profile.status,
        "notes": profile.notes or "",
        "created_at": profile.created_at.isoformat() if profile.created_at else "",
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else "",
    }


def setting_secret_state(name_setting: Setting | None, value_setting: Setting | None) -> SecretState:
    present = bool(value_setting and value_setting.value.strip())
    filename = name_setting.value if name_setting else ""
    updated_at = value_setting.updated_at.isoformat() if value_setting and value_setting.updated_at else ""
    return SecretState(present=present, filename=filename, updated_at=updated_at)


def render_nginx_depot_config(settings: VcfOfflineDepotSettings) -> str:
    certificate_name = settings.server_certificate or settings.hostname or VCF_DEPOT_DEFAULT_HOSTNAME
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        "# Dry-run preview of desired HTTPS endpoint for the VCF Offline Depot.",
        f"# Depot store: {settings.depot_store_path}",
        f"# VCF endpoint: https://{vcf_depot_endpoint(settings)}/",
        "",
        "server {",
        f"  listen {settings.listen_address}:{settings.port} ssl;",
        f"  server_name {settings.hostname};",
        f"  root {settings.depot_store_path};",
        "  autoindex on;",
        "  ssl_certificate /etc/labfoundry/vcf-offline-depot/certs/" + certificate_name + ".crt;",
        "  ssl_certificate_key /etc/labfoundry/vcf-offline-depot/certs/" + certificate_name + ".key;",
        "}",
    ]
    return "\n".join(lines).strip() + "\n"


def _append_optional_flag(command: list[str], flag: str, value: str | None) -> None:
    stripped = (value or "").strip()
    if stripped:
        command.append(f"{flag}={stripped}")


def split_vcf_depot_lines(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").splitlines() if item.strip()]


def vcfdt_commands_for_profile(settings: VcfOfflineDepotSettings, profile: VcfDepotDownloadProfile) -> list[list[str]]:
    if not profile.enabled:
        return []
    profile_type = (profile.profile_type or "binaries").strip() or "binaries"
    if profile_type == "metadata":
        return [
            [
                "vcf-download-tool",
                "metadata",
                "download",
                f"--depot-store={settings.depot_store_path}",
                f"--depot-download-token-file={VCF_DEPOT_STAGED_TOKEN_FILE}",
            ]
        ]
    if profile_type == "esx":
        commands: list[list[str]] = []
        disabled_platforms = split_vcf_depot_lines(profile.disabled_platforms)
        if disabled_platforms:
            configuration = ["vcf-download-tool", "esx", "configuration"]
            for platform in disabled_platforms:
                configuration.extend(["-D", platform])
            commands.append(configuration)
        commands.append(
            [
                "vcf-download-tool",
                "esx",
                "download",
                f"--depot-store={settings.depot_store_path}",
                f"--depot-download-activation-code-file={VCF_DEPOT_STAGED_ACTIVATION_FILE}",
            ]
        )
        return commands

    base = [
        f"--depot-download-token-file={VCF_DEPOT_STAGED_TOKEN_FILE}",
        f"--vcf-version={(profile.vcf_version or '9.1.0').strip()}",
        f"--sku={(profile.sku or 'VCF').strip() or 'VCF'}",
        f"--type={(profile.binary_type or 'INSTALL').strip() or 'INSTALL'}",
    ]
    if profile.automated_install:
        base.append("--automated-install")
    if profile.upgrades_only:
        base.append("--upgrades-only")
    _append_optional_flag(base, "--component", profile.component)
    _append_optional_flag(base, "--component-version", profile.component_version)
    return [
        ["vcf-download-tool", "binaries", "list", *base],
        ["vcf-download-tool", "binaries", "download", f"--depot-store={settings.depot_store_path}", *base],
    ]


def render_vcfdt_command_preview(settings: VcfOfflineDepotSettings, profiles: list[VcfDepotDownloadProfile]) -> str:
    enabled_profiles = [profile for profile in profiles if profile.enabled]
    if not enabled_profiles:
        return "# No enabled VCFDT download profiles are configured.\n"
    lines = [
        "# Dry-run preview. Token and activation-code contents are staged by the appliance helper and are not rendered here.",
        f"# VCF Download Tool: {Path(settings.tool_archive_path).name if settings.tool_archive_path else 'not uploaded'}",
        f"# VCFDT version: {settings.tool_version or 'unknown'}",
        f"# Depot store: {settings.depot_store_path}",
    ]
    for profile in enabled_profiles:
        lines.extend(["", f"# {profile.name}"])
        for command in vcfdt_commands_for_profile(settings, profile):
            lines.append(" ".join(command))
    return "\n".join(lines).strip() + "\n"


def validate_vcf_depot_state(
    settings: VcfOfflineDepotSettings,
    profiles: list[VcfDepotDownloadProfile],
    interface_names: set[str] | None = None,
    download_token_present: bool = False,
    activation_code_present: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    hostname = settings.hostname.strip()
    if not hostname or not HOSTNAME_PATTERN.match(hostname) or "." not in hostname:
        errors.append("Depot hostname must be a fully qualified DNS name.")
    if hostname.endswith(".local"):
        warnings.append("Avoid .local for VCF labs; use labfoundry.internal or another non-.local internal domain.")
    if not settings.listen_interface.strip():
        errors.append("Listen interface is required.")
    elif interface_names is not None and settings.listen_interface not in interface_names:
        errors.append(f"Listen interface {settings.listen_interface} is not configured as an access physical or VLAN interface with an IP address.")
    if settings.listen_address.strip():
        try:
            ip_address(settings.listen_address.strip())
        except ValueError:
            errors.append(f"Listen address {settings.listen_address} is not a valid IP address.")
    if settings.port < 1 or settings.port > 65535:
        errors.append("Depot HTTPS port must be between 1 and 65535.")
    for path_label, path_value in [
        ("Depot store path", settings.depot_store_path),
        ("HTTPS config path", settings.config_path),
    ]:
        if not path_value.startswith("/"):
            errors.append(f"{path_label} must be an absolute Linux path.")
    if not settings.server_certificate.strip():
        errors.append("Server certificate name is required.")
    if settings.telemetry_choice not in VCF_DEPOT_TELEMETRY_CHOICES:
        errors.append("Telemetry choice must be ENABLE, DISABLE, or NOT_PROVIDED.")
    if not settings.tool_archive_path.strip():
        errors.append("Upload the VCF Download Tool before submitting VCF Offline Depot through global appliance apply.")
    elif not Path(settings.tool_archive_path).exists():
        errors.append("The configured VCF Download Tool file is not present on disk.")
    if settings.tool_archive_path and not settings.tool_version:
        warnings.append("The VCF Download Tool version could not be detected from conf/tool-version.txt.")

    seen_names: set[str] = set()
    for profile in profiles:
        name = profile.name.strip()
        if not name:
            errors.append("Every VCFDT download profile needs a name.")
        elif name.lower() in seen_names:
            errors.append(f"VCFDT download profile {name} is duplicated.")
        seen_names.add(name.lower())
        profile_type = profile.profile_type or "binaries"
        profile_status = profile.status or "planned"
        if profile_type not in VCF_DEPOT_PROFILE_TYPES:
            errors.append(f"VCFDT download profile {name or profile.id} has unsupported type {profile_type}.")
        if profile_status not in VCF_DEPOT_STATUS_VALUES:
            errors.append(f"VCFDT download profile {name or profile.id} has unsupported status {profile_status}.")
        if not profile.enabled:
            continue
        if profile_type in {"binaries", "metadata"} and not download_token_present:
            errors.append(f"VCFDT download profile {name or profile.id} requires an uploaded download token file.")
        if profile_type == "esx" and not activation_code_present:
            errors.append(f"VCFDT ESX profile {name or profile.id} requires an uploaded activation-code file.")
        if profile_type == "esx":
            unsupported_platforms = [
                platform
                for platform in split_vcf_depot_lines(profile.disabled_platforms)
                if platform not in VCF_DEPOT_ESX_DISABLED_PLATFORMS
            ]
            for platform in unsupported_platforms:
                errors.append(f"VCFDT ESX profile {name or profile.id} has unsupported disabled platform {platform}.")
        if profile_type == "binaries":
            if (profile.sku or "VCF") not in VCF_DEPOT_SKUS:
                errors.append(f"VCFDT binaries profile {name or profile.id} must use SKU VCF or VVF.")
            if (profile.binary_type or "INSTALL") not in VCF_DEPOT_BINARY_TYPES:
                errors.append(f"VCFDT binaries profile {name or profile.id} must use type INSTALL or UPGRADE.")
            if not (profile.vcf_version or "").strip():
                errors.append(f"VCFDT binaries profile {name or profile.id} needs a VCF version.")
            component = (profile.component or "").strip()
            if component and component not in VCF_DEPOT_COMPONENTS:
                errors.append(f"VCFDT binaries profile {name or profile.id} has unsupported component {component}.")

    return errors, warnings

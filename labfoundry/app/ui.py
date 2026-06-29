import difflib
import hashlib
import json
import logging
import re
import shlex
import shutil
import socket
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_interface, ip_network
from pathlib import Path, PurePosixPath
from secrets import token_urlsafe
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from labfoundry.app.audit import record_audit
from labfoundry.app.adapters.system import SystemAdapter
from labfoundry.app.config import get_settings
from labfoundry.app.database import SessionLocal, get_db
from labfoundry.app.models import (
    ApplianceSettings,
    ApiToken,
    AuditEvent,
    CaCertificate,
    CaProfile,
    CaSettings,
    DhcpOption,
    DhcpReservation,
    DhcpScope,
    DhcpSettings,
    DnsRecord,
    DnsSettings,
    EsxiKickstart,
    EsxiPxeHost,
    FirewallRule,
    FirewallSettings,
    Job,
    JobStatus,
    KmsClient,
    KmsKey,
    KmsSettings,
    NatRule,
    PhysicalInterface,
    Role,
    Route,
    ServiceState,
    Setting,
    User,
    VcfBackupSettings,
    VcfDepotDownloadProfile,
    VcfOfflineDepotSettings,
    VcfPrivateRegistrySettings,
    VcfRegistryBundle,
    VlanInterface,
    WanPolicy,
    utcnow,
)
from labfoundry.app.schemas import ApiTokenCreate, WanPolicyCreate
from labfoundry.app.services.appliance_settings import (
    APPLIANCE_DNS_RECORD_DESCRIPTION,
    APPLIANCE_SETTINGS_STAGED_CONFIG_PATH,
    appliance_settings_to_dict,
    is_app_owned_appliance_dns_record,
    management_interface_context,
    normalize_fqdn,
    normalize_multiline_values,
    render_appliance_settings_config,
    validate_appliance_settings,
)
from labfoundry.app.security import (
    Identity,
    authenticate_user,
    get_session_identity,
    require_session_identity,
)
from labfoundry.app.services.dnsmasq import (
    DNS_CONDITIONAL_FORWARDERS_SETTING_KEY,
    dhcp_bind_target_names,
    dns_domain_warnings,
    dns_reverse_records,
    dhcp_option_to_dict,
    dhcp_scope_to_dict,
    dnsmasq_tag,
    join_conditional_forwarders,
    join_addresses,
    join_domains,
    join_interfaces,
    join_servers,
    parse_hosts_records,
    parse_dnsmasq_leases,
    parse_zone_records,
    render_hosts_records,
    render_zone_file,
    render_zone_hosts_records,
    render_dnsmasq_config,
    reservation_dns_record,
    split_addresses,
    split_conditional_forwarders,
    split_domains,
    split_interfaces,
    split_servers,
    validate_dns_record,
    validate_dhcp_bind_targets,
    validate_dhcp_settings,
    validate_dns_listen_targets,
    validate_dns_settings,
)
from labfoundry.app.services.ca import (
    CA_CLIENT_PROFILE_NAME,
    CA_SERVER_PROFILE_NAME,
    CA_STAGED_CONFIG_PATH,
    ManagedCertificateSpec,
    ca_certificate_to_dict,
    ca_profile_to_dict,
    ensure_ca_issued_state,
    ensure_aware,
    ensure_default_ca_profiles,
    ensure_managed_certificate_rows,
    ensure_root_ca_material,
    join_multiline,
    render_ca_apply_payload,
    render_ca_config,
    safe_certificate_name,
    split_multiline,
    validate_ca_state,
)
from labfoundry.app.secrets import decrypt_secret, secret_key_status
from labfoundry.app.services.networking import (
    INTERFACE_MODES,
    INTERFACE_ROLES,
    VLAN_ROLES,
    normalize_interface_mode,
    physical_interface_to_dict,
    render_network_config,
    sync_host_physical_interfaces,
    trunk_parent_option,
    validate_network_state,
    vlan_interface_to_dict,
)
from labfoundry.app.services.routes_wan import (
    WAN_CONFIG_PATH,
    WAN_MODES,
    nat_rule_to_dict,
    render_wan_config,
    route_to_dict,
    validate_nat_source,
    validate_wan_state,
    wan_policy_to_dict,
)
from labfoundry.app.services.settings_archive import (
    archive_summary,
    desired_state_counts,
    export_settings_archive,
    factory_reset_desired_state,
    restore_settings_archive,
)
from labfoundry.app.services.local_users import (
    LOCAL_USERS_PASSWORD_POLICY_KEY,
    LOCAL_USERS_STAGED_CONFIG_PATH,
    DEFAULT_PASSWORD_POLICY,
    DEFAULT_LOCAL_USER_SHELL,
    LOCAL_USER_SHELLS,
    clear_pending_os_password,
    has_pending_os_password,
    is_valid_user_shell,
    local_user_sync_rows,
    mark_local_users_applied,
    mark_local_users_failed,
    normalize_user_shell,
    password_policy_from_json,
    pending_os_password_count,
    password_policy_summary,
    password_policy_to_json,
    rename_pending_os_password,
    render_local_users_apply_config,
    render_local_users_preview,
    stage_user_os_password,
    validate_local_usernames,
    validate_password,
)
from labfoundry.app.services.firewall import (
    FIREWALL_ACTIONS,
    FIREWALL_DIRECTIONS,
    FIREWALL_POLICIES,
    FIREWALL_PROTOCOLS,
    FIREWALL_ANY_SOURCE_GROUP_ID,
    FIREWALL_SOURCE_GROUP_REFERENCE_PREFIX,
    FIREWALL_SOURCE_GROUPS_SETTING_KEY,
    FIREWALL_STAGED_CONFIG_PATH,
    LABFOUNDRY_DHCP_FIREWALL_RULE_MARKER,
    firewall_interface_networks,
    firewall_rule_to_dict,
    firewall_settings_to_dict,
    firewall_source_group_state,
    is_labfoundry_managed_firewall_rule,
    managed_service_firewall_rules,
    render_nftables_config,
    validate_firewall_source_groups,
    validate_firewall_rule,
    validate_firewall_state,
)
from labfoundry.app.services.kms import (
    KMS_BACKENDS,
    KMS_CLIENT_ROLES,
    KMS_DEFAULT_CONFIG_PATH,
    KMS_DEFAULT_DATABASE_PATH,
    KMS_DNS_RECORD_DESCRIPTION,
    KMS_KEY_ALGORITHMS,
    KMS_KEY_STATES,
    KMS_STAGED_CONFIG_PATH,
    join_csv,
    kms_client_to_dict,
    kms_key_to_dict,
    render_kms_config,
    split_csv,
    validate_kms_state,
)
from labfoundry.app.services.vcf_backups import (
    VCF_BACKUP_DEFAULT_VOLUME_MOUNT,
    VCF_BACKUP_DEFAULT_USERNAME,
    VCF_BACKUP_EFFECTIVE_CONFIG_PATH,
    VCF_BACKUP_STAGED_CONFIG_PATH,
    render_vcf_backup_config,
    validate_vcf_backup_state,
    vcf_backup_remote_directory,
    vcf_backup_settings_to_dict,
)
from labfoundry.app.services.vcf_private_registry import (
    VCF_REGISTRY_DEFAULT_CONFIG_PATH,
    VCF_REGISTRY_DEFAULT_HOSTNAME,
    VCF_REGISTRY_DEFAULT_PROJECT,
    VCF_REGISTRY_DEFAULT_STORAGE_PATH,
    VCF_REGISTRY_UPLOADED_CA_BUNDLE_NAME_KEY,
    VCF_REGISTRY_UPLOADED_CA_BUNDLE_PATH,
    VCF_REGISTRY_UPLOADED_CA_BUNDLE_PEM_KEY,
    default_target_reference,
    render_harbor_config,
    render_imgpkg_relocation_preview,
    validate_vcf_registry_state,
    vcf_registry_bundle_to_dict,
    vcf_registry_endpoint,
    vcf_registry_settings_to_dict,
)
from labfoundry.app.services.vcf_offline_depot import (
    VCF_DEPOT_ACTIVATION_NAME_KEY,
    VCF_DEPOT_ACTIVATION_VALUE_KEY,
    VCF_DEPOT_ARCHIVE_PATTERN,
    VCF_DEPOT_BINARY_TYPES,
    VCF_DEPOT_COMPONENTS,
    VCF_DEPOT_DEFAULT_CONFIG_PATH,
    VCF_DEPOT_DEFAULT_HOSTNAME,
    VCF_DEPOT_DEFAULT_STORE_PATH,
    VCF_DEPOT_ESX_DISABLED_PLATFORMS,
    VCF_DEPOT_EXTRACT_DIR,
    VCF_DEPOT_LEGACY_STORE_PATH,
    VCF_DEPOT_PROFILE_TYPES,
    VCF_DEPOT_RUNTIME_TOOL_DIR,
    VCF_DEPOT_SKUS,
    VCF_DEPOT_STAGED_ACTIVATION_FILE,
    VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY,
    VCF_DEPOT_SOFTWARE_DEPOT_ID_GENERATED_AT_KEY,
    VCF_DEPOT_SOFTWARE_DEPOT_ID_KEY,
    VCF_DEPOT_STAGED_CONFIG_PATH,
    VCF_DEPOT_STAGED_TOKEN_FILE,
    VCF_DEPOT_STAGED_TOOL_DIR,
    VCF_DEPOT_TELEMETRY_CHOICES,
    VCF_DEPOT_TOKEN_NAME_KEY,
    VCF_DEPOT_TOKEN_VALUE_KEY,
    VCF_DEPOT_UPLOAD_DIR,
    detect_vcf_download_tool_version,
    find_local_vcf_download_tool_archive,
    generate_vcf_software_depot_id,
    render_nginx_depot_config,
    render_vcfdt_command_preview,
    safe_archive_upload_name,
    setting_secret_state,
    validate_vcf_depot_state,
    vcf_depot_endpoint,
    vcf_depot_profile_to_dict,
    vcf_depot_settings_to_dict,
    vcfdt_commands_for_profile,
    _find_vcf_download_tool_binary,
    _safe_extract_tar_gz,
)
from labfoundry.app.services.esxi_pxe import (
    ESXI_PXE_DEFAULT_HOSTNAME,
    ESXI_PXE_DNS_RECORD_DESCRIPTION,
    ESXI_PXE_HTTP_PORT,
    ESXI_PXE_STAGED_CONFIG_PATH,
    ESXI_IPXE_HTTP_SCRIPT_PATH,
    assign_kickstart_content,
    canonical_http_path,
    content_hash,
    decode_kickstart_upload,
    esxi_pxe_boot_settings,
    esxi_pxe_host_artifacts,
    generated_kickstart_path,
    host_to_dict,
    installer_iso_inventory,
    installer_iso_root_path,
    kickstart_drift_state,
    kickstart_to_dict,
    kickstart_validation,
    mark_kickstarts_applied,
    normalize_kickstart_content,
    normalize_kickstart_name,
    normalize_installer_iso_path,
    render_esxi_pxe_manifest,
    render_esxi_pxe_preview,
    save_esxi_pxe_boot_settings,
    store_installer_iso_upload,
    strict_validation_enabled,
)
from labfoundry.app.token_service import create_token_for_user

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
VCF_DEPOT_VDT_LOG_PATH = PurePosixPath("/var/lib/labfoundry/vcfDownloadTool/active-tool/log/vdt.log")
LABFOUNDRY_APP_LOG_PATH = get_settings().app_log_path
KMS_SERVER_LOG_PATH = Path("/var/log/labfoundry/kms/server.log")
APPLY_LOGGER = logging.getLogger("labfoundry.appliance_apply")
NETWORK_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/network/labfoundry-network.conf"
DNSMASQ_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/dnsmasq/labfoundry.conf"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
router = APIRouter()


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    if not token or token != request.session.get("csrf_token"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def render(request: Request, template: str, context: dict, status_code: int = 200) -> HTMLResponse:
    context = dict(context)
    identity = context.pop("identity", None)
    if identity and "sidebar_pending_apply_count" not in context:
        if "changed_apply_unit_count" in context:
            context["sidebar_pending_apply_count"] = context["changed_apply_unit_count"]
        elif isinstance(context.get("appliance_apply_status"), dict):
            context["sidebar_pending_apply_count"] = context["appliance_apply_status"].get("sidebar_pending_apply_count", 0)
        else:
            context["sidebar_pending_apply_count"] = 0
    return templates.TemplateResponse(
        request,
        template,
        {
            "app_name": "LabFoundry",
            "identity": identity,
            "csrf_token": csrf_token(request),
            **context,
        },
        status_code=status_code,
    )


def require_admin_identity(identity: Identity) -> None:
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")


def local_user_os_statuses(users: list[User], policy: dict[str, bool | int]) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    adapter = SystemAdapter()
    if adapter.dry_run or not hasattr(adapter, "local_users_status"):
        return statuses
    try:
        config_path = stage_appliance_apply_config(LOCAL_USERS_STAGED_CONFIG_PATH, render_local_users_preview(users, password_policy=policy))
        result = adapter.local_users_status(config_path)
    except OSError:
        result = None
    if result is None or result.returncode != 0:
        return statuses
    payload: dict[str, Any] | None = None
    for raw_line in reversed((result.stdout or "").splitlines()):
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("users"), list):
            payload = parsed
            break
    if payload is None:
        return statuses
    for row in payload.get("users", []):
        if not isinstance(row, dict):
            continue
        username = str(row.get("username") or "").strip().lower()
        if username:
            statuses[username] = row
    return statuses


def user_to_dict(user: User, current_user_id: int | None = None, os_status: dict[str, Any] | None = None) -> dict:
    os_state = str((os_status or {}).get("state") or "status unavailable")
    os_detail = str((os_status or {}).get("detail") or "")
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "shell": normalize_user_shell(user.shell),
        "enabled": user.enabled,
        "created_at": user.created_at.strftime("%Y-%m-%d"),
        "os_sync_status": local_user_sync_rows([user])[0]["sync_status"],
        "os_password_pending": has_pending_os_password(user),
        "os_account_state": os_state,
        "os_account_detail": os_detail,
        "os_unlock_available": os_state in {"locked", "faillock blocked"},
        "unlock_requested": bool(user.os_unlock_requested_at),
        "is_current": user.id == current_user_id,
        "is_new": False,
    }


def local_users_password_policy(db: Session) -> dict[str, bool | int]:
    return password_policy_from_json(setting_value(db, LOCAL_USERS_PASSWORD_POLICY_KEY))


def users_context(db: Session, identity: Identity) -> dict:
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    policy = local_users_password_policy(db)
    os_statuses = local_user_os_statuses(users, policy)
    return {
        "users": users,
        "users_json": [user_to_dict(user, identity.user_id, os_statuses.get(user.username.strip().lower())) for user in users],
        "roles": [role.value for role in Role],
        "shells": LOCAL_USER_SHELLS,
        "password_policy": policy,
        "password_policy_summary": password_policy_summary(policy),
        "local_user_sync_rows": local_user_sync_rows(users),
        "local_user_os_statuses": os_statuses,
    }


def enabled_admin_count(db: Session) -> int:
    return db.execute(
        select(func.count(User.id)).where(User.role == Role.ADMIN.value, User.enabled.is_(True))
    ).scalar_one()


def protect_last_admin(db: Session, user: User, *, next_role: str | None = None, next_enabled: bool | None = None) -> None:
    role = next_role if next_role is not None else user.role
    enabled = next_enabled if next_enabled is not None else user.enabled
    if user.role == Role.ADMIN.value and user.enabled and (role != Role.ADMIN.value or not enabled) and enabled_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="At least one enabled local administrator must remain.")


def revoke_user_tokens(db: Session, user: User, actor: str) -> None:
    tokens = db.execute(
        select(ApiToken).where(ApiToken.owner_user_id == user.id, ApiToken.revoked_at.is_(None), ApiToken.enabled.is_(True))
    ).scalars().all()
    for token in tokens:
        token.enabled = False
        token.revoked_at = utcnow()
        token.revoked_by = actor
        db.add(token)


def disable_default_vcf_backup_user_when_service_off(db: Session, settings: VcfBackupSettings, *, actor: str | None = None) -> bool:
    if settings.enabled or not settings.sftp_user_id:
        return False
    user = db.get(User, settings.sftp_user_id)
    if user is None or user.username != VCF_BACKUP_DEFAULT_USERNAME or not user.enabled:
        return False
    user.enabled = False
    user.os_sync_status = "pending"
    user.os_unlock_requested_at = None
    if actor:
        revoke_user_tokens(db, user, actor)
    db.add(user)
    return True


def get_dns_settings_row(db: Session) -> DnsSettings:
    settings = db.execute(select(DnsSettings)).scalar_one_or_none()
    if settings is None:
        settings = DnsSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_appliance_settings_row(db: Session) -> ApplianceSettings:
    settings = db.execute(select(ApplianceSettings)).scalar_one_or_none()
    if settings is None:
        settings = ApplianceSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_dhcp_settings_row(db: Session) -> DhcpSettings:
    settings = db.execute(select(DhcpSettings)).scalar_one_or_none()
    if settings is None:
        settings = DhcpSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_ca_settings_row(db: Session) -> CaSettings:
    settings = db.execute(select(CaSettings)).scalar_one_or_none()
    if settings is None:
        settings = CaSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def ca_service_cert_paths(service_dir: str, certificate_name: str) -> tuple[str, str, str]:
    safe_name = safe_certificate_name(certificate_name)
    base = f"/etc/labfoundry/{service_dir}/certs/{safe_name}"
    return f"{base}.crt", f"{base}.key", f"{base}-chain.pem"


def kms_client_common_name(client: KmsClient) -> str:
    match = re.search(r"(?:^|,)CN=([^,]+)", client.certificate_subject or "")
    return match.group(1).strip() if match else client.name


def managed_ca_certificate_specs(db: Session) -> list[ManagedCertificateSpec]:
    specs: list[ManagedCertificateSpec] = []
    appliance = get_appliance_settings_row(db)
    management = appliance_settings_management_context(db)
    appliance_cert, appliance_key, appliance_chain = ca_service_cert_paths("https", appliance.fqdn)
    specs.append(
        ManagedCertificateSpec(
            owner="appliance:https",
            common_name=appliance.fqdn,
            dns_names=[appliance.fqdn],
            ip_addresses=[management["ip"]] if management.get("ip") else [],
            profile_name=CA_SERVER_PROFILE_NAME,
            description="Managed LabFoundry appliance HTTPS certificate.",
            cert_path=appliance_cert,
            key_path=appliance_key,
            chain_path=appliance_chain,
        )
    )

    kms_settings = get_kms_settings_row(db)
    if kms_settings.enabled:
        cert_path, key_path, chain_path = ca_service_cert_paths("kms", kms_settings.server_certificate or kms_settings.hostname)
        specs.append(
            ManagedCertificateSpec(
                owner="kms:server",
                common_name=kms_settings.hostname,
                dns_names=[kms_settings.hostname],
                ip_addresses=[kms_settings.listen_address] if kms_settings.listen_address else [],
                profile_name=CA_SERVER_PROFILE_NAME,
                description="Managed KMS/KMIP server TLS certificate.",
                cert_path=cert_path,
                key_path=key_path,
                chain_path=chain_path,
            )
        )
        for client in db.execute(select(KmsClient).where(KmsClient.enabled.is_(True)).order_by(KmsClient.name)).scalars().all():
            common_name = kms_client_common_name(client)
            cert_path, key_path, chain_path = ca_service_cert_paths("kms/clients", client.name)
            specs.append(
                ManagedCertificateSpec(
                    owner=f"kms:client:{client.name}",
                    common_name=common_name,
                    dns_names=[],
                    ip_addresses=[],
                    profile_name=CA_CLIENT_PROFILE_NAME,
                    description=f"Managed KMIP client certificate for {client.name}.",
                    cert_path=cert_path,
                    key_path=key_path,
                    chain_path=chain_path,
                )
            )

    depot_settings = get_vcf_offline_depot_settings_row(db)
    if depot_settings.enabled:
        cert_path, key_path, chain_path = ca_service_cert_paths("vcf-offline-depot", depot_settings.server_certificate or depot_settings.hostname)
        specs.append(
            ManagedCertificateSpec(
                owner="vcf_offline_depot:https",
                common_name=depot_settings.hostname,
                dns_names=[depot_settings.hostname],
                ip_addresses=[depot_settings.listen_address] if depot_settings.listen_address else [],
                profile_name=CA_SERVER_PROFILE_NAME,
                description="Managed VCF Offline Depot HTTPS certificate.",
                cert_path=cert_path,
                key_path=key_path,
                chain_path=chain_path,
            )
        )

    registry_settings = get_vcf_private_registry_settings_row(db)
    if registry_settings.enabled:
        cert_path, key_path, chain_path = ca_service_cert_paths("harbor", registry_settings.server_certificate or registry_settings.hostname)
        specs.append(
            ManagedCertificateSpec(
                owner="vcf_private_registry:https",
                common_name=registry_settings.hostname,
                dns_names=[registry_settings.hostname],
                ip_addresses=[registry_settings.listen_address] if registry_settings.listen_address else [],
                profile_name=CA_SERVER_PROFILE_NAME,
                description="Managed VCF Private Registry HTTPS certificate.",
                cert_path=cert_path,
                key_path=key_path,
                chain_path=chain_path,
            )
        )
    return specs


def ca_certificate_available(db: Session, owner: str) -> bool:
    certificate = db.execute(select(CaCertificate).where(CaCertificate.managed_owner == owner)).scalar_one_or_none()
    return bool(certificate and certificate.status == "issued" and certificate.certificate_pem and certificate.private_key_encrypted)


def ca_managed_certificate_paths(db: Session, owner: str) -> tuple[str, str, str]:
    certificate = db.execute(select(CaCertificate).where(CaCertificate.managed_owner == owner)).scalar_one_or_none()
    if certificate is None or certificate.status != "issued":
        return "", "", ""
    return certificate.cert_path or "", certificate.key_path or "", certificate.chain_path or ""


def ensure_ca_state(db: Session) -> list[str]:
    settings = get_ca_settings_row(db)
    errors: list[str] = []
    try:
        changed = ensure_default_ca_profiles(db)
        profiles = db.execute(select(CaProfile).order_by(CaProfile.name)).scalars().all()
        changed = ensure_root_ca_material(settings) or changed
        changed = ensure_managed_certificate_rows(db, settings=settings, profiles=profiles, specs=managed_ca_certificate_specs(db)) or changed
        certificates = (
            db.execute(select(CaCertificate).options(selectinload(CaCertificate.profile)).order_by(CaCertificate.common_name))
            .scalars()
            .all()
        )
        changed = ensure_ca_issued_state(db, settings=settings, profiles=profiles, certificates=certificates) or changed
        if changed:
            db.commit()
    except ValueError as exc:
        db.rollback()
        errors.append(str(exc))
    return errors


def get_kms_settings_row(db: Session) -> KmsSettings:
    settings = db.execute(select(KmsSettings)).scalar_one_or_none()
    if settings is None:
        settings = KmsSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_firewall_settings_row(db: Session) -> FirewallSettings:
    settings = db.execute(select(FirewallSettings)).scalar_one_or_none()
    if settings is None:
        settings = FirewallSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_vcf_backup_settings_row(db: Session, *, reconcile_default_user: bool = True) -> VcfBackupSettings:
    settings = db.execute(select(VcfBackupSettings).options(selectinload(VcfBackupSettings.sftp_user))).scalar_one_or_none()
    if settings is None:
        first_admin = db.execute(select(User).where(User.role == Role.ADMIN.value, User.enabled.is_(True)).order_by(User.username)).scalar_one_or_none()
        settings = VcfBackupSettings(sftp_user_id=first_admin.id if first_admin else None)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    if reconcile_default_user and disable_default_vcf_backup_user_when_service_off(db, settings):
        db.commit()
        db.refresh(settings)
    return settings


def get_vcf_private_registry_settings_row(db: Session) -> VcfPrivateRegistrySettings:
    settings = db.execute(select(VcfPrivateRegistrySettings)).scalar_one_or_none()
    if settings is None:
        settings = VcfPrivateRegistrySettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_vcf_offline_depot_settings_row(db: Session) -> VcfOfflineDepotSettings:
    settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one_or_none()
    if settings is None:
        settings = VcfOfflineDepotSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    if settings.depot_store_path == VCF_DEPOT_LEGACY_STORE_PATH:
        settings.depot_store_path = VCF_DEPOT_DEFAULT_STORE_PATH
        settings.updated_at = utcnow()
        db.commit()
        db.refresh(settings)
    if not settings.tool_archive_path:
        archive = find_local_vcf_download_tool_archive()
        if archive is not None:
            settings.tool_archive_path = str(archive)
            settings.tool_version = detect_vcf_download_tool_version(archive)
            settings.updated_at = utcnow()
            db.commit()
            db.refresh(settings)
    return settings


def address_from_cidr(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(ip_interface(value).ip)
    except ValueError:
        return ""


def service_bind_options(db: Session) -> list[dict[str, str]]:
    physical_interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlan_interfaces = db.execute(
        select(VlanInterface).where(VlanInterface.enabled.is_(True)).order_by(VlanInterface.parent_interface, VlanInterface.vlan_id)
    ).scalars().all()
    options: list[dict[str, str]] = []
    for interface in physical_interfaces:
        mode = normalize_interface_mode(interface.mode)
        address = address_from_cidr(interface.ip_cidr)
        if mode == "trunk" or not address:
            continue
        options.append(
            {
                "name": interface.name,
                "label": f"{interface.name} - {interface.role} / {mode} / {address}",
                "address": address,
            }
        )
    for vlan in vlan_interfaces:
        address = address_from_cidr(vlan.ip_cidr)
        if not address:
            continue
        options.append(
            {
                "name": vlan.name,
                "label": f"{vlan.name} - VLAN {vlan.vlan_id} on {vlan.parent_interface} / {vlan.role} / {address}",
                "address": address,
            }
        )
    return options


def resolve_single_service_bind(db: Session, listen_interface: str, listen_address: str) -> tuple[str, str]:
    options = service_bind_options(db)
    options_by_name = {option["name"]: option for option in options}
    selected_interface = listen_interface.strip()
    selected_address = listen_address.strip()
    if selected_address:
        address_match = next((option for option in options if option["address"] == selected_address), None)
        if address_match and (not selected_interface or selected_interface not in options_by_name or options_by_name[selected_interface]["address"] != selected_address):
            selected_interface = address_match["name"]
    if selected_interface in options_by_name:
        return selected_interface, options_by_name[selected_interface]["address"]
    return selected_interface, ""


def resolve_service_bind_targets(
    db: Session,
    listen_interfaces: list[str],
    listen_addresses: list[str],
    *,
    current_interface: str = "",
    current_address: str = "",
    listen_interfaces_present: str | None = None,
    listen_addresses_present: str | None = None,
) -> tuple[str, str]:
    options = service_bind_options(db)
    options_by_name = {option["name"]: option for option in options}
    options_by_address = {option["address"]: option for option in options if option.get("address")}

    selected_interfaces = split_interfaces(join_interfaces(listen_interfaces))
    if listen_interfaces_present is None and not selected_interfaces:
        selected_interfaces = split_interfaces(current_interface)
    selected_interfaces = [interface for interface in selected_interfaces if interface in options_by_name]

    selected_addresses = split_addresses(join_addresses(listen_addresses))
    if listen_addresses_present is None and not selected_addresses:
        selected_addresses = split_addresses(current_address)

    for address in list(selected_addresses):
        match = options_by_address.get(address)
        if match and match["name"] not in selected_interfaces:
            selected_interfaces.append(match["name"])
    derived_addresses: list[str] = []
    for interface in selected_interfaces:
        address = options_by_name[interface].get("address", "")
        if address and address not in derived_addresses:
            derived_addresses.append(address)
    selected_addresses = [*derived_addresses, *[address for address in selected_addresses if address not in derived_addresses]]

    return join_interfaces(selected_interfaces), join_addresses(selected_addresses)


def primary_listen_address(raw_address: str | None) -> str:
    addresses = split_addresses(raw_address)
    return addresses[0] if addresses else ""


def primary_listen_interface(raw_interface: str | None) -> str:
    interfaces = split_interfaces(raw_interface)
    return interfaces[0] if interfaces else ""


def service_bind_label(raw_interface: str | None, raw_address: str | None) -> str:
    interfaces = split_interfaces(raw_interface)
    addresses = split_addresses(raw_address)
    if not interfaces and not addresses:
        return "not selected"
    interface_label = ", ".join(interfaces) if interfaces else "no interface"
    address_label = ", ".join(addresses) if addresses else "no interface IP"
    return f"{interface_label} / {address_label}"


def vcf_backup_context(db: Session) -> dict:
    settings = get_vcf_backup_settings_row(db)
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    available_interfaces = service_bind_options(db)
    config_preview = render_vcf_backup_config(settings)
    validation_errors = validate_vcf_backup_state(settings, users, {interface["name"] for interface in available_interfaces})
    return {
        "vcf_backup_settings": settings,
        "vcf_backup_settings_json": vcf_backup_settings_to_dict(settings),
        "vcf_backup_users": users,
        "available_interfaces": available_interfaces,
        "selected_vcf_backup_interfaces": split_interfaces(settings.listen_interface),
        "selected_vcf_backup_addresses": split_addresses(settings.listen_address),
        "available_vcf_backup_addresses": available_service_listen_addresses(settings.listen_address, available_interfaces),
        "vcf_backup_primary_listen_address": primary_listen_address(settings.listen_address),
        "vcf_backup_bind_label": service_bind_label(settings.listen_interface, settings.listen_address),
        "vcf_backup_remote_directory": vcf_backup_remote_directory(settings),
        "vcf_backup_config_preview": config_preview,
        "vcf_backup_validation_errors": validation_errors,
        "system_adapter_dry_run": get_settings().dry_run_system_adapters,
    }


def managed_dns_fqdns(db: Session) -> set[str]:
    records = db.execute(select(DnsRecord)).scalars().all()
    names: set[str] = set()
    for record in records:
        hostname = record.hostname.strip().strip(".").lower()
        if hostname:
            names.add(hostname)
    return names


def setting_value(db: Session, key: str) -> str:
    setting = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    return setting.value if setting else ""


def set_setting_value(db: Session, key: str, value: str) -> Setting:
    setting = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if setting is None:
        setting = Setting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
        setting.updated_at = utcnow()
    db.flush()
    return setting


def appliance_settings_management_context(db: Session) -> dict[str, str]:
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    return management_interface_context(interfaces)


def appliance_dns_record_conflict(db: Session, fqdn: str) -> bool:
    normalized = normalize_fqdn(fqdn)
    if not normalized:
        return False
    records = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == normalized,
            DnsRecord.record_type.in_(["A", "AAAA"]),
        )
    ).scalars().all()
    return any(not is_app_owned_appliance_dns_record(record.description) for record in records)


def appliance_domain_from_fqdn(fqdn: str) -> str:
    normalized = normalize_fqdn(fqdn)
    parts = normalized.split(".", 1)
    return parts[1] if len(parts) == 2 else ""


def ensure_dns_domain_for_appliance_settings(dns_settings: DnsSettings, fqdn: str) -> bool:
    domain = appliance_domain_from_fqdn(fqdn)
    if not domain:
        return False
    domains = split_domains(dns_settings.domain)
    if domain in domains:
        return False
    dns_settings.domain = join_domains([domain, *domains])
    dns_settings.updated_at = utcnow()
    return True


def ensure_dns_for_appliance_settings(
    db: Session,
    settings: ApplianceSettings,
    *,
    previous_fqdn: str,
    actor: str | None,
) -> str | None:
    dns_settings = get_dns_settings_row(db)
    ensure_dns_domain_for_appliance_settings(dns_settings, settings.fqdn)
    fqdn = normalize_fqdn(settings.fqdn)
    management = appliance_settings_management_context(db)
    if not fqdn or not management["ip"]:
        return None
    try:
        parsed_address = ip_address(management["ip"])
    except ValueError:
        return None
    record_type = "AAAA" if parsed_address.version == 6 else "A"
    address = str(parsed_address)
    if validate_dns_record(fqdn, record_type, address):
        return None

    actions: list[str] = []
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == fqdn,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing and not is_app_owned_appliance_dns_record(existing.description):
        actions.append("conflict")
    elif existing:
        if existing.address != address or not existing.enabled:
            existing.address = address
            existing.enabled = True
            existing.description = APPLIANCE_DNS_RECORD_DESCRIPTION
            db.flush()
            if actor:
                record_audit(
                    db,
                    actor=actor,
                    action="update_dns_record_from_appliance_settings",
                    resource_type="dns_record",
                    resource_id=str(existing.id),
                    detail=f"{fqdn} {record_type} -> {address}",
                )
            actions.append("updated")
        else:
            actions.append("unchanged")
    else:
        record = DnsRecord(
            hostname=fqdn,
            record_type=record_type,
            address=address,
            description=APPLIANCE_DNS_RECORD_DESCRIPTION,
            enabled=True,
        )
        db.add(record)
        db.flush()
        if actor:
            record_audit(
                db,
                actor=actor,
                action="create_dns_record_from_appliance_settings",
                resource_type="dns_record",
                resource_id=str(record.id),
                detail=f"{fqdn} {record_type} -> {address}",
            )
        actions.append("created")

    stale_records = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == fqdn,
            DnsRecord.record_type.in_(["A", "AAAA"]),
            DnsRecord.record_type != record_type,
        )
    ).scalars().all()
    removed_stale = 0
    for record in stale_records:
        if not is_app_owned_appliance_dns_record(record.description):
            continue
        db.delete(record)
        removed_stale += 1
        if actor:
            record_audit(
                db,
                actor=actor,
                action="delete_dns_record_from_appliance_settings_ip_family_change",
                resource_type="dns_record",
                resource_id=str(record.id),
                detail=f"{record.hostname} {record.record_type}",
            )
    if removed_stale:
        db.flush()
        actions.append("removed-stale")

    previous = normalize_fqdn(previous_fqdn)
    if previous and previous != fqdn:
        removed = 0
        records = db.execute(
            select(DnsRecord).where(
                DnsRecord.hostname == previous,
                DnsRecord.record_type.in_(["A", "AAAA"]),
            )
        ).scalars().all()
        for record in records:
            if not is_app_owned_appliance_dns_record(record.description):
                continue
            db.delete(record)
            removed += 1
            if actor:
                record_audit(
                    db,
                    actor=actor,
                    action="delete_dns_record_from_appliance_settings_rename",
                    resource_type="dns_record",
                    resource_id=str(record.id),
                    detail=f"{record.hostname} {record.record_type}",
                )
        if removed:
            db.flush()
            actions.append("removed-old")
    return "+".join(actions) if actions else None


def appliance_settings_context(db: Session, *, reconcile_dns: bool = True) -> dict[str, Any]:
    settings = get_appliance_settings_row(db)
    dns_settings = get_dns_settings_row(db)
    if reconcile_dns and ensure_dns_for_appliance_settings(db, settings, previous_fqdn=settings.fqdn, actor=None):
        db.commit()
        db.refresh(settings)
        db.refresh(dns_settings)
    local_dns_enabled = bool(dns_settings.enabled)
    management = appliance_settings_management_context(db)
    ca_settings = get_ca_settings_row(db)
    management_https_cert_path, management_https_key_path, _management_https_chain_path = ca_managed_certificate_paths(db, "appliance:https")
    management_https_cert_available = bool(management_https_cert_path and management_https_key_path and ca_certificate_available(db, "appliance:https"))
    validation_errors, validation_warnings = validate_appliance_settings(
        settings,
        local_dns_enabled=local_dns_enabled,
        management_interface=management,
        dns_record_conflict=local_dns_enabled and appliance_dns_record_conflict(db, settings.fqdn),
        ca_enabled=bool(ca_settings.enabled),
        management_https_cert_available=management_https_cert_available,
    )
    if settings.root_ssh_enabled and get_settings().dry_run_system_adapters:
        validation_warnings.append("Root SSH is enabled as desired state, but dry-run system adapters are active. Global appliance apply will record intent without changing sshd.")
    return {
        "app_settings": get_settings(),
        "runtime_hostname": socket.gethostname(),
        "appliance_settings": settings,
        "appliance_settings_json": appliance_settings_to_dict(settings),
        "local_dns_enabled": local_dns_enabled,
        "ca_enabled": bool(ca_settings.enabled),
        "management_https_cert_available": management_https_cert_available,
        "management_https_cert_path": management_https_cert_path,
        "management_https_key_path": management_https_key_path,
        "management_interface": management,
        "appliance_settings_validation_errors": validation_errors,
        "appliance_settings_validation_warnings": validation_warnings,
        "appliance_settings_config_preview": render_appliance_settings_config(
            settings,
            local_dns_enabled=local_dns_enabled,
            management_interface=management,
            management_https_cert_path=management_https_cert_path,
            management_https_key_path=management_https_key_path,
        ),
    }


def uploaded_vcf_registry_ca_bundle(db: Session) -> dict[str, object]:
    name = setting_value(db, VCF_REGISTRY_UPLOADED_CA_BUNDLE_NAME_KEY)
    pem = setting_value(db, VCF_REGISTRY_UPLOADED_CA_BUNDLE_PEM_KEY)
    return {"name": name, "present": bool(pem.strip())}


def store_uploaded_vcf_registry_ca_bundle(db: Session, ca_bundle_file: UploadFile | None, actor: str) -> str | None:
    if ca_bundle_file is None or not ca_bundle_file.filename:
        return None
    content = ca_bundle_file.file.read()
    if not content:
        return None
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=400, detail="CA bundle upload must be 1 MB or smaller.")
    try:
        pem_text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CA bundle upload must be a PEM text file.") from exc
    if "-----BEGIN CERTIFICATE-----" not in pem_text or "-----END CERTIFICATE-----" not in pem_text:
        raise HTTPException(status_code=400, detail="CA bundle upload must contain at least one PEM certificate.")
    name_setting = set_setting_value(db, VCF_REGISTRY_UPLOADED_CA_BUNDLE_NAME_KEY, ca_bundle_file.filename)
    set_setting_value(db, VCF_REGISTRY_UPLOADED_CA_BUNDLE_PEM_KEY, pem_text)
    record_audit(
        db,
        actor=actor,
        action="upload_vcf_registry_ca_bundle",
        resource_type="setting",
        resource_id=str(name_setting.id),
        detail=ca_bundle_file.filename,
    )
    return ca_bundle_file.filename


def store_uploaded_vcf_depot_secret(
    db: Session,
    upload: UploadFile | None,
    *,
    name_key: str,
    value_key: str,
    actor: str,
    action: str,
) -> str | None:
    if upload is None or not upload.filename:
        return None
    content = upload.file.read()
    if not content:
        return None
    if len(content) > 128 * 1024:
        raise HTTPException(status_code=400, detail="VCFDT credential uploads must be 128 KB or smaller.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="VCFDT credential uploads must be text files.") from exc
    if not text.strip():
        raise HTTPException(status_code=400, detail="VCFDT credential uploads cannot be empty.")
    name_setting = set_setting_value(db, name_key, Path(upload.filename).name)
    set_setting_value(db, value_key, text)
    record_audit(
        db,
        actor=actor,
        action=action,
        resource_type="setting",
        resource_id=str(name_setting.id),
        detail=Path(upload.filename).name,
    )
    return Path(upload.filename).name


def store_pasted_vcf_depot_secret(
    db: Session,
    value: str,
    *,
    name_key: str,
    value_key: str,
    display_name: str,
    actor: str,
    action: str,
) -> str:
    if len(value.encode("utf-8")) > 128 * 1024:
        raise HTTPException(status_code=400, detail="VCFDT credential text must be 128 KB or smaller.")
    if not value.strip():
        raise HTTPException(status_code=400, detail="VCFDT credential text cannot be empty.")
    name_setting = set_setting_value(db, name_key, display_name)
    set_setting_value(db, value_key, value)
    record_audit(
        db,
        actor=actor,
        action=action,
        resource_type="setting",
        resource_id=str(name_setting.id),
        detail=display_name,
    )
    return display_name


def store_uploaded_vcf_depot_archive(settings: VcfOfflineDepotSettings, archive_file: UploadFile | None) -> str | None:
    if archive_file is None or not archive_file.filename:
        return None
    try:
        archive_name = safe_archive_upload_name(archive_file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    VCF_DEPOT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = VCF_DEPOT_UPLOAD_DIR / archive_name
    with archive_path.open("wb") as destination:
        shutil.copyfileobj(archive_file.file, destination)
    version = detect_vcf_download_tool_version(archive_path)
    settings.tool_archive_path = str(archive_path)
    settings.tool_version = version
    return archive_name


def vcf_depot_software_depot_id_context(db: Session) -> dict[str, str]:
    software_id = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_SOFTWARE_DEPOT_ID_KEY)).scalar_one_or_none()
    generated_at = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_SOFTWARE_DEPOT_ID_GENERATED_AT_KEY)).scalar_one_or_none()
    error = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY)).scalar_one_or_none()
    return {
        "id": software_id.value if software_id else "",
        "generated_at": generated_at.value if generated_at else "",
        "error": error.value if error else "",
    }


def generate_and_store_vcf_software_depot_id(db: Session, settings: VcfOfflineDepotSettings) -> dict[str, str]:
    result = generate_vcf_software_depot_id(settings.tool_archive_path)
    if result.success:
        generated_at = utcnow().isoformat()
        set_setting_value(db, VCF_DEPOT_SOFTWARE_DEPOT_ID_KEY, result.software_depot_id)
        set_setting_value(db, VCF_DEPOT_SOFTWARE_DEPOT_ID_GENERATED_AT_KEY, generated_at)
        set_setting_value(db, VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY, "")
        return {"id": result.software_depot_id, "generated_at": generated_at, "error": ""}
    error = result.error or "VCFDT software depot ID generation failed."
    set_setting_value(db, VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY, error)
    return {**vcf_depot_software_depot_id_context(db), "error": error}


def vcf_depot_secret_context(db: Session) -> dict[str, object]:
    token_name = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_NAME_KEY)).scalar_one_or_none()
    token_value = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_VALUE_KEY)).scalar_one_or_none()
    activation_name = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_ACTIVATION_NAME_KEY)).scalar_one_or_none()
    activation_value = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_ACTIVATION_VALUE_KEY)).scalar_one_or_none()
    token_state = setting_secret_state(token_name, token_value)
    activation_state = setting_secret_state(activation_name, activation_value)
    return {
        "download_token": token_state,
        "activation_code": activation_state,
        "download_token_present": token_state.present,
        "activation_code_present": activation_state.present,
    }


def vcf_depot_download_job_rows(db: Session) -> list[dict[str, str]]:
    jobs = (
        db.execute(
            select(Job)
            .where(Job.type == "vcf-depot-download")
            .order_by(desc(Job.created_at))
            .limit(5)
        )
        .scalars()
        .all()
    )
    rows: list[dict[str, str]] = []
    for job in jobs:
        profile_name = ""
        dry_run = False
        try:
            result = json.loads(job.result or "{}")
            profile_name = str(result.get("profile_name") or "")
            dry_run = bool(result.get("dry_run"))
        except json.JSONDecodeError:
            pass
        rows.append(
            {
                "id": job.id,
                "status": job.status,
                "profile_name": profile_name,
                "created_at": job.created_at.isoformat() if job.created_at else "",
                "dry_run": "yes" if dry_run else "no",
            }
        )
    return rows


def vcf_registry_ca_bundle_context(db: Session) -> dict[str, object]:
    ca_settings = get_ca_settings_row(db)
    uploaded_bundle = uploaded_vcf_registry_ca_bundle(db)
    if ca_settings.enabled:
        ensure_ca_state(db)
        path = f"{ca_settings.storage_path.rstrip('/')}/ca-bundle.pem"
        return {
            "source": "local-ca",
            "source_label": "Local CA",
            "path": path,
            "available": True,
            "uploaded_name": uploaded_bundle["name"],
        }
    return {
        "source": "uploaded",
        "source_label": "Uploaded bundle",
        "path": VCF_REGISTRY_UPLOADED_CA_BUNDLE_PATH,
        "available": uploaded_bundle["present"],
        "uploaded_name": uploaded_bundle["name"],
    }


def vcf_private_registry_context(db: Session) -> dict:
    settings = get_vcf_private_registry_settings_row(db)
    bundles = db.execute(select(VcfRegistryBundle).order_by(VcfRegistryBundle.name)).scalars().all()
    available_interfaces = service_bind_options(db)
    ca_bundle_context = vcf_registry_ca_bundle_context(db)
    settings.ca_bundle_path = str(ca_bundle_context["path"])
    validation_errors, validation_warnings = validate_vcf_registry_state(
        settings,
        bundles,
        {interface["name"] for interface in available_interfaces},
        managed_dns_fqdns(db),
        str(ca_bundle_context["source"]),
        bool(ca_bundle_context["available"]),
    )
    if settings.enabled and get_ca_settings_row(db).enabled and not ca_certificate_available(db, "vcf_private_registry:https"):
        validation_errors.append("VCF Private Registry requires an issued CA-managed HTTPS certificate before apply.")
    harbor_config_preview = render_harbor_config(settings)
    relocation_preview = render_imgpkg_relocation_preview(settings, bundles)
    return {
        "vcf_registry_settings": settings,
        "vcf_registry_settings_json": vcf_registry_settings_to_dict(settings),
        "vcf_registry_bundles": bundles,
        "vcf_registry_bundle_rows": [vcf_registry_bundle_to_dict(bundle) for bundle in bundles],
        "vcf_registry_available_interfaces": available_interfaces,
        "selected_vcf_registry_interfaces": split_interfaces(settings.listen_interface),
        "selected_vcf_registry_addresses": split_addresses(settings.listen_address),
        "available_vcf_registry_addresses": available_service_listen_addresses(settings.listen_address, available_interfaces),
        "vcf_registry_primary_listen_address": primary_listen_address(settings.listen_address),
        "vcf_registry_bind_label": service_bind_label(settings.listen_interface, settings.listen_address),
        "vcf_registry_endpoint": vcf_registry_endpoint(settings),
        "vcf_registry_harbor_config_preview": harbor_config_preview,
        "vcf_registry_relocation_preview": relocation_preview,
        "vcf_registry_validation_errors": validation_errors,
        "vcf_registry_validation_warnings": validation_warnings,
        "vcf_registry_ca_bundle_source": ca_bundle_context["source"],
        "vcf_registry_ca_bundle_source_label": ca_bundle_context["source_label"],
        "vcf_registry_ca_bundle_available": ca_bundle_context["available"],
        "vcf_registry_uploaded_ca_bundle_name": ca_bundle_context["uploaded_name"],
    }


def vcf_offline_depot_context(db: Session) -> dict:
    settings = get_vcf_offline_depot_settings_row(db)
    profiles = db.execute(select(VcfDepotDownloadProfile).order_by(VcfDepotDownloadProfile.name)).scalars().all()
    available_interfaces = service_bind_options(db)
    secrets = vcf_depot_secret_context(db)
    software_depot_id = vcf_depot_software_depot_id_context(db)
    validation_errors, validation_warnings = validate_vcf_depot_state(
        settings,
        profiles,
        {interface["name"] for interface in available_interfaces},
        bool(secrets["download_token_present"]),
        bool(secrets["activation_code_present"]),
    )
    depot_cert_path, depot_key_path, _depot_chain_path = ca_managed_certificate_paths(db, "vcf_offline_depot:https")
    if settings.enabled and get_ca_settings_row(db).enabled and not ca_certificate_available(db, "vcf_offline_depot:https"):
        validation_errors.append("VCF Offline Depot requires an issued CA-managed HTTPS certificate before apply.")
    https_config_preview = render_nginx_depot_config(settings, certificate_path=depot_cert_path, key_path=depot_key_path)
    command_preview = render_vcfdt_command_preview(settings, profiles)
    return {
        "vcf_depot_settings": settings,
        "vcf_depot_settings_json": vcf_depot_settings_to_dict(settings),
        "vcf_depot_profiles": profiles,
        "vcf_depot_profile_rows": [vcf_depot_profile_to_dict(profile) for profile in profiles],
        "vcf_depot_available_interfaces": available_interfaces,
        "selected_vcf_depot_interfaces": split_interfaces(settings.listen_interface),
        "selected_vcf_depot_addresses": split_addresses(settings.listen_address),
        "available_vcf_depot_addresses": available_service_listen_addresses(settings.listen_address, available_interfaces),
        "vcf_depot_primary_listen_address": primary_listen_address(settings.listen_address),
        "vcf_depot_bind_label": service_bind_label(settings.listen_interface, settings.listen_address),
        "vcf_depot_endpoint": vcf_depot_endpoint(settings),
        "vcf_depot_https_config_preview": https_config_preview,
        "vcf_depot_https_cert_path": depot_cert_path,
        "vcf_depot_https_key_path": depot_key_path,
        "vcf_depot_command_preview": command_preview,
        "vcf_depot_download_jobs": vcf_depot_download_job_rows(db),
        "vcf_depot_validation_errors": validation_errors,
        "vcf_depot_validation_warnings": validation_warnings,
        "vcf_depot_download_token": secrets["download_token"],
        "vcf_depot_activation_code": secrets["activation_code"],
        "vcf_depot_software_depot_id": software_depot_id,
        "vcf_depot_download_token_present": secrets["download_token_present"],
        "vcf_depot_activation_code_present": secrets["activation_code_present"],
        "vcf_depot_profile_types": sorted(VCF_DEPOT_PROFILE_TYPES),
        "vcf_depot_skus": sorted(VCF_DEPOT_SKUS),
        "vcf_depot_binary_types": sorted(VCF_DEPOT_BINARY_TYPES),
        "vcf_depot_components": [
            {"value": value, "label": f"{value} - {label}"}
            for value, label in sorted(VCF_DEPOT_COMPONENTS.items())
        ],
        "vcf_depot_esx_disabled_platforms": [
            {"value": platform, "label": platform}
            for platform in VCF_DEPOT_ESX_DISABLED_PLATFORMS
        ],
        "vcf_depot_telemetry_choices": sorted(VCF_DEPOT_TELEMETRY_CHOICES),
        "vcf_depot_archive_pattern": VCF_DEPOT_ARCHIVE_PATTERN,
    }


def vcf_depot_secret_snapshot(context: dict[str, Any]) -> str:
    token_state = context["vcf_depot_download_token"]
    activation_state = context["vcf_depot_activation_code"]
    return "\n".join(
        [
            "# VCFDT input file status",
            "# Contents are not rendered here.",
            f"# Download input file: {'staged' if token_state.present else 'not staged'}",
            f"# Download input updated: {token_state.updated_at or 'never'}",
            f"# ESX input file: {'staged' if activation_state.present else 'not staged'}",
            f"# ESX input updated: {activation_state.updated_at or 'never'}",
        ]
    )


def vcf_depot_command_entry(command: list[str], *, dry_run: bool) -> dict[str, Any]:
    resolved = [
        f"{VCF_DEPOT_RUNTIME_TOOL_DIR}/bin/vcf-download-tool" if value == "vcf-download-tool" else value
        for value in command
    ]
    return {
        "command": resolved,
        "command_line": " ".join(shlex.quote(value) for value in resolved),
        "dry_run": dry_run,
        "stdout": "dry-run: VCFDT download command recorded" if dry_run else "",
        "stderr": "",
        "returncode": 0,
    }


def vcf_depot_runtime_secret_path(staged_path: str) -> Path:
    name = Path(staged_path).name
    return VCF_DEPOT_VDT_LOG_PATH.parent.parent / "secrets" / name


def vcf_depot_runtime_command(command: list[str], tool_path: Path) -> list[str]:
    runtime_command: list[str] = []
    for arg in command:
        if arg == "vcf-download-tool":
            runtime_command.append(str(tool_path))
        elif arg == f"--depot-download-token-file={VCF_DEPOT_STAGED_TOKEN_FILE}":
            runtime_command.append(f"--depot-download-token-file={vcf_depot_runtime_secret_path(VCF_DEPOT_STAGED_TOKEN_FILE)}")
        elif arg == f"--depot-download-activation-code-file={VCF_DEPOT_STAGED_ACTIVATION_FILE}":
            runtime_command.append(f"--depot-download-activation-code-file={vcf_depot_runtime_secret_path(VCF_DEPOT_STAGED_ACTIVATION_FILE)}")
        else:
            runtime_command.append(arg)
    return runtime_command


def resolve_vcf_download_tool(settings: VcfOfflineDepotSettings) -> Path:
    archive = Path(settings.tool_archive_path)
    if VCF_DEPOT_EXTRACT_DIR.exists():
        try:
            return _find_vcf_download_tool_binary(VCF_DEPOT_EXTRACT_DIR)
        except FileNotFoundError:
            pass
    if archive.is_file():
        _safe_extract_tar_gz(archive, VCF_DEPOT_EXTRACT_DIR)
        return _find_vcf_download_tool_binary(VCF_DEPOT_EXTRACT_DIR)
    staged = Path(VCF_DEPOT_STAGED_TOOL_DIR) / "vcf-download-tool"
    if staged.is_file():
        return staged
    raise FileNotFoundError(f"VCF Download Tool archive does not exist: {archive}")


def vcf_download_tool_home(tool_path: Path) -> Path:
    return tool_path.parent.parent if tool_path.parent.name == "bin" else tool_path.parent


def write_vcf_depot_runtime_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def prepare_vcf_depot_runtime(settings: VcfOfflineDepotSettings, db: Session) -> Path:
    tool_path = resolve_vcf_download_tool(settings)
    tool_home = vcf_download_tool_home(tool_path)
    vdt_log_path = filesystem_path(VCF_DEPOT_VDT_LOG_PATH)
    vdt_log_path.parent.mkdir(parents=True, exist_ok=True)
    vdt_log_path.touch(exist_ok=True)
    token = setting_value(db, VCF_DEPOT_TOKEN_VALUE_KEY)
    if token.strip():
        write_vcf_depot_runtime_file(vcf_depot_runtime_secret_path(VCF_DEPOT_STAGED_TOKEN_FILE), token)
    activation_code = setting_value(db, VCF_DEPOT_ACTIVATION_VALUE_KEY)
    if activation_code.strip():
        write_vcf_depot_runtime_file(vcf_depot_runtime_secret_path(VCF_DEPOT_STAGED_ACTIVATION_FILE), activation_code)
    telemetry_choice = settings.telemetry_choice if settings.telemetry_choice in VCF_DEPOT_TELEMETRY_CHOICES else "DISABLE"
    if telemetry_choice != "NOT_PROVIDED":
        telemetry_file = tool_home / "conf" / "telemetry" / "telemetry.flag"
        write_vcf_depot_runtime_file(telemetry_file, f"obtu.telemetry.config={telemetry_choice}\n")
    Path(settings.depot_store_path).mkdir(parents=True, exist_ok=True)
    return tool_path


def append_vcf_depot_log(text: str) -> None:
    vdt_log_path = filesystem_path(VCF_DEPOT_VDT_LOG_PATH)
    vdt_log_path.parent.mkdir(parents=True, exist_ok=True)
    with vdt_log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def run_vcf_depot_download_job(job_id: str, profile_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        profile = db.get(VcfDepotDownloadProfile, profile_id)
        settings = get_vcf_offline_depot_settings_row(db)
        started = utcnow()
        if job:
            job.status = JobStatus.RUNNING.value
            job.started_at = started
            job.progress_percent = 5
            db.commit()
        if not job or not profile:
            return
        try:
            commands = vcfdt_commands_for_profile(settings, profile)
            tool_path = prepare_vcf_depot_runtime(settings, db)
            command_results: list[dict[str, Any]] = []
            append_vcf_depot_log(
                "\n".join(
                    [
                        "",
                        f"===== LabFoundry VCFDT job {job_id} started {started.isoformat()} =====",
                        f"profile={profile.name}",
                        f"tool={tool_path}",
                        f"depot_store={settings.depot_store_path}",
                        "",
                    ]
                )
            )
            for index, command in enumerate(commands, start=1):
                runtime_command = vcf_depot_runtime_command(command, tool_path)
                command_line = " ".join(shlex.quote(value) for value in runtime_command)
                append_vcf_depot_log(f"$ {command_line}\n")
                completed = subprocess.run(
                    runtime_command,
                    cwd=str(vcf_download_tool_home(tool_path)),
                    capture_output=True,
                    check=False,
                    text=True,
                )
                if completed.stdout:
                    append_vcf_depot_log(completed.stdout)
                if completed.stderr:
                    append_vcf_depot_log(completed.stderr)
                command_results.append(
                    {
                        "command": runtime_command,
                        "command_line": command_line,
                        "returncode": completed.returncode,
                        "stdout": apply_output_excerpt(completed.stdout),
                        "stderr": apply_output_excerpt(completed.stderr),
                    }
                )
                job.progress_percent = int(index / max(len(commands), 1) * 95)
                job.result = json.dumps({**json.loads(job.result or "{}"), "commands": command_results}, indent=2)
                db.commit()
                if completed.returncode != 0:
                    raise RuntimeError(f"VCFDT command exited with code {completed.returncode}.")
            finished = utcnow()
            profile.status = "synced"
            profile.updated_at = finished
            job.status = JobStatus.SUCCEEDED.value
            job.finished_at = finished
            job.progress_percent = 100
            job.error = None
            append_vcf_depot_log(f"===== LabFoundry VCFDT job {job_id} succeeded {finished.isoformat()} =====\n")
            db.commit()
        except Exception as exc:  # noqa: BLE001 - background worker must persist failures instead of crashing silently.
            finished = utcnow()
            profile.status = "blocked"
            profile.updated_at = finished
            job.status = JobStatus.FAILED.value
            job.finished_at = finished
            job.progress_percent = 100
            job.error = str(exc)
            append_vcf_depot_log(f"ERROR: {exc}\n")
            append_vcf_depot_log(f"===== LabFoundry VCFDT job {job_id} failed {finished.isoformat()} =====\n")
            db.commit()


def queue_vcf_depot_download_job(job_id: str, profile_id: int) -> None:
    thread = threading.Thread(target=run_vcf_depot_download_job, args=(job_id, profile_id), daemon=True)
    thread.start()


def firewall_context(db: Session) -> dict:
    settings = get_firewall_settings_row(db)
    rules = db.execute(select(FirewallRule).order_by(FirewallRule.priority, FirewallRule.name)).scalars().all()
    dns_settings = get_dns_settings_row(db)
    dhcp_settings = get_dhcp_settings_row(db)
    dhcp_scopes = db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().all()
    physical_interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlan_interfaces = db.execute(select(VlanInterface).order_by(VlanInterface.parent_interface, VlanInterface.vlan_id)).scalars().all()
    interface_networks = firewall_interface_networks(physical_interfaces, vlan_interfaces)
    source_group_state = firewall_source_group_state(setting_value(db, FIREWALL_SOURCE_GROUPS_SETTING_KEY), interface_networks)
    generated_rules = managed_service_firewall_rules(
        dns_settings=dns_settings,
        dhcp_settings=dhcp_settings,
        dhcp_scopes=dhcp_scopes,
        kms_settings=get_kms_settings_row(db),
        vcf_backup_settings=get_vcf_backup_settings_row(db),
        vcf_depot_settings=get_vcf_offline_depot_settings_row(db),
        vcf_registry_settings=get_vcf_private_registry_settings_row(db),
        esxi_pxe_boot=esxi_pxe_boot_settings(db),
        interface_networks=interface_networks,
        source_groups=source_group_state["groups"],
        source_group_assignments=source_group_state["assignments"],
    )
    config_preview = render_nftables_config(
        settings,
        rules,
        generated_rules,
        source_groups=source_group_state["groups"],
        replace_labfoundry_service_rules=True,
    )
    validation_errors = [
        *validate_firewall_source_groups(source_group_state["groups"]),
        *validate_firewall_state(
            settings,
            rules,
            generated_rules,
            source_groups=source_group_state["groups"],
            replace_labfoundry_service_rules=True,
        ),
    ]
    editable_rules = [rule for rule in rules if not is_labfoundry_managed_firewall_rule(rule)]
    replaced_rules = [rule for rule in rules if is_labfoundry_managed_firewall_rule(rule)]
    available_interfaces = service_bind_options(db)
    return {
        "firewall_settings": settings,
        "firewall_rules": editable_rules,
        "firewall_rules_json": [firewall_rule_to_dict(rule) for rule in editable_rules],
        "firewall_generated_rules": generated_rules,
        "firewall_generated_rules_json": [firewall_rule_to_dict(rule) for rule in generated_rules],
        "firewall_managed_rule_rows": managed_firewall_rule_rows(generated_rules, replaced_rules, source_group_state["groups"], source_group_state["assignments"]),
        "firewall_source_groups": source_group_state["groups"],
        "firewall_source_group_assignments": source_group_state["assignments"],
        "firewall_config_preview": config_preview,
        "firewall_validation_errors": validation_errors,
        "firewall_directions": FIREWALL_DIRECTIONS,
        "firewall_actions": FIREWALL_ACTIONS,
        "firewall_protocols": FIREWALL_PROTOCOLS,
        "firewall_policies": FIREWALL_POLICIES,
        "firewall_interface_options": available_interfaces,
    }


def managed_firewall_rule_rows(
    generated_rules: list[FirewallRule],
    replaced_rules: list[FirewallRule],
    source_groups: list[dict] | None = None,
    assignments: dict[str, str] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    replaced_by_name: dict[str, list[FirewallRule]] = {}
    source_groups_by_id = {str(group["id"]): group for group in source_groups or []}
    assignments = assignments or {}
    for rule in replaced_rules:
        replaced_by_name.setdefault(rule.name.strip().lower(), []).append(rule)
    for rule in generated_rules:
        if LABFOUNDRY_DHCP_FIREWALL_RULE_MARKER in (rule.description or ""):
            source_group_id = ""
            source_group = {"name": "DHCP bootstrap", "entries": ["interface-bound"]}
        else:
            source_group_id = assignments.get(rule.name, "any")
            if source_group_id not in source_groups_by_id:
                source_group_id = "any"
            source_group = source_groups_by_id.get(source_group_id, {})
        rows.append(
            {
                **firewall_rule_to_dict(rule),
                "id": f"generated:{rule.name}",
                "managed_state": "generated",
                "managed_status": "generated",
                "source_group_id": source_group_id,
                "source_group_name": source_group.get("name", source_group_id),
                "source_group_sources": ", ".join(source_group.get("entries") or source_group.get("sources") or []),
            }
        )
        for replaced_rule in replaced_by_name.pop(rule.name.strip().lower(), []):
            rows.append(managed_replaced_firewall_rule_row(replaced_rule))
    for matching_replaced_rules in replaced_by_name.values():
        for rule in matching_replaced_rules:
            rows.append(managed_replaced_firewall_rule_row(rule))
    return rows


def managed_replaced_firewall_rule_row(rule: FirewallRule) -> dict:
    return {
        **firewall_rule_to_dict(rule),
        "id": f"replaced:{rule.id or rule.name}",
        "managed_state": "replaced",
        "managed_status": "replaced",
        "source_group_id": "",
        "source_group_name": "",
        "source_group_sources": "",
        "enabled": False,
    }


def ca_context(db: Session) -> dict:
    state_errors = ensure_ca_state(db)
    settings = get_ca_settings_row(db)
    available_interfaces = service_bind_options(db)
    available_names = {option["name"] for option in available_interfaces}
    profiles = db.execute(select(CaProfile).order_by(CaProfile.name)).scalars().all()
    certificates = (
        db.execute(select(CaCertificate).options(selectinload(CaCertificate.profile)).order_by(CaCertificate.common_name))
        .scalars()
        .all()
    )
    config_preview = render_ca_config(settings=settings, profiles=profiles, certificates=certificates)
    apply_payload = render_ca_apply_payload(settings, certificates, include_private_keys=False)
    validation_errors = [*state_errors, *validate_ca_state(settings=settings, profiles=profiles, certificates=certificates)]
    selected_interfaces = split_interfaces(settings.listen_interface)
    invalid_interfaces = [interface for interface in selected_interfaces if interface not in available_names]
    if settings.enabled and not selected_interfaces:
        validation_errors.append("CA service requires at least one listen interface.")
    if settings.enabled and invalid_interfaces:
        validation_errors.append("CA listen interfaces must be access physical interfaces or enabled VLANs with IP addresses.")
    issued_count = len([certificate for certificate in certificates if certificate.status == "issued"])
    expiring_count = len(
        [
            certificate
            for certificate in certificates
            if certificate.status == "issued" and certificate.expires_at and ensure_aware(certificate.expires_at) <= utcnow() + timedelta(days=30)
        ]
    )
    managed_count = len([certificate for certificate in certificates if certificate.managed_owner])
    key_status = secret_key_status()
    return {
        "ca_settings": settings,
        "ca_profiles": profiles,
        "ca_profile_rows": [ca_profile_to_dict(profile) for profile in profiles],
        "ca_certificate_rows": [ca_certificate_to_dict(certificate) for certificate in certificates],
        "ca_profile_choices": [{"id": profile.id, "label": profile.name} for profile in profiles if profile.enabled],
        "available_interfaces": available_interfaces,
        "available_ca_addresses": available_service_listen_addresses(settings.listen_address, available_interfaces),
        "selected_ca_interfaces": selected_interfaces,
        "selected_ca_addresses": split_addresses(settings.listen_address),
        "ca_certificates": certificates,
        "ca_config_preview": config_preview,
        "ca_apply_payload": apply_payload,
        "ca_apply_config_path": CA_STAGED_CONFIG_PATH,
        "ca_validation_errors": validation_errors,
        "ca_status_summary": {
            "root_present": bool(settings.root_certificate_pem),
            "bundle_present": bool(settings.root_certificate_pem),
            "issued_count": issued_count,
            "expiring_count": expiring_count,
            "managed_count": managed_count,
            "secrets_key_source": key_status.source,
            "secrets_key_dedicated": key_status.dedicated,
        },
    }


def kms_context(db: Session) -> dict:
    settings = get_kms_settings_row(db)
    available_interfaces = service_bind_options(db)
    changed = False
    normalized_interfaces, normalized_addresses = resolve_service_bind_targets(
        db,
        [],
        [],
        current_interface=settings.listen_interface,
        current_address=settings.listen_address,
    )
    if normalized_interfaces != settings.listen_interface or normalized_addresses != settings.listen_address:
        settings.listen_interface = normalized_interfaces
        settings.listen_address = normalized_addresses
        changed = True
    normalized_hostname = normalize_dns_hostname(settings.hostname)
    if normalized_hostname and settings.hostname != normalized_hostname:
        settings.hostname = normalized_hostname
        changed = True
    if settings.enabled:
        dns_action = ensure_dns_for_kms(db, settings, actor=None, previous_hostname=settings.hostname)
        changed = bool(dns_action) or changed
    if changed:
        settings.updated_at = utcnow()
        db.commit()
        db.refresh(settings)
    ca_state_errors = ensure_ca_state(db)
    clients = db.execute(select(KmsClient).order_by(KmsClient.name)).scalars().all()
    keys = db.execute(select(KmsKey).options(selectinload(KmsKey.owner_client)).order_by(KmsKey.name)).scalars().all()
    config_preview = render_kms_config(settings=settings, clients=clients, keys=keys)
    validation_errors = [*ca_state_errors, *validate_kms_state(settings=settings, clients=clients, keys=keys)]
    ca_settings = get_ca_settings_row(db)
    if settings.enabled:
        invalid_interfaces = [
            interface
            for interface in split_interfaces(settings.listen_interface)
            if interface not in {option["name"] for option in available_interfaces}
        ]
        if invalid_interfaces:
            validation_errors.append("KMS listen interface must be an access physical interface or enabled VLAN with an IP address.")
        if kms_dns_record_conflict(db, settings.hostname):
            validation_errors.append("KMS hostname conflicts with an existing non-KMS DNS record.")
        if not ca_settings.enabled:
            validation_errors.append("KMS requires Certificate Authority to be enabled before activation.")
        elif ca_state_errors:
            validation_errors.append("KMS cannot be activated until Certificate Authority state is healthy.")
        elif not ca_certificate_available(db, "kms:server"):
            validation_errors.append("KMS requires an issued CA-managed server certificate before apply.")
        else:
            for client in clients:
                if client.enabled and not ca_certificate_available(db, f"kms:client:{client.name}"):
                    validation_errors.append(f"KMS client {client.name} requires an issued CA-managed client certificate before apply.")
    return {
        "kms_settings": settings,
        "kms_clients": clients,
        "kms_keys": keys,
        "kms_client_rows": [kms_client_to_dict(client) for client in clients],
        "kms_key_rows": [kms_key_to_dict(key) for key in keys],
        "kms_client_choices": [{"id": client.id, "label": client.name} for client in clients if client.enabled],
        "kms_backend_options": KMS_BACKENDS,
        "kms_client_roles": KMS_CLIENT_ROLES,
        "kms_key_algorithms": KMS_KEY_ALGORITHMS,
        "kms_key_states": KMS_KEY_STATES,
        "available_interfaces": available_interfaces,
        "selected_kms_interfaces": split_interfaces(settings.listen_interface),
        "selected_kms_addresses": split_addresses(settings.listen_address),
        "available_kms_addresses": available_service_listen_addresses(settings.listen_address, available_interfaces),
        "kms_config_preview": config_preview,
        "kms_validation_errors": validation_errors,
        "system_adapter_dry_run": get_settings().dry_run_system_adapters,
        "kms_lab_notice": (
            "PyKMIP is useful for KMIP lab and compatibility testing. Treat this backend as a lab KMS, "
            "not a production HSM or hardened enterprise key manager."
        ),
    }


def network_context(db: Session) -> dict:
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlans = db.execute(select(VlanInterface).order_by(VlanInterface.parent_interface, VlanInterface.vlan_id)).scalars().all()
    vlan_counts: dict[str, int] = {}
    for vlan in vlans:
        vlan_counts[vlan.parent_interface] = vlan_counts.get(vlan.parent_interface, 0) + 1
    config_preview = render_network_config(interfaces=interfaces, vlans=vlans)
    validation_errors = validate_network_state(interfaces=interfaces, vlans=vlans)
    trunk_interfaces = [interface for interface in interfaces if normalize_interface_mode(interface.mode) == "trunk"]
    return {
        "physical_interfaces": interfaces,
        "physical_interface_rows": [physical_interface_to_dict(interface, vlan_counts.get(interface.name, 0)) for interface in interfaces],
        "vlan_interfaces": vlans,
        "vlan_interface_rows": [vlan_interface_to_dict(vlan) for vlan in vlans],
        "interface_names": [interface.name for interface in interfaces],
        "trunk_interface_names": [interface.name for interface in trunk_interfaces],
        "trunk_parent_options": [trunk_parent_option(interface) for interface in trunk_interfaces],
        "interface_roles": INTERFACE_ROLES,
        "interface_modes": INTERFACE_MODES,
        "vlan_roles": VLAN_ROLES,
        "network_config_preview": config_preview,
        "network_validation_errors": validation_errors,
        "network_config_path": NETWORK_STAGED_CONFIG_PATH,
    }


def wan_route_targets(db: Session) -> list[dict[str, str]]:
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlans = db.execute(select(VlanInterface).order_by(VlanInterface.parent_interface, VlanInterface.vlan_id)).scalars().all()
    targets: list[dict[str, str]] = []
    for interface in interfaces:
        mode = normalize_interface_mode(interface.mode)
        if mode == "trunk" or not interface.ip_cidr:
            continue
        targets.append(
            {
                "name": interface.name,
                "kind": "physical",
                "role": interface.role,
                "ip_cidr": interface.ip_cidr or "",
                "wan": interface.role == "wan",
                "label": f"{interface.name} - physical / {interface.role} / {interface.ip_cidr}",
            }
        )
    for vlan in vlans:
        if not vlan.enabled or not vlan.ip_cidr:
            continue
        targets.append(
            {
                "name": vlan.name,
                "kind": "vlan",
                "role": vlan.role,
                "ip_cidr": vlan.ip_cidr or "",
                "wan": vlan.role == "wan",
                "label": f"{vlan.name} - VLAN {vlan.vlan_id} on {vlan.parent_interface} / {vlan.role} / {vlan.ip_cidr}",
            }
        )
    return targets


def routes_wan_context(db: Session) -> dict:
    routes = db.execute(select(Route).options(selectinload(Route.wan_policy)).order_by(Route.destination_cidr)).scalars().all()
    policies = db.execute(select(WanPolicy).order_by(WanPolicy.name)).scalars().all()
    nat_rules = db.execute(select(NatRule).order_by(NatRule.priority, NatRule.name)).scalars().all()
    targets = wan_route_targets(db)
    source_groups = firewall_source_group_state_for_db(db)["groups"]
    source_group_ids = {str(group.get("id", "")) for group in source_groups}
    validation_errors = validate_wan_state(
        routes,
        policies,
        {target["name"] for target in targets},
        nat_rules,
        {target["name"] for target in targets},
        source_group_ids,
    )
    config_preview = render_wan_config(routes, policies, nat_rules, targets, source_groups=source_groups)
    return {
        "routes": routes,
        "policies": policies,
        "nat_rules": nat_rules,
        "route_rows": [route_to_dict(route) for route in routes],
        "nat_rule_rows": [nat_rule_to_dict(rule) for rule in nat_rules],
        "policy_rows": [wan_policy_to_dict(policy) for policy in policies],
        "wan_route_targets": targets,
        "wan_route_target_names": [target["name"] for target in targets],
        "wan_nat_targets": targets,
        "wan_nat_target_names": [target["name"] for target in targets],
        "wan_source_groups": source_groups,
        "wan_policy_options": [{"id": policy.id, "label": policy.name} for policy in policies],
        "wan_modes": WAN_MODES,
        "wan_config_path": WAN_CONFIG_PATH,
        "wan_config_preview": config_preview,
        "wan_validation_errors": validation_errors,
    }


def dnsmasq_context(db: Session) -> dict:
    dns_settings = get_dns_settings_row(db)
    appliance_settings = get_appliance_settings_row(db)
    if ensure_dns_for_appliance_settings(db, appliance_settings, previous_fqdn=appliance_settings.fqdn, actor=None):
        db.commit()
        db.refresh(dns_settings)
    conditional_forwarders = setting_value(db, DNS_CONDITIONAL_FORWARDERS_SETTING_KEY)
    dns_records = db.execute(select(DnsRecord).order_by(DnsRecord.hostname)).scalars().all()
    dhcp_settings = get_dhcp_settings_row(db)
    dhcp_scopes = db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().all()
    dhcp_options = db.execute(select(DhcpOption).order_by(DhcpOption.scope_id, DhcpOption.option_code)).scalars().all()
    dhcp_reservations = db.execute(select(DhcpReservation).order_by(DhcpReservation.hostname)).scalars().all()
    esxi_boot = esxi_pxe_boot_settings(db)
    available_interfaces = service_bind_options(db)
    vlan_interfaces = db.execute(select(VlanInterface).order_by(VlanInterface.name)).scalars().all()
    config_preview = render_dnsmasq_config(
        dns_settings=dns_settings,
        dns_records=dns_records,
        dhcp_settings=dhcp_settings,
        dhcp_reservations=dhcp_reservations,
        dhcp_scopes=dhcp_scopes,
        dhcp_options=dhcp_options,
        conditional_forwarders=conditional_forwarders,
        esxi_pxe_boot=esxi_boot,
    )
    validation_errors = (
        validate_dns_settings(dns_settings, dns_records, conditional_forwarders)
        + validate_dns_listen_targets(dns_settings, {interface["name"] for interface in available_interfaces})
        + validate_dhcp_bind_targets(
            dhcp_settings,
            dhcp_scopes,
            dhcp_bind_target_names(
                db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all(),
                vlan_interfaces,
            ),
        )
        + validate_dhcp_settings(
            dhcp_settings,
            dhcp_reservations,
            dhcp_scopes,
            dhcp_options,
        )
    )
    if (esxi_boot.get("enabled") or esxi_boot.get("native_uefi_http_enabled")) and not dhcp_settings.enabled:
        validation_errors.append("ESXi PXE boot services require DHCP to be enabled so clients receive boot files.")
    dns_domains = split_domains(dns_settings.domain) or ["labfoundry.internal"]
    dns_warnings = dns_domain_warnings(dns_domains)
    dns_record_groups = dns_records_by_domain(dns_records, dns_domains)
    for group in dns_record_groups:
        group["suggested_ipv4"] = dns_record_suggested_ipv4(dns_records, group["domain"], dhcp_scopes, dhcp_reservations)
    reverse_zone_groups = reverse_records_by_zone(dns_reverse_records(dns_records))
    lease_result = SystemAdapter().read_dhcp_leases()
    dhcp_lease_error = lease_result.stderr.strip() if lease_result.returncode != 0 else ""
    dhcp_leases = [] if dhcp_lease_error else parse_dnsmasq_leases(lease_result.stdout)
    return {
        "dns_settings": dns_settings,
        "dns_records": dns_records,
        "dns_record_groups": dns_record_groups,
        "reverse_zone_groups": reverse_zone_groups,
        "dhcp_settings": dhcp_settings,
        "dhcp_scopes": dhcp_scopes,
        "dhcp_scope_rows": [dhcp_scope_to_dict(scope) for scope in dhcp_scopes],
        "dhcp_options": dhcp_options,
        "dhcp_option_rows": [dhcp_option_to_dict(option) for option in dhcp_options],
        "dhcp_option_scope_choices": dhcp_option_scope_choices(dhcp_scopes),
        "dhcp_generated_pxe_options": generated_esxi_pxe_dhcp_options(esxi_boot, dhcp_scopes),
        "dhcp_reservations": dhcp_reservations,
        "dhcp_reservation_rows": [dhcp_reservation_payload(item) for item in dhcp_reservations],
        "dhcp_leases": dhcp_leases,
        "dhcp_lease_dry_run": lease_result.dry_run,
        "dhcp_lease_command": " ".join(lease_result.command),
        "dhcp_lease_error": dhcp_lease_error,
        "available_interfaces": available_interfaces,
        "available_dns_addresses": available_dns_listen_addresses(dns_settings, dhcp_settings, available_interfaces, vlan_interfaces),
        "selected_dns_interfaces": split_interfaces(dns_settings.listen_interface),
        "selected_dns_addresses": split_addresses(dns_settings.listen_address),
        "config_preview": config_preview,
        "dns_domains": "\n".join(dns_domains),
        "hosts_editor_text": render_hosts_records(dns_records),
        "validation_errors": validation_errors,
        "dns_warnings": dns_warnings,
        "upstream_servers": "\n".join(split_servers(dns_settings.upstream_servers)),
        "conditional_forwarders": join_conditional_forwarders(split_conditional_forwarders(conditional_forwarders)),
        "dns_domain_options": dns_domains,
        "system_adapter_dry_run": get_settings().dry_run_system_adapters,
    }


def generated_esxi_pxe_dhcp_options(esxi_boot: dict[str, Any], scopes: list[DhcpScope]) -> list[dict[str, str]]:
    if not esxi_boot or not (esxi_boot.get("enabled") or esxi_boot.get("native_uefi_http_enabled")):
        return []
    rows: list[dict[str, str]] = []
    scope = next((item for item in scopes if item.id == esxi_boot.get("dhcp_scope_id")), None)
    applies_to = scope.name if scope is not None else "All DHCP zones"
    scope_prefix = f"tag:{dnsmasq_tag(scope.name)}," if scope is not None else ""
    tftp_hostname = str(esxi_boot.get("hostname") or "").strip()
    tftp_address = next(
        (line.strip() for line in str(esxi_boot.get("listen_address") or "").replace(",", "\n").splitlines() if line.strip()),
        "",
    )
    boot_server = f",{tftp_hostname},{tftp_address}" if tftp_hostname and tftp_address else ""
    native_http_url = str(esxi_boot.get("effective_native_uefi_http_url") or esxi_boot.get("native_uefi_http_url") or "").strip()

    def add(flow: str, line: str, note: str) -> None:
        rows.append({"applies_to": applies_to, "flow": flow, "line": line, "note": note})

    if esxi_boot.get("native_uefi_http_enabled") and native_http_url:
        add("Native UEFI HTTP", "dhcp-vendorclass=set:uefi-http,HTTPClient", "Detect HTTPClient firmware")
        add("Native UEFI HTTP", "dhcp-match=set:uefi-http-x64,option:client-arch,16", "Match x64 HTTP boot")
        add("Native UEFI HTTP", f"dhcp-boot={scope_prefix}tag:uefi-http,tag:uefi-http-x64,{native_http_url}", "Return mboot.efi HTTP URL")

    if esxi_boot.get("enabled"):
        if tftp_hostname:
            add("PXE TFTP", f"dhcp-option={scope_prefix}66,{tftp_hostname}", "Advertise TFTP server name")
        add("PXE TFTP", "enable-tftp", "Enable dnsmasq TFTP")
        add("PXE TFTP", f"tftp-root={esxi_boot.get('tftp_root')}", "Serve generated boot files")
        add("iPXE detection", "dhcp-userclass=set:ipxe,iPXE", "Detect iPXE second request")
        add("iPXE detection", "dhcp-match=set:ipxe,175", "Compatibility iPXE marker")
        add("UEFI PXE detection", "dhcp-match=set:efi-x86_64,option:client-arch,7", "Match x64 UEFI PXE")
        add("UEFI PXE detection", "dhcp-match=set:efi-x86_64,option:client-arch,9", "Match x64 UEFI PXE")
        add("iPXE second stage", f"dhcp-boot={scope_prefix}tag:ipxe,tag:efi-x86_64,{esxi_boot.get('uefi_second_stage_bootfile')}{boot_server}", "UEFI iPXE loads ESXi mboot")
        add("iPXE second stage", f"dhcp-boot={scope_prefix}tag:ipxe,tag:!efi-x86_64,{esxi_boot.get('bios_second_stage_bootfile')}{boot_server}", "BIOS iPXE loads PXELINUX")
        add("PXE first stage", f"dhcp-boot={scope_prefix}tag:efi-x86_64,{esxi_boot.get('uefi_bootfile')}{boot_server}", "UEFI PXE first-stage iPXE")
        add("PXE first stage", f"dhcp-boot={scope_prefix}tag:!efi-x86_64,{esxi_boot.get('bios_bootfile')}{boot_server}", "BIOS PXE first-stage iPXE")
    return rows


def dhcp_reservation_payload(reservation: DhcpReservation) -> dict:
    return {
        "id": reservation.id,
        "hostname": reservation.hostname,
        "mac_address": reservation.mac_address,
        "ip_address": reservation.ip_address,
        "description": reservation.description or "",
        "enabled": reservation.enabled,
    }


def dhcp_option_scope_choices(scopes: list[DhcpScope]) -> list[dict]:
    return [{"id": "__global__", "label": "Global defaults"}, *[{"id": scope.id, "label": scope.name} for scope in scopes]]


def parse_dhcp_option_scope_id(raw_value: str) -> int | None:
    if raw_value in {"", "__global__", "global", "None"}:
        return None
    return int(raw_value)


def ensure_dns_for_dhcp_reservation(db: Session, reservation: DhcpReservation, actor: str) -> None:
    scopes = db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().all()
    record_values = reservation_dns_record(reservation, scopes)
    if record_values is None:
        return
    hostname, record_type, address = record_values
    reservation.hostname = hostname
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing:
        return
    record = DnsRecord(
        hostname=hostname,
        record_type=record_type,
        address=address,
        description=f"Created from DHCP reservation for {reservation.mac_address}.",
        enabled=True,
    )
    db.add(record)
    db.flush()
    record_audit(db, actor=actor, action="create_dns_record_from_dhcp_reservation", resource_type="dns_record", resource_id=str(record.id))


def ensure_dns_for_vcf_registry(db: Session, settings: VcfPrivateRegistrySettings, actor: str) -> str | None:
    hostname = normalize_dns_hostname(settings.hostname)
    selected_address = primary_listen_address(settings.listen_address)
    if not hostname or not selected_address:
        return None
    try:
        parsed_address = ip_address(selected_address)
    except ValueError:
        return None
    record_type = "AAAA" if parsed_address.version == 6 else "A"
    address = str(parsed_address)
    if validate_dns_record(hostname, record_type, address):
        return None
    settings.hostname = hostname
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing:
        if existing.address == address and existing.enabled:
            return "unchanged"
        existing.address = address
        existing.enabled = True
        if not existing.description:
            existing.description = "Created from VCF private registry endpoint."
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="update_dns_record_from_vcf_registry",
            resource_type="dns_record",
            resource_id=str(existing.id),
            detail=f"{hostname} {record_type} -> {address}",
        )
        return "updated"
    record = DnsRecord(
        hostname=hostname,
        record_type=record_type,
        address=address,
        description="Created from VCF private registry endpoint.",
        enabled=True,
    )
    db.add(record)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="create_dns_record_from_vcf_registry",
        resource_type="dns_record",
        resource_id=str(record.id),
        detail=f"{hostname} {record_type} -> {address}",
    )
    return "created"


def ensure_dns_for_vcf_offline_depot(db: Session, settings: VcfOfflineDepotSettings, actor: str) -> str | None:
    hostname = normalize_dns_hostname(settings.hostname)
    selected_address = primary_listen_address(settings.listen_address)
    if not hostname or not selected_address:
        return None
    try:
        parsed_address = ip_address(selected_address)
    except ValueError:
        return None
    record_type = "AAAA" if parsed_address.version == 6 else "A"
    address = str(parsed_address)
    if validate_dns_record(hostname, record_type, address):
        return None
    settings.hostname = hostname
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing:
        if existing.address == address and existing.enabled:
            return "unchanged"
        existing.address = address
        existing.enabled = True
        if not existing.description:
            existing.description = "Created from VCF Offline Depot endpoint."
        db.flush()
        record_audit(
            db,
            actor=actor,
            action="update_dns_record_from_vcf_offline_depot",
            resource_type="dns_record",
            resource_id=str(existing.id),
            detail=f"{hostname} {record_type} -> {address}",
        )
        return "updated"
    record = DnsRecord(
        hostname=hostname,
        record_type=record_type,
        address=address,
        description="Created from VCF Offline Depot endpoint.",
        enabled=True,
    )
    db.add(record)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="create_dns_record_from_vcf_offline_depot",
        resource_type="dns_record",
        resource_id=str(record.id),
        detail=f"{hostname} {record_type} -> {address}",
    )
    return "created"


def kms_dns_record_conflict(db: Session, hostname: str) -> bool:
    normalized = normalize_dns_hostname(hostname)
    if not normalized:
        return False
    records = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == normalized,
            DnsRecord.record_type.in_(["A", "AAAA"]),
        )
    ).scalars().all()
    return any(record.description != KMS_DNS_RECORD_DESCRIPTION for record in records)


def ensure_dns_for_kms(db: Session, settings: KmsSettings, actor: str | None, *, previous_hostname: str | None = None) -> str | None:
    hostname = normalize_dns_hostname(settings.hostname)
    selected_address = primary_listen_address(settings.listen_address)
    if not hostname or not selected_address:
        return None
    try:
        parsed_address = ip_address(selected_address)
    except ValueError:
        return None
    record_type = "AAAA" if parsed_address.version == 6 else "A"
    address = str(parsed_address)
    if validate_dns_record(hostname, record_type, address):
        return None
    settings.hostname = hostname
    actions: list[str] = []
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing and existing.description != KMS_DNS_RECORD_DESCRIPTION:
        actions.append("conflict")
    elif existing:
        if existing.address != address or not existing.enabled:
            existing.address = address
            existing.enabled = True
            existing.description = KMS_DNS_RECORD_DESCRIPTION
            db.flush()
            if actor:
                record_audit(
                    db,
                    actor=actor,
                    action="update_dns_record_from_kms",
                    resource_type="dns_record",
                    resource_id=str(existing.id),
                    detail=f"{hostname} {record_type} -> {address}",
                )
            actions.append("updated")
        else:
            actions.append("unchanged")
    else:
        record = DnsRecord(
            hostname=hostname,
            record_type=record_type,
            address=address,
            description=KMS_DNS_RECORD_DESCRIPTION,
            enabled=True,
        )
        db.add(record)
        db.flush()
        if actor:
            record_audit(
                db,
                actor=actor,
                action="create_dns_record_from_kms",
                resource_type="dns_record",
                resource_id=str(record.id),
                detail=f"{hostname} {record_type} -> {address}",
            )
        actions.append("created")

    stale_records = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type.in_(["A", "AAAA"]),
            DnsRecord.record_type != record_type,
        )
    ).scalars().all()
    for record in stale_records:
        if record.description != KMS_DNS_RECORD_DESCRIPTION:
            continue
        db.delete(record)
        if actor:
            record_audit(
                db,
                actor=actor,
                action="delete_dns_record_from_kms_ip_family_change",
                resource_type="dns_record",
                resource_id=str(record.id),
                detail=f"{record.hostname} {record.record_type}",
            )
        actions.append("removed-stale")

    previous = normalize_dns_hostname(previous_hostname or "")
    if previous and previous != hostname:
        old_records = db.execute(
            select(DnsRecord).where(
                DnsRecord.hostname == previous,
                DnsRecord.record_type.in_(["A", "AAAA"]),
            )
        ).scalars().all()
        for record in old_records:
            if record.description != KMS_DNS_RECORD_DESCRIPTION:
                continue
            db.delete(record)
            if actor:
                record_audit(
                    db,
                    actor=actor,
                    action="delete_dns_record_from_kms_rename",
                    resource_type="dns_record",
                    resource_id=str(record.id),
                    detail=f"{record.hostname} {record.record_type}",
                )
            actions.append("removed-old")
    if actions:
        db.flush()
    return "+".join(actions) if actions else None


def remove_dns_for_vcf_offline_depot_hostname(db: Session, hostname: str, actor: str) -> str | None:
    normalized_hostname = normalize_dns_hostname(hostname)
    if not normalized_hostname:
        return None
    records = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == normalized_hostname,
            DnsRecord.record_type.in_(["A", "AAAA"]),
        )
    ).scalars().all()
    removed = 0
    for record in records:
        if record.description and "VCF Offline Depot endpoint" not in record.description:
            continue
        db.delete(record)
        removed += 1
        record_audit(
            db,
            actor=actor,
            action="delete_dns_record_from_vcf_offline_depot_rename",
            resource_type="dns_record",
            resource_id=str(record.id),
            detail=f"{record.hostname} {record.record_type}",
        )
    if removed:
        db.flush()
        return "removed-old"
    return None


def esxi_pxe_dns_record_conflict(db: Session, hostname: str) -> bool:
    normalized = normalize_dns_hostname(hostname)
    if not normalized:
        return False
    records = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == normalized,
            DnsRecord.record_type.in_(["A", "AAAA"]),
        )
    ).scalars().all()
    return any(record.description != ESXI_PXE_DNS_RECORD_DESCRIPTION for record in records)


def remove_dns_for_esxi_pxe_hostname(db: Session, hostname: str, actor: str | None) -> str | None:
    normalized_hostname = normalize_dns_hostname(hostname)
    if not normalized_hostname:
        return None
    records = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == normalized_hostname,
            DnsRecord.record_type.in_(["A", "AAAA"]),
        )
    ).scalars().all()
    removed = 0
    for record in records:
        if record.description != ESXI_PXE_DNS_RECORD_DESCRIPTION:
            continue
        db.delete(record)
        removed += 1
        if actor:
            record_audit(db, actor=actor, action="delete_dns_record_from_esxi_pxe", resource_type="dns_record", resource_id=str(record.id), detail=f"{record.hostname} {record.record_type}")
    if removed:
        db.flush()
        return "removed"
    return None


def ensure_dns_for_esxi_pxe(db: Session, boot: dict[str, Any], actor: str | None, *, previous_hostname: str | None = None) -> str | None:
    hostname = normalize_dns_hostname(str(boot.get("hostname") or ESXI_PXE_DEFAULT_HOSTNAME))
    selected_address = primary_listen_address(str(boot.get("listen_address") or ""))
    if not bool(boot.get("enabled")):
        return remove_dns_for_esxi_pxe_hostname(db, previous_hostname or hostname, actor)
    if not hostname or not selected_address:
        return None
    try:
        parsed_address = ip_address(selected_address)
    except ValueError:
        return None
    record_type = "AAAA" if parsed_address.version == 6 else "A"
    address = str(parsed_address)
    if validate_dns_record(hostname, record_type, address):
        return None
    actions: list[str] = []
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing and existing.description != ESXI_PXE_DNS_RECORD_DESCRIPTION:
        actions.append("conflict")
    elif existing:
        if existing.address != address or not existing.enabled:
            existing.address = address
            existing.enabled = True
            existing.description = ESXI_PXE_DNS_RECORD_DESCRIPTION
            db.flush()
            if actor:
                record_audit(db, actor=actor, action="update_dns_record_from_esxi_pxe", resource_type="dns_record", resource_id=str(existing.id), detail=f"{hostname} {record_type} -> {address}")
            actions.append("updated")
        else:
            actions.append("unchanged")
    else:
        record = DnsRecord(hostname=hostname, record_type=record_type, address=address, description=ESXI_PXE_DNS_RECORD_DESCRIPTION, enabled=True)
        db.add(record)
        db.flush()
        if actor:
            record_audit(db, actor=actor, action="create_dns_record_from_esxi_pxe", resource_type="dns_record", resource_id=str(record.id), detail=f"{hostname} {record_type} -> {address}")
        actions.append("created")
    for record in db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type.in_(["A", "AAAA"]),
            DnsRecord.record_type != record_type,
        )
    ).scalars().all():
        if record.description != ESXI_PXE_DNS_RECORD_DESCRIPTION:
            continue
        db.delete(record)
        if actor:
            record_audit(db, actor=actor, action="delete_dns_record_from_esxi_pxe_ip_family_change", resource_type="dns_record", resource_id=str(record.id), detail=f"{record.hostname} {record.record_type}")
        actions.append("removed-stale")
    previous = normalize_dns_hostname(previous_hostname or "")
    if previous and previous != hostname:
        removed = remove_dns_for_esxi_pxe_hostname(db, previous, actor)
        if removed:
            actions.append("removed-old")
    if actions:
        db.flush()
    return "+".join(actions) if actions else None


def available_dns_listen_addresses(
    dns_settings: DnsSettings,
    dhcp_settings: DhcpSettings,
    listen_options: list[dict[str, str]],
    vlan_interfaces: list[VlanInterface],
) -> list[dict[str, str]]:
    choices: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(address: str | None, source: str) -> None:
        for item in split_addresses(address):
            if item not in seen:
                seen.add(item)
                choices.append({"address": item, "source": source})

    add(dns_settings.listen_address, "current DNS")
    for option in listen_options:
        add(option.get("address"), option["name"])
    add(dhcp_settings.site_address, "SiteA gateway")
    for vlan in vlan_interfaces:
        if vlan.ip_cidr:
            try:
                add(str(ip_interface(vlan.ip_cidr).ip), vlan.name)
            except ValueError:
                add(vlan.ip_cidr, vlan.name)
    return choices


def available_service_listen_addresses(current_addresses: str | None, listen_options: list[dict[str, str]]) -> list[dict[str, str]]:
    choices: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(address: str | None, source: str) -> None:
        for item in split_addresses(address):
            if item not in seen:
                seen.add(item)
                choices.append({"address": item, "source": source})

    add(current_addresses, "current")
    for option in listen_options:
        add(option.get("address"), option["name"])
    return choices


def dns_records_by_domain(records: list[DnsRecord], domains: list[str]) -> list[dict]:
    groups = [{"domain": domain, "records": []} for domain in domains]
    group_map = {group["domain"]: group for group in groups}
    for record in records:
        domain = matching_domain(record.hostname, domains) or domains[0]
        group_map.setdefault(domain, {"domain": domain, "records": []})
        group_map[domain]["records"].append(dns_record_payload(record, domain))
    for group in groups:
        group["hosts_editor_text"] = render_zone_hosts_records(group["records"])
        group["zone_file_text"] = render_zone_file(group["domain"], group["records"])
    return groups


def ipv4_address_or_none(value: str | None) -> IPv4Address | None:
    try:
        address = ip_address((value or "").strip())
    except ValueError:
        return None
    return address if isinstance(address, IPv4Address) else None


def ipv4_range(start: str | None, end: str | None) -> set[IPv4Address]:
    start_address = ipv4_address_or_none(start)
    end_address = ipv4_address_or_none(end)
    if not start_address or not end_address:
        return set()
    start_int = int(start_address)
    end_int = int(end_address)
    if end_int < start_int or end_int - start_int > 8192:
        return set()
    return {IPv4Address(value) for value in range(start_int, end_int + 1)}


def dhcp_scope_network(scope: DhcpScope) -> IPv4Network | None:
    site_address = ipv4_address_or_none(scope.site_address)
    if not site_address:
        return None
    try:
        return ip_network(f"{site_address}/{scope.prefix_length}", strict=False)
    except ValueError:
        return None


def dns_record_suggested_ipv4(records: list[DnsRecord], domain: str, dhcp_scopes: list[DhcpScope], dhcp_reservations: list[DhcpReservation]) -> str:
    domain_records = [record for record in records if matching_domain(record.hostname, [domain]) == domain]
    used_addresses = {
        address
        for address in [ipv4_address_or_none(record.address) for record in records if record.record_type.strip().upper() == "A"]
        if address is not None
    }
    used_addresses.update(
        address
        for address in [ipv4_address_or_none(reservation.ip_address) for reservation in dhcp_reservations]
        if address is not None
    )

    excluded_addresses = set(used_addresses)
    candidate_networks: list[tuple[IPv4Network, set[IPv4Address]]] = []
    for scope in dhcp_scopes:
        if not scope.enabled:
            continue
        if scope.domain_name.strip().strip(".").lower() != domain:
            continue
        network = dhcp_scope_network(scope)
        if network is None:
            continue
        scope_excluded = set(excluded_addresses)
        site_address = ipv4_address_or_none(scope.site_address)
        if site_address:
            scope_excluded.add(site_address)
        scope_excluded.update(ipv4_range(scope.range_start, scope.range_end))
        candidate_networks.append((network, scope_excluded))

    inferred_networks: dict[IPv4Network, int] = {}
    for record in domain_records:
        if record.record_type.strip().upper() != "A":
            continue
        address = ipv4_address_or_none(record.address)
        if not address:
            continue
        network = ip_network(f"{address}/24", strict=False)
        inferred_networks[network] = inferred_networks.get(network, 0) + 1
    for network, _count in sorted(inferred_networks.items(), key=lambda item: (-item[1], int(item[0].network_address))):
        candidate_networks.append((network, set(excluded_addresses)))

    for network, excluded in candidate_networks:
        for candidate in network.hosts():
            if candidate not in excluded:
                return str(candidate)
    return ""


def validate_vlan_form_values(parent_interface: str, vlan_id: str, ip_cidr: str, db: Session) -> tuple[str, int, str] | Response:
    parent_name = parent_interface.strip()
    if not parent_name:
        return Response("VLAN parent interface is required.", status_code=409, media_type="text/plain")
    raw_vlan_id = str(vlan_id).strip()
    if not raw_vlan_id:
        return Response("VLAN ID is required.", status_code=409, media_type="text/plain")
    try:
        parsed_vlan_id = int(raw_vlan_id)
    except ValueError:
        return Response("VLAN ID must be a number between 1 and 4094.", status_code=409, media_type="text/plain")
    if parsed_vlan_id < 1 or parsed_vlan_id > 4094:
        return Response("VLAN ID must be between 1 and 4094.", status_code=409, media_type="text/plain")
    ip_value = ip_cidr.strip()
    if not ip_value:
        return Response("VLAN IP CIDR is required.", status_code=409, media_type="text/plain")
    try:
        ip_interface(ip_value)
    except ValueError:
        return Response("VLAN IP CIDR must be a valid address and prefix, for example 192.168.50.1/24.", status_code=409, media_type="text/plain")
    parent = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == parent_name)).scalar_one_or_none()
    if not parent or normalize_interface_mode(parent.mode) != "trunk":
        return Response(
            f"{parent_name or 'Selected parent'} is not a trunk interface. Mark the physical NIC as trunk before creating VLANs on it.",
            status_code=409,
            media_type="text/plain",
        )
    return parent_name, parsed_vlan_id, ip_value


def reverse_records_by_zone(records: list[dict[str, str]]) -> list[dict]:
    groups: dict[str, dict] = {}
    for record in records:
        group = groups.setdefault(record["zone"], {"zone": record["zone"], "records": []})
        group["records"].append(record)
    return sorted(groups.values(), key=lambda item: item["zone"])


def matching_domain(hostname: str, domains: list[str]) -> str | None:
    normalized = hostname.strip().strip(".").lower()
    for domain in sorted(domains, key=len, reverse=True):
        if normalized == domain or normalized.endswith(f".{domain}"):
            return domain
    return None


def dns_record_payload(record: DnsRecord, domain: str) -> dict:
    hostname = record.hostname.strip().strip(".").lower()
    suffix = f".{domain}"
    if hostname == domain:
        host_label = "@"
    elif hostname.endswith(suffix):
        host_label = hostname[: -len(suffix)]
    else:
        host_label = hostname
    return {
        "id": record.id,
        "hostname": record.hostname,
        "host_label": host_label,
        "domain": domain,
        "record_type": record.record_type,
        "address": record.address,
        "description": record.description or "",
        "enabled": record.enabled,
        **dns_record_reverse_status(record),
    }


def dns_record_reverse_status(record: DnsRecord) -> dict[str, str]:
    record_type = record.record_type.strip().upper()
    if record_type not in {"A", "AAAA"}:
        return {
            "reverse_status": "not-applicable",
            "reverse_label": "not applicable",
            "reverse_ptr": "",
            "reverse_zone": "",
        }
    if record.enabled is False:
        return {
            "reverse_status": "disabled",
            "reverse_label": "disabled",
            "reverse_ptr": "",
            "reverse_zone": "",
        }
    reverse_records = dns_reverse_records([record])
    if not reverse_records:
        return {
            "reverse_status": "invalid",
            "reverse_label": "invalid address",
            "reverse_ptr": "",
            "reverse_zone": "",
        }
    reverse_record = reverse_records[0]
    return {
        "reverse_status": "generated",
        "reverse_label": reverse_record["ptr_name"],
        "reverse_ptr": reverse_record["ptr_name"],
        "reverse_zone": reverse_record["zone"],
    }


def normalize_dns_hostname(hostname: str, domain: str | None = None) -> str:
    normalized = hostname.strip().strip(".").lower()
    zone = (domain or "").strip().strip(".").lower()
    if zone and normalized == "@":
        return zone
    if zone and normalized and normalized != zone and not normalized.endswith(f".{zone}"):
        return f"{normalized}.{zone}"
    return normalized


def dns_domains_for_settings(settings: DnsSettings) -> list[str]:
    return split_domains(settings.domain) or ["labfoundry.internal"]


def save_dns_domains(settings: DnsSettings, domains: list[str]) -> None:
    settings.domain = join_domains(domains) or "labfoundry.internal"


def records_for_domain(db: Session, domain: str) -> list[DnsRecord]:
    records = db.execute(select(DnsRecord).order_by(DnsRecord.hostname)).scalars().all()
    return [record for record in records if matching_domain(record.hostname, [domain]) == domain]


APPLIANCE_APPLY_BASELINES_KEY = "appliance_apply.baselines.v1"
APPLIANCE_APPLY_UNIT_IDS = {
    "local_users",
    "appliance_settings",
    "network",
    "wan",
    "firewall",
    "dnsmasq",
    "esxi_pxe",
    "ca",
    "kms",
    "vcf_backups",
    "vcf_offline_depot",
    "vcf_private_registry",
}
SECRET_LINE_PATTERN = re.compile(
    r"(rootpw|password|passwd|token|secret|credential|private[_-]?key|robot[_-]?account|ca[_-]?bundle[_-]?pem|activation[_-]?code|license|ipxe[_-]?script)",
    re.IGNORECASE,
)
PRIVATE_KEY_BEGIN_PATTERN = re.compile(r"-----BEGIN .*PRIVATE KEY-----")
PRIVATE_KEY_END_PATTERN = re.compile(r"-----END .*PRIVATE KEY-----")
JWT_PATH_SEGMENT_PATTERN = re.compile(r"(?<=/)[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}(?=/|$)")
JSON_SECRET_FIELD_PATTERN = re.compile(r'^(\s*"[^"]+"\s*:\s*)(.*?)(,?)\s*$')


def redact_config_preview(config_preview: str) -> str:
    lines: list[str] = []
    in_private_key = False
    for line in (config_preview or "").splitlines():
        if PRIVATE_KEY_BEGIN_PATTERN.search(line):
            lines.append("[redacted private key]")
            in_private_key = True
            continue
        if in_private_key:
            if PRIVATE_KEY_END_PATTERN.search(line):
                in_private_key = False
            continue
        if SECRET_LINE_PATTERN.search(line):
            json_match = JSON_SECRET_FIELD_PATTERN.match(line)
            if json_match:
                lines.append(f'{json_match.group(1)}"[redacted]"{json_match.group(3)}')
                continue
            separator = "=" if "=" in line else ":" if ":" in line else None
            if separator:
                prefix = line.split(separator, 1)[0].rstrip()
                lines.append(f"{prefix}{separator} [redacted]")
            else:
                lines.append("[redacted sensitive line]")
            continue
        lines.append(JWT_PATH_SEGMENT_PATTERN.sub("[redacted-token]", line))
    return "\n".join(lines)


def load_appliance_apply_baselines(db: Session) -> dict[str, dict[str, Any]]:
    raw_value = setting_value(db, APPLIANCE_APPLY_BASELINES_KEY)
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def save_appliance_apply_baselines(db: Session, baselines: dict[str, dict[str, Any]]) -> None:
    set_setting_value(db, APPLIANCE_APPLY_BASELINES_KEY, json.dumps(baselines, indent=2, sort_keys=True))


def appliance_snapshot_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def config_diff_for_unit(unit_id: str, current_preview: str, baseline: dict[str, Any] | None) -> str:
    if not baseline or not baseline.get("config_preview"):
        return ""
    previous_preview = str(baseline.get("config_preview") or "")
    if previous_preview == current_preview:
        return ""
    return "\n".join(
        difflib.unified_diff(
            previous_preview.splitlines(),
            current_preview.splitlines(),
            fromfile=f"last-applied/{unit_id}",
            tofile=f"current/{unit_id}",
            lineterm="",
        )
    )


def network_vlan_entries_from_config(config_preview: str) -> list[dict[str, str]]:
    vlan_entries: list[dict[str, str]] = []
    current_section = ""
    current: dict[str, str] | None = None
    for raw_line in (config_preview or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line.strip("[]")
            current = None
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if current_section == "vlan_interfaces" and key == "vlan":
            current = {"name": value}
            vlan_entries.append(current)
            continue
        if current_section == "vlan_interfaces" and current is not None:
            current[key] = value
    return vlan_entries


def successful_network_apply_vlan_entries(db: Session, baseline: dict[str, Any] | None) -> list[dict[str, str]]:
    applied_by_name: dict[str, dict[str, str]] = {}
    baseline_preview = str((baseline or {}).get("config_preview") or "")
    for entry in network_vlan_entries_from_config(baseline_preview):
        if entry.get("name"):
            applied_by_name[entry["name"]] = entry

    jobs = (
        db.execute(
            select(Job)
            .where(Job.type == "appliance-apply", Job.status == JobStatus.SUCCEEDED.value)
            .order_by(Job.created_at)
        )
        .scalars()
        .all()
    )
    for job in jobs:
        try:
            result = json.loads(job.result or "")
        except json.JSONDecodeError:
            continue
        for unit in result.get("units", []):
            if unit.get("unit_id") != "network" or not unit.get("success") or unit.get("dry_run"):
                continue
            for entry in network_vlan_entries_from_config(str(unit.get("config_preview") or "")):
                if entry.get("name"):
                    applied_by_name[entry["name"]] = entry
            for entry in unit.get("removed_vlan_interfaces", []):
                name = entry.get("name") if isinstance(entry, dict) else ""
                if name:
                    applied_by_name.pop(str(name), None)
    return list(applied_by_name.values())


def removed_network_vlan_entries(current_preview: str, applied_entries: list[dict[str, str]]) -> list[dict[str, str]]:
    current_names = {entry.get("name", "") for entry in network_vlan_entries_from_config(current_preview)}
    removed: list[dict[str, str]] = []
    for entry in applied_entries:
        name = entry.get("name", "")
        if name and name not in current_names:
            removed.append(
                {
                    "name": name,
                    "parent": entry.get("parent", ""),
                    "vlan_id": entry.get("vlan_id", ""),
                }
            )
    return removed


def local_usernames_from_config(config_preview: str) -> list[str]:
    try:
        payload = json.loads(config_preview or "")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    usernames: list[str] = []
    for row in payload.get("users", []):
        if not isinstance(row, dict):
            continue
        username = str(row.get("username") or "").strip().lower()
        if username and username not in usernames:
            usernames.append(username)
    return usernames


def removed_local_usernames(users: list[User], baseline: dict[str, Any] | None) -> list[str]:
    current = {user.username.strip().lower() for user in users}
    previous = local_usernames_from_config(str((baseline or {}).get("config_preview") or ""))
    return [username for username in previous if username not in current]


def network_config_with_removed_vlans(config_preview: str, removed_vlans: list[dict[str, str]]) -> str:
    if not removed_vlans:
        return config_preview
    lines = [config_preview.rstrip(), "", "[removed_vlan_interfaces]"]
    for vlan in removed_vlans:
        lines.extend(
            [
                f"vlan={vlan['name']}",
                f"  parent={vlan.get('parent', '')}",
                f"  vlan_id={vlan.get('vlan_id', '')}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def wan_route_entries_from_config(config_preview: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current_section = ""
    current: dict[str, str] | None = None
    for raw_line in (config_preview or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line.strip("[]")
            current = None
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if current_section == "routes" and key == "route":
            current = {"destination_cidr": value}
            entries.append(current)
            continue
        if current_section == "routes" and current is not None:
            current[key] = value
    return entries


def removed_wan_route_entries(current_preview: str, baseline: dict[str, Any] | None) -> list[dict[str, str]]:
    baseline_preview = str((baseline or {}).get("config_preview") or "")
    current_keys = {
        (entry.get("destination_cidr", ""), entry.get("interface", ""))
        for entry in wan_route_entries_from_config(current_preview)
    }
    removed: list[dict[str, str]] = []
    for entry in wan_route_entries_from_config(baseline_preview):
        key = (entry.get("destination_cidr", ""), entry.get("interface", ""))
        if key[0] and key[1] and key not in current_keys:
            removed.append(
                {
                    "destination_cidr": key[0],
                    "gateway": entry.get("gateway", ""),
                    "interface_name": key[1],
                    "metric": entry.get("metric", "100"),
                }
            )
    return removed


def make_appliance_apply_unit(
    *,
    unit_id: str,
    label: str,
    page_url: str,
    context: dict[str, Any],
    summary: list[str],
    validation_errors: list[str],
    validation_warnings: list[str] | None = None,
    config_path: str,
    config_preview: str,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    redacted_preview = redact_config_preview(config_preview)
    snapshot_payload = {
        "unit_id": unit_id,
        "summary": summary,
        "config_path": config_path,
        "config_preview": redacted_preview,
    }
    current_hash = appliance_snapshot_hash(snapshot_payload)
    baseline_hash = str((baseline or {}).get("snapshot_hash") or "")
    return {
        "id": unit_id,
        "label": label,
        "page_url": page_url,
        "context": context,
        "summary": summary,
        "validation_errors": validation_errors,
        "validation_warnings": validation_warnings or [],
        "valid": not validation_errors,
        "config_path": config_path,
        "raw_config_preview": config_preview,
        "config_preview": redacted_preview,
        "snapshot_hash": current_hash,
        "changed": current_hash != baseline_hash,
        "has_baseline": bool(baseline_hash),
        "last_applied_at": (baseline or {}).get("applied_at"),
        "config_diff": config_diff_for_unit(unit_id, redacted_preview, baseline),
    }


def local_users_apply_context(db: Session, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    validation_errors = validate_local_usernames(users)
    policy = local_users_password_policy(db)
    removed_users = removed_local_usernames(users, baseline)
    config_preview = render_local_users_preview(users, password_policy=policy, removed_users=removed_users)
    try:
        config_preview = render_local_users_apply_config(users, password_policy=policy, removed_users=removed_users)
    except ValueError as exc:
        validation_errors.append(str(exc))
    return {
        "local_users": users,
        "local_user_sync_rows": local_user_sync_rows(users),
        "local_user_validation_errors": validation_errors,
        "local_user_config_preview": config_preview,
        "local_user_display_preview": render_local_users_preview(users, password_policy=policy, removed_users=removed_users),
        "local_user_pending_password_count": pending_os_password_count(users),
        "local_user_unlock_request_count": sum(1 for user in users if user.os_unlock_requested_at),
        "local_user_removed_users": removed_users,
    }


def esxi_pxe_context(db: Session) -> dict[str, Any]:
    kickstarts = db.execute(select(EsxiKickstart).order_by(EsxiKickstart.name)).scalars().all()
    hosts = db.execute(select(EsxiPxeHost).options(selectinload(EsxiPxeHost.kickstart)).order_by(EsxiPxeHost.hostname)).scalars().all()
    dhcp_scopes = db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().all()
    available_interfaces = service_bind_options(db)
    iso_error = ""
    try:
        installer_isos = installer_iso_inventory()
    except OSError as exc:
        installer_isos = []
        iso_error = f"Installer ISO folder could not be prepared: {exc}"
    strict = strict_validation_enabled(db)
    max_bytes = get_settings().esxi_kickstart_max_bytes
    validation_errors: list[str] = []
    validation_warnings: list[str] = []
    validation_by_id: dict[int, dict[str, list[str] | bool]] = {}
    for row in kickstarts:
        errors, warnings = kickstart_validation(row.content, strict=strict, max_bytes=max_bytes)
        validation_by_id[row.id] = {"valid": not errors, "errors": errors, "warnings": warnings}
        validation_errors.extend(f"{row.name}: {error}" for error in errors)
        validation_warnings.extend(f"{row.name}: {warning}" for warning in warnings)
        if kickstart_drift_state(row) == "filesystem_modified":
            validation_warnings.append(
                f"{row.name}: Filesystem copy differs from database source. The next ESXi PXE apply will overwrite the filesystem copy from the database."
            )
    known_iso_paths = {row["path"] for row in installer_isos}
    for host in hosts:
        if host.enabled and not (host.installer_iso_path or "").strip():
            validation_warnings.append(f"{host.hostname}: no installer ISO selected.")
        if host.installer_iso_path and host.installer_iso_path not in known_iso_paths:
            validation_warnings.append(f"{host.hostname}: selected installer ISO is missing from the ESX_HOST depot folder.")
    if iso_error:
        validation_warnings.append(iso_error)
    boot_settings = esxi_pxe_boot_settings(db)
    selected_boot_interfaces = split_interfaces(boot_settings.get("listen_interface"))
    selected_boot_addresses = split_addresses(boot_settings.get("listen_address"))
    available_boot_addresses = available_service_listen_addresses(boot_settings.get("listen_address"), available_interfaces)
    if boot_settings["native_uefi_http_enabled"] and not boot_settings["native_uefi_http_url"]:
        if boot_settings.get("effective_native_uefi_http_url"):
            validation_warnings.append("Native UEFI HTTP boot URL will be generated from the ESXi PXE HTTP endpoint.")
        else:
            validation_warnings.append("Native UEFI HTTP boot is enabled, but no listen address is available to generate the boot URL.")
    if boot_settings["enabled"]:
        if not boot_settings["hostname"]:
            validation_errors.append("ESXi PXE hostname is required when PXE/TFTP bootstrap is enabled.")
        if not boot_settings.get("dhcp_scope_id"):
            validation_errors.append("ESXi PXE boot service requires a DHCP IP zone.")
        if not selected_boot_addresses:
            validation_errors.append("ESXi PXE boot service requires at least one listen address.")
        if esxi_pxe_dns_record_conflict(db, boot_settings["hostname"]):
            validation_errors.append("ESXi PXE hostname conflicts with an existing non-ESXi PXE DNS record.")
        elif boot_settings["hostname"].lower() not in managed_dns_fqdns(db):
            validation_warnings.append(f"ESXi PXE hostname {boot_settings['hostname']} is not present in managed DNS records.")
        if not esxi_pxe_host_artifacts(hosts, boot_settings):
            validation_warnings.append("ESXi PXE bootstrap is enabled, but no enabled host has an installer ISO selected.")
    return {
        "esxi_kickstarts": kickstarts,
        "esxi_kickstart_rows": [kickstart_to_dict(row, include_content=True) for row in kickstarts],
        "esxi_pxe_hosts": hosts,
        "esxi_pxe_host_rows": [host_to_dict(row) for row in hosts],
        "esxi_installer_iso_root": installer_iso_root_path(),
        "esxi_installer_isos": installer_isos,
        "esxi_installer_iso_error": iso_error,
        "esxi_pxe_boot": boot_settings,
        "esxi_pxe_dhcp_scope_options": [
            {
                "id": scope.id,
                "name": scope.name,
                "interface_name": scope.interface_name,
                "site_address": scope.site_address,
                "label": f"{scope.name} - {scope.interface_name} / {scope.site_address}/{scope.prefix_length}",
            }
            for scope in dhcp_scopes
            if scope.enabled is not False
        ],
        "esxi_pxe_available_interfaces": available_interfaces,
        "esxi_pxe_selected_interfaces": selected_boot_interfaces,
        "esxi_pxe_selected_addresses": selected_boot_addresses,
        "esxi_pxe_available_addresses": available_boot_addresses,
        "esxi_pxe_bind_label": service_bind_label(boot_settings.get("listen_interface"), boot_settings.get("listen_address")),
        "esxi_pxe_primary_listen_address": primary_listen_address(boot_settings.get("listen_address")),
        "esxi_pxe_artifacts": esxi_pxe_host_artifacts(hosts, boot_settings),
        "esxi_pxe_validation_errors": validation_errors,
        "esxi_pxe_validation_warnings": list(dict.fromkeys(validation_warnings)),
        "esxi_pxe_validation_by_id": validation_by_id,
        "esxi_pxe_manifest": render_esxi_pxe_manifest(kickstarts, hosts, boot_settings),
        "esxi_pxe_preview": render_esxi_pxe_preview(kickstarts, hosts, boot_settings),
        "esxi_pxe_config_path": ESXI_PXE_STAGED_CONFIG_PATH,
        "esxi_pxe_strict_validation": strict,
    }


def appliance_apply_units(db: Session) -> list[dict[str, Any]]:
    baselines = load_appliance_apply_baselines(db)
    local_users = local_users_apply_context(db, baselines.get("local_users"))
    appliance_settings = appliance_settings_context(db)
    network = network_context(db)
    wan = routes_wan_context(db)
    firewall = firewall_context(db)
    dnsmasq = dnsmasq_context(db)
    esxi_pxe = esxi_pxe_context(db)
    ca = ca_context(db)
    kms = kms_context(db)
    vcf_backup = vcf_backup_context(db)
    vcf_depot = vcf_offline_depot_context(db)
    vcf_registry = vcf_private_registry_context(db)

    network_baseline = baselines.get("network")
    network_removed_vlans = removed_network_vlan_entries(
        network["network_config_preview"],
        successful_network_apply_vlan_entries(db, network_baseline),
    )
    network_summary = [f"{len(network['physical_interfaces'])} physical interfaces", f"{len(network['vlan_interfaces'])} VLAN interfaces"]
    if network_removed_vlans:
        network_summary.append(f"{len(network_removed_vlans)} VLAN removals")
    network_unit = make_appliance_apply_unit(
        unit_id="network",
        label="Network",
        page_url="/physical-interfaces",
        context=network,
        summary=network_summary,
        validation_errors=network["network_validation_errors"],
        config_path=network["network_config_path"],
        config_preview=network["network_config_preview"],
        baseline=network_baseline,
    )
    network_unit["removed_vlan_interfaces"] = network_removed_vlans

    wan_baseline = baselines.get("wan")
    wan_removed_routes = removed_wan_route_entries(wan["wan_config_preview"], wan_baseline)
    if wan_removed_routes:
        wan["wan_config_preview"] = render_wan_config(
            wan["routes"],
            wan["policies"],
            wan["nat_rules"],
            wan["wan_route_targets"],
            removed_routes=wan_removed_routes,
            source_groups=wan["wan_source_groups"],
        )
    wan_summary = [f"{len(wan['routes'])} routes", f"{len(wan['nat_rules'])} NAT rules", f"{len(wan['policies'])} WAN policies"]
    if wan_removed_routes:
        wan_summary.append(f"{len(wan_removed_routes)} route removals")

    return [
        make_appliance_apply_unit(
            unit_id="local_users",
            label="Local Users",
            page_url="/users",
            context=local_users,
            summary=[
                f"{len(local_users['local_users'])} local users",
                f"{local_users['local_user_pending_password_count']} pending OS passwords",
                f"{local_users['local_user_unlock_request_count']} unlock requests",
                f"{len(local_users['local_user_removed_users'])} removed OS accounts",
            ],
            validation_errors=local_users["local_user_validation_errors"],
            config_path=LOCAL_USERS_STAGED_CONFIG_PATH,
            config_preview=local_users["local_user_config_preview"],
            baseline=baselines.get("local_users"),
        ),
        make_appliance_apply_unit(
            unit_id="appliance_settings",
            label="Appliance Settings",
            page_url="/settings",
            context=appliance_settings,
            summary=[
                f"FQDN {appliance_settings['appliance_settings'].fqdn}",
                f"resolver {'local DNS' if appliance_settings['local_dns_enabled'] else 'external DNS'}",
                f"root SSH {'enabled' if appliance_settings['appliance_settings'].root_ssh_enabled else 'disabled'}",
                f"{len(appliance_settings['appliance_settings_json']['ntp_servers'])} NTP servers",
            ],
            validation_errors=appliance_settings["appliance_settings_validation_errors"],
            validation_warnings=appliance_settings["appliance_settings_validation_warnings"],
            config_path=appliance_settings["appliance_settings"].config_path,
            config_preview=appliance_settings["appliance_settings_config_preview"],
            baseline=baselines.get("appliance_settings"),
        ),
        network_unit,
        make_appliance_apply_unit(
            unit_id="firewall",
            label="Firewall",
            page_url="/firewall",
            context=firewall,
            summary=[
                "service enabled" if firewall["firewall_settings"].enabled else "service disabled",
                f"{len(firewall['firewall_rules'])} editable rules",
                f"{len(firewall['firewall_generated_rules'])} managed service rules",
            ],
            validation_errors=firewall["firewall_validation_errors"],
            config_path=firewall["firewall_settings"].config_path,
            config_preview=firewall["firewall_config_preview"],
            baseline=baselines.get("firewall"),
        ),
        make_appliance_apply_unit(
            unit_id="wan",
            label="Routes & WAN Simulation",
            page_url="/routes-wan",
            context=wan,
            summary=wan_summary,
            validation_errors=wan["wan_validation_errors"],
            config_path=wan["wan_config_path"],
            config_preview=wan["wan_config_preview"],
            baseline=wan_baseline,
        ),
        make_appliance_apply_unit(
            unit_id="dnsmasq",
            label="DNS/DHCP (dnsmasq)",
            page_url="/dns",
            context=dnsmasq,
            summary=[
                "DNS enabled" if dnsmasq["dns_settings"].enabled else "DNS disabled",
                "DHCP enabled" if dnsmasq["dhcp_settings"].enabled else "DHCP disabled",
                f"{len(dnsmasq['dns_records'])} DNS records",
                f"{len(dnsmasq['dhcp_scopes'])} DHCP scopes",
                f"{len(dnsmasq['dhcp_reservations'])} reservations",
            ],
            validation_errors=dnsmasq["validation_errors"],
            validation_warnings=dnsmasq["dns_warnings"],
            config_path=dnsmasq["dns_settings"].config_path,
            config_preview=dnsmasq["config_preview"],
            baseline=baselines.get("dnsmasq"),
        ),
        make_appliance_apply_unit(
            unit_id="esxi_pxe",
            label="ESXi PXE",
            page_url="/esxi-pxe",
            context=esxi_pxe,
            summary=[
                f"{len(esxi_pxe['esxi_kickstarts'])} Kickstarts",
                f"{len([row for row in esxi_pxe['esxi_kickstarts'] if row.enabled])} enabled",
                f"{len(esxi_pxe['esxi_pxe_hosts'])} host definitions",
                "boot services enabled" if esxi_pxe["esxi_pxe_boot"]["enabled"] else "boot services disabled",
            ],
            validation_errors=esxi_pxe["esxi_pxe_validation_errors"],
            validation_warnings=esxi_pxe["esxi_pxe_validation_warnings"],
            config_path=esxi_pxe["esxi_pxe_config_path"],
            config_preview=esxi_pxe["esxi_pxe_manifest"],
            baseline=baselines.get("esxi_pxe"),
        ),
        make_appliance_apply_unit(
            unit_id="ca",
            label="Certificate Authority",
            page_url="/certificate-authority",
            context=ca,
            summary=[
                "service enabled" if ca["ca_settings"].enabled else "service disabled",
                f"{len(ca['ca_profiles'])} profiles",
                f"{len(ca['ca_certificates'])} certificate requests",
            ],
            validation_errors=ca["ca_validation_errors"],
            config_path=CA_STAGED_CONFIG_PATH,
            config_preview=ca["ca_apply_payload"],
            baseline=baselines.get("ca"),
        ),
        make_appliance_apply_unit(
            unit_id="kms",
            label="KMS / KMIP",
            page_url="/kms",
            context=kms,
            summary=["service enabled" if kms["kms_settings"].enabled else "service disabled", f"{len(kms['kms_clients'])} clients", f"{len(kms['kms_keys'])} keys"],
            validation_errors=kms["kms_validation_errors"],
            config_path=KMS_STAGED_CONFIG_PATH,
            config_preview=kms["kms_config_preview"],
            baseline=baselines.get("kms"),
        ),
        make_appliance_apply_unit(
            unit_id="vcf_backups",
            label="VCF Backups",
            page_url="/vcf-backups",
            context=vcf_backup,
            summary=["service enabled" if vcf_backup["vcf_backup_settings"].enabled else "service disabled", f"remote {vcf_backup['vcf_backup_remote_directory']}"],
            validation_errors=vcf_backup["vcf_backup_validation_errors"],
            config_path=vcf_backup["vcf_backup_settings"].config_path,
            config_preview=vcf_backup["vcf_backup_config_preview"],
            baseline=baselines.get("vcf_backups"),
        ),
        make_appliance_apply_unit(
            unit_id="vcf_offline_depot",
            label="VCF Offline Depot",
            page_url="/vcf-offline-depot",
            context=vcf_depot,
            summary=[
                "service enabled" if vcf_depot["vcf_depot_settings"].enabled else "service disabled",
                f"{len([profile for profile in vcf_depot['vcf_depot_profiles'] if profile.enabled])} enabled profiles",
            ],
            validation_errors=vcf_depot["vcf_depot_validation_errors"],
            validation_warnings=vcf_depot["vcf_depot_validation_warnings"],
            config_path=vcf_depot["vcf_depot_settings"].config_path,
            config_preview=f"{vcf_depot['vcf_depot_https_config_preview']}\n\n{vcf_depot_secret_snapshot(vcf_depot)}\n\n# VCFDT command preview\n{vcf_depot['vcf_depot_command_preview']}",
            baseline=baselines.get("vcf_offline_depot"),
        ),
        make_appliance_apply_unit(
            unit_id="vcf_private_registry",
            label="VCF Private Registry",
            page_url="/vcf-private-registry",
            context=vcf_registry,
            summary=[
                "service enabled" if vcf_registry["vcf_registry_settings"].enabled else "service disabled",
                f"{len([bundle for bundle in vcf_registry['vcf_registry_bundles'] if bundle.enabled])} enabled bundles",
            ],
            validation_errors=vcf_registry["vcf_registry_validation_errors"],
            validation_warnings=vcf_registry["vcf_registry_validation_warnings"],
            config_path=vcf_registry["vcf_registry_settings"].config_path,
            config_preview=f"{vcf_registry['vcf_registry_harbor_config_preview']}\n\n# Bundle relocation preview\n{vcf_registry['vcf_registry_relocation_preview']}",
            baseline=baselines.get("vcf_private_registry"),
        ),
    ]


def appliance_apply_status(db: Session, unit_id: str) -> dict[str, Any]:
    units = appliance_apply_units(db)
    sidebar_count = len([unit for unit in units if unit["changed"]])
    for unit in units:
        if unit["id"] == unit_id:
            if unit["validation_errors"]:
                state = "needs attention"
                pill = "warn"
            elif unit["changed"]:
                state = "pending"
                pill = "warn"
            else:
                state = "current"
                pill = "good"
            return {"state": state, "pill": pill, "sidebar_pending_apply_count": sidebar_count, **unit}
    return {"state": "unknown", "pill": "muted", "changed": False, "validation_errors": [], "sidebar_pending_apply_count": sidebar_count}


def appliance_apply_context(db: Session) -> dict[str, Any]:
    units = appliance_apply_units(db)
    changed_units = [unit for unit in units if unit["changed"]]
    return {
        "apply_units": units,
        "changed_apply_units": changed_units,
        "unchanged_apply_units": [unit for unit in units if not unit["changed"]],
        "changed_apply_unit_count": len(changed_units),
    }


def adapter_result_to_payload(result: Any) -> dict[str, Any]:
    return {
        "command": result.command,
        "command_line": " ".join(result.command),
        "dry_run": result.dry_run,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def apply_output_excerpt(value: str, *, limit: int = 2400) -> str:
    redacted = redact_config_preview(value or "").strip()
    if len(redacted) <= limit:
        return redacted
    return f"{redacted[:limit].rstrip()}\n... output truncated ..."


def filesystem_path(path: Path | PurePosixPath) -> Path:
    return path if isinstance(path, Path) else Path(path)


def tail_fixed_log_file(path: Path | PurePosixPath, *, max_bytes: int = 64 * 1024, max_lines: int = 240) -> dict[str, Any]:
    read_path = filesystem_path(path)
    try:
        if not read_path.exists():
            return {"path": str(path), "available": False, "lines": [], "size_bytes": 0, "updated_at": "", "error": ""}
        size = read_path.stat().st_size
        with read_path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            raw = handle.read(max_bytes)
    except OSError as exc:
        return {"path": str(path), "available": False, "lines": [], "size_bytes": 0, "updated_at": "", "error": str(exc)}
    text = raw.decode("utf-8", errors="replace")
    lines = redact_config_preview(text).splitlines()[-max_lines:]
    updated_at = utcnow()
    try:
        updated_at = datetime.fromtimestamp(read_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        pass
    return {
        "path": str(path),
        "available": True,
        "lines": lines,
        "size_bytes": size,
        "updated_at": updated_at.isoformat(),
        "truncated": size > max_bytes,
    }


def logs_context(db: Session) -> dict[str, Any]:
    sources = [
        ("vcfdt", "VCFDT", VCF_DEPOT_VDT_LOG_PATH),
        ("app", "LabFoundry App", LABFOUNDRY_APP_LOG_PATH),
        ("kms", "KMS", KMS_SERVER_LOG_PATH),
    ]
    events = db.execute(select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(100)).scalars().all()
    return {
        "log_sources": [
            {
                "id": source_id,
                "label": label,
                **tail_fixed_log_file(path),
            }
            for source_id, label, path in sources
        ],
        "audit_events": events,
    }


def appliance_apply_failure_summaries(unit_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for unit in unit_results:
        failed_commands = []
        for command in unit.get("commands", []):
            if int(command.get("returncode") or 0) == 0:
                continue
            failed_commands.append(
                {
                    "command_line": apply_output_excerpt(str(command.get("command_line") or ""), limit=800),
                    "returncode": command.get("returncode"),
                    "stdout": apply_output_excerpt(str(command.get("stdout") or "")),
                    "stderr": apply_output_excerpt(str(command.get("stderr") or "")),
                }
            )
        if failed_commands:
            summaries.append(
                {
                    "unit_id": unit.get("unit_id"),
                    "label": unit.get("label") or unit.get("unit_id"),
                    "commands": failed_commands,
                }
            )
    return summaries


def log_appliance_apply_failures(job_id: str, unit_results: list[dict[str, Any]]) -> None:
    for failure in appliance_apply_failure_summaries(unit_results):
        for command in failure["commands"]:
            APPLY_LOGGER.error(
                "Appliance apply task %s failed unit=%s command=%s returncode=%s stderr=%s stdout=%s",
                job_id,
                failure["label"],
                command["command_line"],
                command["returncode"],
                command["stderr"] or "",
                command["stdout"] or "",
            )


def stage_appliance_apply_config(config_path: str, config_preview: str) -> str:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config_preview, encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def execute_appliance_apply_unit(unit: dict[str, Any]) -> dict[str, Any]:
    context = unit["context"]
    adapter = SystemAdapter()
    unit_id = unit["id"]
    if unit_id == "local_users":
        config_path = LOCAL_USERS_STAGED_CONFIG_PATH
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(LOCAL_USERS_STAGED_CONFIG_PATH, unit["raw_config_preview"])
        results = [adapter.validate_local_users_config(config_path), adapter.apply_local_users_config(config_path)]
    elif unit_id == "appliance_settings":
        settings = context["appliance_settings"]
        config_path = settings.config_path
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(APPLIANCE_SETTINGS_STAGED_CONFIG_PATH, unit["raw_config_preview"])
        results = [adapter.validate_appliance_settings_config(config_path), adapter.apply_appliance_settings_config(config_path)]
    elif unit_id == "network":
        config_path = context["network_config_path"]
        if not adapter.dry_run:
            config_preview = network_config_with_removed_vlans(unit["raw_config_preview"], unit.get("removed_vlan_interfaces", []))
            config_path = stage_appliance_apply_config(NETWORK_STAGED_CONFIG_PATH, config_preview)
        results = [adapter.validate_network_config(config_path), adapter.apply_network_config(config_path)]
    elif unit_id == "wan":
        config_path = context["wan_config_path"]
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(WAN_CONFIG_PATH, unit["raw_config_preview"])
        results = [adapter.validate_wan_config(config_path), adapter.apply_wan_config(config_path)]
    elif unit_id == "firewall":
        settings = context["firewall_settings"]
        config_path = settings.config_path
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(FIREWALL_STAGED_CONFIG_PATH, unit["raw_config_preview"])
        results = [adapter.validate_firewall_config(config_path), adapter.apply_firewall_config(config_path)]
    elif unit_id == "dnsmasq":
        config_path = context["dns_settings"].config_path
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(DNSMASQ_STAGED_CONFIG_PATH, unit["raw_config_preview"])
        results = [adapter.validate_dnsmasq_config(config_path), adapter.apply_dnsmasq_config(config_path), adapter.reload_dnsmasq()]
    elif unit_id == "esxi_pxe":
        config_path = context["esxi_pxe_config_path"]
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(ESXI_PXE_STAGED_CONFIG_PATH, context["esxi_pxe_manifest"])
        results = [adapter.validate_esxi_pxe_config(config_path), adapter.apply_esxi_pxe_config(config_path)]
    elif unit_id == "ca":
        config_path = CA_STAGED_CONFIG_PATH
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(
                CA_STAGED_CONFIG_PATH,
                render_ca_apply_payload(context["ca_settings"], context["ca_certificates"], include_private_keys=True),
            )
        results = [adapter.validate_ca_config(config_path), adapter.apply_ca_config(config_path)]
    elif unit_id == "kms":
        config_path = KMS_STAGED_CONFIG_PATH
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(KMS_STAGED_CONFIG_PATH, unit["raw_config_preview"])
        results = [adapter.validate_kms_config(config_path), adapter.apply_kms_config(config_path)]
    elif unit_id == "vcf_backups":
        settings = context["vcf_backup_settings"]
        config_path = settings.config_path
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(VCF_BACKUP_STAGED_CONFIG_PATH, unit["raw_config_preview"])
        results = [adapter.validate_vcf_backup_config(config_path), adapter.apply_vcf_backup_config(config_path)]
    elif unit_id == "vcf_offline_depot":
        settings = context["vcf_depot_settings"]
        config_path = settings.config_path
        if not adapter.dry_run:
            config_path = stage_appliance_apply_config(VCF_DEPOT_STAGED_CONFIG_PATH, context["vcf_depot_https_config_preview"])
        results = [
            adapter.validate_vcf_offline_depot_config(config_path),
            adapter.sync_vcf_offline_depot(config_path),
            adapter.apply_vcf_offline_depot_https_config(config_path),
        ]
        if settings.tool_archive_path:
            results.insert(1, adapter.stage_vcf_offline_depot_tool(settings.tool_archive_path))
    elif unit_id == "vcf_private_registry":
        settings = context["vcf_registry_settings"]
        results = [
            adapter.validate_vcf_private_registry_config(settings.config_path),
            adapter.apply_vcf_private_registry_config(settings.config_path),
            adapter.relocate_vcf_private_registry_bundles(settings.config_path),
        ]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown apply unit {unit_id}.")

    succeeded = all(result.returncode == 0 for result in results)
    if unit_id == "local_users" and not any(result.dry_run for result in results):
        users = list(context["local_users"])
        if succeeded:
            mark_local_users_applied(users)
        else:
            error = "\n".join(result.stderr for result in results if result.stderr).strip() or "Local user OS sync failed."
            mark_local_users_failed(users, error)
    if unit_id == "esxi_pxe" and succeeded and not any(result.dry_run for result in results):
        mark_kickstarts_applied(list(context["esxi_kickstarts"]))
    return {
        "unit_id": unit_id,
        "label": unit["label"],
        "status": JobStatus.SUCCEEDED.value if succeeded else JobStatus.FAILED.value,
        "success": succeeded,
        "dry_run": any(result.dry_run for result in results),
        "commands": [adapter_result_to_payload(result) for result in results],
        "summary": unit["summary"],
        "validation_errors": unit["validation_errors"],
        "validation_warnings": unit["validation_warnings"],
        "removed_vlan_interfaces": unit.get("removed_vlan_interfaces", []),
        "generated_files": [str(generated_kickstart_path(row.id)) for row in context.get("esxi_kickstarts", []) if row.enabled],
        "config_path": unit["config_path"],
        "config_preview": unit["config_preview"],
        "config_diff": unit["config_diff"],
    }


def update_appliance_apply_baselines(db: Session, units: list[dict[str, Any]], selected_ids: set[str]) -> None:
    baselines = load_appliance_apply_baselines(db)
    applied_at = utcnow().isoformat()
    for unit in units:
        if unit["id"] not in selected_ids:
            continue
        baselines[unit["id"]] = {
            "snapshot_hash": unit["snapshot_hash"],
            "config_path": unit["config_path"],
            "config_preview": unit["config_preview"],
            "summary": unit["summary"],
            "applied_at": applied_at,
        }
    save_appliance_apply_baselines(db, baselines)


def _has_operator_appliance_activity(db: Session) -> bool:
    if db.execute(select(Job.id).where(Job.type == "appliance-apply").limit(1)).first() is not None:
        return True
    return db.execute(select(AuditEvent.id).where(AuditEvent.resource_type != "auth").limit(1)).first() is not None


def _mark_provisioned_bootstrap_admin_applied(db: Session) -> None:
    settings = get_settings()
    bootstrap_user = db.execute(select(User).where(User.username == settings.bootstrap_admin_username)).scalar_one_or_none()
    if bootstrap_user is None or not bootstrap_user.enabled:
        return
    timestamp = utcnow()
    clear_pending_os_password(bootstrap_user)
    bootstrap_user.os_password_applied_at = bootstrap_user.os_password_applied_at or timestamp
    bootstrap_user.os_sync_applied_at = bootstrap_user.os_sync_applied_at or timestamp
    bootstrap_user.os_sync_status = "applied"
    bootstrap_user.os_sync_error = None
    bootstrap_user.os_unlock_requested_at = None
    db.add(bootstrap_user)


def initialize_factory_appliance_apply_baseline(db: Session) -> bool:
    settings = get_settings()
    if settings.environment != "appliance":
        return False
    if setting_value(db, APPLIANCE_APPLY_BASELINES_KEY):
        return False
    if _has_operator_appliance_activity(db):
        return False

    _mark_provisioned_bootstrap_admin_applied(db)
    units = appliance_apply_units(db)
    update_appliance_apply_baselines(db, units, {unit["id"] for unit in units})
    db.commit()
    record_audit(
        db,
        actor="system",
        action="initialize_factory_appliance_apply_baseline",
        resource_type="appliance_apply",
        detail=f"{len(units)} factory desired-state units baselined without host mutation",
    )
    return True


@router.get("/favicon.ico", response_model=None)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "brand" / "labfoundry-mark.svg", media_type="image/svg+xml")


@router.get("/manifest.webmanifest", response_model=None)
def webmanifest() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/service-worker.js", response_model=None)
def service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "service-worker.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


@router.get("/", response_class=HTMLResponse, response_model=None)
def root(request: Request, identity: Identity | None = Depends(get_session_identity)) -> HTMLResponse | RedirectResponse:
    if not identity:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(request: Request, identity: Identity | None = Depends(get_session_identity)) -> HTMLResponse | RedirectResponse:
    if identity:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {"error": None})


@router.post("/login", response_model=None)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse | JSONResponse:
    verify_csrf(request, csrf)
    user = authenticate_user(db, username, password)
    if not user:
        record_audit(db, actor=username, action="ui_login_failed", resource_type="auth", success=False)
        return render(request, "login.html", {"error": "Invalid username or password"})
    request.session["user_id"] = user.id
    record_audit(db, actor=user.username, action="ui_login", resource_type="auth")
    return RedirectResponse("/", status_code=303)


@router.post("/logout", response_model=None)
def logout(request: Request, csrf: str = Form(...)) -> RedirectResponse:
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse, response_model=None)
def dashboard(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    services = db.execute(select(ServiceState).order_by(ServiceState.display_name)).scalars().all()
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    routes = db.execute(select(Route).options(selectinload(Route.wan_policy)).order_by(Route.destination_cidr)).scalars().all()
    audit_events = db.execute(select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(8)).scalars().all()
    return render(
        request,
        "dashboard.html",
        {
            "identity": identity,
            "services": services,
            "interfaces": interfaces,
            "routes": routes,
            "audit_events": audit_events,
        },
    )


@router.get("/appliance-apply", response_class=HTMLResponse, response_model=None)
def appliance_apply_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "appliance_apply.html", {"identity": identity, **appliance_apply_context(db)})


@router.get("/appliance-apply/status", response_class=JSONResponse, response_model=None)
def appliance_apply_status_api(
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> JSONResponse:
    context = appliance_apply_context(db)
    pending_count = context["changed_apply_unit_count"]
    return JSONResponse(
        {
            "pending_count": pending_count,
            "label": "Review appliance changes" if pending_count else "Appliance Apply",
            "detail": f"{pending_count} pending {'unit' if pending_count == 1 else 'units'}" if pending_count else "Desired state current",
            "badge": "pending" if pending_count else "current",
        }
    )


@router.post("/appliance-apply", response_class=HTMLResponse, response_model=None)
def submit_appliance_apply(
    request: Request,
    selected_units: list[str] = Form(default=[]),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    units = appliance_apply_units(db)
    unit_map = {unit["id"]: unit for unit in units}
    selected_ids = {unit_id for unit_id in selected_units if unit_id in APPLIANCE_APPLY_UNIT_IDS}
    if not selected_ids:
        return render(
            request,
            "appliance_apply.html",
            {
                "identity": identity,
                **appliance_apply_context(db),
                "apply_error": "Select at least one appliance change to submit.",
                "selected_apply_unit_ids": selected_ids,
            },
            status_code=422,
        )
    invalid_units = [unit for unit in units if unit["id"] in selected_ids and unit["validation_errors"]]
    if invalid_units:
        return render(
            request,
            "appliance_apply.html",
            {
                "identity": identity,
                **appliance_apply_context(db),
                "selected_apply_unit_ids": selected_ids,
                "apply_error": "Resolve validation errors before submitting appliance changes.",
            },
            status_code=422,
        )

    selected_ordered_units = [unit for unit in units if unit["id"] in selected_ids]
    unit_results = [execute_appliance_apply_unit(unit) for unit in selected_ordered_units]
    succeeded = all(result["success"] for result in unit_results)
    now = utcnow()
    skipped_changed_units = [
        {"unit_id": unit["id"], "label": unit["label"], "summary": unit["summary"]}
        for unit in units
        if unit["changed"] and unit["id"] not in selected_ids
    ]
    job_result = {
        "selected_units": [unit["id"] for unit in selected_ordered_units],
        "skipped_changed_units": skipped_changed_units,
        "units": unit_results,
        "dry_run": any(result["dry_run"] for result in unit_results),
    }
    job_id = f"job_{uuid4().hex[:12]}"
    job = Job(
        id=job_id,
        type="appliance-apply",
        status=JobStatus.SUCCEEDED.value if succeeded else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if succeeded else "One or more appliance apply units reported a failure.",
    )
    db.add(job)
    if succeeded:
        update_appliance_apply_baselines(db, appliance_apply_units(db), selected_ids)
    else:
        log_appliance_apply_failures(job_id, unit_results)
    db.commit()
    detail = " ; ".join(
        " ".join(command["command"])
        for result in unit_results
        for command in result["commands"]
    )
    record_audit(
        db,
        actor=identity.username,
        action="create_appliance_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=detail,
        success=succeeded,
    )
    return render(
        request,
        "appliance_apply.html",
        {
            "identity": identity,
            **appliance_apply_context(db),
            "apply_task": job,
            "apply_task_dry_run": job_result["dry_run"],
            "applied_unit_results": unit_results,
            "apply_failure_summaries": appliance_apply_failure_summaries(unit_results),
        },
    )


@router.get("/routes-wan", response_class=HTMLResponse, response_model=None)
def routes_wan(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "routes_wan.html", {"identity": identity, **routes_wan_context(db), "appliance_apply_status": appliance_apply_status(db, "wan")})


def parse_int_form_value(value: str, field_label: str, *, default: int = 0, minimum: int | None = None) -> int | Response:
    if value == "":
        parsed = default
    else:
        try:
            parsed = int(value)
        except ValueError:
            return Response(f"{field_label} must be a number.", status_code=422, media_type="text/plain")
    if minimum is not None and parsed < minimum:
        return Response(f"{field_label} must be at least {minimum}.", status_code=422, media_type="text/plain")
    return parsed


def parse_optional_int_form_value(value: str, field_label: str, *, minimum: int | None = None) -> int | None | Response:
    if value == "":
        return None
    return parse_int_form_value(value, field_label, minimum=minimum or None)


def parse_float_form_value(value: str, field_label: str, *, default: float = 0.0, minimum: float | None = None, maximum: float | None = None) -> float | Response:
    if value == "":
        parsed = default
    else:
        try:
            parsed = float(value)
        except ValueError:
            return Response(f"{field_label} must be a number.", status_code=422, media_type="text/plain")
    if minimum is not None and parsed < minimum:
        return Response(f"{field_label} must be at least {minimum}.", status_code=422, media_type="text/plain")
    if maximum is not None and parsed > maximum:
        return Response(f"{field_label} must be at most {maximum}.", status_code=422, media_type="text/plain")
    return parsed


def validate_route_form_values(
    destination_cidr: str,
    gateway: str,
    interface_name: str,
    metric: str,
    wan_policy_id: str,
    wan_mode: str,
    db: Session,
) -> tuple[str, str | None, str, int, int | None, str] | Response:
    destination = destination_cidr.strip()
    if not destination:
        return Response("Destination CIDR is required.", status_code=422, media_type="text/plain")
    try:
        ip_network(destination, strict=False)
    except ValueError:
        return Response(f"{destination} is not a valid destination CIDR.", status_code=422, media_type="text/plain")
    gateway_value = gateway.strip() or None
    if gateway_value:
        try:
            ip_address(gateway_value)
        except ValueError:
            return Response(f"{gateway_value} is not a valid gateway IP address.", status_code=422, media_type="text/plain")
    target_names = {target["name"] for target in wan_route_targets(db)}
    interface_value = interface_name.strip()
    if interface_value not in target_names:
        return Response("Choose an access physical interface or enabled VLAN interface with an IP CIDR.", status_code=422, media_type="text/plain")
    metric_value = parse_int_form_value(metric.strip(), "Metric", default=100, minimum=0)
    if isinstance(metric_value, Response):
        return metric_value
    policy_id_value: int | None = None
    if wan_policy_id.strip():
        parsed_policy_id = parse_int_form_value(wan_policy_id.strip(), "WAN policy", minimum=1)
        if isinstance(parsed_policy_id, Response):
            return parsed_policy_id
        if db.get(WanPolicy, parsed_policy_id) is None:
            return Response("WAN policy does not exist.", status_code=422, media_type="text/plain")
        policy_id_value = parsed_policy_id
    raw_mode = wan_mode.strip() or "interface"
    if raw_mode not in WAN_MODES:
        return Response("WAN route mode is planned but not supported in v1. Use interface mode.", status_code=422, media_type="text/plain")
    mode_value = "interface"
    return destination, gateway_value, interface_value, metric_value, policy_id_value, mode_value


def validate_wan_policy_form_values(
    name: str,
    latency_ms: str,
    jitter_ms: str,
    packet_loss_percent: str,
    bandwidth_mbit: str,
    corrupt_percent: str,
    duplicate_percent: str,
    reorder_percent: str,
) -> tuple[str, int, int, float, int | None, float, float, float] | Response:
    name_value = name.strip()
    if not name_value:
        return Response("WAN policy name is required.", status_code=422, media_type="text/plain")
    latency_value = parse_int_form_value(latency_ms.strip(), "Latency", default=0, minimum=0)
    jitter_value = parse_int_form_value(jitter_ms.strip(), "Jitter", default=0, minimum=0)
    loss_value = parse_float_form_value(packet_loss_percent.strip(), "Packet loss", default=0.0, minimum=0.0, maximum=100.0)
    bandwidth_value = parse_optional_int_form_value(bandwidth_mbit.strip(), "Bandwidth", minimum=1)
    corrupt_value = parse_float_form_value(corrupt_percent.strip(), "Corruption", default=0.0, minimum=0.0, maximum=100.0)
    duplicate_value = parse_float_form_value(duplicate_percent.strip(), "Duplication", default=0.0, minimum=0.0, maximum=100.0)
    reorder_value = parse_float_form_value(reorder_percent.strip(), "Reordering", default=0.0, minimum=0.0, maximum=100.0)
    for value in [latency_value, jitter_value, loss_value, bandwidth_value, corrupt_value, duplicate_value, reorder_value]:
        if isinstance(value, Response):
            return value
    return name_value, latency_value, jitter_value, loss_value, bandwidth_value, corrupt_value, duplicate_value, reorder_value


def validate_nat_rule_form_values(
    name: str,
    source: str,
    outbound_interface: str,
    priority: str,
    masquerade: str | None,
    db: Session,
) -> tuple[str, str, str, bool, int] | Response:
    name_value = name.strip()
    if not name_value:
        return Response("NAT rule name is required.", status_code=422, media_type="text/plain")
    source_value = source.strip() or "any"
    source_groups = firewall_source_group_state_for_db(db)["groups"]
    source_errors = validate_nat_source(source_value, {str(group.get("id", "")) for group in source_groups})
    if source_errors:
        return Response(source_errors[0], status_code=422, media_type="text/plain")
    target_names = {target["name"] for target in wan_route_targets(db)}
    outbound_value = outbound_interface.strip()
    if outbound_value not in target_names:
        return Response("Choose an access physical interface or enabled VLAN interface with an IP CIDR.", status_code=422, media_type="text/plain")
    masquerade_value = masquerade == "on"
    if not masquerade_value:
        return Response("NAT v1 supports masquerade only.", status_code=422, media_type="text/plain")
    priority_value = parse_int_form_value(priority.strip(), "Priority", default=100, minimum=0)
    if isinstance(priority_value, Response):
        return priority_value
    return name_value, source_value, outbound_value, masquerade_value, priority_value


@router.post("/routes-wan/routes", response_model=None)
def create_route_from_ui(
    request: Request,
    destination_cidr: str = Form(""),
    gateway: str = Form(""),
    interface_name: str = Form(""),
    metric: str = Form("100"),
    wan_policy_id: str = Form(""),
    wan_mode: str = Form("interface"),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | Response:
    verify_csrf(request, csrf)
    parsed = validate_route_form_values(destination_cidr, gateway, interface_name, metric, wan_policy_id, wan_mode, db)
    if isinstance(parsed, Response):
        return parsed
    destination, gateway_value, interface_value, metric_value, policy_id_value, mode_value = parsed
    route = Route(
        destination_cidr=destination,
        gateway=gateway_value,
        interface_name=interface_value,
        metric=metric_value,
        wan_policy_id=policy_id_value,
        wan_mode=mode_value,
        enabled=enabled == "on",
    )
    db.add(route)
    db.commit()
    record_audit(db, actor=identity.username, action="create_route", resource_type="route", resource_id=str(route.id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/routes/{route_id}/edit", response_model=None)
def edit_route_from_ui(
    request: Request,
    route_id: int,
    destination_cidr: str = Form(""),
    gateway: str = Form(""),
    interface_name: str = Form(""),
    metric: str = Form("100"),
    wan_policy_id: str = Form(""),
    wan_mode: str = Form("interface"),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | Response:
    verify_csrf(request, csrf)
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    parsed = validate_route_form_values(destination_cidr, gateway, interface_name, metric, wan_policy_id, wan_mode, db)
    if isinstance(parsed, Response):
        return parsed
    destination, gateway_value, interface_value, metric_value, policy_id_value, mode_value = parsed
    route.destination_cidr = destination
    route.gateway = gateway_value
    route.interface_name = interface_value
    route.metric = metric_value
    route.wan_policy_id = policy_id_value
    route.wan_mode = mode_value
    route.enabled = enabled == "on"
    db.add(route)
    db.commit()
    record_audit(db, actor=identity.username, action="update_route", resource_type="route", resource_id=str(route.id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/routes/{route_id}/delete", response_model=None)
def delete_route_from_ui(
    request: Request,
    route_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    db.delete(route)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_route", resource_type="route", resource_id=str(route_id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/nat-rules", response_model=None)
def create_nat_rule_from_ui(
    request: Request,
    name: str = Form(""),
    source: str = Form("any"),
    outbound_interface: str = Form(""),
    masquerade: str | None = Form(None),
    priority: str = Form("100"),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | Response:
    verify_csrf(request, csrf)
    parsed = validate_nat_rule_form_values(name, source, outbound_interface, priority, masquerade, db)
    if isinstance(parsed, Response):
        return parsed
    name_value, source_value, outbound_value, masquerade_value, priority_value = parsed
    rule = NatRule(
        name=name_value,
        source=source_value,
        outbound_interface=outbound_value,
        masquerade=masquerade_value,
        priority=priority_value,
        description=description.strip() or None,
        enabled=enabled == "on",
    )
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return Response(f"NAT rule {rule.name} already exists.", status_code=409, media_type="text/plain")
    record_audit(db, actor=identity.username, action="create_nat_rule", resource_type="nat_rule", resource_id=str(rule.id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/nat-rules/{rule_id}/edit", response_model=None)
def edit_nat_rule_from_ui(
    request: Request,
    rule_id: int,
    name: str = Form(""),
    source: str = Form("any"),
    outbound_interface: str = Form(""),
    masquerade: str | None = Form(None),
    priority: str = Form("100"),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | Response:
    verify_csrf(request, csrf)
    rule = db.get(NatRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="NAT rule not found")
    parsed = validate_nat_rule_form_values(name, source, outbound_interface, priority, masquerade, db)
    if isinstance(parsed, Response):
        return parsed
    name_value, source_value, outbound_value, masquerade_value, priority_value = parsed
    rule.name = name_value
    rule.source = source_value
    rule.outbound_interface = outbound_value
    rule.masquerade = masquerade_value
    rule.priority = priority_value
    rule.description = description.strip() or None
    rule.enabled = enabled == "on"
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return Response(f"NAT rule {rule.name} already exists.", status_code=409, media_type="text/plain")
    record_audit(db, actor=identity.username, action="update_nat_rule", resource_type="nat_rule", resource_id=str(rule.id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/nat-rules/{rule_id}/delete", response_model=None)
def delete_nat_rule_from_ui(
    request: Request,
    rule_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    rule = db.get(NatRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="NAT rule not found")
    db.delete(rule)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_nat_rule", resource_type="nat_rule", resource_id=str(rule_id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/policies", response_model=None)
def create_policy_from_ui(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    latency_ms: str = Form("0"),
    jitter_ms: str = Form("0"),
    packet_loss_percent: str = Form("0"),
    bandwidth_mbit: str = Form(""),
    corrupt_percent: str = Form("0"),
    duplicate_percent: str = Form("0"),
    reorder_percent: str = Form("0"),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | Response:
    verify_csrf(request, csrf)
    parsed = validate_wan_policy_form_values(
        name,
        latency_ms,
        jitter_ms,
        packet_loss_percent,
        bandwidth_mbit,
        corrupt_percent,
        duplicate_percent,
        reorder_percent,
    )
    if isinstance(parsed, Response):
        return parsed
    name_value, latency_value, jitter_value, loss_value, bandwidth_value, corrupt_value, duplicate_value, reorder_value = parsed
    policy = WanPolicy(
        name=name_value,
        description=description.strip() or None,
        latency_ms=latency_value,
        jitter_ms=jitter_value,
        packet_loss_percent=loss_value,
        bandwidth_mbit=bandwidth_value,
        corrupt_percent=corrupt_value,
        duplicate_percent=duplicate_value,
        reorder_percent=reorder_value,
        enabled=enabled == "on",
    )
    db.add(policy)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return Response(f"WAN policy {policy.name} already exists.", status_code=409, media_type="text/plain")
    record_audit(db, actor=identity.username, action="create_wan_policy", resource_type="wan_policy", resource_id=str(policy.id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/policies/{policy_id}/edit", response_model=None)
def edit_policy_from_ui(
    request: Request,
    policy_id: int,
    name: str = Form(""),
    description: str = Form(""),
    latency_ms: str = Form("0"),
    jitter_ms: str = Form("0"),
    packet_loss_percent: str = Form("0"),
    bandwidth_mbit: str = Form(""),
    corrupt_percent: str = Form("0"),
    duplicate_percent: str = Form("0"),
    reorder_percent: str = Form("0"),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | Response:
    verify_csrf(request, csrf)
    policy = db.get(WanPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="WAN policy not found")
    parsed = validate_wan_policy_form_values(
        name,
        latency_ms,
        jitter_ms,
        packet_loss_percent,
        bandwidth_mbit,
        corrupt_percent,
        duplicate_percent,
        reorder_percent,
    )
    if isinstance(parsed, Response):
        return parsed
    name_value, latency_value, jitter_value, loss_value, bandwidth_value, corrupt_value, duplicate_value, reorder_value = parsed
    policy.name = name_value
    policy.description = description.strip() or None
    policy.latency_ms = latency_value
    policy.jitter_ms = jitter_value
    policy.packet_loss_percent = loss_value
    policy.bandwidth_mbit = bandwidth_value
    policy.corrupt_percent = corrupt_value
    policy.duplicate_percent = duplicate_value
    policy.reorder_percent = reorder_value
    policy.enabled = enabled == "on"
    db.add(policy)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return Response(f"WAN policy {policy.name} already exists.", status_code=409, media_type="text/plain")
    record_audit(db, actor=identity.username, action="update_wan_policy", resource_type="wan_policy", resource_id=str(policy.id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.post("/routes-wan/policies/{policy_id}/delete", response_model=None)
def delete_policy_from_ui(
    request: Request,
    policy_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    policy = db.get(WanPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="WAN policy not found")
    for route in db.execute(select(Route).where(Route.wan_policy_id == policy.id)).scalars().all():
        route.wan_policy_id = None
        db.add(route)
    db.delete(policy)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_wan_policy", resource_type="wan_policy", resource_id=str(policy_id))
    return RedirectResponse("/routes-wan", status_code=303)


@router.get("/firewall", response_class=HTMLResponse, response_model=None)
def firewall(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "firewall.html", {"identity": identity, **firewall_context(db), "appliance_apply_status": appliance_apply_status(db, "firewall")})


@router.post("/firewall/settings", response_model=None)
def update_firewall_settings(
    request: Request,
    enabled: str | None = Form(None),
    default_input_policy: str = Form("drop"),
    default_forward_policy: str = Form("drop"),
    default_output_policy: str = Form("accept"),
    allow_established: str | None = Form(None),
    allow_loopback: str | None = Form(None),
    allow_icmp: str | None = Form(None),
    log_dropped: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf)
    settings = get_firewall_settings_row(db)
    settings.enabled = enabled == "on"
    settings.default_input_policy = default_input_policy if default_input_policy in FIREWALL_POLICIES else "drop"
    settings.default_forward_policy = default_forward_policy if default_forward_policy in FIREWALL_POLICIES else "drop"
    settings.default_output_policy = default_output_policy if default_output_policy in FIREWALL_POLICIES else "accept"
    settings.allow_established = allow_established == "on"
    settings.allow_loopback = allow_loopback == "on"
    settings.allow_icmp = allow_icmp == "on"
    settings.log_dropped = log_dropped == "on"
    settings.updated_at = utcnow()
    db.add(settings)
    db.commit()
    db.refresh(settings)
    record_audit(db, actor=identity.username, action="update_firewall_settings", resource_type="firewall", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave"):
        context = firewall_context(db)
        return JSONResponse(
            {
                "updated_at": settings.updated_at.isoformat(),
                "settings": firewall_settings_to_dict(settings),
                "enabled": settings.enabled,
                "valid": not context["firewall_validation_errors"],
                "validation_errors": context["firewall_validation_errors"],
                "config_path": settings.config_path,
                "config_preview": context["firewall_config_preview"],
            }
        )
    return RedirectResponse("/firewall", status_code=303)


def firewall_source_group_state_for_db(db: Session) -> dict:
    physical_interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlan_interfaces = db.execute(select(VlanInterface).order_by(VlanInterface.parent_interface, VlanInterface.vlan_id)).scalars().all()
    interface_networks = firewall_interface_networks(physical_interfaces, vlan_interfaces)
    return firewall_source_group_state(setting_value(db, FIREWALL_SOURCE_GROUPS_SETTING_KEY), interface_networks)


def persist_firewall_source_group_state(db: Session, state: dict) -> None:
    set_setting_value(db, FIREWALL_SOURCE_GROUPS_SETTING_KEY, json.dumps(state, indent=2, sort_keys=True))


def _source_group_entries_from_form(form) -> list[str]:
    values = [str(item).strip() for item in form.getlist("group_entries") if str(item).strip()]
    return values or ["any"]


def _firewall_source_group_id(name: str, groups: list[dict]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "group"
    existing = {str(group.get("id", "")) for group in groups}
    candidate = f"custom:{base}"
    index = 2
    while candidate in existing:
        candidate = f"custom:{base}-{index}"
        index += 1
    return candidate


def _normalized_firewall_source_group(group: dict) -> dict:
    entries = [str(item).strip() for item in (group.get("entries") or group.get("sources") or []) if str(item).strip()] or ["any"]
    normalized_entries = []
    for entry in entries:
        if entry.lower() == "any":
            normalized_entries.append("any")
        elif entry.lower().startswith(FIREWALL_SOURCE_GROUP_REFERENCE_PREFIX):
            normalized_entries.append(f"{FIREWALL_SOURCE_GROUP_REFERENCE_PREFIX}{entry[len(FIREWALL_SOURCE_GROUP_REFERENCE_PREFIX):]}")
        else:
            normalized_entries.append(entry)
    return {
        "id": str(group.get("id", "")),
        "name": str(group.get("name", "")).strip() or str(group.get("id", "")),
        "entries": normalized_entries,
        "sources": normalized_entries,
        "description": str(group.get("description") or "Custom firewall group."),
        "builtin": bool(group.get("builtin")),
    }


def _strip_deleted_source_group_references(groups: list[dict], deleted_group_id: str, deleted_group_name: str) -> list[dict]:
    stripped: list[dict] = []
    deleted_ref = f"{FIREWALL_SOURCE_GROUP_REFERENCE_PREFIX}{deleted_group_id}"
    deleted_name_ref = f"@{deleted_group_name}".strip().lower()
    for group in groups:
        entries = []
        for entry in group.get("entries") or group.get("sources") or []:
            normalized_entry = str(entry).strip()
            if normalized_entry == deleted_ref or normalized_entry.lower() == deleted_name_ref:
                continue
            entries.append(normalized_entry)
        stripped.append(_normalized_firewall_source_group({**group, "entries": entries or ["any"]}))
    return stripped


def _firewall_source_group_response(db: Session, updated_at: str) -> JSONResponse:
    context = firewall_context(db)
    return JSONResponse(
        {
            "status": "saved",
            "updated_at": updated_at,
            "valid": not context["firewall_validation_errors"],
            "validation_errors": context["firewall_validation_errors"],
            "config_path": context["firewall_settings"].config_path,
            "config_preview": context["firewall_config_preview"],
        }
    )


@router.post("/firewall/source-groups", response_model=None)
async def update_firewall_source_groups(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    form = await request.form()
    verify_csrf(request, str(form.get("csrf", "")))
    state = firewall_source_group_state_for_db(db)
    groups = [_normalized_firewall_source_group(group) for group in state["groups"]]
    assignments = dict(state["assignments"])
    action = str(form.get("action") or "update")
    group_id = str(form.get("group_id") or "")

    if action == "create":
        name = str(form.get("group_name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Firewall group name is required.")
        groups.append(
            _normalized_firewall_source_group(
                {
                    "id": _firewall_source_group_id(name, groups),
                    "name": name,
                    "entries": _source_group_entries_from_form(form),
                    "description": "Custom firewall group.",
                }
            )
        )
    elif action == "rename":
        name = str(form.get("group_name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Firewall group name is required.")
        updated = False
        for index, group in enumerate(groups):
            if group["id"] != group_id:
                continue
            if group["id"] == FIREWALL_ANY_SOURCE_GROUP_ID:
                raise HTTPException(status_code=422, detail="Any cannot be renamed.")
            groups[index] = _normalized_firewall_source_group({**group, "name": name})
            updated = True
            break
        if not updated:
            raise HTTPException(status_code=404, detail="Firewall group not found.")
    elif action == "delete":
        if group_id == FIREWALL_ANY_SOURCE_GROUP_ID:
            raise HTTPException(status_code=422, detail="Any cannot be removed.")
        deleted_group = next((group for group in groups if group["id"] == group_id), None)
        if not deleted_group:
            raise HTTPException(status_code=404, detail="Firewall group not found.")
        groups = [group for group in groups if group["id"] != group_id]
        assignments = {
            rule_name: (FIREWALL_ANY_SOURCE_GROUP_ID if assigned_group == group_id else assigned_group)
            for rule_name, assigned_group in assignments.items()
        }
        groups = _strip_deleted_source_group_references(groups, group_id, str(deleted_group.get("name") or group_id))
    else:
        updated = False
        for index, group in enumerate(groups):
            if group["id"] != group_id:
                continue
            if group["id"] == FIREWALL_ANY_SOURCE_GROUP_ID:
                groups[index] = _normalized_firewall_source_group({**group, "name": "Any", "entries": ["any"], "builtin": True})
            else:
                groups[index] = _normalized_firewall_source_group(
                    {
                        **group,
                        "name": str(form.get("group_name") or group["name"]).strip(),
                        "entries": _source_group_entries_from_form(form),
                    }
                )
            updated = True
            break
        if not updated:
            raise HTTPException(status_code=404, detail="Firewall group not found.")
    errors = validate_firewall_source_groups(groups)
    if errors:
        return JSONResponse({"status": "error", "errors": errors}, status_code=422)
    updated_state = {"groups": groups, "assignments": assignments}
    persist_firewall_source_group_state(db, updated_state)
    db.commit()
    updated_at = utcnow().isoformat()
    record_audit(
        db,
        actor=identity.username,
        action=f"{action}_firewall_source_group",
        resource_type="firewall",
        resource_id=group_id or "managed-source-groups",
    )
    if request.headers.get("X-LabFoundry-Autosave"):
        return _firewall_source_group_response(db, updated_at)
    return RedirectResponse("/firewall", status_code=303)


@router.post("/firewall/managed-rules/source-group", response_model=None)
def update_managed_firewall_rule_source_group(
    request: Request,
    rule_name: str = Form(...),
    source_group_id: str = Form(...),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> JSONResponse:
    verify_csrf(request, csrf)
    context = firewall_context(db)
    valid_rule_names = {
        row["name"]
        for row in context["firewall_managed_rule_rows"]
        if row["managed_state"] == "generated" and row["source_group_id"]
    }
    valid_group_ids = {group["id"] for group in context["firewall_source_groups"]}
    if rule_name not in valid_rule_names:
        raise HTTPException(status_code=404, detail="Managed firewall rule not found.")
    if source_group_id not in valid_group_ids:
        raise HTTPException(status_code=422, detail="Firewall group does not exist.")
    state = firewall_source_group_state_for_db(db)
    state["assignments"][rule_name] = source_group_id
    persist_firewall_source_group_state(db, state)
    db.commit()
    record_audit(db, actor=identity.username, action="update_managed_firewall_source_group", resource_type="firewall_rule", resource_id=rule_name)
    return JSONResponse({"status": "saved", "updated_at": utcnow().isoformat()})


def _assign_firewall_rule(
    rule: FirewallRule,
    *,
    name: str,
    direction: str,
    action: str,
    protocol: str,
    source: str,
    destination: str,
    destination_port: str,
    interface_name: str,
    priority: int,
    enabled: bool,
    description: str,
) -> FirewallRule:
    rule.name = name.strip()
    rule.direction = direction
    rule.action = action
    rule.protocol = protocol
    rule.source = source.strip() or "any"
    rule.destination = destination.strip() or "any"
    rule.destination_port = destination_port.strip()
    rule.interface_name = interface_name.strip()
    rule.priority = priority
    rule.enabled = enabled
    rule.description = description.strip() or None
    rule.updated_at = utcnow()
    return rule


@router.post("/firewall/rules", response_model=None)
def create_firewall_rule(
    request: Request,
    name: str = Form(...),
    direction: str = Form("input"),
    action: str = Form("accept"),
    protocol: str = Form("tcp"),
    source: str = Form("any"),
    destination: str = Form("any"),
    destination_port: str = Form(""),
    interface_name: str = Form(""),
    priority: int = Form(100),
    enabled: str | None = Form(None),
    description: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    rule = _assign_firewall_rule(
        FirewallRule(),
        name=name,
        direction=direction,
        action=action,
        protocol=protocol,
        source=source,
        destination=destination,
        destination_port=destination_port,
        interface_name=interface_name,
        priority=priority,
        enabled=enabled == "on",
        description=description,
    )
    state = firewall_source_group_state_for_db(db)
    errors = validate_firewall_rule(rule, state["groups"], require_group_addresses=True)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Firewall rule {rule.name} already exists.") from exc
    record_audit(db, actor=identity.username, action="create_firewall_rule", resource_type="firewall_rule", resource_id=str(rule.id))
    return RedirectResponse("/firewall", status_code=303)


@router.post("/firewall/rules/{rule_id}/edit", response_model=None)
def update_firewall_rule(
    rule_id: int,
    request: Request,
    name: str = Form(...),
    direction: str = Form("input"),
    action: str = Form("accept"),
    protocol: str = Form("tcp"),
    source: str = Form("any"),
    destination: str = Form("any"),
    destination_port: str = Form(""),
    interface_name: str = Form(""),
    priority: int = Form(100),
    enabled: str | None = Form(None),
    description: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Firewall rule not found")
    _assign_firewall_rule(
        rule,
        name=name,
        direction=direction,
        action=action,
        protocol=protocol,
        source=source,
        destination=destination,
        destination_port=destination_port,
        interface_name=interface_name,
        priority=priority,
        enabled=enabled == "on",
        description=description,
    )
    state = firewall_source_group_state_for_db(db)
    errors = validate_firewall_rule(rule, state["groups"], require_group_addresses=True)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Firewall rule {rule.name} already exists.") from exc
    record_audit(db, actor=identity.username, action="update_firewall_rule", resource_type="firewall_rule", resource_id=str(rule.id))
    return RedirectResponse("/firewall", status_code=303)


@router.post("/firewall/rules/{rule_id}/delete", response_model=None)
def delete_firewall_rule(
    rule_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Firewall rule not found")
    db.delete(rule)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_firewall_rule", resource_type="firewall_rule", resource_id=str(rule_id))
    return RedirectResponse("/firewall", status_code=303)


@router.get("/physical-interfaces", response_class=HTMLResponse, response_model=None)
def physical_interfaces_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "physical_interfaces.html", {"identity": identity, **network_context(db), "appliance_apply_status": appliance_apply_status(db, "network")})


@router.post("/physical-interfaces/refresh", response_model=None)
def refresh_physical_interfaces_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    _interfaces, discovered_count = sync_host_physical_interfaces(db)
    record_audit(
        db,
        actor=identity.username,
        action="refresh_physical_interface_inventory",
        resource_type="interface",
        detail=f"{discovered_count} host interface{'s' if discovered_count != 1 else ''} discovered",
    )
    return RedirectResponse("/physical-interfaces", status_code=303)


@router.post("/physical-interfaces/{interface_id}/edit", response_model=None)
def edit_physical_interface_from_ui(
    request: Request,
    interface_id: int,
    role: str = Form("unused"),
    mode: str = Form("unused"),
    ip_cidr: str = Form(""),
    mtu: int = Form(1500),
    admin_state: str = Form("up"),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    interface = db.get(PhysicalInterface, interface_id)
    if not interface:
        raise HTTPException(status_code=404, detail="Physical interface not found")
    new_mode = normalize_interface_mode(mode)
    vlan_count = db.scalar(select(func.count()).select_from(VlanInterface).where(VlanInterface.parent_interface == interface.name)) or 0
    if new_mode != "trunk" and vlan_count:
        return Response(
            f"{interface.name} is the parent of {vlan_count} VLAN interface{'s' if vlan_count != 1 else ''}. "
            "Move or delete those VLANs before changing the link type.",
            status_code=409,
            media_type="text/plain",
        )
    interface.role = role.strip()
    interface.mode = new_mode
    interface.ip_cidr = ip_cidr.strip() or None
    interface.mtu = mtu
    interface.admin_state = admin_state.strip()
    interface.desired_state_source = "user"
    db.commit()
    record_audit(db, actor=identity.username, action="update_physical_interface", resource_type="interface", resource_id=interface.name)
    return RedirectResponse("/physical-interfaces", status_code=303)


@router.get("/vlan-interfaces", response_class=HTMLResponse, response_model=None)
def vlan_interfaces_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "vlan_interfaces.html", {"identity": identity, **network_context(db), "appliance_apply_status": appliance_apply_status(db, "network")})


@router.post("/vlan-interfaces", response_model=None)
def create_vlan_interface_from_ui(
    request: Request,
    parent_interface: str = Form(...),
    vlan_id: str = Form(""),
    ip_cidr: str = Form(""),
    mtu: int = Form(1500),
    role: str = Form("access"),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse | JSONResponse:
    verify_csrf(request, csrf)
    parsed = validate_vlan_form_values(parent_interface, vlan_id, ip_cidr, db)
    if isinstance(parsed, Response):
        return parsed
    parent_name, parsed_vlan_id, ip_value = parsed
    vlan = VlanInterface(
        name=f"{parent_name}.{parsed_vlan_id}",
        parent_interface=parent_name,
        vlan_id=parsed_vlan_id,
        ip_cidr=ip_value,
        mtu=mtu,
        role=role.strip(),
        enabled=enabled == "on",
    )
    db.add(vlan)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "vlan_interfaces.html",
            {"identity": identity, **network_context(db), "form_error": f"VLAN {vlan.name} already exists."},
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="create_vlan_interface", resource_type="vlan", resource_id=str(vlan.id))
    return RedirectResponse("/vlan-interfaces", status_code=303)


@router.post("/vlan-interfaces/{vlan_id}/edit", response_model=None)
def edit_vlan_interface_from_ui(
    request: Request,
    vlan_id: int,
    parent_interface: str = Form(...),
    vlan_id_value: str = Form("", alias="vlan_id"),
    ip_cidr: str = Form(""),
    mtu: int = Form(1500),
    role: str = Form("access"),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse | JSONResponse:
    verify_csrf(request, csrf)
    vlan = db.get(VlanInterface, vlan_id)
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN interface not found")
    parsed = validate_vlan_form_values(parent_interface, vlan_id_value, ip_cidr, db)
    if isinstance(parsed, Response):
        return parsed
    parent_name, parsed_vlan_id, ip_value = parsed
    vlan.parent_interface = parent_name
    vlan.vlan_id = parsed_vlan_id
    vlan.name = f"{vlan.parent_interface}.{vlan.vlan_id}"
    vlan.ip_cidr = ip_value
    vlan.mtu = mtu
    vlan.role = role.strip()
    vlan.enabled = enabled == "on"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "vlan_interfaces.html",
            {"identity": identity, **network_context(db), "form_error": f"VLAN {vlan.name} already exists."},
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="update_vlan_interface", resource_type="vlan", resource_id=str(vlan.id))
    return RedirectResponse("/vlan-interfaces", status_code=303)


@router.post("/vlan-interfaces/{vlan_id}/delete", response_model=None)
def delete_vlan_interface_from_ui(
    request: Request,
    vlan_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    vlan = db.get(VlanInterface, vlan_id)
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN interface not found")
    db.delete(vlan)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_vlan_interface", resource_type="vlan", resource_id=str(vlan_id))
    return RedirectResponse("/vlan-interfaces", status_code=303)


@router.get("/dns", response_class=HTMLResponse, response_model=None)
def dns_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "dns.html", {"identity": identity, **dnsmasq_context(db), "appliance_apply_status": appliance_apply_status(db, "dnsmasq")})


@router.post("/dns/settings", response_model=None)
def update_dns_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    listen_interfaces: list[str] = Form(default_factory=list),
    listen_addresses: list[str] = Form(default_factory=list),
    listen_interfaces_present: str | None = Form(None),
    listen_addresses_present: str | None = Form(None),
    domains: str | None = Form(None),
    upstream_servers: str = Form(""),
    conditional_forwarders: str = Form(""),
    cache_size: int = Form(1000),
    expand_hosts: str | None = Form(None),
    authoritative: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_dns_settings_row(db)
    available_options = service_bind_options(db)
    available_names = {item["name"] for item in available_options}
    selected_interfaces = [interface.strip() for interface in listen_interfaces if interface.strip()]
    if available_names:
        selected_interfaces = [interface for interface in selected_interfaces if interface in available_names]
    if not selected_interfaces:
        selected_interfaces = [interface for interface in split_interfaces(settings.listen_interface) if interface in available_names]
    if not selected_interfaces:
        selected_interfaces = [available_options[0]["name"]] if available_options else []
    selected_addresses = split_addresses(join_addresses(listen_addresses))
    if listen_addresses_present is None and not selected_addresses:
        selected_addresses = split_addresses(settings.listen_address)
    settings.enabled = enabled == "on"
    settings.listen_interface = join_interfaces(selected_interfaces)
    settings.listen_address = join_addresses(selected_addresses) or None
    if domains is not None:
        settings.domain = join_domains(split_domains(domains))
    settings.upstream_servers = join_servers(split_servers(upstream_servers))
    set_setting_value(
        db,
        DNS_CONDITIONAL_FORWARDERS_SETTING_KEY,
        join_conditional_forwarders(split_conditional_forwarders(conditional_forwarders)),
    )
    settings.cache_size = cache_size
    settings.expand_hosts = expand_hosts == "on"
    settings.authoritative = authoritative == "on"
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_dns_settings", resource_type="dns", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = dnsmasq_context(db)
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": settings.updated_at.isoformat(),
                "listen_interfaces": split_interfaces(settings.listen_interface),
                "listen_addresses": split_addresses(settings.listen_address),
                "valid": not context["validation_errors"],
                "validation_errors": context["validation_errors"],
                "validation_warnings": context["dns_warnings"],
                "config_path": context["dns_settings"].config_path,
                "config_preview": context["config_preview"],
            }
        )
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/zones", response_model=None)
def create_dns_zone_from_ui(
    request: Request,
    domain: str = Form(...),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    settings = get_dns_settings_row(db)
    existing_domains = dns_domains_for_settings(settings)
    new_domains = split_domains(domain)
    if len(new_domains) != 1:
        return render(
            request,
            "dns.html",
            {"identity": identity, **dnsmasq_context(db), "form_error": "Enter one valid domain name."},
            status_code=422,
        )
    new_domain = new_domains[0]
    if new_domain in existing_domains:
        return render(
            request,
            "dns.html",
            {"identity": identity, **dnsmasq_context(db), "form_error": f"DNS domain {new_domain} already exists."},
            status_code=409,
        )
    save_dns_domains(settings, [*existing_domains, new_domain])
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="create_dns_zone", resource_type="dns_zone", resource_id=new_domain)
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/zones/delete", response_model=None)
def delete_dns_zone_from_ui(
    request: Request,
    domain: str = Form(...),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    settings = get_dns_settings_row(db)
    existing_domains = dns_domains_for_settings(settings)
    normalized_domain = split_domains(domain)
    if len(normalized_domain) != 1 or normalized_domain[0] not in existing_domains:
        return render(
            request,
            "dns.html",
            {"identity": identity, **dnsmasq_context(db), "form_error": "DNS domain was not found."},
            status_code=404,
        )
    deleted_domain = normalized_domain[0]
    if len(existing_domains) == 1:
        return render(
            request,
            "dns.html",
            {"identity": identity, **dnsmasq_context(db), "form_error": "At least one DNS domain must remain managed."},
            status_code=422,
        )
    deleted_records = records_for_domain(db, deleted_domain)
    for record in deleted_records:
        db.delete(record)
    save_dns_domains(settings, [item for item in existing_domains if item != deleted_domain])
    settings.updated_at = utcnow()
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="delete_dns_zone",
        resource_type="dns_zone",
        resource_id=deleted_domain,
        detail=f"Deleted {len(deleted_records)} scoped DNS records.",
    )
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/records", response_model=None)
def create_dns_record_from_ui(
    request: Request,
    hostname: str = Form(...),
    domain: str = Form(""),
    record_type: str = Form("A"),
    address: str = Form(...),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    hostname = normalize_dns_hostname(hostname, domain)
    record_type = record_type.strip().upper()
    address = address.strip()
    validation_errors = validate_dns_record(hostname, record_type, address)
    if validation_errors:
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": " ".join(validation_errors),
            },
            status_code=422,
        )
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.hostname == hostname,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing:
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DNS {record_type} record already exists for {hostname}.",
            },
            status_code=409,
        )
    record = DnsRecord(
        hostname=hostname,
        record_type=record_type,
        address=address,
        description=description or None,
        enabled=enabled == "on",
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DNS {record_type} record already exists for {hostname}.",
            },
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="create_dns_record", resource_type="dns_record", resource_id=str(record.id))
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/records/{record_id}/delete", response_model=None)
def delete_dns_record_from_ui(
    request: Request,
    record_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    record = db.get(DnsRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="DNS record not found")
    db.delete(record)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dns_record", resource_type="dns_record", resource_id=str(record_id))
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/records/{record_id}/edit", response_model=None)
def edit_dns_record_from_ui(
    request: Request,
    record_id: int,
    hostname: str = Form(...),
    domain: str = Form(""),
    record_type: str = Form("A"),
    address: str = Form(...),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    record = db.get(DnsRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="DNS record not found")
    hostname = normalize_dns_hostname(hostname, domain)
    record_type = record_type.strip().upper()
    address = address.strip()
    validation_errors = validate_dns_record(hostname, record_type, address)
    if validation_errors:
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": " ".join(validation_errors),
            },
            status_code=422,
        )
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.id != record_id,
            DnsRecord.hostname == hostname,
            DnsRecord.record_type == record_type,
        )
    ).scalar_one_or_none()
    if existing:
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DNS {record_type} record already exists for {hostname}.",
            },
            status_code=409,
        )
    record.hostname = hostname
    record.record_type = record_type
    record.address = address
    record.description = description or None
    record.enabled = enabled == "on"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DNS {record_type} record already exists for {hostname}.",
            },
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="update_dns_record", resource_type="dns_record", resource_id=str(record.id))
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/records/import", response_model=None)
def import_dns_hosts_from_ui(
    request: Request,
    hosts_text: str = Form(...),
    domain: str = Form(""),
    replace_existing: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    parsed_records, errors = parse_hosts_records(hosts_text)
    if errors:
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "bulk_error": " ".join(errors),
                "hosts_editor_text": hosts_text,
            },
            status_code=422,
    )
    replace = replace_existing == "on"
    scoped_domain = domain.strip().strip(".").lower()
    if replace:
        records_to_delete = records_for_domain(db, scoped_domain) if scoped_domain else db.execute(select(DnsRecord)).scalars().all()
        for record in records_to_delete:
            db.delete(record)
        db.flush()
    for item in parsed_records:
        if scoped_domain:
            item["hostname"] = normalize_dns_hostname(str(item["hostname"]), scoped_domain)
        existing = None
        if not replace:
            existing = db.execute(
                select(DnsRecord).where(
                    DnsRecord.hostname == item["hostname"],
                    DnsRecord.record_type == item["record_type"],
                )
            ).scalar_one_or_none()
        if existing:
            existing.address = str(item["address"])
            existing.description = str(item["description"] or "")
            existing.enabled = bool(item["enabled"])
        else:
            db.add(DnsRecord(**item))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "bulk_error": "Imported hosts contain duplicate DNS records.",
                "hosts_editor_text": hosts_text,
            },
            status_code=409,
        )
    record_audit(
        db,
        actor=identity.username,
        action="import_dns_hosts_file",
        resource_type="dns_record",
        detail=f"Imported {len(parsed_records)} records; replace_existing={replace}",
    )
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/zones/import", response_model=None)
def import_dns_zone_from_ui(
    request: Request,
    domain: str = Form(...),
    zone_text: str = Form(...),
    replace_existing: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    scoped_domain = domain.strip().strip(".").lower()
    parsed_records, errors = parse_zone_records(zone_text, scoped_domain)
    if errors:
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "bulk_error": " ".join(errors),
                "active_zone_import_domain": scoped_domain,
                "zone_editor_text": zone_text,
            },
            status_code=422,
        )
    replace = replace_existing == "on"
    if replace:
        for record in records_for_domain(db, scoped_domain):
            db.delete(record)
        db.flush()
    for item in parsed_records:
        existing = None
        if not replace:
            existing = db.execute(
                select(DnsRecord).where(
                    DnsRecord.hostname == item["hostname"],
                    DnsRecord.record_type == item["record_type"],
                )
            ).scalar_one_or_none()
        if existing:
            existing.address = str(item["address"])
            existing.description = str(item["description"] or "")
            existing.enabled = bool(item["enabled"])
        else:
            db.add(DnsRecord(**item))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "bulk_error": "Zone file contains duplicate DNS records.",
                "active_zone_import_domain": scoped_domain,
                "zone_editor_text": zone_text,
            },
            status_code=409,
        )
    record_audit(
        db,
        actor=identity.username,
        action="import_dns_zone_file",
        resource_type="dns_zone",
        resource_id=scoped_domain,
        detail=f"Imported {len(parsed_records)} records; replace_existing={replace}",
    )
    return RedirectResponse("/dns", status_code=303)


@router.get("/dhcp", response_class=HTMLResponse, response_model=None)
def dhcp_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "dhcp.html", {"identity": identity, **dnsmasq_context(db), "appliance_apply_status": appliance_apply_status(db, "dnsmasq")})


@router.post("/dhcp/settings", response_model=None)
def update_dhcp_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    interface_name: str | None = Form(None),
    site_address: str | None = Form(None),
    prefix_length: str | None = Form(None),
    range_start: str | None = Form(None),
    range_end: str | None = Form(None),
    lease_time: str | None = Form(None),
    domain_name: str | None = Form(None),
    dns_server: str | None = Form(None),
    authoritative: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_dhcp_settings_row(db)
    settings.enabled = enabled == "on"
    if interface_name is not None:
        settings.interface_name = interface_name.strip()
    if site_address is not None:
        settings.site_address = site_address.strip()
    prefix_text = (prefix_length or "").strip()
    if prefix_text:
        try:
            settings.prefix_length = int(prefix_text)
        except ValueError:
            return JSONResponse({"status": "error", "error": "DHCP prefix length must be an integer."}, status_code=422)
    if range_start is not None:
        settings.range_start = range_start.strip()
    if range_end is not None:
        settings.range_end = range_end.strip()
    if lease_time is not None:
        settings.lease_time = lease_time.strip() or settings.lease_time
    if domain_name is not None:
        settings.domain_name = domain_name.strip() or settings.domain_name
    if dns_server is not None:
        settings.dns_server = dns_server.strip()
    settings.authoritative = authoritative == "on"
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_dhcp_settings", resource_type="dhcp", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": settings.updated_at.isoformat(),
            }
        )
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/scopes", response_model=None)
def create_dhcp_scope_from_ui(
    request: Request,
    name: str = Form(...),
    interface_name: str = Form(...),
    site_address: str = Form(...),
    prefix_length: int = Form(...),
    range_start: str = Form(...),
    range_end: str = Form(...),
    lease_time: str = Form(...),
    domain_name: str = Form(...),
    dns_server: str = Form(...),
    ntp_server: str = Form(""),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    scope = DhcpScope(
        name=name.strip(),
        interface_name=interface_name.strip(),
        site_address=site_address.strip(),
        prefix_length=prefix_length,
        range_start=range_start.strip(),
        range_end=range_end.strip(),
        lease_time=lease_time.strip(),
        domain_name=domain_name.strip(),
        dns_server=dns_server.strip(),
        ntp_server=ntp_server.strip(),
        description=description or None,
        enabled=enabled == "on",
    )
    db.add(scope)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dhcp.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DHCP IP zone {name} already exists.",
            },
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="create_dhcp_scope", resource_type="dhcp_scope", resource_id=str(scope.id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/scopes/{scope_id}/edit", response_model=None)
def edit_dhcp_scope_from_ui(
    request: Request,
    scope_id: int,
    name: str = Form(...),
    interface_name: str = Form(...),
    site_address: str = Form(...),
    prefix_length: int = Form(...),
    range_start: str = Form(...),
    range_end: str = Form(...),
    lease_time: str = Form(...),
    domain_name: str = Form(...),
    dns_server: str = Form(...),
    ntp_server: str = Form(""),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    scope = db.get(DhcpScope, scope_id)
    if not scope:
        raise HTTPException(status_code=404, detail="DHCP IP zone not found")
    scope.name = name.strip()
    scope.interface_name = interface_name.strip()
    scope.site_address = site_address.strip()
    scope.prefix_length = prefix_length
    scope.range_start = range_start.strip()
    scope.range_end = range_end.strip()
    scope.lease_time = lease_time.strip()
    scope.domain_name = domain_name.strip()
    scope.dns_server = dns_server.strip()
    scope.ntp_server = ntp_server.strip()
    scope.description = description or None
    scope.enabled = enabled == "on"
    scope.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dhcp.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DHCP IP zone {name} already exists.",
            },
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="update_dhcp_scope", resource_type="dhcp_scope", resource_id=str(scope.id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/scopes/{scope_id}/delete", response_model=None)
def delete_dhcp_scope_from_ui(
    request: Request,
    scope_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    scope = db.get(DhcpScope, scope_id)
    if not scope:
        raise HTTPException(status_code=404, detail="DHCP IP zone not found")
    for option in db.execute(select(DhcpOption).where(DhcpOption.scope_id == scope_id)).scalars().all():
        db.delete(option)
    db.delete(scope)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dhcp_scope", resource_type="dhcp_scope", resource_id=str(scope_id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/options", response_model=None)
def create_dhcp_option_from_ui(
    request: Request,
    scope_id: str = Form("__global__"),
    option_code: str = Form(...),
    value: str = Form(...),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    option = DhcpOption(
        scope_id=parse_dhcp_option_scope_id(scope_id),
        option_code=option_code.strip(),
        value=value.strip(),
        description=description or None,
        enabled=enabled == "on",
    )
    db.add(option)
    db.commit()
    record_audit(db, actor=identity.username, action="create_dhcp_option", resource_type="dhcp_option", resource_id=str(option.id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/options/{option_id}/edit", response_model=None)
def edit_dhcp_option_from_ui(
    request: Request,
    option_id: int,
    scope_id: str = Form("__global__"),
    option_code: str = Form(...),
    value: str = Form(...),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    option = db.get(DhcpOption, option_id)
    if not option:
        raise HTTPException(status_code=404, detail="DHCP option not found")
    option.scope_id = parse_dhcp_option_scope_id(scope_id)
    option.option_code = option_code.strip()
    option.value = value.strip()
    option.description = description or None
    option.enabled = enabled == "on"
    option.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_dhcp_option", resource_type="dhcp_option", resource_id=str(option.id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/options/{option_id}/delete", response_model=None)
def delete_dhcp_option_from_ui(
    request: Request,
    option_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    option = db.get(DhcpOption, option_id)
    if not option:
        raise HTTPException(status_code=404, detail="DHCP option not found")
    db.delete(option)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dhcp_option", resource_type="dhcp_option", resource_id=str(option_id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/reservations", response_model=None)
def create_dhcp_reservation_from_ui(
    request: Request,
    hostname: str = Form(...),
    mac_address: str = Form(...),
    ip_address: str = Form(...),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    reservation = DhcpReservation(
        hostname=hostname.strip(),
        mac_address=mac_address.strip(),
        ip_address=ip_address.strip(),
        description=description or None,
        enabled=enabled == "on",
    )
    db.add(reservation)
    try:
        db.flush()
        ensure_dns_for_dhcp_reservation(db, reservation, identity.username)
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dhcp.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DHCP reservation already exists for MAC address {mac_address}.",
            },
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="create_dhcp_reservation", resource_type="dhcp_reservation", resource_id=str(reservation.id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/reservations/{reservation_id}/edit", response_model=None)
def edit_dhcp_reservation_from_ui(
    request: Request,
    reservation_id: int,
    hostname: str = Form(...),
    mac_address: str = Form(...),
    ip_address: str = Form(...),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    reservation = db.get(DhcpReservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="DHCP reservation not found")
    reservation.hostname = hostname.strip()
    reservation.mac_address = mac_address.strip()
    reservation.ip_address = ip_address.strip()
    reservation.description = description or None
    reservation.enabled = enabled == "on"
    try:
        ensure_dns_for_dhcp_reservation(db, reservation, identity.username)
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "dhcp.html",
            {
                "identity": identity,
                **dnsmasq_context(db),
                "form_error": f"DHCP reservation already exists for MAC address {mac_address}.",
            },
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="update_dhcp_reservation", resource_type="dhcp_reservation", resource_id=str(reservation.id))
    return RedirectResponse("/dhcp", status_code=303)


@router.post("/dhcp/reservations/{reservation_id}/delete", response_model=None)
def delete_dhcp_reservation_from_ui(
    request: Request,
    reservation_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    reservation = db.get(DhcpReservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="DHCP reservation not found")
    db.delete(reservation)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dhcp_reservation", resource_type="dhcp_reservation", resource_id=str(reservation_id))
    return RedirectResponse("/dhcp", status_code=303)


@router.get("/certificate-authority", response_class=HTMLResponse, response_model=None)
def certificate_authority_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "certificate_authority.html", {"identity": identity, **ca_context(db), "appliance_apply_status": appliance_apply_status(db, "ca")})


@router.get("/certificate-authority/downloads/root-ca.pem", response_model=None)
def download_root_ca(
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    ensure_ca_state(db)
    settings = get_ca_settings_row(db)
    if not settings.root_certificate_pem:
        changed = ensure_root_ca_material(settings)
        if changed:
            db.commit()
    record_audit(db, actor=identity.username, action="download_ca_root_certificate", resource_type="ca", resource_id=str(settings.id))
    return Response(
        settings.root_certificate_pem.encode("utf-8"),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="labfoundry-root-ca.pem"'},
    )


@router.get("/certificate-authority/downloads/ca-bundle.pem", response_model=None)
def download_ca_bundle(
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    ensure_ca_state(db)
    settings = get_ca_settings_row(db)
    if not settings.root_certificate_pem:
        changed = ensure_root_ca_material(settings)
        if changed:
            db.commit()
    record_audit(db, actor=identity.username, action="download_ca_bundle", resource_type="ca", resource_id=str(settings.id))
    return Response(
        settings.root_certificate_pem.encode("utf-8"),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="labfoundry-ca-bundle.pem"'},
    )


def get_exportable_ca_certificate(db: Session, certificate_id: int) -> CaCertificate:
    ensure_ca_state(db)
    certificate = db.get(CaCertificate, certificate_id)
    if not certificate:
        raise HTTPException(status_code=404, detail="CA certificate not found")
    if certificate.status != "issued" or not certificate.certificate_pem:
        raise HTTPException(status_code=404, detail="CA certificate has not been issued")
    return certificate


@router.get("/certificate-authority/certificates/{certificate_id}/downloads/certificate.pem", response_model=None)
def download_ca_certificate(
    certificate_id: int,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    certificate = get_exportable_ca_certificate(db, certificate_id)
    record_audit(db, actor=identity.username, action="download_ca_certificate", resource_type="ca_certificate", resource_id=str(certificate.id))
    filename = f"{safe_certificate_name(certificate.common_name)}.crt"
    return Response(
        certificate.certificate_pem.encode("utf-8"),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/certificate-authority/certificates/{certificate_id}/downloads/chain.pem", response_model=None)
def download_ca_certificate_chain(
    certificate_id: int,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    certificate = get_exportable_ca_certificate(db, certificate_id)
    chain = certificate.chain_pem or certificate.certificate_pem
    record_audit(db, actor=identity.username, action="download_ca_certificate_chain", resource_type="ca_certificate", resource_id=str(certificate.id))
    filename = f"{safe_certificate_name(certificate.common_name)}-chain.pem"
    return Response(
        chain.encode("utf-8"),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/certificate-authority/certificates/{certificate_id}/downloads/private-key.pem", response_model=None)
def download_ca_certificate_private_key(
    certificate_id: int,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    certificate = get_exportable_ca_certificate(db, certificate_id)
    if not certificate.private_key_encrypted:
        raise HTTPException(status_code=404, detail="No LabFoundry-generated private key is available for this certificate")
    private_key = decrypt_secret(certificate.private_key_encrypted)
    record_audit(db, actor=identity.username, action="download_ca_certificate_private_key", resource_type="ca_certificate", resource_id=str(certificate.id))
    filename = f"{safe_certificate_name(certificate.common_name)}.key"
    return Response(
        private_key.encode("utf-8"),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/certificate-authority/settings", response_model=None)
def update_ca_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    listen_interfaces: list[str] = Form(default_factory=list),
    listen_addresses: list[str] = Form(default_factory=list),
    listen_interfaces_present: str | None = Form(None),
    listen_addresses_present: str | None = Form(None),
    root_common_name: str = Form(...),
    organization: str = Form(...),
    organizational_unit: str = Form(""),
    country: str = Form("US"),
    state: str = Form(""),
    locality: str = Form(""),
    key_algorithm: str = Form("RSA"),
    key_size: int = Form(4096),
    digest_algorithm: str = Form("sha256"),
    root_valid_days: int = Form(3650),
    intermediate_valid_days: int = Form(1825),
    publish_crl: str | None = Form(None),
    ocsp_enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_ca_settings_row(db)
    selected_interfaces, selected_addresses = resolve_service_bind_targets(
        db,
        listen_interfaces,
        listen_addresses,
        current_interface=settings.listen_interface,
        current_address=settings.listen_address,
        listen_interfaces_present=listen_interfaces_present,
        listen_addresses_present=listen_addresses_present,
    )
    settings.enabled = enabled == "on"
    settings.listen_interface = selected_interfaces
    settings.listen_address = selected_addresses
    settings.root_common_name = root_common_name.strip()
    settings.organization = organization.strip()
    settings.organizational_unit = organizational_unit.strip()
    settings.country = country.strip().upper()
    settings.state = state.strip()
    settings.locality = locality.strip()
    settings.key_algorithm = key_algorithm.strip().upper()
    settings.key_size = key_size
    settings.digest_algorithm = digest_algorithm.strip().lower()
    settings.root_valid_days = root_valid_days
    settings.intermediate_valid_days = intermediate_valid_days
    settings.publish_crl = publish_crl == "on"
    settings.ocsp_enabled = ocsp_enabled == "on"
    settings.storage_path = settings.storage_path.strip() or "/etc/labfoundry/ca"
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_ca_settings", resource_type="ca", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        db.refresh(settings)
        context = ca_context(db)
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": settings.updated_at.isoformat(),
                "enabled": settings.enabled,
                "listen_interfaces": split_interfaces(settings.listen_interface),
                "listen_addresses": split_addresses(settings.listen_address),
                "validation_errors": context["ca_validation_errors"],
                "config_preview": context["ca_config_preview"],
                "apply_payload": context["ca_apply_payload"],
            }
        )
    return RedirectResponse("/certificate-authority", status_code=303)


def parse_ca_profile_id(raw_value: str | int | None) -> int | None:
    if raw_value in {None, "", "None", "unassigned"}:
        return None
    return int(raw_value)


@router.post("/certificate-authority/profiles", response_model=None)
def create_ca_profile_from_ui(
    request: Request,
    name: str = Form(...),
    certificate_type: str = Form("server"),
    validity_days: int = Form(825),
    key_algorithm: str = Form("RSA"),
    key_size: int = Form(2048),
    key_usage: str = Form("digitalSignature,keyEncipherment"),
    extended_key_usage: str = Form("serverAuth"),
    san_required: str | None = Form(None),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    profile = CaProfile(
        name=name.strip(),
        certificate_type=certificate_type.strip(),
        validity_days=validity_days,
        key_algorithm=key_algorithm.strip().upper(),
        key_size=key_size,
        key_usage=key_usage.strip(),
        extended_key_usage=extended_key_usage.strip(),
        san_required=san_required == "on",
        description=description or None,
        enabled=enabled == "on",
    )
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "certificate_authority.html",
            {"identity": identity, **ca_context(db), "form_error": f"CA profile {name} already exists."},
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="create_ca_profile", resource_type="ca_profile", resource_id=str(profile.id))
    return RedirectResponse("/certificate-authority", status_code=303)


@router.post("/certificate-authority/profiles/{profile_id}/edit", response_model=None)
def edit_ca_profile_from_ui(
    request: Request,
    profile_id: int,
    name: str = Form(...),
    certificate_type: str = Form("server"),
    validity_days: int = Form(825),
    key_algorithm: str = Form("RSA"),
    key_size: int = Form(2048),
    key_usage: str = Form("digitalSignature,keyEncipherment"),
    extended_key_usage: str = Form("serverAuth"),
    san_required: str | None = Form(None),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    profile = db.get(CaProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="CA profile not found")
    profile.name = name.strip()
    profile.certificate_type = certificate_type.strip()
    profile.validity_days = validity_days
    profile.key_algorithm = key_algorithm.strip().upper()
    profile.key_size = key_size
    profile.key_usage = key_usage.strip()
    profile.extended_key_usage = extended_key_usage.strip()
    profile.san_required = san_required == "on"
    profile.description = description or None
    profile.enabled = enabled == "on"
    profile.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "certificate_authority.html",
            {"identity": identity, **ca_context(db), "form_error": f"CA profile {name} already exists."},
            status_code=409,
        )
    record_audit(db, actor=identity.username, action="update_ca_profile", resource_type="ca_profile", resource_id=str(profile.id))
    return RedirectResponse("/certificate-authority", status_code=303)


@router.post("/certificate-authority/profiles/{profile_id}/delete", response_model=None)
def delete_ca_profile_from_ui(
    request: Request,
    profile_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    profile = db.get(CaProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="CA profile not found")
    for certificate in db.execute(select(CaCertificate).where(CaCertificate.profile_id == profile_id)).scalars().all():
        certificate.profile_id = None
    db.delete(profile)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_ca_profile", resource_type="ca_profile", resource_id=str(profile_id))
    return RedirectResponse("/certificate-authority", status_code=303)


@router.post("/certificate-authority/certificates", response_model=None)
def create_ca_certificate_from_ui(
    request: Request,
    common_name: str = Form(...),
    profile_id: str = Form(""),
    subject_alt_names: str = Form(""),
    ip_addresses: str = Form(""),
    status: str = Form("planned"),
    serial_number: str = Form(""),
    description: str = Form(""),
    csr_text: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    certificate = CaCertificate(
        common_name=common_name.strip(),
        profile_id=parse_ca_profile_id(profile_id),
        subject_alt_names=join_multiline(split_multiline(subject_alt_names)),
        ip_addresses=join_multiline(split_multiline(ip_addresses)),
        status=status.strip() or "planned",
        serial_number=serial_number.strip() or None,
        description=description or None,
        csr_text=csr_text.strip() or None,
        enabled=enabled == "on",
    )
    db.add(certificate)
    db.commit()
    record_audit(db, actor=identity.username, action="create_ca_certificate_request", resource_type="ca_certificate", resource_id=str(certificate.id))
    return RedirectResponse("/certificate-authority", status_code=303)


@router.post("/certificate-authority/certificates/{certificate_id}/edit", response_model=None)
def edit_ca_certificate_from_ui(
    request: Request,
    certificate_id: int,
    common_name: str = Form(...),
    profile_id: str = Form(""),
    subject_alt_names: str = Form(""),
    ip_addresses: str = Form(""),
    status: str = Form("planned"),
    serial_number: str = Form(""),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    certificate = db.get(CaCertificate, certificate_id)
    if not certificate:
        raise HTTPException(status_code=404, detail="CA certificate request not found")
    certificate.common_name = common_name.strip()
    certificate.profile_id = parse_ca_profile_id(profile_id)
    certificate.subject_alt_names = join_multiline(split_multiline(subject_alt_names))
    certificate.ip_addresses = join_multiline(split_multiline(ip_addresses))
    certificate.status = status.strip() or "planned"
    certificate.serial_number = serial_number.strip() or None
    certificate.description = description or None
    certificate.enabled = enabled == "on"
    db.commit()
    record_audit(db, actor=identity.username, action="update_ca_certificate_request", resource_type="ca_certificate", resource_id=str(certificate.id))
    return RedirectResponse("/certificate-authority", status_code=303)


@router.post("/certificate-authority/certificates/{certificate_id}/delete", response_model=None)
def delete_ca_certificate_from_ui(
    request: Request,
    certificate_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    certificate = db.get(CaCertificate, certificate_id)
    if not certificate:
        raise HTTPException(status_code=404, detail="CA certificate request not found")
    db.delete(certificate)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_ca_certificate_request", resource_type="ca_certificate", resource_id=str(certificate_id))
    return RedirectResponse("/certificate-authority", status_code=303)


@router.get("/kms", response_class=HTMLResponse, response_model=None)
def kms_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "kms.html", {"identity": identity, **kms_context(db), "appliance_apply_status": appliance_apply_status(db, "kms")})


@router.post("/kms/settings", response_model=None)
def update_kms_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    backend: str = Form("pykmip"),
    listen_interfaces: list[str] = Form(default_factory=list),
    listen_addresses: list[str] = Form(default_factory=list),
    listen_interfaces_present: str | None = Form(None),
    listen_addresses_present: str | None = Form(None),
    listen_interface: str = Form(""),
    listen_address: str = Form(""),
    port: int = Form(5696),
    hostname: str = Form("kms.labfoundry.internal"),
    server_certificate: str | None = Form(None),
    require_client_cert: str | None = Form(None),
    allow_register: str | None = Form(None),
    allow_destroy: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_kms_settings_row(db)
    previous_hostname = settings.hostname
    selected_interfaces, selected_addresses = resolve_service_bind_targets(
        db,
        [*listen_interfaces, listen_interface],
        [*listen_addresses, listen_address],
        current_interface=settings.listen_interface,
        current_address=settings.listen_address,
        listen_interfaces_present=listen_interfaces_present,
        listen_addresses_present=listen_addresses_present,
    )
    settings.enabled = enabled == "on"
    settings.backend = backend.strip().lower() or "pykmip"
    settings.listen_interface = selected_interfaces
    settings.listen_address = selected_addresses
    settings.port = port
    settings.hostname = normalize_dns_hostname(hostname.strip() or "kms.labfoundry.internal")
    settings.server_certificate = settings.hostname
    settings.ca_certificate_path = settings.ca_certificate_path.strip() or "/etc/labfoundry/ca/root.crt"
    settings.database_path = KMS_DEFAULT_DATABASE_PATH
    settings.config_path = KMS_DEFAULT_CONFIG_PATH
    settings.require_client_cert = require_client_cert == "on"
    settings.allow_register = allow_register == "on"
    settings.allow_destroy = allow_destroy == "on"
    settings.updated_at = utcnow()
    if settings.enabled:
        ensure_dns_for_kms(db, settings, identity.username, previous_hostname=previous_hostname)
        ensure_ca_state(db)
    db.commit()
    record_audit(db, actor=identity.username, action="update_kms_settings", resource_type="kms", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = kms_context(db)
        saved_settings = context["kms_settings"]
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": saved_settings.updated_at.isoformat(),
                "enabled": saved_settings.enabled,
                "listen_interface": primary_listen_interface(saved_settings.listen_interface),
                "listen_address": primary_listen_address(saved_settings.listen_address),
                "listen_interfaces": split_interfaces(saved_settings.listen_interface),
                "listen_addresses": split_addresses(saved_settings.listen_address),
                "port": saved_settings.port,
                "hostname": saved_settings.hostname,
                "server_certificate": saved_settings.server_certificate,
                "valid": not context["kms_validation_errors"],
                "validation_errors": context["kms_validation_errors"],
                "config_path": KMS_DEFAULT_CONFIG_PATH,
                "config_preview": context["kms_config_preview"],
            }
        )
    return RedirectResponse("/kms", status_code=303)


def parse_kms_owner_client_id(raw_value: str | int | None) -> int | None:
    if raw_value in {None, "", "None", "unassigned"}:
        return None
    return int(raw_value)


@router.post("/kms/clients", response_model=None)
def create_kms_client_from_ui(
    request: Request,
    name: str = Form(...),
    certificate_subject: str = Form(...),
    role: str = Form("service"),
    allowed_operations: str = Form("locate,get,register,create"),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    client = KmsClient(
        name=name.strip(),
        certificate_subject=certificate_subject.strip(),
        role=role.strip() or "service",
        allowed_operations=join_csv(split_csv(allowed_operations)),
        description=description or None,
        enabled=enabled == "on",
    )
    db.add(client)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(request, "kms.html", {"identity": identity, **kms_context(db), "form_error": f"KMS client {name} already exists."}, status_code=409)
    record_audit(db, actor=identity.username, action="create_kms_client", resource_type="kms_client", resource_id=str(client.id))
    return RedirectResponse("/kms", status_code=303)


@router.post("/kms/clients/{client_id}/edit", response_model=None)
def edit_kms_client_from_ui(
    request: Request,
    client_id: int,
    name: str = Form(...),
    certificate_subject: str = Form(...),
    role: str = Form("service"),
    allowed_operations: str = Form("locate,get,register,create"),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    client = db.get(KmsClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="KMS client not found")
    client.name = name.strip()
    client.certificate_subject = certificate_subject.strip()
    client.role = role.strip() or "service"
    client.allowed_operations = join_csv(split_csv(allowed_operations))
    client.description = description or None
    client.enabled = enabled == "on"
    client.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(request, "kms.html", {"identity": identity, **kms_context(db), "form_error": f"KMS client {name} already exists."}, status_code=409)
    record_audit(db, actor=identity.username, action="update_kms_client", resource_type="kms_client", resource_id=str(client.id))
    return RedirectResponse("/kms", status_code=303)


@router.post("/kms/clients/{client_id}/delete", response_model=None)
def delete_kms_client_from_ui(
    request: Request,
    client_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    client = db.get(KmsClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="KMS client not found")
    for key in db.execute(select(KmsKey).where(KmsKey.owner_client_id == client_id)).scalars().all():
        key.owner_client_id = None
    db.delete(client)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_kms_client", resource_type="kms_client", resource_id=str(client_id))
    return RedirectResponse("/kms", status_code=303)


@router.post("/kms/keys", response_model=None)
def create_kms_key_from_ui(
    request: Request,
    name: str = Form(...),
    algorithm: str = Form("AES"),
    length: int = Form(256),
    usage: str = Form("encrypt,decrypt"),
    state: str = Form("active"),
    owner_client_id: str = Form(""),
    exportable: str | None = Form(None),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    key = KmsKey(
        name=name.strip(),
        algorithm=algorithm.strip().upper() or "AES",
        length=length,
        usage=join_csv(split_csv(usage)),
        state=state.strip() or "active",
        owner_client_id=parse_kms_owner_client_id(owner_client_id),
        exportable=exportable == "on",
        description=description or None,
        enabled=enabled == "on",
    )
    db.add(key)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(request, "kms.html", {"identity": identity, **kms_context(db), "form_error": f"KMS key {name} already exists."}, status_code=409)
    record_audit(db, actor=identity.username, action="create_kms_key", resource_type="kms_key", resource_id=str(key.id))
    return RedirectResponse("/kms", status_code=303)


@router.post("/kms/keys/{key_id}/edit", response_model=None)
def edit_kms_key_from_ui(
    request: Request,
    key_id: int,
    name: str = Form(...),
    algorithm: str = Form("AES"),
    length: int = Form(256),
    usage: str = Form("encrypt,decrypt"),
    state: str = Form("active"),
    owner_client_id: str = Form(""),
    exportable: str | None = Form(None),
    description: str = Form(""),
    enabled: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    key = db.get(KmsKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="KMS key not found")
    key.name = name.strip()
    key.algorithm = algorithm.strip().upper() or "AES"
    key.length = length
    key.usage = join_csv(split_csv(usage))
    key.state = state.strip() or "active"
    key.owner_client_id = parse_kms_owner_client_id(owner_client_id)
    key.exportable = exportable == "on"
    key.description = description or None
    key.enabled = enabled == "on"
    key.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(request, "kms.html", {"identity": identity, **kms_context(db), "form_error": f"KMS key {name} already exists."}, status_code=409)
    record_audit(db, actor=identity.username, action="update_kms_key", resource_type="kms_key", resource_id=str(key.id))
    return RedirectResponse("/kms", status_code=303)


@router.post("/kms/keys/{key_id}/delete", response_model=None)
def delete_kms_key_from_ui(
    request: Request,
    key_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    key = db.get(KmsKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="KMS key not found")
    db.delete(key)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_kms_key", resource_type="kms_key", resource_id=str(key_id))
    return RedirectResponse("/kms", status_code=303)


@router.get("/https-repository", response_model=None)
def legacy_https_repository_redirect(identity: Identity = Depends(require_session_identity)) -> RedirectResponse:
    return RedirectResponse("/vcf-offline-depot", status_code=307)


@router.get("/vcf-offline-depot", response_class=HTMLResponse, response_model=None)
def vcf_offline_depot_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "vcf_offline_depot.html", {"identity": identity, **vcf_offline_depot_context(db), "appliance_apply_status": appliance_apply_status(db, "vcf_offline_depot")})


@router.post("/vcf-offline-depot/settings", response_model=None)
def update_vcf_offline_depot_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    hostname: str = Form(VCF_DEPOT_DEFAULT_HOSTNAME),
    listen_interfaces: list[str] = Form(default_factory=list),
    listen_addresses: list[str] = Form(default_factory=list),
    listen_interfaces_present: str | None = Form(None),
    listen_addresses_present: str | None = Form(None),
    listen_interface: str = Form(""),
    listen_address: str = Form(""),
    port: int = Form(443),
    server_certificate: str | None = Form(None),
    telemetry_choice: str | None = Form(None),
    telemetry_enabled: str | None = Form(None),
    tool_archive_file: UploadFile | None = File(None),
    download_token_file: UploadFile | None = File(None),
    activation_code_file: UploadFile | None = File(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_offline_depot_settings_row(db)
    previous_hostname = settings.hostname
    selected_interfaces, selected_addresses = resolve_service_bind_targets(
        db,
        [*listen_interfaces, listen_interface],
        [*listen_addresses, listen_address],
        current_interface=settings.listen_interface,
        current_address=settings.listen_address,
        listen_interfaces_present=listen_interfaces_present,
        listen_addresses_present=listen_addresses_present,
    )

    settings.enabled = enabled == "on"
    settings.hostname = hostname.strip() or VCF_DEPOT_DEFAULT_HOSTNAME
    settings.listen_interface = selected_interfaces
    settings.listen_address = selected_addresses
    settings.port = port
    settings.server_certificate = settings.hostname
    settings.depot_store_path = VCF_DEPOT_DEFAULT_STORE_PATH
    settings.config_path = VCF_DEPOT_DEFAULT_CONFIG_PATH
    if telemetry_choice in VCF_DEPOT_TELEMETRY_CHOICES:
        settings.telemetry_choice = telemetry_choice
    else:
        settings.telemetry_choice = "ENABLE" if telemetry_enabled == "on" else "DISABLE"
    uploaded_archive_name = store_uploaded_vcf_depot_archive(settings, tool_archive_file)
    software_depot_id_result: dict[str, str] | None = None
    if uploaded_archive_name:
        software_depot_id_result = generate_and_store_vcf_software_depot_id(db, settings)
    uploaded_token_name = store_uploaded_vcf_depot_secret(
        db,
        download_token_file,
        name_key=VCF_DEPOT_TOKEN_NAME_KEY,
        value_key=VCF_DEPOT_TOKEN_VALUE_KEY,
        actor=identity.username,
        action="upload_vcf_depot_download_token",
    )
    uploaded_activation_name = store_uploaded_vcf_depot_secret(
        db,
        activation_code_file,
        name_key=VCF_DEPOT_ACTIVATION_NAME_KEY,
        value_key=VCF_DEPOT_ACTIVATION_VALUE_KEY,
        actor=identity.username,
        action="upload_vcf_depot_activation_code",
    )
    settings.updated_at = utcnow()
    dns_record_action = ensure_dns_for_vcf_offline_depot(db, settings, identity.username)
    old_dns_record_action = None
    if normalize_dns_hostname(previous_hostname) != normalize_dns_hostname(settings.hostname):
        old_dns_record_action = remove_dns_for_vcf_offline_depot_hostname(db, previous_hostname, identity.username)
    dns_actions = [action for action in [dns_record_action, old_dns_record_action] if action]
    dns_record_action = "+".join(dns_actions) if dns_actions else None
    db.commit()
    record_audit(db, actor=identity.username, action="update_vcf_offline_depot_settings", resource_type="vcf_offline_depot", resource_id=str(settings.id))

    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = vcf_offline_depot_context(db)
        saved_settings = context["vcf_depot_settings"]
        validation_errors = context["vcf_depot_validation_errors"]
        validation_warnings = context["vcf_depot_validation_warnings"]
        token_state = context["vcf_depot_download_token"]
        activation_state = context["vcf_depot_activation_code"]
        software_depot_id = context["vcf_depot_software_depot_id"]
        software_depot_id_payload = software_depot_id_result or software_depot_id
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": saved_settings.updated_at.isoformat(),
                "hostname": saved_settings.hostname,
                "endpoint": context["vcf_depot_endpoint"],
                "listen_interface": primary_listen_interface(saved_settings.listen_interface),
                "listen_address": primary_listen_address(saved_settings.listen_address),
                "listen_interfaces": split_interfaces(saved_settings.listen_interface),
                "listen_addresses": split_addresses(saved_settings.listen_address),
                "port": saved_settings.port,
                "server_certificate": saved_settings.server_certificate,
                "depot_store_path": saved_settings.depot_store_path,
                "tool_archive_name": uploaded_archive_name or Path(saved_settings.tool_archive_path).name if saved_settings.tool_archive_path else "",
                "tool_version": saved_settings.tool_version,
                "software_depot_id": software_depot_id_payload["id"],
                "software_depot_id_generated_at": software_depot_id_payload["generated_at"],
                "software_depot_id_error": software_depot_id_payload["error"],
                "download_token_present": token_state.present,
                "download_token_name": uploaded_token_name or token_state.filename,
                "download_token_updated_at": token_state.updated_at,
                "activation_code_present": activation_state.present,
                "activation_code_name": uploaded_activation_name or activation_state.filename,
                "activation_code_updated_at": activation_state.updated_at,
                "telemetry_choice": saved_settings.telemetry_choice,
                "dns_record_action": dns_record_action,
                "config_path": saved_settings.config_path,
                "valid": not validation_errors,
                "validation_errors": validation_errors,
                "validation_warnings": validation_warnings,
                "https_config_preview": context["vcf_depot_https_config_preview"],
                "command_preview": context["vcf_depot_command_preview"],
            }
        )
    return RedirectResponse("/vcf-offline-depot", status_code=303)


@router.post("/vcf-offline-depot/download-token", response_model=None)
def paste_vcf_depot_download_token_from_ui(
    request: Request,
    download_token_text: str = Form(""),
    download_token_file: UploadFile | None = File(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    display_name = store_uploaded_vcf_depot_secret(
        db,
        download_token_file,
        name_key=VCF_DEPOT_TOKEN_NAME_KEY,
        value_key=VCF_DEPOT_TOKEN_VALUE_KEY,
        actor=identity.username,
        action="upload_vcf_depot_download_token",
    )
    if not display_name:
        display_name = store_pasted_vcf_depot_secret(
            db,
            download_token_text,
            name_key=VCF_DEPOT_TOKEN_NAME_KEY,
            value_key=VCF_DEPOT_TOKEN_VALUE_KEY,
            display_name="pasted token",
            actor=identity.username,
            action="paste_vcf_depot_download_token",
        )
    db.commit()
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = vcf_offline_depot_context(db)
        token_state = context["vcf_depot_download_token"]
        validation_errors = context["vcf_depot_validation_errors"]
        validation_warnings = context["vcf_depot_validation_warnings"]
        return JSONResponse(
            {
                "status": "saved",
                "download_token_present": token_state.present,
                "download_token_name": display_name,
                "download_token_updated_at": token_state.updated_at,
                "valid": not validation_errors,
                "validation_errors": validation_errors,
                "validation_warnings": validation_warnings,
                "config_path": context["vcf_depot_settings"].config_path,
                "https_config_preview": context["vcf_depot_https_config_preview"],
                "command_preview": context["vcf_depot_command_preview"],
            }
        )
    return RedirectResponse("/vcf-offline-depot", status_code=303)


@router.post("/vcf-offline-depot/software-depot-id/generate", response_model=None)
def generate_vcf_depot_software_depot_id_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_offline_depot_settings_row(db)
    if not settings.tool_archive_path:
        raise HTTPException(status_code=400, detail="Upload VCFDT before generating the software depot ID.")
    result = generate_and_store_vcf_software_depot_id(db, settings)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="generate_vcf_depot_software_depot_id",
        resource_type="vcf_offline_depot",
        resource_id=str(settings.id),
        success=not bool(result["error"]),
        detail="software depot ID generated" if result["id"] and not result["error"] else "software depot ID generation failed",
    )
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        return JSONResponse(
            {
                "status": "generated" if not result["error"] else "error",
                "software_depot_id": result["id"],
                "software_depot_id_generated_at": result["generated_at"],
                "software_depot_id_error": result["error"],
            },
            status_code=200 if not result["error"] else 400,
        )
    return RedirectResponse("/vcf-offline-depot", status_code=303)


@router.post("/vcf-offline-depot/profiles", response_model=None)
def create_vcf_depot_profile_from_ui(
    request: Request,
    name: str = Form(...),
    profile_type: str = Form("binaries"),
    sku: str = Form("VCF"),
    vcf_version: str = Form("9.1.0"),
    binary_type: str = Form("INSTALL"),
    automated_install: str | None = Form(None),
    upgrades_only: str | None = Form(None),
    patches_only: str | None = Form(None),
    component: str = Form(""),
    component_version: str = Form(""),
    disabled_platforms: str = Form(""),
    enabled: str | None = Form(None),
    status: str = Form("planned"),
    notes: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    profile = VcfDepotDownloadProfile(
        name=name.strip(),
        profile_type=profile_type.strip() or "binaries",
        sku=sku.strip() or "VCF",
        vcf_version=vcf_version.strip() or "9.1.0",
        binary_type=binary_type.strip() or "INSTALL",
        automated_install=automated_install == "on",
        upgrades_only=upgrades_only == "on",
        patches_only=patches_only == "on",
        component=component.strip(),
        component_version=component_version.strip(),
        disabled_platforms=disabled_platforms.strip(),
        enabled=enabled == "on",
        status=status.strip() or "planned",
        notes=notes.strip() or None,
    )
    db.add(profile)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="A VCFDT download profile with this name already exists.") from exc
    record_audit(db, actor=identity.username, action="create_vcf_depot_profile", resource_type="vcf_depot_profile", resource_id=str(profile.id))
    return RedirectResponse("/vcf-offline-depot", status_code=303)


@router.post("/vcf-offline-depot/profiles/{profile_id}/edit", response_model=None)
def edit_vcf_depot_profile_from_ui(
    request: Request,
    profile_id: int,
    name: str = Form(...),
    profile_type: str = Form("binaries"),
    sku: str = Form("VCF"),
    vcf_version: str = Form("9.1.0"),
    binary_type: str = Form("INSTALL"),
    automated_install: str | None = Form(None),
    upgrades_only: str | None = Form(None),
    patches_only: str | None = Form(None),
    component: str = Form(""),
    component_version: str = Form(""),
    disabled_platforms: str = Form(""),
    enabled: str | None = Form(None),
    status: str = Form("planned"),
    notes: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    profile = db.get(VcfDepotDownloadProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="VCFDT download profile not found.")
    profile.name = name.strip()
    profile.profile_type = profile_type.strip() or "binaries"
    profile.sku = sku.strip() or "VCF"
    profile.vcf_version = vcf_version.strip() or "9.1.0"
    profile.binary_type = binary_type.strip() or "INSTALL"
    profile.automated_install = automated_install == "on"
    profile.upgrades_only = upgrades_only == "on"
    profile.patches_only = patches_only == "on"
    profile.component = component.strip()
    profile.component_version = component_version.strip()
    profile.disabled_platforms = disabled_platforms.strip()
    profile.enabled = enabled == "on"
    profile.status = status.strip() or "planned"
    profile.notes = notes.strip() or None
    profile.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="A VCFDT download profile with this name already exists.") from exc
    record_audit(db, actor=identity.username, action="update_vcf_depot_profile", resource_type="vcf_depot_profile", resource_id=str(profile.id))
    return RedirectResponse("/vcf-offline-depot", status_code=303)


@router.post("/vcf-offline-depot/profiles/{profile_id}/download", response_model=None)
def start_vcf_depot_profile_download_from_ui(
    request: Request,
    profile_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_offline_depot_settings_row(db)
    profile = db.get(VcfDepotDownloadProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="VCFDT download profile not found.")
    if not profile.enabled:
        raise HTTPException(status_code=400, detail="Enable the VCFDT download profile before starting a download.")
    secrets = vcf_depot_secret_context(db)
    validation_errors, validation_warnings = validate_vcf_depot_state(
        settings,
        [profile],
        {interface["name"] for interface in service_bind_options(db)},
        bool(secrets["download_token_present"]),
        bool(secrets["activation_code_present"]),
    )
    if validation_errors:
        raise HTTPException(status_code=400, detail=" ".join(validation_errors))
    system_dry_run = get_settings().dry_run_system_adapters
    commands = [vcf_depot_command_entry(command, dry_run=False) for command in vcfdt_commands_for_profile(settings, profile)]
    if not commands:
        raise HTTPException(status_code=400, detail="The VCFDT download profile did not produce any commands.")
    now = utcnow()
    job_result = {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "profile_type": profile.profile_type,
        "dry_run": False,
        "system_adapter_dry_run": system_dry_run,
        "log_path": str(VCF_DEPOT_VDT_LOG_PATH),
        "commands": commands,
        "validation_warnings": validation_warnings,
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="vcf-depot-download",
        status=JobStatus.PENDING.value,
        created_by=identity.username,
        started_at=None,
        finished_at=None,
        progress_percent=0,
        result=json.dumps(job_result, indent=2),
    )
    profile.status = "ready"
    profile.updated_at = now
    db.add(job)
    db.commit()
    queue_vcf_depot_download_job(job.id, profile.id)
    record_audit(
        db,
        actor=identity.username,
        action="start_vcf_depot_profile_download",
        resource_type="job",
        resource_id=job.id,
        detail=f"profile={profile.name}; log={VCF_DEPOT_VDT_LOG_PATH}",
    )
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        return JSONResponse(
            {
                "status": "started",
                "job_id": job.id,
                "job_status": JobStatus.RUNNING.value,
                "profile_id": profile.id,
                "profile_name": profile.name,
                "profile_status": profile.status,
                "dry_run": False,
                "system_adapter_dry_run": system_dry_run,
                "log_path": str(VCF_DEPOT_VDT_LOG_PATH),
                "commands": commands,
                "validation_warnings": validation_warnings,
            }
        )
    return RedirectResponse("/vcf-offline-depot", status_code=303)


@router.post("/vcf-offline-depot/profiles/{profile_id}/delete", response_model=None)
def delete_vcf_depot_profile_from_ui(
    request: Request,
    profile_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    profile = db.get(VcfDepotDownloadProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="VCFDT download profile not found.")
    db.delete(profile)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_vcf_depot_profile", resource_type="vcf_depot_profile", resource_id=str(profile_id))
    return RedirectResponse("/vcf-offline-depot", status_code=303)


@router.get("/vcf-private-registry", response_class=HTMLResponse, response_model=None)
def vcf_private_registry_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "vcf_private_registry.html", {"identity": identity, **vcf_private_registry_context(db), "appliance_apply_status": appliance_apply_status(db, "vcf_private_registry")})


@router.post("/vcf-private-registry/settings", response_model=None)
def update_vcf_private_registry_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    hostname: str = Form(VCF_REGISTRY_DEFAULT_HOSTNAME),
    listen_interfaces: list[str] = Form(default_factory=list),
    listen_addresses: list[str] = Form(default_factory=list),
    listen_interfaces_present: str | None = Form(None),
    listen_addresses_present: str | None = Form(None),
    listen_interface: str = Form(""),
    listen_address: str = Form(""),
    port: int = Form(443),
    harbor_project: str = Form(VCF_REGISTRY_DEFAULT_PROJECT),
    server_certificate: str = Form(VCF_REGISTRY_DEFAULT_HOSTNAME),
    robot_account: str = Form("robot$vcf-supervisor-services"),
    relocation_dry_run: str | None = Form(None),
    ca_bundle_file: UploadFile | None = File(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_private_registry_settings_row(db)
    selected_interfaces, selected_addresses = resolve_service_bind_targets(
        db,
        [*listen_interfaces, listen_interface],
        [*listen_addresses, listen_address],
        current_interface=settings.listen_interface,
        current_address=settings.listen_address,
        listen_interfaces_present=listen_interfaces_present,
        listen_addresses_present=listen_addresses_present,
    )
    settings.enabled = enabled == "on"
    settings.hostname = hostname.strip() or VCF_REGISTRY_DEFAULT_HOSTNAME
    settings.listen_interface = selected_interfaces
    settings.listen_address = selected_addresses
    settings.port = port
    settings.harbor_project = harbor_project.strip() or VCF_REGISTRY_DEFAULT_PROJECT
    settings.storage_path = VCF_REGISTRY_DEFAULT_STORAGE_PATH
    settings.config_path = VCF_REGISTRY_DEFAULT_CONFIG_PATH
    uploaded_ca_bundle_name = store_uploaded_vcf_registry_ca_bundle(db, ca_bundle_file, identity.username)
    ca_bundle_context = vcf_registry_ca_bundle_context(db)
    settings.ca_bundle_path = str(ca_bundle_context["path"])
    settings.server_certificate = server_certificate.strip() or settings.hostname
    settings.robot_account = robot_account.strip() or f"robot${settings.harbor_project}"
    settings.relocation_dry_run = relocation_dry_run == "on"
    settings.updated_at = utcnow()
    dns_record_action = ensure_dns_for_vcf_registry(db, settings, identity.username)
    db.commit()
    record_audit(db, actor=identity.username, action="update_vcf_private_registry_settings", resource_type="vcf_private_registry", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = vcf_private_registry_context(db)
        saved_settings = context["vcf_registry_settings"]
        validation_errors = context["vcf_registry_validation_errors"]
        validation_warnings = context["vcf_registry_validation_warnings"]
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": saved_settings.updated_at.isoformat(),
                "hostname": saved_settings.hostname,
                "listen_interface": primary_listen_interface(saved_settings.listen_interface),
                "listen_address": primary_listen_address(saved_settings.listen_address),
                "listen_interfaces": split_interfaces(saved_settings.listen_interface),
                "listen_addresses": split_addresses(saved_settings.listen_address),
                "port": saved_settings.port,
                "endpoint": context["vcf_registry_endpoint"],
                "harbor_project": saved_settings.harbor_project,
                "storage_path": saved_settings.storage_path,
                "config_path": saved_settings.config_path,
                "ca_bundle_path": saved_settings.ca_bundle_path,
                "ca_bundle_source": context["vcf_registry_ca_bundle_source"],
                "ca_bundle_source_label": context["vcf_registry_ca_bundle_source_label"],
                "ca_bundle_available": context["vcf_registry_ca_bundle_available"],
                "ca_bundle_uploaded_name": uploaded_ca_bundle_name or context["vcf_registry_uploaded_ca_bundle_name"],
                "server_certificate": saved_settings.server_certificate,
                "robot_account": saved_settings.robot_account,
                "relocation_dry_run": saved_settings.relocation_dry_run,
                "dns_record_action": dns_record_action,
                "valid": not validation_errors,
                "validation_errors": validation_errors,
                "validation_warnings": validation_warnings,
                "harbor_config_preview": context["vcf_registry_harbor_config_preview"],
                "relocation_preview": context["vcf_registry_relocation_preview"],
            }
        )
    return RedirectResponse("/vcf-private-registry", status_code=303)


@router.post("/vcf-private-registry/bundles", response_model=None)
def create_vcf_registry_bundle_from_ui(
    request: Request,
    name: str = Form(...),
    source_reference: str = Form(""),
    target_reference: str = Form(""),
    enabled: str | None = Form(None),
    status: str = Form("planned"),
    notes: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_private_registry_settings_row(db)
    bundle = VcfRegistryBundle(
        name=name.strip(),
        source_reference=source_reference.strip(),
        target_reference=target_reference.strip() or default_target_reference(settings, source_reference),
        enabled=enabled == "on",
        status=status.strip() or "planned",
        notes=notes.strip() or None,
    )
    db.add(bundle)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="A Supervisor Service bundle with this name already exists.") from exc
    record_audit(db, actor=identity.username, action="create_vcf_registry_bundle", resource_type="vcf_registry_bundle", resource_id=str(bundle.id))
    return RedirectResponse("/vcf-private-registry", status_code=303)


@router.post("/vcf-private-registry/bundles/{bundle_id}/edit", response_model=None)
def edit_vcf_registry_bundle_from_ui(
    request: Request,
    bundle_id: int,
    name: str = Form(...),
    source_reference: str = Form(""),
    target_reference: str = Form(""),
    enabled: str | None = Form(None),
    status: str = Form("planned"),
    notes: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_private_registry_settings_row(db)
    bundle = db.get(VcfRegistryBundle, bundle_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Supervisor Service bundle not found.")
    bundle.name = name.strip()
    bundle.source_reference = source_reference.strip()
    bundle.target_reference = target_reference.strip() or default_target_reference(settings, source_reference)
    bundle.enabled = enabled == "on"
    bundle.status = status.strip() or "planned"
    bundle.notes = notes.strip() or None
    bundle.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="A Supervisor Service bundle with this name already exists.") from exc
    record_audit(db, actor=identity.username, action="update_vcf_registry_bundle", resource_type="vcf_registry_bundle", resource_id=str(bundle.id))
    return RedirectResponse("/vcf-private-registry", status_code=303)


@router.post("/vcf-private-registry/bundles/{bundle_id}/delete", response_model=None)
def delete_vcf_registry_bundle_from_ui(
    request: Request,
    bundle_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    bundle = db.get(VcfRegistryBundle, bundle_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Supervisor Service bundle not found.")
    db.delete(bundle)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_vcf_registry_bundle", resource_type="vcf_registry_bundle", resource_id=str(bundle_id))
    return RedirectResponse("/vcf-private-registry", status_code=303)


@router.get("/vcf-backups", response_class=HTMLResponse, response_model=None)
def vcf_backups_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "vcf_backups.html", {"identity": identity, **vcf_backup_context(db), "appliance_apply_status": appliance_apply_status(db, "vcf_backups")})


@router.post("/vcf-backups/settings", response_model=None)
def update_vcf_backup_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    listen_interfaces: list[str] = Form(default_factory=list),
    listen_addresses: list[str] = Form(default_factory=list),
    listen_interfaces_present: str | None = Form(None),
    listen_addresses_present: str | None = Form(None),
    listen_interface: str = Form(""),
    listen_address: str = Form(""),
    port: int = Form(22),
    sftp_user_id: str = Form(""),
    chroot_enabled: str | None = Form(None),
    allow_password_auth: str | None = Form(None),
    allow_public_key_auth: str | None = Form(None),
    max_sessions: int = Form(4),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_backup_settings_row(db, reconcile_default_user=False)
    user_id = int(sftp_user_id) if str(sftp_user_id).strip() else None
    if user_id and not db.get(User, user_id):
        raise HTTPException(status_code=400, detail="Selected SFTP user does not exist.")
    selected_interfaces, selected_addresses = resolve_service_bind_targets(
        db,
        [*listen_interfaces, listen_interface],
        [*listen_addresses, listen_address],
        current_interface=settings.listen_interface,
        current_address=settings.listen_address,
        listen_interfaces_present=listen_interfaces_present,
        listen_addresses_present=listen_addresses_present,
    )
    settings.enabled = enabled == "on"
    settings.listen_interface = selected_interfaces
    settings.listen_address = selected_addresses
    settings.port = port
    settings.sftp_user_id = user_id
    settings.storage_path = VCF_BACKUP_DEFAULT_VOLUME_MOUNT
    settings.chroot_enabled = chroot_enabled == "on"
    settings.allow_password_auth = allow_password_auth == "on"
    settings.allow_public_key_auth = allow_public_key_auth == "on"
    settings.max_sessions = max_sessions
    settings.config_path = VCF_BACKUP_EFFECTIVE_CONFIG_PATH
    settings.updated_at = utcnow()
    selected_user = db.get(User, user_id) if user_id else None
    if settings.enabled and selected_user and selected_user.username == VCF_BACKUP_DEFAULT_USERNAME and not selected_user.enabled:
        if has_pending_os_password(selected_user) or selected_user.os_password_applied_at:
            selected_user.enabled = True
            selected_user.os_sync_status = "pending"
            db.add(selected_user)
    disabled_default_user = disable_default_vcf_backup_user_when_service_off(db, settings, actor=identity.username)
    db.commit()
    record_audit(db, actor=identity.username, action="update_vcf_backup_settings", resource_type="vcf_backups", resource_id=str(settings.id))
    if disabled_default_user:
        record_audit(
            db,
            actor=identity.username,
            action="disable_vcf_backup_default_user",
            resource_type="user",
            resource_id=str(user_id or ""),
        )
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = vcf_backup_context(db)
        saved_settings = context["vcf_backup_settings"]
        validation_errors = context["vcf_backup_validation_errors"]
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": saved_settings.updated_at.isoformat(),
                "listen_interface": primary_listen_interface(saved_settings.listen_interface),
                "listen_address": primary_listen_address(saved_settings.listen_address),
                "listen_interfaces": split_interfaces(saved_settings.listen_interface),
                "listen_addresses": split_addresses(saved_settings.listen_address),
                "port": saved_settings.port,
                "sftp_username": saved_settings.sftp_user.username if saved_settings.sftp_user else "",
                "storage_path": saved_settings.storage_path,
                "remote_directory": vcf_backup_remote_directory(saved_settings),
                "chroot_label": "appliance mount, chroot enabled" if saved_settings.chroot_enabled else "appliance mount",
                "auth_methods": " + ".join(
                    [
                        label
                        for enabled_value, label in [
                            (saved_settings.allow_password_auth, "password"),
                            (saved_settings.allow_public_key_auth, "public key"),
                        ]
                        if enabled_value
                    ]
                )
                or "none",
                "max_sessions": saved_settings.max_sessions,
                "valid": not validation_errors,
                "validation_errors": validation_errors,
                "config_path": saved_settings.config_path,
                "config_preview": context["vcf_backup_config_preview"],
            }
        )
    return RedirectResponse("/vcf-backups", status_code=303)


@router.get("/authentication", response_class=HTMLResponse, response_model=None)
def authentication(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    tokens = db.execute(select(ApiToken).order_by(desc(ApiToken.created_at))).scalars().all()
    return render(request, "authentication.html", {"identity": identity, "tokens": tokens, "raw_token": None})


@router.post("/authentication/api-tokens", response_model=None)
def create_token_from_ui(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    scopes: str = Form("read:dashboard read:routes read:wan"),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    user = db.get(User, identity.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Current user not found")
    token_result = create_token_for_user(
        db,
        user=user,
        create=ApiTokenCreate(name=name, description=description or None, scopes=scopes.split()),
        settings=get_settings(),
        actor=identity.username,
    )
    tokens = db.execute(select(ApiToken).order_by(desc(ApiToken.created_at))).scalars().all()
    return render(
        request,
        "authentication.html",
        {"identity": identity, "tokens": tokens, "raw_token": token_result.raw_token},
    )


@router.post("/authentication/api-tokens/{token_id}/revoke", response_model=None)
def revoke_token_from_ui(
    request: Request,
    token_id: int,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_csrf(request, csrf)
    token = db.get(ApiToken, token_id)
    if not token:
        raise HTTPException(status_code=404, detail="API token not found")
    token.enabled = False
    token.revoked_at = utcnow()
    token.revoked_by = identity.username
    db.commit()
    record_audit(db, actor=identity.username, action="revoke_api_token", resource_type="api_token", resource_id=str(token.id))
    return RedirectResponse("/authentication", status_code=303)


@router.get("/users", response_class=HTMLResponse, response_model=None)
def users_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    require_admin_identity(identity)
    return render(
        request,
        "users.html",
        {"identity": identity, **users_context(db, identity), "appliance_apply_status": appliance_apply_status(db, "local_users")},
    )


@router.post("/users/password-policy", response_model=None)
def update_users_password_policy(
    request: Request,
    min_length: str = Form(str(DEFAULT_PASSWORD_POLICY["min_length"])),
    require_uppercase: str | None = Form(None),
    require_lowercase: str | None = Form(None),
    require_number: str | None = Form(None),
    require_special: str | None = Form(None),
    disallow_username: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    try:
        parsed_min_length = int(min_length)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Minimum length must be a number.") from exc
    if parsed_min_length < 8 or parsed_min_length > 128:
        raise HTTPException(status_code=422, detail="Minimum length must be between 8 and 128.")
    policy = password_policy_from_json(
        json.dumps(
            {
                "min_length": parsed_min_length,
                "require_uppercase": require_uppercase == "on",
                "require_lowercase": require_lowercase == "on",
                "require_number": require_number == "on",
                "require_special": require_special == "on",
                "disallow_username": disallow_username == "on",
            }
        )
    )
    setting = set_setting_value(db, LOCAL_USERS_PASSWORD_POLICY_KEY, password_policy_to_json(policy))
    db.commit()
    record_audit(db, actor=identity.username, action="update_local_user_password_policy", resource_type="user_policy")
    return JSONResponse({"updated_at": setting.updated_at.isoformat(), "policy": policy, "summary": password_policy_summary(policy)})


@router.post("/users", response_model=None)
def create_user_from_ui(
    request: Request,
    username: str = Form(...),
    role: str = Form(Role.VIEWER.value),
    shell: str = Form(DEFAULT_LOCAL_USER_SHELL),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    username = username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    if role not in {item.value for item in Role}:
        raise HTTPException(status_code=400, detail="Unknown role.")
    if not is_valid_user_shell(shell):
        raise HTTPException(status_code=400, detail=f"Shell must be one of {', '.join(LOCAL_USER_SHELLS)}.")
    shell = normalize_user_shell(shell)
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"User {username} already exists.")
    user = User(username=username, role=role, shell=shell, enabled=False)
    db.add(user)
    db.commit()
    record_audit(db, actor=identity.username, action="create_local_user", resource_type="user", resource_id=str(user.id))
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/edit", response_model=None)
def update_user_from_ui(
    user_id: int,
    request: Request,
    username: str = Form(...),
    role: str = Form(Role.VIEWER.value),
    shell: str = Form(DEFAULT_LOCAL_USER_SHELL),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    username = username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    if role not in {item.value for item in Role}:
        raise HTTPException(status_code=400, detail="Unknown role.")
    if not is_valid_user_shell(shell):
        raise HTTPException(status_code=400, detail=f"Shell must be one of {', '.join(LOCAL_USER_SHELLS)}.")
    shell = normalize_user_shell(shell)
    next_enabled = user.enabled
    protect_last_admin(db, user, next_role=role, next_enabled=next_enabled)
    existing = db.execute(select(User).where(User.username == username, User.id != user.id)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"User {username} already exists.")
    old_username = user.username
    user.username = username
    user.role = role
    shell_changed = user.shell != shell
    user.shell = shell
    if old_username != username:
        rename_pending_os_password(old_username, username)
        user.os_password_applied_at = None
        user.os_sync_status = "pending" if has_pending_os_password(user) else "password_not_staged"
    elif shell_changed and next_enabled:
        user.os_sync_status = "pending"
    if old_username != username:
        tokens = db.execute(select(ApiToken).where(ApiToken.owner_user_id == user.id)).scalars().all()
        for token in tokens:
            token.owner_username = username
            db.add(token)
    db.add(user)
    db.commit()
    record_audit(db, actor=identity.username, action="update_local_user", resource_type="user", resource_id=str(user.id))
    db.refresh(user)
    return JSONResponse({"user": user_to_dict(user, identity.user_id)})


@router.post("/users/{user_id}/disable", response_model=None)
def disable_user_from_ui(
    user_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == identity.user_id:
        raise HTTPException(status_code=400, detail="You cannot disable your own active session account.")
    if not user.enabled:
        return JSONResponse({"user": user_to_dict(user, identity.user_id)})
    protect_last_admin(db, user, next_enabled=False)
    user.enabled = False
    user.os_sync_status = "pending"
    user.os_unlock_requested_at = None
    clear_pending_os_password(user)
    revoke_user_tokens(db, user, identity.username)
    db.add(user)
    db.commit()
    record_audit(db, actor=identity.username, action="disable_local_user", resource_type="user", resource_id=str(user.id))
    db.refresh(user)
    return JSONResponse({"user": user_to_dict(user, identity.user_id)})


@router.post("/users/{user_id}/unlock", response_model=None)
def request_user_os_unlock_from_ui(
    user_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.enabled:
        raise HTTPException(status_code=400, detail="Disabled local users are removed from Photon OS during appliance apply.")
    user.os_unlock_requested_at = utcnow()
    user.os_sync_status = "pending"
    db.add(user)
    db.commit()
    record_audit(db, actor=identity.username, action="request_local_user_os_unlock", resource_type="user", resource_id=str(user.id))
    db.refresh(user)
    return JSONResponse({"user": user_to_dict(user, identity.user_id)})


@router.post("/users/{user_id}/delete", response_model=None)
def delete_user_from_ui(
    user_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == identity.user_id:
        raise HTTPException(status_code=400, detail="You cannot remove your own active session account.")
    protect_last_admin(db, user, next_enabled=False)
    revoke_user_tokens(db, user, identity.username)
    for token in db.execute(select(ApiToken).where(ApiToken.owner_user_id == user.id)).scalars().all():
        db.delete(token)
    db.delete(user)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_local_user", resource_type="user", resource_id=str(user_id))
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/password", response_model=None)
def reset_user_password_from_ui(
    user_id: int,
    request: Request,
    password: str = Form(...),
    confirm_password: str = Form(...),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="Password confirmation does not match.")
    policy_errors = validate_password(password, user.username, local_users_password_policy(db))
    if policy_errors:
        raise HTTPException(status_code=400, detail=" ".join(policy_errors))
    stage_user_os_password(user, password)
    user.enabled = True
    db.add(user)
    revoke_user_tokens(db, user, identity.username)
    db.commit()
    record_audit(db, actor=identity.username, action="reset_local_user_password", resource_type="user", resource_id=str(user.id))
    return RedirectResponse("/users", status_code=303)


@router.get("/ldap-users", response_model=None)
def legacy_ldap_users_redirect() -> RedirectResponse:
    return RedirectResponse("/authentication", status_code=303)


def service_state_to_grid_row(service: ServiceState) -> dict[str, object]:
    return {
        "id": service.id,
        "service": service.service,
        "display_name": service.display_name,
        "running": service.running,
        "enabled": service.enabled,
        "health": service.health,
        "detail": service.detail or "native host service",
    }


def services_template_context(db: Session) -> dict[str, object]:
    rows = db.execute(select(ServiceState).order_by(ServiceState.display_name)).scalars().all()
    return {
        "services": rows,
        "service_rows": [service_state_to_grid_row(row) for row in rows],
    }


def backup_restore_context(db: Session, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    counts = desired_state_counts(db)
    return {
        "settings_backup_counts": counts,
        "settings_backup_total_rows": sum(counts.values()),
        "backup_restore_result": result,
        "backup_restore_error": error,
    }


def require_esxi_pxe_write(identity: Identity) -> None:
    if not identity.can("write:esxi-pxe"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ESXi PXE write permission required")


def next_kickstart_copy_name(db: Session, base_name: str) -> str:
    names = {row.name.lower() for row in db.execute(select(EsxiKickstart)).scalars().all()}
    candidate = f"{base_name} Copy"
    if candidate.lower() not in names:
        return candidate
    index = 2
    while True:
        candidate = f"{base_name} Copy {index}"
        if candidate.lower() not in names:
            return candidate
        index += 1


def esxi_pxe_page_context(
    db: Session,
    identity: Identity,
    *,
    selected_id: int | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    context = esxi_pxe_context(db)
    kickstarts = context["esxi_kickstarts"]
    selected = next((row for row in kickstarts if row.id == selected_id), None) or (kickstarts[0] if kickstarts else None)
    selected_validation = {"valid": True, "errors": [], "warnings": []}
    if selected is not None:
        selected_validation = context["esxi_pxe_validation_by_id"].get(selected.id, selected_validation)
    return {
        **context,
        "esxi_selected_kickstart": selected,
        "esxi_selected_kickstart_json": kickstart_to_dict(selected, include_content=identity.can("write:esxi-pxe")) if selected else None,
        "esxi_selected_validation": selected_validation,
        "esxi_can_write": identity.can("write:esxi-pxe"),
        "esxi_pxe_result": result,
        "esxi_pxe_error": error,
    }


@router.post("/services/{service}/{action}", response_model=None)
def service_action_from_ui(
    service: str,
    action: str,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    row = db.execute(select(ServiceState).where(ServiceState.service == service)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    if action not in {"start", "stop", "restart", "enable", "disable"}:
        raise HTTPException(status_code=422, detail="Unsupported service action")
    if action == "enable":
        row.enabled = True
    elif action == "disable":
        row.enabled = False
    elif action in {"start", "restart"}:
        row.running = True
    elif action == "stop":
        row.running = False
    db.add(row)
    result = SystemAdapter().service_action(service, action)
    record_audit(
        db,
        actor=identity.username,
        action=f"{action}_service_dry_run",
        resource_type="service",
        resource_id=service,
        detail=" ".join(result.command),
    )
    return render(
        request,
        "services.html",
        {
            "identity": identity,
            **services_template_context(db),
            "service_action_result": {
                "service": row.display_name,
                "action": action,
                "command": " ".join(result.command),
                "dry_run": result.dry_run,
            },
        },
    )


@router.get("/services/{service}/logs", response_class=HTMLResponse, response_model=None)
def service_logs_from_ui(
    service: str,
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    row = db.execute(select(ServiceState).where(ServiceState.service == service)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return render(
        request,
        "services.html",
        {
            "identity": identity,
            **services_template_context(db),
            "service_logs": {
                "service": row.display_name,
                "lines": [f"dry-run log source for {service}", "No host journal is read in development mode."],
            },
        },
    )


@router.get("/services", response_class=HTMLResponse, response_model=None)
def services(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "services.html", {"identity": identity, **services_template_context(db)})


@router.get("/logs", response_class=HTMLResponse, response_model=None)
def logs_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request,
        "logs.html",
        {
            "identity": identity,
            **logs_context(db),
        },
    )


@router.get("/audit-log", response_class=HTMLResponse, response_model=None)
def audit_log(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    return RedirectResponse("/logs#logs-audit-panel", status_code=303)


@router.get("/pxe/esxi/ks/{kickstart_file}", response_model=None)
def serve_esxi_kickstart_file(kickstart_file: str, db: Session = Depends(get_db)) -> FileResponse:
    if not kickstart_file.endswith(".cfg"):
        raise HTTPException(status_code=404, detail="Kickstart not found")
    raw_id = kickstart_file.removesuffix(".cfg")
    if not raw_id.isdigit():
        raise HTTPException(status_code=404, detail="Kickstart not found")
    kickstart = db.get(EsxiKickstart, int(raw_id))
    path = generated_kickstart_path(int(raw_id))
    if not kickstart or not kickstart.enabled or not path.is_file():
        raise HTTPException(status_code=404, detail="Kickstart not found")
    return FileResponse(path, media_type="text/plain; charset=utf-8")


@router.get("/pxe/esxi/boot.ipxe", response_model=None)
def serve_esxi_http_ipxe_script() -> FileResponse:
    if not ESXI_IPXE_HTTP_SCRIPT_PATH.is_file():
        raise HTTPException(status_code=404, detail="ESXi iPXE boot script is not enabled")
    return FileResponse(ESXI_IPXE_HTTP_SCRIPT_PATH, media_type="text/plain; charset=utf-8")


@router.get("/esxi-pxe", response_class=HTMLResponse, response_model=None)
def esxi_pxe_page(
    request: Request,
    kickstart_id: int | None = None,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request,
        "esxi_pxe.html",
        {
            "identity": identity,
            **esxi_pxe_page_context(db, identity, selected_id=kickstart_id),
            "appliance_apply_status": appliance_apply_status(db, "esxi_pxe"),
        },
    )


@router.post("/esxi-pxe/boot-settings", response_model=None)
def update_esxi_pxe_boot_settings_from_ui(
    request: Request,
    enabled: bool = Form(False),
    hostname: str = Form(ESXI_PXE_DEFAULT_HOSTNAME),
    dhcp_scope_id: str = Form(""),
    listen_interfaces: list[str] = Form(default=[]),
    listen_addresses: list[str] = Form(default=[]),
    listen_interfaces_present: str | None = Form(None),
    listen_addresses_present: str | None = Form(None),
    listen_interface: str = Form(""),
    listen_address: str = Form(""),
    tftp_root: str = Form(...),
    http_port: int = Form(ESXI_PXE_HTTP_PORT),
    bios_bootfile: str = Form(...),
    uefi_bootfile: str = Form(...),
    native_uefi_http_enabled: bool = Form(False),
    native_uefi_http_url: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    previous_boot = esxi_pxe_boot_settings(db)
    selected_interfaces, selected_addresses = resolve_service_bind_targets(
        db,
        [*listen_interfaces, listen_interface],
        [*listen_addresses, listen_address],
        current_interface=str(previous_boot.get("listen_interface") or ""),
        current_address=str(previous_boot.get("listen_address") or ""),
        listen_interfaces_present=listen_interfaces_present,
        listen_addresses_present=listen_addresses_present,
    )
    try:
        boot = save_esxi_pxe_boot_settings(
            db,
            enabled=enabled,
            hostname=hostname,
            listen_interface=selected_interfaces,
            listen_address=selected_addresses,
            dhcp_scope_id=dhcp_scope_id,
            tftp_root=tftp_root,
            http_port=http_port,
            bios_bootfile=bios_bootfile,
            uefi_bootfile=uefi_bootfile,
            native_uefi_http_enabled=native_uefi_http_enabled,
            native_uefi_http_url=native_uefi_http_url,
        )
        dns_record_action = ensure_dns_for_esxi_pxe(db, boot, identity.username, previous_hostname=str(previous_boot.get("hostname") or ""))
        db.commit()
    except ValueError as exc:
        db.rollback()
        return render(
            request,
            "esxi_pxe.html",
            {
                "identity": identity,
                **esxi_pxe_page_context(db, identity, error=str(exc)),
                "appliance_apply_status": appliance_apply_status(db, "esxi_pxe"),
            },
            status_code=400,
        )
    record_audit(
        db,
        actor=identity.username,
        action="update_esxi_pxe_boot_settings",
        resource_type="esxi_pxe_boot",
        resource_id="default",
        detail=f"enabled={boot['enabled']} native_uefi_http_enabled={boot['native_uefi_http_enabled']} tftp_root={boot['tftp_root']} http_port={boot['http_port']}",
        request_id=request.state.request_id,
    )
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = esxi_pxe_context(db)
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": utcnow().isoformat(),
                "hostname": context["esxi_pxe_boot"]["hostname"],
                "listen_address": context["esxi_pxe_primary_listen_address"],
                "bind_label": context["esxi_pxe_bind_label"],
                "dns_record_action": dns_record_action,
                "validation_errors": context["esxi_pxe_validation_errors"],
                "validation_warnings": context["esxi_pxe_validation_warnings"],
            }
        )
    return RedirectResponse("/esxi-pxe", status_code=303)


@router.post("/esxi-pxe/kickstarts", response_model=None)
def create_esxi_kickstart_from_ui(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    content: str = Form(...),
    enabled: bool = Form(False),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    try:
        kickstart = EsxiKickstart(name=normalize_kickstart_name(name), description=description or None, content="", content_hash="", enabled=enabled)
        db.add(kickstart)
        db.flush()
        assign_kickstart_content(kickstart, content, max_bytes=get_settings().esxi_kickstart_max_bytes)
        kickstart.http_path = canonical_http_path(kickstart.id)
        db.commit()
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        return render(
            request,
            "esxi_pxe.html",
            {
                "identity": identity,
                **esxi_pxe_page_context(db, identity, error=str(exc)),
                "appliance_apply_status": appliance_apply_status(db, "esxi_pxe"),
            },
            status_code=400,
        )
    record_audit(db, actor=identity.username, action="create_esxi_kickstart", resource_type="esxi_kickstart", resource_id=str(kickstart.id), detail=f"name={kickstart.name} hash={kickstart.content_hash}", request_id=request.state.request_id)
    return RedirectResponse(f"/esxi-pxe?kickstart_id={kickstart.id}", status_code=303)


@router.post("/esxi-pxe/kickstarts/{kickstart_id}", response_model=None)
def update_esxi_kickstart_from_ui(
    kickstart_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    content: str = Form(...),
    enabled: bool = Form(False),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    kickstart = db.get(EsxiKickstart, kickstart_id)
    if not kickstart:
        raise HTTPException(status_code=404, detail="Kickstart not found")
    try:
        kickstart.name = normalize_kickstart_name(name)
        kickstart.description = description or None
        kickstart.enabled = enabled
        assign_kickstart_content(kickstart, content, max_bytes=get_settings().esxi_kickstart_max_bytes)
        kickstart.http_path = canonical_http_path(kickstart.id)
        db.add(kickstart)
        db.commit()
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        return render(
            request,
            "esxi_pxe.html",
            {
                "identity": identity,
                **esxi_pxe_page_context(db, identity, selected_id=kickstart_id, error=str(exc)),
                "appliance_apply_status": appliance_apply_status(db, "esxi_pxe"),
            },
            status_code=400,
        )
    record_audit(db, actor=identity.username, action="update_esxi_kickstart", resource_type="esxi_kickstart", resource_id=str(kickstart.id), detail=f"name={kickstart.name} hash={kickstart.content_hash}", request_id=request.state.request_id)
    return RedirectResponse(f"/esxi-pxe?kickstart_id={kickstart.id}", status_code=303)


@router.post("/esxi-pxe/kickstarts/{kickstart_id}/duplicate", response_model=None)
def duplicate_esxi_kickstart_from_ui(
    kickstart_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    source = db.get(EsxiKickstart, kickstart_id)
    if not source:
        raise HTTPException(status_code=404, detail="Kickstart not found")
    duplicate = EsxiKickstart(
        name=next_kickstart_copy_name(db, source.name),
        description=source.description,
        content=source.content,
        content_hash=source.content_hash,
        rendered_content=source.rendered_content,
        enabled=source.enabled,
    )
    db.add(duplicate)
    db.flush()
    duplicate.http_path = canonical_http_path(duplicate.id)
    db.commit()
    record_audit(db, actor=identity.username, action="duplicate_esxi_kickstart", resource_type="esxi_kickstart", resource_id=str(duplicate.id), detail=f"source_id={source.id} name={duplicate.name}", request_id=request.state.request_id)
    return RedirectResponse(f"/esxi-pxe?kickstart_id={duplicate.id}", status_code=303)


@router.post("/esxi-pxe/kickstarts/{kickstart_id}/delete", response_model=None)
def delete_esxi_kickstart_from_ui(
    kickstart_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    kickstart = db.get(EsxiKickstart, kickstart_id)
    if not kickstart:
        raise HTTPException(status_code=404, detail="Kickstart not found")
    for host in db.execute(select(EsxiPxeHost).where(EsxiPxeHost.kickstart_id == kickstart.id)).scalars().all():
        host.kickstart_id = None
        host.updated_at = utcnow()
        db.add(host)
    db.delete(kickstart)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_esxi_kickstart", resource_type="esxi_kickstart", resource_id=str(kickstart_id), request_id=request.state.request_id)
    return RedirectResponse("/esxi-pxe", status_code=303)


@router.post("/esxi-pxe/kickstarts/{kickstart_id}/validate", response_class=HTMLResponse, response_model=None)
def validate_esxi_kickstart_from_ui(
    kickstart_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    kickstart = db.get(EsxiKickstart, kickstart_id)
    if not kickstart:
        raise HTTPException(status_code=404, detail="Kickstart not found")
    errors, warnings = kickstart_validation(kickstart.content, strict=strict_validation_enabled(db), max_bytes=get_settings().esxi_kickstart_max_bytes)
    record_audit(db, actor=identity.username, action="validate_esxi_kickstart", resource_type="esxi_kickstart", resource_id=str(kickstart.id), detail=f"errors={len(errors)} warnings={len(warnings)}", request_id=request.state.request_id)
    return render(
        request,
        "esxi_pxe.html",
        {
            "identity": identity,
            **esxi_pxe_page_context(
                db,
                identity,
                selected_id=kickstart_id,
                result={"title": "Validation complete", "message": f"{len(errors)} errors, {len(warnings)} warnings."},
            ),
            "appliance_apply_status": appliance_apply_status(db, "esxi_pxe"),
        },
    )


@router.get("/esxi-pxe/kickstarts/{kickstart_id}/download", response_model=None)
def download_esxi_kickstart_from_ui(
    kickstart_id: int,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    require_esxi_pxe_write(identity)
    kickstart = db.get(EsxiKickstart, kickstart_id)
    if not kickstart:
        raise HTTPException(status_code=404, detail="Kickstart not found")
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "-", kickstart.name).strip("-") or f"kickstart-{kickstart.id}"
    return Response(kickstart.content, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}.cfg"'})


@router.post("/esxi-pxe/kickstarts/upload", response_model=None)
async def upload_esxi_kickstart_from_ui(
    request: Request,
    kickstart_file: UploadFile = File(...),
    name: str = Form(""),
    description: str = Form(""),
    enabled: bool = Form(False),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    try:
        content = decode_kickstart_upload(await kickstart_file.read(), max_bytes=get_settings().esxi_kickstart_max_bytes)
        kickstart = EsxiKickstart(
            name=normalize_kickstart_name(name or Path(kickstart_file.filename or "uploaded-kickstart").stem),
            description=description or None,
            content=content,
            content_hash=content_hash(content),
            rendered_content=content,
            enabled=enabled,
        )
        db.add(kickstart)
        db.flush()
        kickstart.http_path = canonical_http_path(kickstart.id)
        db.commit()
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        return render(
            request,
            "esxi_pxe.html",
            {
                "identity": identity,
                **esxi_pxe_page_context(db, identity, error=str(exc)),
                "appliance_apply_status": appliance_apply_status(db, "esxi_pxe"),
            },
            status_code=400,
        )
    record_audit(db, actor=identity.username, action="upload_esxi_kickstart", resource_type="esxi_kickstart", resource_id=str(kickstart.id), detail=f"name={kickstart.name} hash={kickstart.content_hash}", request_id=request.state.request_id)
    return RedirectResponse(f"/esxi-pxe?kickstart_id={kickstart.id}", status_code=303)


@router.post("/esxi-pxe/isos/upload", response_model=None)
async def upload_esxi_installer_iso_from_ui(
    request: Request,
    iso_file: UploadFile = File(...),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    wants_json = request.headers.get("X-LabFoundry-Upload") == "1"
    try:
        iso = await store_installer_iso_upload(iso_file, max_bytes=get_settings().esxi_installer_iso_max_bytes)
    except ValueError as exc:
        status_code = 413 if "too large" in str(exc).lower() else 400
        if wants_json:
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=status_code)
        return render(
            request,
            "esxi_pxe.html",
            {
                "identity": identity,
                **esxi_pxe_page_context(db, identity, error=str(exc)),
                "appliance_apply_status": appliance_apply_status(db, "esxi_pxe"),
            },
            status_code=status_code,
        )
    record_audit(db, actor=identity.username, action="upload_esxi_installer_iso", resource_type="esxi_installer_iso", resource_id=iso["relative_path"], detail=f"path={iso['path']} size={iso['size_bytes']}", request_id=request.state.request_id)
    if wants_json:
        return JSONResponse({"status": "uploaded", **iso})
    return RedirectResponse("/esxi-pxe#esxi-pxe-hosts-panel", status_code=303)


@router.post("/esxi-pxe/kickstarts/{kickstart_id}/import-filesystem", response_model=None)
def import_esxi_kickstart_filesystem_copy(
    kickstart_id: int,
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    kickstart = db.get(EsxiKickstart, kickstart_id)
    if not kickstart:
        raise HTTPException(status_code=404, detail="Kickstart not found")
    path = generated_kickstart_path(kickstart.id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Generated Kickstart file not found")
    assign_kickstart_content(kickstart, normalize_kickstart_content(path.read_text(encoding="utf-8"), max_bytes=get_settings().esxi_kickstart_max_bytes), max_bytes=get_settings().esxi_kickstart_max_bytes)
    db.add(kickstart)
    db.commit()
    record_audit(db, actor=identity.username, action="import_esxi_kickstart_from_filesystem", resource_type="esxi_kickstart", resource_id=str(kickstart.id), detail=f"path={path} hash={kickstart.content_hash}", request_id=request.state.request_id)
    return RedirectResponse(f"/esxi-pxe?kickstart_id={kickstart.id}", status_code=303)


@router.post("/esxi-pxe/hosts", response_model=None)
def create_esxi_pxe_host_from_ui(
    request: Request,
    hostname: str = Form(...),
    mac_address: str = Form(...),
    kickstart_id: str = Form(""),
    installer_iso_path: str = Form(""),
    enabled: bool = Form(False),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    try:
        normalized_iso_path = normalize_installer_iso_path(installer_iso_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    host = EsxiPxeHost(hostname=hostname.strip(), mac_address=mac_address.strip().lower(), kickstart_id=int(kickstart_id) if kickstart_id else None, installer_iso_path=normalized_iso_path, enabled=enabled)
    db.add(host)
    db.commit()
    record_audit(db, actor=identity.username, action="update_esxi_pxe_host", resource_type="esxi_pxe_host", resource_id=str(host.id), detail=f"kickstart_id={host.kickstart_id} installer_iso={host.installer_iso_path}", request_id=request.state.request_id)
    return RedirectResponse("/esxi-pxe#esxi-pxe-hosts", status_code=303)


@router.post("/esxi-pxe/hosts/{host_id}", response_model=None)
def update_esxi_pxe_host_from_ui(
    host_id: int,
    request: Request,
    hostname: str = Form(...),
    mac_address: str = Form(...),
    kickstart_id: str = Form(""),
    installer_iso_path: str = Form(""),
    enabled: bool = Form(False),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    require_esxi_pxe_write(identity)
    verify_csrf(request, csrf)
    host = db.get(EsxiPxeHost, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="ESXi PXE host not found")
    try:
        normalized_iso_path = normalize_installer_iso_path(installer_iso_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    host.hostname = hostname.strip()
    host.mac_address = mac_address.strip().lower()
    host.kickstart_id = int(kickstart_id) if kickstart_id else None
    host.installer_iso_path = normalized_iso_path
    host.enabled = enabled
    host.updated_at = utcnow()
    db.add(host)
    db.commit()
    record_audit(db, actor=identity.username, action="update_esxi_pxe_host", resource_type="esxi_pxe_host", resource_id=str(host.id), detail=f"kickstart_id={host.kickstart_id} installer_iso={host.installer_iso_path}", request_id=request.state.request_id)
    return RedirectResponse("/esxi-pxe#esxi-pxe-hosts", status_code=303)


@router.get("/backup-restore", response_class=HTMLResponse, response_model=None)
def backup_restore_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    require_admin_identity(identity)
    return render(request, "backup_restore.html", {"identity": identity, **backup_restore_context(db)})


@router.post("/backup-restore/export", response_model=None)
def export_backup_restore_archive(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    archive = export_settings_archive(db, actor=identity.username)
    exported_at = utcnow().strftime("%Y%m%d-%H%M%SZ")
    record_audit(
        db,
        actor=identity.username,
        action="export_settings_backup",
        resource_type="settings_backup",
        detail=f"{sum(len(value) for value in archive['data'].values() if isinstance(value, list))} desired-state rows",
        request_id=request.state.request_id,
    )
    return Response(
        json.dumps(archive, indent=2, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="labfoundry-settings-{exported_at}.json"'},
    )


@router.post("/backup-restore/restore", response_class=HTMLResponse, response_model=None)
async def restore_backup_restore_archive(
    request: Request,
    archive_file: UploadFile = File(...),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    raw_archive = await archive_file.read()
    if len(raw_archive) > 3_000_000:
        return render(
            request,
            "backup_restore.html",
            {"identity": identity, **backup_restore_context(db, error="The settings archive is too large.")},
            status_code=413,
        )
    try:
        archive = json.loads(raw_archive.decode("utf-8"))
        summary = archive_summary(archive)
        counts = restore_settings_archive(db, archive)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return render(
            request,
            "backup_restore.html",
            {"identity": identity, **backup_restore_context(db, error=str(exc))},
            status_code=400,
        )
    record_audit(
        db,
        actor=identity.username,
        action="restore_settings_backup",
        resource_type="settings_backup",
        detail=f"Restored {sum(counts.values())} desired-state rows from {archive_file.filename or 'uploaded archive'}; services forced stopped/unconfigured.",
        request_id=request.state.request_id,
    )
    return render(
        request,
        "backup_restore.html",
        {
            "identity": identity,
            **backup_restore_context(
                db,
                result={
                    "title": "Settings restored",
                    "message": "Desired-state settings were restored. Services are stopped and unconfigured until reviewed and applied through the global appliance workflow.",
                    "summary": summary,
                    "counts": counts,
                },
            ),
        },
    )


@router.post("/backup-restore/factory-reset", response_class=HTMLResponse, response_model=None)
def factory_reset_backup_restore(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    require_admin_identity(identity)
    verify_csrf(request, csrf)
    counts = factory_reset_desired_state(db)
    record_audit(
        db,
        actor=identity.username,
        action="factory_reset_settings",
        resource_type="settings_backup",
        detail="Desired-state settings reset to core factory defaults without demo resources or service listener bindings; services forced stopped/unconfigured.",
        request_id=request.state.request_id,
    )
    return render(
        request,
        "backup_restore.html",
        {
            "identity": identity,
            **backup_restore_context(
                db,
                result={
                    "title": "Factory reset complete",
                    "message": "Desired-state settings were reset to core LabFoundry defaults without demo resources or service listener bindings. Non-management NICs are desired admin down, and services are stopped and unconfigured until reviewed and applied through the global appliance workflow.",
                    "counts": counts,
                },
            ),
        },
    )


@router.get("/settings", response_class=HTMLResponse, response_model=None)
def settings_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request,
        "settings.html",
        {"identity": identity, **appliance_settings_context(db), "appliance_apply_status": appliance_apply_status(db, "appliance_settings")},
    )


@router.post("/settings", response_model=None)
def update_settings_from_ui(
    request: Request,
    fqdn: str = Form("labfoundry.labfoundry.internal"),
    management_https_enabled: bool = Form(False),
    root_ssh_enabled: bool = Form(False),
    external_dns_servers: str = Form(""),
    ntp_servers: str = Form(""),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_appliance_settings_row(db)
    previous_fqdn = settings.fqdn
    settings.fqdn = normalize_fqdn(fqdn) or "labfoundry.labfoundry.internal"
    settings.management_https_enabled = bool(management_https_enabled)
    settings.root_ssh_enabled = bool(root_ssh_enabled)
    settings.external_dns_servers = normalize_multiline_values(external_dns_servers)
    settings.ntp_servers = normalize_multiline_values(ntp_servers)
    settings.config_path = APPLIANCE_SETTINGS_STAGED_CONFIG_PATH
    settings.updated_at = utcnow()
    dns_settings = get_dns_settings_row(db)
    management = appliance_settings_management_context(db)
    ca_settings = get_ca_settings_row(db)
    management_https_cert_path, management_https_key_path, _management_https_chain_path = ca_managed_certificate_paths(db, "appliance:https")
    management_https_cert_available = bool(management_https_cert_path and management_https_key_path and ca_certificate_available(db, "appliance:https"))
    validation_errors, _validation_warnings = validate_appliance_settings(
        settings,
        local_dns_enabled=bool(dns_settings.enabled),
        management_interface=management,
        dns_record_conflict=bool(dns_settings.enabled) and appliance_dns_record_conflict(db, settings.fqdn),
        ca_enabled=bool(ca_settings.enabled),
        management_https_cert_available=management_https_cert_available,
    )
    dns_record_action = None
    if not validation_errors:
        dns_record_action = ensure_dns_for_appliance_settings(db, settings, previous_fqdn=previous_fqdn, actor=identity.username)
    db.add(settings)
    db.commit()
    record_audit(db, actor=identity.username, action="update_appliance_settings", resource_type="settings", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = appliance_settings_context(db, reconcile_dns=not validation_errors)
        saved = context["appliance_settings"]
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": saved.updated_at.isoformat(),
                "fqdn": saved.fqdn,
                "management_https_enabled": saved.management_https_enabled,
                "management_https_cert_available": context["management_https_cert_available"],
                "root_ssh_enabled": saved.root_ssh_enabled,
                "external_dns_servers": context["appliance_settings_json"]["external_dns_servers"],
                "ntp_servers": context["appliance_settings_json"]["ntp_servers"],
                "local_dns_enabled": context["local_dns_enabled"],
                "management_interface": context["management_interface"],
                "dns_record_action": dns_record_action,
                "valid": not context["appliance_settings_validation_errors"],
                "validation_errors": context["appliance_settings_validation_errors"],
                "validation_warnings": context["appliance_settings_validation_warnings"],
                "config_path": saved.config_path,
                "config_preview": context["appliance_settings_config_preview"],
            }
        )
    return RedirectResponse("/settings", status_code=303)


@router.get("/{page}", response_class=HTMLResponse, response_model=None)
def placeholder_page(page: str, request: Request, identity: Identity = Depends(require_session_identity)) -> HTMLResponse:
    known = {
        "physical-interfaces": "Physical Interfaces",
        "vlan-interfaces": "VLAN Interfaces",
        "certificate-authority": "Certificate Authority",
        "vcf-offline-depot": "VCF Offline Depot",
        "vcf-backups": "VCF Backups",
        "logs": "Logs",
    }
    if page not in known:
        raise HTTPException(status_code=404, detail="Page not found")
    return render(request, "placeholder.html", {"identity": identity, "title": known[page]})

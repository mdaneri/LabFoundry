from datetime import datetime
from ipaddress import ip_interface
from pathlib import Path
import socket
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from labfoundry.app.adapters.system import SystemAdapter
from labfoundry.app.audit import record_audit
from labfoundry.app.config import Settings, get_settings
from labfoundry.app.database import get_db
from labfoundry.app.models import (
    ApplianceSettings,
    ApiToken,
    AuditEvent,
    CaCertificate,
    CaSettings,
    DhcpOption,
    DhcpReservation,
    DhcpScope,
    DhcpSettings,
    DnsRecord,
    DnsSettings,
    FirewallRule,
    FirewallSettings,
    Job,
    KmsSettings,
    NatRule,
    PhysicalInterface,
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
from labfoundry.app.schemas import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenResponse,
    AuditEventResponse,
    DashboardResponse,
    DhcpLeaseResponse,
    DhcpReservationCreate,
    DhcpReservationResponse,
    DhcpOptionCreate,
    DhcpOptionResponse,
    DhcpScopeCreate,
    DhcpScopeResponse,
    DhcpSettingsResponse,
    DhcpSettingsUpdate,
    DhcpStatusResponse,
    ConfigApplyResponse,
    ConfigValidationResponse,
    DnsHostsImportRequest,
    DnsHostsImportResponse,
    DnsRecordCreate,
    DnsRecordResponse,
    DnsSettingsResponse,
    DnsSettingsUpdate,
    DnsStatusResponse,
    FirewallRuleCreate,
    FirewallRuleResponse,
    FirewallSettingsResponse,
    FirewallSettingsUpdate,
    FirewallStatusResponse,
    IdentityResponse,
    JobResponse,
    NatRuleCreate,
    NatRuleResponse,
    PhysicalInterfaceResponse,
    RouteCreate,
    RouteResponse,
    ServiceActionResponse,
    ServiceStateResponse,
    SettingsResponse,
    SettingsUpdate,
    VcfBackupStatusResponse,
    VcfOfflineDepotStatusResponse,
    VcfPrivateRegistryStatusResponse,
    VlanCreate,
    VlanResponse,
    WanPolicyCreate,
    WanPolicyResponse,
    WanStatusResponse,
)
from labfoundry.app.services.firewall import FIREWALL_SOURCE_GROUPS_SETTING_KEY, firewall_interface_networks, firewall_source_group_state
from labfoundry.app.services.networking import normalize_interface_mode
from labfoundry.app.services.routes_wan import validate_nat_source
from labfoundry.app.security import (
    Identity,
    ALL_SCOPES,
    authenticate_user,
    require_scope,
    role_allows_scopes,
    scopes_from_string,
)
from labfoundry.app.services.dnsmasq import (
    DNS_CONDITIONAL_FORWARDERS_SETTING_KEY,
    dhcp_bind_target_names,
    dns_domain_warnings,
    dns_settings_to_dict,
    dnsmasq_test_command,
    join_conditional_forwarders,
    join_domains,
    join_servers,
    parse_hosts_records,
    parse_dnsmasq_leases,
    render_dnsmasq_config,
    reservation_dns_record,
    split_domains,
    validate_dns_record,
    validate_dhcp_bind_targets,
    validate_dhcp_settings,
    validate_dns_listen_targets,
    validate_dns_settings,
)
from labfoundry.app.services.appliance_settings import (
    APPLIANCE_SETTINGS_STAGED_CONFIG_PATH,
    appliance_settings_to_dict,
    management_interface_context,
    normalize_fqdn,
    normalize_multiline_values,
    render_appliance_settings_config,
    validate_appliance_settings,
)
from labfoundry.app.services.firewall import (
    FIREWALL_ACTIONS,
    FIREWALL_DIRECTIONS,
    FIREWALL_POLICIES,
    FIREWALL_PROTOCOLS,
    FIREWALL_SOURCE_GROUPS_SETTING_KEY,
    FIREWALL_STAGED_CONFIG_PATH,
    firewall_interface_networks,
    firewall_source_group_state,
    managed_service_firewall_rules,
    render_nftables_config,
    validate_firewall_source_groups,
    validate_firewall_rule,
    validate_firewall_state,
)
from labfoundry.app.services.networking import normalize_interface_mode, sync_host_physical_interfaces
from labfoundry.app.services.vcf_backups import vcf_backup_settings_to_dict
from labfoundry.app.services.vcf_private_registry import (
    VCF_REGISTRY_UPLOADED_CA_BUNDLE_PEM_KEY,
    validate_vcf_registry_state,
    vcf_registry_settings_to_dict,
)
from labfoundry.app.services.vcf_offline_depot import (
    VCF_DEPOT_ACTIVATION_VALUE_KEY,
    VCF_DEPOT_DEFAULT_STORE_PATH,
    VCF_DEPOT_LEGACY_STORE_PATH,
    VCF_DEPOT_TOKEN_VALUE_KEY,
    detect_vcf_download_tool_version,
    find_local_vcf_download_tool_archive,
    validate_vcf_depot_state,
    vcf_depot_settings_to_dict,
)
from labfoundry.app.token_service import create_token_for_user, token_to_response

router = APIRouter(prefix="/api/v1")
DNSMASQ_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/dnsmasq/labfoundry.conf"

APPROVED_SERVICES = {
    "routing",
    "firewall",
    "dns",
    "dhcp",
    "kms",
    "repository",
    "vcf-offline-depot",
    "vcf-private-registry",
    "vcf-backups",
    "ca",
    "ldap",
    "auth",
}


def validate_vlan_api_payload(payload: VlanCreate, db: Session) -> dict:
    values = payload.model_dump()
    values["parent_interface"] = values["parent_interface"].strip()
    values["ip_cidr"] = values["ip_cidr"].strip()
    if not values["ip_cidr"]:
        raise HTTPException(status_code=422, detail="VLAN IP CIDR is required.")
    try:
        ip_interface(values["ip_cidr"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="VLAN IP CIDR must be a valid address and prefix, for example 192.168.50.1/24.") from exc
    parent = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == values["parent_interface"])).scalar_one_or_none()
    if not parent or normalize_interface_mode(parent.mode) != "trunk":
        raise HTTPException(
            status_code=409,
            detail=f"{values['parent_interface'] or 'Selected parent'} is not a trunk interface. Mark the physical NIC as trunk before creating VLANs on it.",
        )
    return values


def get_firewall_settings(db: Session) -> FirewallSettings:
    settings = db.execute(select(FirewallSettings)).scalar_one_or_none()
    if settings is None:
        settings = FirewallSettings()
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


def get_kms_settings_row(db: Session) -> KmsSettings:
    settings = db.execute(select(KmsSettings)).scalar_one_or_none()
    if settings is None:
        settings = KmsSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_appliance_settings(db: Session) -> ApplianceSettings:
    settings = db.execute(select(ApplianceSettings)).scalar_one_or_none()
    if settings is None:
        settings = ApplianceSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def ca_managed_certificate_available(db: Session, owner: str) -> tuple[bool, str, str]:
    certificate = db.execute(select(CaCertificate).where(CaCertificate.managed_owner == owner)).scalar_one_or_none()
    if certificate is None or certificate.status != "issued":
        return False, "", ""
    available = bool(certificate.certificate_pem and certificate.private_key_encrypted and certificate.cert_path and certificate.key_path)
    return available, certificate.cert_path or "", certificate.key_path or ""


def appliance_settings_response(db: Session, app_settings: Settings) -> SettingsResponse:
    desired = get_appliance_settings(db)
    dns_settings = db.execute(select(DnsSettings)).scalar_one_or_none()
    ca_settings = db.execute(select(CaSettings)).scalar_one_or_none()
    management_https_cert_available, management_https_cert_path, management_https_key_path = ca_managed_certificate_available(db, "appliance:https")
    local_dns_enabled = bool(dns_settings and dns_settings.enabled)
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    management = management_interface_context(interfaces)
    validation_errors, validation_warnings = validate_appliance_settings(
        desired,
        local_dns_enabled=local_dns_enabled,
        management_interface=management,
        ca_enabled=bool(ca_settings and ca_settings.enabled),
        management_https_cert_available=management_https_cert_available,
    )
    return SettingsResponse(
        app_name=app_settings.app_name,
        appliance_hostname=socket.gethostname(),
        dry_run_system_adapters=app_settings.dry_run_system_adapters,
        repository_path=str(app_settings.repository_path),
        vcf_backup_path=str(app_settings.vcf_backup_path),
        appliance_fqdn=desired.fqdn,
        management_https_enabled=desired.management_https_enabled,
        management_https_cert_available=management_https_cert_available,
        root_ssh_enabled=desired.root_ssh_enabled,
        external_dns_servers=appliance_settings_to_dict(desired)["external_dns_servers"],
        ntp_servers=appliance_settings_to_dict(desired)["ntp_servers"],
        appliance_settings_config_path=desired.config_path,
        local_dns_enabled=local_dns_enabled,
        management_interface=management["name"],
        management_ip=management["ip"],
        valid=not validation_errors,
        validation_errors=validation_errors,
        validation_warnings=validation_warnings,
        config_preview=render_appliance_settings_config(
            desired,
            local_dns_enabled=local_dns_enabled,
            management_interface=management,
            management_https_cert_path=management_https_cert_path,
            management_https_key_path=management_https_key_path,
        ),
    )


def assign_firewall_rule_values(rule: FirewallRule, values: dict) -> FirewallRule:
    rule.name = values["name"].strip()
    rule.direction = values.get("direction", "input")
    rule.action = values.get("action", "accept")
    rule.protocol = values.get("protocol", "tcp")
    rule.source = values.get("source", "any").strip() or "any"
    rule.destination = values.get("destination", "any").strip() or "any"
    rule.destination_port = values.get("destination_port", "").strip()
    rule.interface_name = values.get("interface_name", "").strip()
    rule.priority = values.get("priority", 100)
    rule.enabled = values.get("enabled", True)
    rule.description = values.get("description") or None
    rule.updated_at = utcnow()
    return rule


def get_vcf_backup_settings(db: Session) -> VcfBackupSettings:
    settings = db.execute(select(VcfBackupSettings).options(selectinload(VcfBackupSettings.sftp_user))).scalar_one_or_none()
    if settings is None:
        user = db.execute(select(User).where(User.enabled.is_(True)).order_by(User.username)).scalar_one_or_none()
        settings = VcfBackupSettings(sftp_user_id=user.id if user else None)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_vcf_private_registry_settings(db: Session) -> VcfPrivateRegistrySettings:
    settings = db.execute(select(VcfPrivateRegistrySettings)).scalar_one_or_none()
    if settings is None:
        settings = VcfPrivateRegistrySettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_vcf_offline_depot_settings(db: Session) -> VcfOfflineDepotSettings:
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


def vcf_registry_ca_bundle_status(db: Session) -> tuple[str, bool]:
    ca_settings = db.execute(select(CaSettings)).scalar_one_or_none()
    if ca_settings is not None and ca_settings.enabled:
        return "local-ca", True
    uploaded = db.execute(select(Setting).where(Setting.key == VCF_REGISTRY_UPLOADED_CA_BUNDLE_PEM_KEY)).scalar_one_or_none()
    return "uploaded", bool(uploaded and uploaded.value.strip())


def vcf_depot_secret_status(db: Session) -> tuple[bool, bool]:
    token = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_VALUE_KEY)).scalar_one_or_none()
    activation_code = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_ACTIVATION_VALUE_KEY)).scalar_one_or_none()
    return bool(token and token.value.strip()), bool(activation_code and activation_code.value.strip())


def firewall_validation_payload(db: Session) -> tuple[FirewallSettings, list[FirewallRule], str, list[str]]:
    settings = get_firewall_settings(db)
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
        vcf_backup_settings=get_vcf_backup_settings(db),
        vcf_depot_settings=get_vcf_offline_depot_settings(db),
        vcf_registry_settings=get_vcf_private_registry_settings(db),
        interface_networks=interface_networks,
        source_groups=source_group_state["groups"],
        source_group_assignments=source_group_state["assignments"],
    )
    return (
        settings,
        rules,
        render_nftables_config(
            settings,
            rules,
            generated_rules,
            source_groups=source_group_state["groups"],
            replace_labfoundry_service_rules=True,
        ),
        [
            *validate_firewall_source_groups(source_group_state["groups"]),
            *validate_firewall_state(
                settings,
                rules,
                generated_rules,
                source_groups=source_group_state["groups"],
                replace_labfoundry_service_rules=True,
            ),
        ],
    )


def stage_api_firewall_config(config_preview: str) -> str:
    path = Path(FIREWALL_STAGED_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config_preview, encoding="utf-8")
    return str(path)


def stage_api_dnsmasq_config(config_preview: str) -> str:
    path = Path(DNSMASQ_STAGED_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config_preview, encoding="utf-8")
    return str(path)


@router.post(
    "/auth/login",
    response_model=ApiTokenCreated,
    tags=["Auth"],
    operation_id="loginForApi",
    responses={401: {"description": "Invalid credentials"}},
)
def login_for_api(
    payload: ApiTokenCreate,
    request: Request,
    username: str = Query(...),
    password: str = Query(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiTokenCreated:
    user = authenticate_user(db, username, password)
    if not user:
        record_audit(
            db,
            actor=username,
            action="api_login_failed",
            resource_type="auth",
            success=False,
            request_id=getattr(request.state, "request_id", None),
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return create_token_for_user(db, user=user, create=payload, settings=settings, actor=user.username)


@router.get("/auth/me", response_model=IdentityResponse, tags=["Auth"], operation_id="getCurrentIdentity")
def get_me(identity: Annotated[Identity, Depends(require_scope("read:dashboard"))]) -> IdentityResponse:
    return IdentityResponse(
        username=identity.username,
        role=identity.role,
        scopes=sorted(identity.scopes),
        auth_type=identity.auth_type,
    )


@router.get("/api-tokens", response_model=list[ApiTokenResponse], tags=["API Tokens"], operation_id="listApiTokens")
def list_api_tokens(
    identity: Annotated[Identity, Depends(require_scope("read:dashboard"))],
    db: Session = Depends(get_db),
) -> list[ApiTokenResponse]:
    query = select(ApiToken).order_by(desc(ApiToken.created_at))
    if identity.role != "admin":
        query = query.where(ApiToken.owner_user_id == identity.user_id)
    return [token_to_response(token) for token in db.execute(query).scalars().all()]


@router.post(
    "/api-tokens",
    response_model=ApiTokenCreated,
    status_code=201,
    tags=["API Tokens"],
    operation_id="createApiToken",
)
def create_api_token(
    payload: ApiTokenCreate,
    identity: Annotated[Identity, Depends(require_scope("read:dashboard"))],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiTokenCreated:
    user = db.get(User, identity.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Current user not found")
    return create_token_for_user(db, user=user, create=payload, settings=settings, actor=identity.username)


@router.get("/api-tokens/{token_id}", response_model=ApiTokenResponse, tags=["API Tokens"], operation_id="getApiToken")
def get_api_token(
    token_id: int,
    identity: Annotated[Identity, Depends(require_scope("read:dashboard"))],
    db: Session = Depends(get_db),
) -> ApiTokenResponse:
    token = db.get(ApiToken, token_id)
    if not token or (identity.role != "admin" and token.owner_user_id != identity.user_id):
        raise HTTPException(status_code=404, detail="API token not found")
    return token_to_response(token)


def revoke_token(db: Session, token: ApiToken, identity: Identity) -> ApiTokenResponse:
    token.enabled = False
    token.revoked_at = utcnow()
    token.revoked_by = identity.username
    db.add(token)
    db.commit()
    db.refresh(token)
    record_audit(
        db,
        actor=identity.username,
        action="revoke_api_token",
        resource_type="api_token",
        resource_id=str(token.id),
        detail=f"Revoked API token {token.name}",
    )
    return token_to_response(token)


@router.delete("/api-tokens/{token_id}", status_code=204, tags=["API Tokens"], operation_id="deleteApiToken")
def delete_api_token(
    token_id: int,
    identity: Annotated[Identity, Depends(require_scope("read:dashboard"))],
    db: Session = Depends(get_db),
) -> Response:
    token = db.get(ApiToken, token_id)
    if not token or (identity.role != "admin" and token.owner_user_id != identity.user_id):
        raise HTTPException(status_code=404, detail="API token not found")
    revoke_token(db, token, identity)
    return Response(status_code=204)


@router.post("/api-tokens/{token_id}/revoke", response_model=ApiTokenResponse, tags=["API Tokens"], operation_id="revokeApiToken")
def revoke_api_token(
    token_id: int,
    identity: Annotated[Identity, Depends(require_scope("read:dashboard"))],
    db: Session = Depends(get_db),
) -> ApiTokenResponse:
    token = db.get(ApiToken, token_id)
    if not token or (identity.role != "admin" and token.owner_user_id != identity.user_id):
        raise HTTPException(status_code=404, detail="API token not found")
    return revoke_token(db, token, identity)


@router.get("/dashboard", response_model=DashboardResponse, tags=["Dashboard"], operation_id="getDashboard")
def get_dashboard(
    identity: Annotated[Identity, Depends(require_scope("read:dashboard"))],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DashboardResponse:
    services = db.execute(select(ServiceState).order_by(ServiceState.display_name)).scalars().all()
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    policies = db.execute(select(WanPolicy).where(WanPolicy.enabled.is_(True)).order_by(WanPolicy.name)).scalars().all()
    audit_events = db.execute(select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(5)).scalars().all()
    return DashboardResponse(
        appliance={
            "hostname": socket.gethostname(),
            "management_ip": "127.0.0.1",
            "uptime": "development session",
            "cpu_usage_percent": 12,
            "memory_usage_percent": 38,
        },
        service_health=[ServiceStateResponse.model_validate(service) for service in services],
        interfaces=[PhysicalInterfaceResponse.model_validate(interface) for interface in interfaces],
        active_wan_policies=[WanPolicyResponse.model_validate(policy) for policy in policies],
        disk_usage={"root_percent": 41, "repository_percent": 3, "vcf_backup_percent": 1},
        recent_audit_events=[
            {
                "created_at": event.created_at.isoformat(),
                "actor": event.actor,
                "action": event.action,
                "resource_type": event.resource_type,
                "success": event.success,
            }
            for event in audit_events
        ],
    )


@router.get(
    "/interfaces/physical",
    response_model=list[PhysicalInterfaceResponse],
    tags=["Interfaces"],
    operation_id="listPhysicalInterfaces",
)
def list_physical_interfaces(
    identity: Annotated[Identity, Depends(require_scope("read:interfaces"))],
    db: Session = Depends(get_db),
) -> list[PhysicalInterfaceResponse]:
    return [PhysicalInterfaceResponse.model_validate(row) for row in db.execute(select(PhysicalInterface)).scalars().all()]


@router.get(
    "/interfaces/physical/{name}",
    response_model=PhysicalInterfaceResponse,
    tags=["Interfaces"],
    operation_id="getPhysicalInterface",
)
def get_physical_interface(
    name: str,
    identity: Annotated[Identity, Depends(require_scope("read:interfaces"))],
    db: Session = Depends(get_db),
) -> PhysicalInterfaceResponse:
    interface = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == name)).scalar_one_or_none()
    if not interface:
        raise HTTPException(status_code=404, detail="Interface not found")
    return PhysicalInterfaceResponse.model_validate(interface)


@router.patch(
    "/interfaces/physical/{name}",
    response_model=PhysicalInterfaceResponse,
    tags=["Interfaces"],
    operation_id="updatePhysicalInterface",
)
def update_physical_interface(
    name: str,
    payload: dict,
    identity: Annotated[Identity, Depends(require_scope("write:interfaces"))],
    db: Session = Depends(get_db),
) -> PhysicalInterfaceResponse:
    interface = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == name)).scalar_one_or_none()
    if not interface:
        raise HTTPException(status_code=404, detail="Interface not found")
    for field in ("role", "mtu", "admin_state", "ip_cidr"):
        if field in payload:
            setattr(interface, field, payload[field])
    if "mode" in payload:
        new_mode = normalize_interface_mode(payload["mode"])
        vlan_count = db.scalar(select(func.count()).select_from(VlanInterface).where(VlanInterface.parent_interface == interface.name)) or 0
        if new_mode != "trunk" and vlan_count:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{interface.name} is the parent of {vlan_count} VLAN interface{'s' if vlan_count != 1 else ''}. "
                    "Move or delete those VLANs before changing the link type."
                ),
            )
        interface.mode = new_mode
    interface.desired_state_source = "user"
    db.add(interface)
    db.commit()
    db.refresh(interface)
    record_audit(db, actor=identity.username, action="update_interface", resource_type="interface", resource_id=name)
    return PhysicalInterfaceResponse.model_validate(interface)


@router.post("/interfaces/physical/{name}/enable", response_model=PhysicalInterfaceResponse, tags=["Interfaces"], operation_id="enablePhysicalInterface")
def enable_physical_interface(
    name: str,
    identity: Annotated[Identity, Depends(require_scope("write:interfaces"))],
    db: Session = Depends(get_db),
) -> PhysicalInterfaceResponse:
    return update_physical_interface(name, {"admin_state": "up"}, identity, db)


@router.post("/interfaces/physical/{name}/disable", response_model=PhysicalInterfaceResponse, tags=["Interfaces"], operation_id="disablePhysicalInterface")
def disable_physical_interface(
    name: str,
    identity: Annotated[Identity, Depends(require_scope("write:interfaces"))],
    db: Session = Depends(get_db),
) -> PhysicalInterfaceResponse:
    return update_physical_interface(name, {"admin_state": "down"}, identity, db)


@router.post("/interfaces/refresh", response_model=list[PhysicalInterfaceResponse], tags=["Interfaces"], operation_id="refreshPhysicalInterfaces")
def refresh_physical_interfaces(
    identity: Annotated[Identity, Depends(require_scope("write:interfaces"))],
    db: Session = Depends(get_db),
) -> list[PhysicalInterfaceResponse]:
    interfaces, discovered_count = sync_host_physical_interfaces(db)
    record_audit(
        db,
        actor=identity.username,
        action="refresh_physical_interface_inventory",
        resource_type="interface",
        detail=f"{discovered_count} host interface{'s' if discovered_count != 1 else ''} discovered",
    )
    return [PhysicalInterfaceResponse.model_validate(row) for row in interfaces]


@router.get("/vlans", response_model=list[VlanResponse], tags=["VLANs"], operation_id="listVlans")
def list_vlans(
    identity: Annotated[Identity, Depends(require_scope("read:vlans"))],
    db: Session = Depends(get_db),
) -> list[VlanResponse]:
    return [VlanResponse.model_validate(row) for row in db.execute(select(VlanInterface)).scalars().all()]


@router.post("/vlans", response_model=VlanResponse, status_code=201, tags=["VLANs"], operation_id="createVlan")
def create_vlan(
    payload: VlanCreate,
    identity: Annotated[Identity, Depends(require_scope("write:vlans"))],
    db: Session = Depends(get_db),
) -> VlanResponse:
    values = validate_vlan_api_payload(payload, db)
    vlan = VlanInterface(name=f"{values['parent_interface']}.{values['vlan_id']}", **values)
    db.add(vlan)
    db.commit()
    db.refresh(vlan)
    record_audit(db, actor=identity.username, action="create_vlan", resource_type="vlan", resource_id=str(vlan.id))
    return VlanResponse.model_validate(vlan)


@router.get("/vlans/{vlan_id}", response_model=VlanResponse, tags=["VLANs"], operation_id="getVlan")
def get_vlan(
    vlan_id: int,
    identity: Annotated[Identity, Depends(require_scope("read:vlans"))],
    db: Session = Depends(get_db),
) -> VlanResponse:
    vlan = db.get(VlanInterface, vlan_id)
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN not found")
    return VlanResponse.model_validate(vlan)


@router.patch("/vlans/{vlan_id}", response_model=VlanResponse, tags=["VLANs"], operation_id="updateVlan")
def update_vlan(
    vlan_id: int,
    payload: VlanCreate,
    identity: Annotated[Identity, Depends(require_scope("write:vlans"))],
    db: Session = Depends(get_db),
) -> VlanResponse:
    vlan = db.get(VlanInterface, vlan_id)
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN not found")
    values = validate_vlan_api_payload(payload, db)
    for key, value in values.items():
        setattr(vlan, key, value)
    vlan.name = f"{vlan.parent_interface}.{vlan.vlan_id}"
    db.commit()
    db.refresh(vlan)
    record_audit(db, actor=identity.username, action="update_vlan", resource_type="vlan", resource_id=str(vlan.id))
    return VlanResponse.model_validate(vlan)


@router.delete("/vlans/{vlan_id}", status_code=204, tags=["VLANs"], operation_id="deleteVlan")
def delete_vlan(
    vlan_id: int,
    identity: Annotated[Identity, Depends(require_scope("write:vlans"))],
    db: Session = Depends(get_db),
) -> Response:
    vlan = db.get(VlanInterface, vlan_id)
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN not found")
    db.delete(vlan)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_vlan", resource_type="vlan", resource_id=str(vlan_id))
    return Response(status_code=204)


@router.post("/vlans/{vlan_id}/enable", response_model=VlanResponse, tags=["VLANs"], operation_id="enableVlan")
def enable_vlan(vlan_id: int, identity: Annotated[Identity, Depends(require_scope("write:vlans"))], db: Session = Depends(get_db)) -> VlanResponse:
    vlan = db.get(VlanInterface, vlan_id)
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN not found")
    vlan.enabled = True
    db.commit()
    db.refresh(vlan)
    record_audit(db, actor=identity.username, action="enable_vlan", resource_type="vlan", resource_id=str(vlan.id))
    return VlanResponse.model_validate(vlan)


@router.post("/vlans/{vlan_id}/disable", response_model=VlanResponse, tags=["VLANs"], operation_id="disableVlan")
def disable_vlan(vlan_id: int, identity: Annotated[Identity, Depends(require_scope("write:vlans"))], db: Session = Depends(get_db)) -> VlanResponse:
    vlan = db.get(VlanInterface, vlan_id)
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN not found")
    vlan.enabled = False
    db.commit()
    db.refresh(vlan)
    record_audit(db, actor=identity.username, action="disable_vlan", resource_type="vlan", resource_id=str(vlan.id))
    return VlanResponse.model_validate(vlan)


@router.post("/vlans/{vlan_id}/apply", response_model=VlanResponse, tags=["VLANs"], operation_id="applyVlan")
def apply_vlan(vlan_id: int, identity: Annotated[Identity, Depends(require_scope("write:vlans"))], db: Session = Depends(get_db)) -> VlanResponse:
    vlan = get_vlan(vlan_id, identity, db)
    record_audit(db, actor=identity.username, action="apply_vlan_dry_run", resource_type="vlan", resource_id=str(vlan_id))
    return vlan


@router.get("/routes", response_model=list[RouteResponse], tags=["Routes"], operation_id="listRoutes")
def list_routes(identity: Annotated[Identity, Depends(require_scope("read:routes"))], db: Session = Depends(get_db)) -> list[RouteResponse]:
    rows = db.execute(select(Route).options(selectinload(Route.wan_policy)).order_by(Route.destination_cidr)).scalars().all()
    return [route_response(row) for row in rows]


def route_response(route: Route) -> RouteResponse:
    return RouteResponse(
        id=route.id,
        destination_cidr=route.destination_cidr,
        gateway=route.gateway,
        interface_name=route.interface_name,
        metric=route.metric,
        enabled=route.enabled,
        wan_policy_id=route.wan_policy_id,
        wan_mode="interface",
        wan_policy=WanPolicyResponse.model_validate(route.wan_policy) if route.wan_policy else None,
    )


@router.post("/routes", response_model=RouteResponse, status_code=201, tags=["Routes"], operation_id="createRoute")
def create_route(payload: RouteCreate, identity: Annotated[Identity, Depends(require_scope("write:routes"))], db: Session = Depends(get_db)) -> RouteResponse:
    route = Route(**payload.model_dump())
    db.add(route)
    db.commit()
    db.refresh(route)
    record_audit(db, actor=identity.username, action="create_route", resource_type="route", resource_id=str(route.id))
    return route_response(db.get(Route, route.id))


@router.get("/routes/{route_id}", response_model=RouteResponse, tags=["Routes"], operation_id="getRoute")
def get_route(route_id: int, identity: Annotated[Identity, Depends(require_scope("read:routes"))], db: Session = Depends(get_db)) -> RouteResponse:
    route = db.execute(select(Route).options(selectinload(Route.wan_policy)).where(Route.id == route_id)).scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return route_response(route)


@router.patch("/routes/{route_id}", response_model=RouteResponse, tags=["Routes"], operation_id="updateRoute")
def update_route(route_id: int, payload: RouteCreate, identity: Annotated[Identity, Depends(require_scope("write:routes"))], db: Session = Depends(get_db)) -> RouteResponse:
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    for key, value in payload.model_dump().items():
        setattr(route, key, value)
    db.commit()
    record_audit(db, actor=identity.username, action="update_route", resource_type="route", resource_id=str(route_id))
    return get_route(route_id, identity, db)


@router.delete("/routes/{route_id}", status_code=204, tags=["Routes"], operation_id="deleteRoute")
def delete_route(route_id: int, identity: Annotated[Identity, Depends(require_scope("write:routes"))], db: Session = Depends(get_db)) -> Response:
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    db.delete(route)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_route", resource_type="route", resource_id=str(route_id))
    return Response(status_code=204)


@router.post("/routes/{route_id}/enable", response_model=RouteResponse, tags=["Routes"], operation_id="enableRoute")
def enable_route(route_id: int, identity: Annotated[Identity, Depends(require_scope("write:routes"))], db: Session = Depends(get_db)) -> RouteResponse:
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    route.enabled = True
    db.commit()
    record_audit(db, actor=identity.username, action="enable_route", resource_type="route", resource_id=str(route_id))
    return get_route(route_id, identity, db)


@router.post("/routes/{route_id}/disable", response_model=RouteResponse, tags=["Routes"], operation_id="disableRoute")
def disable_route(route_id: int, identity: Annotated[Identity, Depends(require_scope("write:routes"))], db: Session = Depends(get_db)) -> RouteResponse:
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    route.enabled = False
    db.commit()
    record_audit(db, actor=identity.username, action="disable_route", resource_type="route", resource_id=str(route_id))
    return get_route(route_id, identity, db)


@router.post("/routes/{route_id}/wan-policy", response_model=RouteResponse, tags=["Routes"], operation_id="assignRouteWanPolicy")
def assign_route_wan_policy(
    route_id: int,
    wan_policy_id: int,
    identity: Annotated[Identity, Depends(require_scope("write:routes"))],
    db: Session = Depends(get_db),
) -> RouteResponse:
    route = db.get(Route, route_id)
    policy = db.get(WanPolicy, wan_policy_id)
    if not route or not policy:
        raise HTTPException(status_code=404, detail="Route or WAN policy not found")
    route.wan_policy_id = policy.id
    db.commit()
    record_audit(db, actor=identity.username, action="assign_wan_policy", resource_type="route", resource_id=str(route_id))
    return get_route(route_id, identity, db)


@router.delete("/routes/{route_id}/wan-policy", response_model=RouteResponse, tags=["Routes"], operation_id="clearRouteWanPolicy")
def clear_route_wan_policy(route_id: int, identity: Annotated[Identity, Depends(require_scope("write:routes"))], db: Session = Depends(get_db)) -> RouteResponse:
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    route.wan_policy_id = None
    db.commit()
    record_audit(db, actor=identity.username, action="clear_route_wan_policy", resource_type="route", resource_id=str(route_id))
    return get_route(route_id, identity, db)


@router.get("/wan/policies", response_model=list[WanPolicyResponse], tags=["WAN"], operation_id="listWanPolicies")
def list_wan_policies(identity: Annotated[Identity, Depends(require_scope("read:wan"))], db: Session = Depends(get_db)) -> list[WanPolicyResponse]:
    return [WanPolicyResponse.model_validate(row) for row in db.execute(select(WanPolicy).order_by(WanPolicy.name)).scalars().all()]


@router.post("/wan/policies", response_model=WanPolicyResponse, status_code=201, tags=["WAN"], operation_id="createWanPolicy")
def create_wan_policy(payload: WanPolicyCreate, identity: Annotated[Identity, Depends(require_scope("write:wan"))], db: Session = Depends(get_db)) -> WanPolicyResponse:
    policy = WanPolicy(**payload.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    record_audit(db, actor=identity.username, action="create_wan_policy", resource_type="wan_policy", resource_id=str(policy.id))
    return WanPolicyResponse.model_validate(policy)


@router.get("/wan/policies/{policy_id}", response_model=WanPolicyResponse, tags=["WAN"], operation_id="getWanPolicy")
def get_wan_policy(policy_id: int, identity: Annotated[Identity, Depends(require_scope("read:wan"))], db: Session = Depends(get_db)) -> WanPolicyResponse:
    policy = db.get(WanPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="WAN policy not found")
    return WanPolicyResponse.model_validate(policy)


@router.patch("/wan/policies/{policy_id}", response_model=WanPolicyResponse, tags=["WAN"], operation_id="updateWanPolicy")
def update_wan_policy(policy_id: int, payload: WanPolicyCreate, identity: Annotated[Identity, Depends(require_scope("write:wan"))], db: Session = Depends(get_db)) -> WanPolicyResponse:
    policy = db.get(WanPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="WAN policy not found")
    for key, value in payload.model_dump().items():
        setattr(policy, key, value)
    db.commit()
    db.refresh(policy)
    record_audit(db, actor=identity.username, action="update_wan_policy", resource_type="wan_policy", resource_id=str(policy.id))
    return WanPolicyResponse.model_validate(policy)


@router.delete("/wan/policies/{policy_id}", status_code=204, tags=["WAN"], operation_id="deleteWanPolicy")
def delete_wan_policy(policy_id: int, identity: Annotated[Identity, Depends(require_scope("write:wan"))], db: Session = Depends(get_db)) -> Response:
    policy = db.get(WanPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="WAN policy not found")
    db.delete(policy)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_wan_policy", resource_type="wan_policy", resource_id=str(policy_id))
    return Response(status_code=204)


def nat_outbound_target_names(db: Session) -> set[str]:
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlans = db.execute(select(VlanInterface).order_by(VlanInterface.name)).scalars().all()
    names = {
        interface.name
        for interface in interfaces
        if interface.ip_cidr and normalize_interface_mode(interface.mode) != "trunk"
    }
    names.update({vlan.name for vlan in vlans if vlan.enabled and vlan.ip_cidr})
    return names


def nat_source_group_ids(db: Session) -> set[str]:
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlans = db.execute(select(VlanInterface).order_by(VlanInterface.name)).scalars().all()
    networks = firewall_interface_networks(interfaces, vlans)
    state = firewall_source_group_state(setting_value(db, FIREWALL_SOURCE_GROUPS_SETTING_KEY), networks)
    return {str(group.get("id", "")) for group in state["groups"]}


def validate_nat_rule_payload(payload: NatRuleCreate, db: Session) -> None:
    source_errors = validate_nat_source(payload.source, nat_source_group_ids(db))
    if source_errors:
        raise HTTPException(status_code=422, detail=source_errors[0])
    if payload.outbound_interface not in nat_outbound_target_names(db):
        raise HTTPException(status_code=422, detail="Choose an access physical interface or enabled VLAN interface with an IP CIDR.")
    if not payload.masquerade:
        raise HTTPException(status_code=422, detail="NAT v1 supports masquerade only.")


@router.get("/nat/rules", response_model=list[NatRuleResponse], tags=["NAT"], operation_id="listNatRules")
def list_nat_rules(identity: Annotated[Identity, Depends(require_scope("read:wan"))], db: Session = Depends(get_db)) -> list[NatRuleResponse]:
    rows = db.execute(select(NatRule).order_by(NatRule.priority, NatRule.name)).scalars().all()
    return [NatRuleResponse.model_validate(row) for row in rows]


@router.post("/nat/rules", response_model=NatRuleResponse, status_code=201, tags=["NAT"], operation_id="createNatRule")
def create_nat_rule(payload: NatRuleCreate, identity: Annotated[Identity, Depends(require_scope("write:wan"))], db: Session = Depends(get_db)) -> NatRuleResponse:
    validate_nat_rule_payload(payload, db)
    rule = NatRule(**payload.model_dump())
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"NAT rule {rule.name} already exists") from None
    db.refresh(rule)
    record_audit(db, actor=identity.username, action="create_nat_rule", resource_type="nat_rule", resource_id=str(rule.id))
    return NatRuleResponse.model_validate(rule)


@router.get("/nat/rules/{rule_id}", response_model=NatRuleResponse, tags=["NAT"], operation_id="getNatRule")
def get_nat_rule(rule_id: int, identity: Annotated[Identity, Depends(require_scope("read:wan"))], db: Session = Depends(get_db)) -> NatRuleResponse:
    rule = db.get(NatRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="NAT rule not found")
    return NatRuleResponse.model_validate(rule)


@router.patch("/nat/rules/{rule_id}", response_model=NatRuleResponse, tags=["NAT"], operation_id="updateNatRule")
def update_nat_rule(rule_id: int, payload: NatRuleCreate, identity: Annotated[Identity, Depends(require_scope("write:wan"))], db: Session = Depends(get_db)) -> NatRuleResponse:
    rule = db.get(NatRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="NAT rule not found")
    validate_nat_rule_payload(payload, db)
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"NAT rule {rule.name} already exists") from None
    db.refresh(rule)
    record_audit(db, actor=identity.username, action="update_nat_rule", resource_type="nat_rule", resource_id=str(rule.id))
    return NatRuleResponse.model_validate(rule)


@router.delete("/nat/rules/{rule_id}", status_code=204, tags=["NAT"], operation_id="deleteNatRule")
def delete_nat_rule(rule_id: int, identity: Annotated[Identity, Depends(require_scope("write:wan"))], db: Session = Depends(get_db)) -> Response:
    rule = db.get(NatRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="NAT rule not found")
    db.delete(rule)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_nat_rule", resource_type="nat_rule", resource_id=str(rule_id))
    return Response(status_code=204)


@router.get("/wan/status", response_model=WanStatusResponse, tags=["WAN"], operation_id="getWanStatus")
def get_wan_status(identity: Annotated[Identity, Depends(require_scope("read:wan"))], db: Session = Depends(get_db)) -> WanStatusResponse:
    routes = db.execute(select(Route).where(Route.wan_policy_id.is_not(None))).scalars().all()
    nat_rules = db.execute(select(NatRule).where(NatRule.enabled.is_(True))).scalars().all()
    return WanStatusResponse(
        active_policy_count=len(routes),
        managed_interfaces=sorted({route.interface_name for route in routes} | {rule.outbound_interface for rule in nat_rules}),
        dry_run=SystemAdapter().dry_run,
    )


def get_dns_settings_row(db: Session) -> DnsSettings:
    settings = db.execute(select(DnsSettings)).scalar_one_or_none()
    if settings is None:
        settings = DnsSettings()
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


def get_dnsmasq_state(db: Session) -> tuple[DnsSettings, list[DnsRecord], DhcpSettings, list[DhcpScope], list[DhcpOption], list[DhcpReservation], str]:
    dns_settings = get_dns_settings_row(db)
    conditional_forwarders = setting_value(db, DNS_CONDITIONAL_FORWARDERS_SETTING_KEY)
    dns_records = db.execute(select(DnsRecord).order_by(DnsRecord.hostname)).scalars().all()
    dhcp_settings = get_dhcp_settings_row(db)
    dhcp_scopes = db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().all()
    dhcp_options = db.execute(select(DhcpOption).order_by(DhcpOption.scope_id, DhcpOption.option_code)).scalars().all()
    dhcp_reservations = db.execute(select(DhcpReservation).order_by(DhcpReservation.hostname)).scalars().all()
    config_preview = render_dnsmasq_config(
        dns_settings=dns_settings,
        dns_records=dns_records,
        dhcp_settings=dhcp_settings,
        dhcp_reservations=dhcp_reservations,
        dhcp_scopes=dhcp_scopes,
        dhcp_options=dhcp_options,
        conditional_forwarders=conditional_forwarders,
    )
    return dns_settings, dns_records, dhcp_settings, dhcp_scopes, dhcp_options, dhcp_reservations, config_preview


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


@router.get("/dns/status", response_model=DnsStatusResponse, tags=["DNS"], operation_id="getDnsStatus")
def get_dns_status(identity: Annotated[Identity, Depends(require_scope("read:dns"))], db: Session = Depends(get_db)) -> DnsStatusResponse:
    settings = get_dns_settings_row(db)
    service = db.execute(select(ServiceState).where(ServiceState.service == "dns")).scalar_one_or_none()
    record_count = db.scalar(select(func.count()).select_from(DnsRecord).where(DnsRecord.enabled.is_(True)))
    return DnsStatusResponse(
        enabled=settings.enabled,
        service=ServiceStateResponse.model_validate(service) if service else None,
        listen_interface=settings.listen_interface,
        listen_address=settings.listen_address,
        domain=settings.domain,
        record_count=record_count or 0,
        config_path=settings.config_path,
        dry_run=SystemAdapter().dry_run,
    )


@router.get("/dns/settings", response_model=DnsSettingsResponse, tags=["DNS"], operation_id="getDnsSettings")
def get_dns_settings(identity: Annotated[Identity, Depends(require_scope("read:dns"))], db: Session = Depends(get_db)) -> DnsSettingsResponse:
    return DnsSettingsResponse(
        **dns_settings_to_dict(
            get_dns_settings_row(db),
            setting_value(db, DNS_CONDITIONAL_FORWARDERS_SETTING_KEY),
        )
    )


@router.patch("/dns/settings", response_model=DnsSettingsResponse, tags=["DNS"], operation_id="updateDnsSettings")
def update_dns_settings(
    payload: DnsSettingsUpdate,
    identity: Annotated[Identity, Depends(require_scope("write:dns"))],
    db: Session = Depends(get_db),
) -> DnsSettingsResponse:
    settings = get_dns_settings_row(db)
    for key, value in payload.model_dump().items():
        if key == "upstream_servers":
            value = join_servers(value)
        elif key == "conditional_forwarders":
            set_setting_value(db, DNS_CONDITIONAL_FORWARDERS_SETTING_KEY, join_conditional_forwarders(value))
            continue
        elif key == "domain":
            value = join_domains(split_domains(value))
        setattr(settings, key, value)
    settings.updated_at = utcnow()
    db.commit()
    db.refresh(settings)
    record_audit(db, actor=identity.username, action="update_dns_settings", resource_type="dns", resource_id=str(settings.id))
    return DnsSettingsResponse(
        **dns_settings_to_dict(
            settings,
            setting_value(db, DNS_CONDITIONAL_FORWARDERS_SETTING_KEY),
        )
    )


@router.get("/dns/records", response_model=list[DnsRecordResponse], tags=["DNS"], operation_id="listDnsRecords")
def list_dns_records(identity: Annotated[Identity, Depends(require_scope("read:dns"))], db: Session = Depends(get_db)) -> list[DnsRecordResponse]:
    return [DnsRecordResponse.model_validate(row) for row in db.execute(select(DnsRecord).order_by(DnsRecord.hostname)).scalars().all()]


@router.post("/dns/records", response_model=DnsRecordResponse, status_code=201, tags=["DNS"], operation_id="createDnsRecord")
def create_dns_record(
    payload: DnsRecordCreate,
    identity: Annotated[Identity, Depends(require_scope("write:dns"))],
    db: Session = Depends(get_db),
) -> DnsRecordResponse:
    hostname = payload.hostname.strip().lower()
    record_type = payload.record_type.strip().upper()
    address = payload.address.strip()
    validation_errors = validate_dns_record(hostname, record_type, address)
    if validation_errors:
        raise HTTPException(status_code=422, detail=" ".join(validation_errors))
    existing = db.execute(
        select(DnsRecord).where(
            func.lower(DnsRecord.hostname) == hostname,
            func.lower(DnsRecord.record_type) == record_type.lower(),
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"DNS {record_type} record already exists for {hostname}")
    record = DnsRecord(
        hostname=hostname,
        record_type=record_type,
        address=address,
        description=payload.description,
        enabled=payload.enabled,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"DNS {record_type} record already exists for {hostname}") from exc
    db.refresh(record)
    record_audit(db, actor=identity.username, action="create_dns_record", resource_type="dns_record", resource_id=str(record.id))
    return DnsRecordResponse.model_validate(record)


@router.patch("/dns/records/{record_id}", response_model=DnsRecordResponse, tags=["DNS"], operation_id="updateDnsRecord")
def update_dns_record(
    record_id: int,
    payload: DnsRecordCreate,
    identity: Annotated[Identity, Depends(require_scope("write:dns"))],
    db: Session = Depends(get_db),
) -> DnsRecordResponse:
    record = db.get(DnsRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="DNS record not found")
    hostname = payload.hostname.strip().lower()
    record_type = payload.record_type.strip().upper()
    address = payload.address.strip()
    validation_errors = validate_dns_record(hostname, record_type, address)
    if validation_errors:
        raise HTTPException(status_code=422, detail=" ".join(validation_errors))
    existing = db.execute(
        select(DnsRecord).where(
            DnsRecord.id != record_id,
            func.lower(DnsRecord.hostname) == hostname,
            func.lower(DnsRecord.record_type) == record_type.lower(),
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"DNS {record_type} record already exists for {hostname}")
    record.hostname = hostname
    record.record_type = record_type
    record.address = address
    record.description = payload.description
    record.enabled = payload.enabled
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"DNS {record_type} record already exists for {hostname}") from exc
    db.refresh(record)
    record_audit(db, actor=identity.username, action="update_dns_record", resource_type="dns_record", resource_id=str(record.id))
    return DnsRecordResponse.model_validate(record)


@router.post("/dns/records/import", response_model=DnsHostsImportResponse, tags=["DNS"], operation_id="importDnsHostsFile")
def import_dns_hosts_file(
    payload: DnsHostsImportRequest,
    identity: Annotated[Identity, Depends(require_scope("write:dns"))],
    db: Session = Depends(get_db),
) -> DnsHostsImportResponse:
    parsed_records, errors = parse_hosts_records(payload.hosts_text)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    if payload.replace_existing:
        for record in db.execute(select(DnsRecord)).scalars().all():
            db.delete(record)
        db.flush()
    for item in parsed_records:
        existing = None
        if not payload.replace_existing:
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
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Imported hosts contain duplicate DNS records") from exc
    rows = db.execute(select(DnsRecord).order_by(DnsRecord.hostname)).scalars().all()
    record_audit(
        db,
        actor=identity.username,
        action="import_dns_hosts_file",
        resource_type="dns_record",
        detail=f"Imported {len(parsed_records)} records; replace_existing={payload.replace_existing}",
    )
    return DnsHostsImportResponse(
        imported_count=len(parsed_records),
        replaced_existing=payload.replace_existing,
        records=[DnsRecordResponse.model_validate(row) for row in rows],
    )


@router.delete("/dns/records/{record_id}", status_code=204, tags=["DNS"], operation_id="deleteDnsRecord")
def delete_dns_record(
    record_id: int,
    identity: Annotated[Identity, Depends(require_scope("write:dns"))],
    db: Session = Depends(get_db),
) -> Response:
    record = db.get(DnsRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="DNS record not found")
    db.delete(record)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dns_record", resource_type="dns_record", resource_id=str(record_id))
    return Response(status_code=204)


def dnsmasq_validation_response(db: Session) -> ConfigValidationResponse:
    dns_settings, dns_records, dhcp_settings, dhcp_scopes, dhcp_options, dhcp_reservations, config_preview = get_dnsmasq_state(db)
    conditional_forwarders = setting_value(db, DNS_CONDITIONAL_FORWARDERS_SETTING_KEY)
    physical_interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlan_interfaces = db.execute(select(VlanInterface).order_by(VlanInterface.name)).scalars().all()
    bind_targets = dhcp_bind_target_names(physical_interfaces, vlan_interfaces)
    errors = (
        validate_dns_settings(dns_settings, dns_records, conditional_forwarders)
        + validate_dns_listen_targets(dns_settings, bind_targets)
        + validate_dhcp_bind_targets(dhcp_settings, dhcp_scopes, bind_targets)
        + validate_dhcp_settings(
            dhcp_settings,
            dhcp_reservations,
            dhcp_scopes,
            dhcp_options,
        )
    )
    warnings = dns_domain_warnings(split_domains(dns_settings.domain))
    adapter = SystemAdapter()
    config_path = dns_settings.config_path
    if not adapter.dry_run:
        config_path = stage_api_dnsmasq_config(config_preview)
    result = adapter.validate_dnsmasq_config(config_path)
    return ConfigValidationResponse(
        valid=not errors,
        dry_run=result.dry_run,
        command=result.command if result.command else dnsmasq_test_command(config_path),
        config_path=config_path,
        config_preview=config_preview,
        errors=errors,
        warnings=warnings,
    )


@router.post("/dns/validate", response_model=ConfigValidationResponse, tags=["DNS"], operation_id="validateDnsConfig")
def validate_dns_config(identity: Annotated[Identity, Depends(require_scope("read:dns"))], db: Session = Depends(get_db)) -> ConfigValidationResponse:
    return dnsmasq_validation_response(db)


@router.post("/dns/apply", response_model=ConfigApplyResponse, tags=["DNS"], operation_id="applyDnsConfig")
def apply_dns_config(identity: Annotated[Identity, Depends(require_scope("write:dns"))], db: Session = Depends(get_db)) -> ConfigApplyResponse:
    validation = dnsmasq_validation_response(db)
    if not validation.valid:
        return ConfigApplyResponse(**validation.model_dump(), reloaded=False)
    apply_result = SystemAdapter().apply_dnsmasq_config(validation.config_path)
    reload_result = SystemAdapter().reload_dnsmasq()
    record_audit(
        db,
        actor=identity.username,
        action="apply_dns_config_dry_run",
        resource_type="dns",
        detail=" ".join(apply_result.command + [";"] + reload_result.command),
    )
    payload = validation.model_dump()
    payload["command"] = apply_result.command
    return ConfigApplyResponse(**payload, reloaded=not apply_result.dry_run)


@router.get("/dns/logs", response_model=list[str], tags=["DNS"], operation_id="getDnsLogs")
def get_dns_logs(identity: Annotated[Identity, Depends(require_scope("read:dns"))]) -> list[str]:
    return ["dry-run log source for dnsmasq", "Host journal reading is reserved for the provisioned appliance."]


@router.get("/dhcp/status", response_model=DhcpStatusResponse, tags=["DHCP"], operation_id="getDhcpStatus")
def get_dhcp_status(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))], db: Session = Depends(get_db)) -> DhcpStatusResponse:
    settings = get_dhcp_settings_row(db)
    first_scope = db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().first()
    service = db.execute(select(ServiceState).where(ServiceState.service == "dhcp")).scalar_one_or_none()
    reservations = db.execute(select(DhcpReservation).where(DhcpReservation.enabled.is_(True))).scalars().all()
    return DhcpStatusResponse(
        enabled=settings.enabled,
        service=ServiceStateResponse.model_validate(service) if service else None,
        interface_name=first_scope.interface_name if first_scope else settings.interface_name,
        range_start=first_scope.range_start if first_scope else settings.range_start,
        range_end=first_scope.range_end if first_scope else settings.range_end,
        reservation_count=len(reservations),
        config_path=settings.config_path,
        dry_run=SystemAdapter().dry_run,
    )


@router.get("/dhcp/settings", response_model=DhcpSettingsResponse, tags=["DHCP"], operation_id="getDhcpSettings")
def get_dhcp_settings(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))], db: Session = Depends(get_db)) -> DhcpSettingsResponse:
    return DhcpSettingsResponse.model_validate(get_dhcp_settings_row(db))


@router.patch("/dhcp/settings", response_model=DhcpSettingsResponse, tags=["DHCP"], operation_id="updateDhcpSettings")
def update_dhcp_settings(
    payload: DhcpSettingsUpdate,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> DhcpSettingsResponse:
    settings = get_dhcp_settings_row(db)
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    settings.updated_at = utcnow()
    db.commit()
    db.refresh(settings)
    record_audit(db, actor=identity.username, action="update_dhcp_settings", resource_type="dhcp", resource_id=str(settings.id))
    return DhcpSettingsResponse.model_validate(settings)


@router.get("/dhcp/scopes", response_model=list[DhcpScopeResponse], tags=["DHCP"], operation_id="listDhcpScopes")
def list_dhcp_scopes(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))], db: Session = Depends(get_db)) -> list[DhcpScopeResponse]:
    return [DhcpScopeResponse.model_validate(row) for row in db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().all()]


@router.post("/dhcp/scopes", response_model=DhcpScopeResponse, status_code=201, tags=["DHCP"], operation_id="createDhcpScope")
def create_dhcp_scope(
    payload: DhcpScopeCreate,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> DhcpScopeResponse:
    scope = DhcpScope(**payload.model_dump())
    db.add(scope)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="DHCP IP zone already exists") from exc
    db.refresh(scope)
    record_audit(db, actor=identity.username, action="create_dhcp_scope", resource_type="dhcp_scope", resource_id=str(scope.id))
    return DhcpScopeResponse.model_validate(scope)


@router.patch("/dhcp/scopes/{scope_id}", response_model=DhcpScopeResponse, tags=["DHCP"], operation_id="updateDhcpScope")
def update_dhcp_scope(
    scope_id: int,
    payload: DhcpScopeCreate,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> DhcpScopeResponse:
    scope = db.get(DhcpScope, scope_id)
    if not scope:
        raise HTTPException(status_code=404, detail="DHCP IP zone not found")
    for key, value in payload.model_dump().items():
        setattr(scope, key, value)
    scope.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="DHCP IP zone already exists") from exc
    db.refresh(scope)
    record_audit(db, actor=identity.username, action="update_dhcp_scope", resource_type="dhcp_scope", resource_id=str(scope.id))
    return DhcpScopeResponse.model_validate(scope)


@router.delete("/dhcp/scopes/{scope_id}", status_code=204, tags=["DHCP"], operation_id="deleteDhcpScope")
def delete_dhcp_scope(
    scope_id: int,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> Response:
    scope = db.get(DhcpScope, scope_id)
    if not scope:
        raise HTTPException(status_code=404, detail="DHCP IP zone not found")
    for option in db.execute(select(DhcpOption).where(DhcpOption.scope_id == scope_id)).scalars().all():
        db.delete(option)
    db.delete(scope)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dhcp_scope", resource_type="dhcp_scope", resource_id=str(scope_id))
    return Response(status_code=204)


@router.get("/dhcp/options", response_model=list[DhcpOptionResponse], tags=["DHCP"], operation_id="listDhcpOptions")
def list_dhcp_options(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))], db: Session = Depends(get_db)) -> list[DhcpOptionResponse]:
    return [DhcpOptionResponse.model_validate(row) for row in db.execute(select(DhcpOption).order_by(DhcpOption.scope_id, DhcpOption.option_code)).scalars().all()]


@router.post("/dhcp/options", response_model=DhcpOptionResponse, status_code=201, tags=["DHCP"], operation_id="createDhcpOption")
def create_dhcp_option(
    payload: DhcpOptionCreate,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> DhcpOptionResponse:
    if payload.scope_id is not None and not db.get(DhcpScope, payload.scope_id):
        raise HTTPException(status_code=404, detail="DHCP IP zone not found")
    option = DhcpOption(**payload.model_dump())
    db.add(option)
    db.commit()
    db.refresh(option)
    record_audit(db, actor=identity.username, action="create_dhcp_option", resource_type="dhcp_option", resource_id=str(option.id))
    return DhcpOptionResponse.model_validate(option)


@router.patch("/dhcp/options/{option_id}", response_model=DhcpOptionResponse, tags=["DHCP"], operation_id="updateDhcpOption")
def update_dhcp_option(
    option_id: int,
    payload: DhcpOptionCreate,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> DhcpOptionResponse:
    option = db.get(DhcpOption, option_id)
    if not option:
        raise HTTPException(status_code=404, detail="DHCP option not found")
    if payload.scope_id is not None and not db.get(DhcpScope, payload.scope_id):
        raise HTTPException(status_code=404, detail="DHCP IP zone not found")
    for key, value in payload.model_dump().items():
        setattr(option, key, value)
    option.updated_at = utcnow()
    db.commit()
    db.refresh(option)
    record_audit(db, actor=identity.username, action="update_dhcp_option", resource_type="dhcp_option", resource_id=str(option.id))
    return DhcpOptionResponse.model_validate(option)


@router.delete("/dhcp/options/{option_id}", status_code=204, tags=["DHCP"], operation_id="deleteDhcpOption")
def delete_dhcp_option(
    option_id: int,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> Response:
    option = db.get(DhcpOption, option_id)
    if not option:
        raise HTTPException(status_code=404, detail="DHCP option not found")
    db.delete(option)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dhcp_option", resource_type="dhcp_option", resource_id=str(option_id))
    return Response(status_code=204)


@router.get("/dhcp/reservations", response_model=list[DhcpReservationResponse], tags=["DHCP"], operation_id="listDhcpReservations")
def list_dhcp_reservations(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))], db: Session = Depends(get_db)) -> list[DhcpReservationResponse]:
    return [DhcpReservationResponse.model_validate(row) for row in db.execute(select(DhcpReservation).order_by(DhcpReservation.hostname)).scalars().all()]


@router.get("/dhcp/leases", response_model=list[DhcpLeaseResponse], tags=["DHCP"], operation_id="listDhcpLeases")
def list_dhcp_leases(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))]) -> list[DhcpLeaseResponse]:
    result = SystemAdapter().read_dhcp_leases()
    if result.returncode != 0:
        raise HTTPException(status_code=502, detail=result.stderr.strip() or "Unable to read dnsmasq DHCP leases.")
    return [DhcpLeaseResponse(**lease) for lease in parse_dnsmasq_leases(result.stdout)]


@router.post("/dhcp/reservations", response_model=DhcpReservationResponse, status_code=201, tags=["DHCP"], operation_id="createDhcpReservation")
def create_dhcp_reservation(
    payload: DhcpReservationCreate,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> DhcpReservationResponse:
    reservation = DhcpReservation(**payload.model_dump())
    db.add(reservation)
    db.flush()
    ensure_dns_for_dhcp_reservation(db, reservation, identity.username)
    db.commit()
    db.refresh(reservation)
    record_audit(db, actor=identity.username, action="create_dhcp_reservation", resource_type="dhcp_reservation", resource_id=str(reservation.id))
    return DhcpReservationResponse.model_validate(reservation)


@router.delete("/dhcp/reservations/{reservation_id}", status_code=204, tags=["DHCP"], operation_id="deleteDhcpReservation")
def delete_dhcp_reservation(
    reservation_id: int,
    identity: Annotated[Identity, Depends(require_scope("write:dhcp"))],
    db: Session = Depends(get_db),
) -> Response:
    reservation = db.get(DhcpReservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="DHCP reservation not found")
    db.delete(reservation)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_dhcp_reservation", resource_type="dhcp_reservation", resource_id=str(reservation_id))
    return Response(status_code=204)


@router.post("/dhcp/validate", response_model=ConfigValidationResponse, tags=["DHCP"], operation_id="validateDhcpConfig")
def validate_dhcp_config(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))], db: Session = Depends(get_db)) -> ConfigValidationResponse:
    return dnsmasq_validation_response(db)


@router.post("/dhcp/apply", response_model=ConfigApplyResponse, tags=["DHCP"], operation_id="applyDhcpConfig")
def apply_dhcp_config(identity: Annotated[Identity, Depends(require_scope("write:dhcp"))], db: Session = Depends(get_db)) -> ConfigApplyResponse:
    validation = dnsmasq_validation_response(db)
    if not validation.valid:
        return ConfigApplyResponse(**validation.model_dump(), reloaded=False)
    apply_result = SystemAdapter().apply_dnsmasq_config(validation.config_path)
    reload_result = SystemAdapter().reload_dnsmasq()
    record_audit(
        db,
        actor=identity.username,
        action="apply_dhcp_config_dry_run",
        resource_type="dhcp",
        detail=" ".join(apply_result.command + [";"] + reload_result.command),
    )
    payload = validation.model_dump()
    payload["command"] = apply_result.command
    return ConfigApplyResponse(**payload, reloaded=not apply_result.dry_run)


@router.get("/dhcp/logs", response_model=list[str], tags=["DHCP"], operation_id="getDhcpLogs")
def get_dhcp_logs(identity: Annotated[Identity, Depends(require_scope("read:dhcp"))]) -> list[str]:
    return ["dry-run log source for dnsmasq DHCP leases", "Host lease files are read only on provisioned appliances."]


@router.get("/firewall/status", response_model=FirewallStatusResponse, tags=["Firewall"], operation_id="getFirewallStatus")
def get_firewall_status(identity: Annotated[Identity, Depends(require_scope("read:firewall"))], db: Session = Depends(get_db)) -> FirewallStatusResponse:
    settings = get_firewall_settings(db)
    service = db.execute(select(ServiceState).where(ServiceState.service == "firewall")).scalar_one_or_none()
    rule_count = db.scalar(select(func.count()).select_from(FirewallRule)) or 0
    return FirewallStatusResponse(
        enabled=settings.enabled,
        service=ServiceStateResponse.model_validate(service) if service else None,
        rule_count=rule_count,
        config_path=settings.config_path,
        dry_run=get_settings().dry_run_system_adapters,
    )


@router.get("/firewall/settings", response_model=FirewallSettingsResponse, tags=["Firewall"], operation_id="getFirewallSettings")
def get_firewall_settings_api(identity: Annotated[Identity, Depends(require_scope("read:firewall"))], db: Session = Depends(get_db)) -> FirewallSettingsResponse:
    return FirewallSettingsResponse.model_validate(get_firewall_settings(db))


@router.patch("/firewall/settings", response_model=FirewallSettingsResponse, tags=["Firewall"], operation_id="updateFirewallSettings")
def update_firewall_settings_api(
    payload: FirewallSettingsUpdate,
    identity: Annotated[Identity, Depends(require_scope("write:firewall"))],
    db: Session = Depends(get_db),
) -> FirewallSettingsResponse:
    settings = get_firewall_settings(db)
    values = payload.model_dump()
    if values["default_input_policy"] not in FIREWALL_POLICIES or values["default_forward_policy"] not in FIREWALL_POLICIES or values["default_output_policy"] not in FIREWALL_POLICIES:
        raise HTTPException(status_code=422, detail="Firewall default policies must be accept or drop.")
    for key, value in values.items():
        setattr(settings, key, value)
    settings.updated_at = utcnow()
    db.add(settings)
    db.commit()
    record_audit(db, actor=identity.username, action="update_firewall_settings", resource_type="firewall", resource_id=str(settings.id))
    db.refresh(settings)
    return FirewallSettingsResponse.model_validate(settings)


@router.get("/firewall/rules", response_model=list[FirewallRuleResponse], tags=["Firewall"], operation_id="listFirewallRules")
def list_firewall_rules(identity: Annotated[Identity, Depends(require_scope("read:firewall"))], db: Session = Depends(get_db)) -> list[FirewallRuleResponse]:
    return [FirewallRuleResponse.model_validate(row) for row in db.execute(select(FirewallRule).order_by(FirewallRule.priority, FirewallRule.name)).scalars().all()]


def firewall_groups_for_api_validation(db: Session) -> list[dict]:
    physical_interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlan_interfaces = db.execute(select(VlanInterface).order_by(VlanInterface.parent_interface, VlanInterface.vlan_id)).scalars().all()
    interface_networks = firewall_interface_networks(physical_interfaces, vlan_interfaces)
    return firewall_source_group_state(setting_value(db, FIREWALL_SOURCE_GROUPS_SETTING_KEY), interface_networks)["groups"]


@router.post("/firewall/rules", response_model=FirewallRuleResponse, tags=["Firewall"], operation_id="createFirewallRule")
def create_firewall_rule_api(
    payload: FirewallRuleCreate,
    identity: Annotated[Identity, Depends(require_scope("write:firewall"))],
    db: Session = Depends(get_db),
) -> FirewallRuleResponse:
    rule = assign_firewall_rule_values(FirewallRule(), payload.model_dump())
    errors = validate_firewall_rule(rule, firewall_groups_for_api_validation(db), require_group_addresses=True)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Firewall rule {rule.name} already exists.") from exc
    record_audit(db, actor=identity.username, action="create_firewall_rule", resource_type="firewall_rule", resource_id=str(rule.id))
    db.refresh(rule)
    return FirewallRuleResponse.model_validate(rule)


@router.patch("/firewall/rules/{rule_id}", response_model=FirewallRuleResponse, tags=["Firewall"], operation_id="updateFirewallRule")
def update_firewall_rule_api(
    rule_id: int,
    payload: FirewallRuleCreate,
    identity: Annotated[Identity, Depends(require_scope("write:firewall"))],
    db: Session = Depends(get_db),
) -> FirewallRuleResponse:
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Firewall rule not found")
    assign_firewall_rule_values(rule, payload.model_dump())
    errors = validate_firewall_rule(rule, firewall_groups_for_api_validation(db), require_group_addresses=True)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Firewall rule {rule.name} already exists.") from exc
    record_audit(db, actor=identity.username, action="update_firewall_rule", resource_type="firewall_rule", resource_id=str(rule.id))
    db.refresh(rule)
    return FirewallRuleResponse.model_validate(rule)


@router.delete("/firewall/rules/{rule_id}", response_model=dict, tags=["Firewall"], operation_id="deleteFirewallRule")
def delete_firewall_rule_api(
    rule_id: int,
    identity: Annotated[Identity, Depends(require_scope("write:firewall"))],
    db: Session = Depends(get_db),
) -> dict:
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Firewall rule not found")
    db.delete(rule)
    db.commit()
    record_audit(db, actor=identity.username, action="delete_firewall_rule", resource_type="firewall_rule", resource_id=str(rule_id))
    return {"deleted": True}


@router.get("/firewall/validate", response_model=ConfigValidationResponse, tags=["Firewall"], operation_id="validateFirewall")
def validate_firewall(identity: Annotated[Identity, Depends(require_scope("read:firewall"))], db: Session = Depends(get_db)) -> ConfigValidationResponse:
    settings, _rules, config_preview, errors = firewall_validation_payload(db)
    adapter = SystemAdapter()
    config_path = settings.config_path
    if not adapter.dry_run:
        config_path = stage_api_firewall_config(config_preview)
    result = adapter.validate_firewall_config(config_path)
    return ConfigValidationResponse(
        valid=not errors,
        dry_run=result.dry_run,
        command=result.command,
        config_path=config_path,
        config_preview=config_preview,
        errors=errors,
    )


@router.post("/firewall/apply", response_model=ConfigApplyResponse, tags=["Firewall"], operation_id="applyFirewall")
def apply_firewall(identity: Annotated[Identity, Depends(require_scope("write:firewall"))], db: Session = Depends(get_db)) -> ConfigApplyResponse:
    validation = validate_firewall(identity, db)
    apply_result = SystemAdapter().apply_firewall_config(validation.config_path)
    record_audit(db, actor=identity.username, action="apply_firewall_dry_run", resource_type="firewall", detail=" ".join(apply_result.command))
    payload = validation.model_dump()
    payload["command"] = apply_result.command
    return ConfigApplyResponse(**payload, reloaded=not apply_result.dry_run)


@router.get("/firewall/logs", response_model=list[str], tags=["Firewall"], operation_id="getFirewallLogs")
def get_firewall_logs(identity: Annotated[Identity, Depends(require_scope("read:firewall"))]) -> list[str]:
    return ["dry-run log source for nftables", "Host nftables logs are not read in development mode."]


@router.get("/services", response_model=list[ServiceStateResponse], tags=["Services"], operation_id="listServices")
def list_services(identity: Annotated[Identity, Depends(require_scope("read:services"))], db: Session = Depends(get_db)) -> list[ServiceStateResponse]:
    return [ServiceStateResponse.model_validate(row) for row in db.execute(select(ServiceState).order_by(ServiceState.display_name)).scalars().all()]


@router.get("/services/{service}", response_model=ServiceStateResponse, tags=["Services"], operation_id="getService")
def get_service(service: str, identity: Annotated[Identity, Depends(require_scope("read:services"))], db: Session = Depends(get_db)) -> ServiceStateResponse:
    row = db.execute(select(ServiceState).where(ServiceState.service == service)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return ServiceStateResponse.model_validate(row)


def service_action(service: str, action: str, identity: Identity, db: Session) -> ServiceActionResponse:
    if service not in APPROVED_SERVICES:
        raise HTTPException(status_code=404, detail="Service is not approved for control")
    if action not in {"start", "stop", "restart", "enable", "disable"}:
        raise HTTPException(status_code=422, detail="Unsupported service action")
    row = db.execute(select(ServiceState).where(ServiceState.service == service)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
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
    record_audit(db, actor=identity.username, action=f"{action}_service_dry_run", resource_type="service", resource_id=service, detail=" ".join(result.command))
    return ServiceActionResponse(service=service, action=action, dry_run=result.dry_run, command=result.command)


@router.post("/services/{service}/start", response_model=ServiceActionResponse, tags=["Services"], operation_id="startService")
def start_service(service: str, identity: Annotated[Identity, Depends(require_scope("write:services"))], db: Session = Depends(get_db)) -> ServiceActionResponse:
    return service_action(service, "start", identity, db)


@router.post("/services/{service}/stop", response_model=ServiceActionResponse, tags=["Services"], operation_id="stopService")
def stop_service(service: str, identity: Annotated[Identity, Depends(require_scope("write:services"))], db: Session = Depends(get_db)) -> ServiceActionResponse:
    return service_action(service, "stop", identity, db)


@router.post("/services/{service}/restart", response_model=ServiceActionResponse, tags=["Services"], operation_id="restartService")
def restart_service(service: str, identity: Annotated[Identity, Depends(require_scope("write:services"))], db: Session = Depends(get_db)) -> ServiceActionResponse:
    return service_action(service, "restart", identity, db)


@router.post("/services/{service}/enable", response_model=ServiceActionResponse, tags=["Services"], operation_id="enableService")
def enable_service(service: str, identity: Annotated[Identity, Depends(require_scope("write:services"))], db: Session = Depends(get_db)) -> ServiceActionResponse:
    return service_action(service, "enable", identity, db)


@router.post("/services/{service}/disable", response_model=ServiceActionResponse, tags=["Services"], operation_id="disableService")
def disable_service(service: str, identity: Annotated[Identity, Depends(require_scope("write:services"))], db: Session = Depends(get_db)) -> ServiceActionResponse:
    return service_action(service, "disable", identity, db)


@router.get("/services/{service}/logs", response_model=list[str], tags=["Services"], operation_id="getServiceLogs")
def get_service_logs(service: str, identity: Annotated[Identity, Depends(require_scope("read:logs"))]) -> list[str]:
    if service not in APPROVED_SERVICES:
        raise HTTPException(status_code=404, detail="Log source is not approved")
    return [f"dry-run log source for {service}", "No host journal is read in development mode."]


@router.get("/logs", response_model=list[str], tags=["Logs"], operation_id="listLogs")
def list_logs(identity: Annotated[Identity, Depends(require_scope("read:logs"))]) -> list[str]:
    return ["system", "labfoundry", "dnsmasq", "nginx", "openssh", "nftables"]


@router.get("/logs/{source}", response_model=list[str], tags=["Logs"], operation_id="getLogSource")
def get_log_source(source: str, identity: Annotated[Identity, Depends(require_scope("read:logs"))]) -> list[str]:
    if source not in {"system", "labfoundry", "dnsmasq", "nginx", "openssh", "nftables"}:
        raise HTTPException(status_code=404, detail="Log source is not approved")
    return [f"dry-run log source for {source}", "Host log streaming is not enabled in the MVP scaffold."]


@router.get("/audit", response_model=list[AuditEventResponse], tags=["Audit"], operation_id="listAuditEvents")
def list_audit_events(
    identity: Annotated[Identity, Depends(require_scope("read:audit"))],
    db: Session = Depends(get_db),
    user: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    success: bool | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[AuditEventResponse]:
    query = select(AuditEvent)
    if user:
        query = query.where(AuditEvent.actor == user)
    if action:
        query = query.where(AuditEvent.action == action)
    if resource_type:
        query = query.where(AuditEvent.resource_type == resource_type)
    if success is not None:
        query = query.where(AuditEvent.success.is_(success))
    if start_time:
        query = query.where(AuditEvent.created_at >= start_time)
    if end_time:
        query = query.where(AuditEvent.created_at <= end_time)
    return [AuditEventResponse.model_validate(row) for row in db.execute(query.order_by(desc(AuditEvent.created_at)).limit(200)).scalars().all()]


@router.get("/jobs", response_model=list[JobResponse], tags=["Jobs"], operation_id="listJobs")
def list_jobs(identity: Annotated[Identity, Depends(require_scope("read:dashboard"))], db: Session = Depends(get_db)) -> list[JobResponse]:
    return [JobResponse.model_validate(row) for row in db.execute(select(Job).order_by(desc(Job.created_at))).scalars().all()]


@router.post("/jobs", response_model=JobResponse, status_code=202, tags=["Jobs"], operation_id="createJob")
def create_job(identity: Annotated[Identity, Depends(require_scope("admin:all"))], db: Session = Depends(get_db)) -> JobResponse:
    job = Job(id=f"job_{uuid4().hex[:12]}", type="manual-placeholder", created_by=identity.username)
    db.add(job)
    db.commit()
    db.refresh(job)
    record_audit(db, actor=identity.username, action="create_job", resource_type="job", resource_id=job.id)
    return JobResponse.model_validate(job)


@router.get("/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"], operation_id="getJob")
def get_job(job_id: str, identity: Annotated[Identity, Depends(require_scope("read:dashboard"))], db: Session = Depends(get_db)) -> JobResponse:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse, tags=["Jobs"], operation_id="cancelJob")
def cancel_job(job_id: str, identity: Annotated[Identity, Depends(require_scope("admin:all"))], db: Session = Depends(get_db)) -> JobResponse:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "cancelled"
    job.finished_at = utcnow()
    db.commit()
    db.refresh(job)
    record_audit(db, actor=identity.username, action="cancel_job", resource_type="job", resource_id=job.id)
    return JobResponse.model_validate(job)


@router.get("/settings", response_model=SettingsResponse, tags=["Settings"], operation_id="getSettings")
def get_app_settings(
    identity: Annotated[Identity, Depends(require_scope("read:dashboard"))],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SettingsResponse:
    return appliance_settings_response(db, settings)


@router.patch("/settings", response_model=SettingsResponse, tags=["Settings"], operation_id="updateSettings")
def update_app_settings(
    payload: SettingsUpdate,
    identity: Annotated[Identity, Depends(require_scope("admin:all"))],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SettingsResponse:
    desired = get_appliance_settings(db)
    desired.fqdn = normalize_fqdn(payload.appliance_fqdn)
    desired.management_https_enabled = payload.management_https_enabled
    desired.root_ssh_enabled = payload.root_ssh_enabled
    desired.external_dns_servers = normalize_multiline_values("\n".join(payload.external_dns_servers))
    desired.ntp_servers = normalize_multiline_values("\n".join(payload.ntp_servers))
    desired.config_path = APPLIANCE_SETTINGS_STAGED_CONFIG_PATH
    desired.updated_at = utcnow()
    db.add(desired)
    db.commit()
    db.refresh(desired)
    record_audit(db, actor=identity.username, action="update_appliance_settings", resource_type="settings", resource_id=str(desired.id))
    return appliance_settings_response(db, settings)


@router.get("/vcf-backups/status", response_model=VcfBackupStatusResponse, tags=["VCF Backups"], operation_id="getVcfBackupsStatus")
def get_vcf_backups_status(
    identity: Annotated[Identity, Depends(require_scope("read:vcf-backups"))],
    db: Session = Depends(get_db),
) -> VcfBackupStatusResponse:
    settings = get_vcf_backup_settings(db)
    row = db.execute(select(ServiceState).where(ServiceState.service == "vcf-backups")).scalar_one_or_none()
    payload = vcf_backup_settings_to_dict(settings)
    return VcfBackupStatusResponse(
        enabled=settings.enabled,
        service=ServiceStateResponse.model_validate(row) if row else None,
        listen_interface=payload["listen_interface"],
        listen_address=payload["listen_address"],
        port=payload["port"],
        sftp_username=payload["sftp_username"] or None,
        storage_path=payload["storage_path"],
        remote_directory=payload["remote_directory"],
        config_path=payload["config_path"],
        dry_run=get_settings().dry_run_system_adapters,
    )


def build_vcf_offline_depot_status(db: Session) -> VcfOfflineDepotStatusResponse:
    settings = get_vcf_offline_depot_settings(db)
    profiles = db.execute(select(VcfDepotDownloadProfile).order_by(VcfDepotDownloadProfile.name)).scalars().all()
    row = db.execute(select(ServiceState).where(ServiceState.service == "repository")).scalar_one_or_none()
    download_token_present, activation_code_present = vcf_depot_secret_status(db)
    validation_errors, _warnings = validate_vcf_depot_state(
        settings,
        profiles,
        download_token_present=download_token_present,
        activation_code_present=activation_code_present,
    )
    payload = vcf_depot_settings_to_dict(settings)
    return VcfOfflineDepotStatusResponse(
        enabled=settings.enabled,
        service=ServiceStateResponse.model_validate(row) if row else None,
        hostname=str(payload["hostname"]),
        endpoint=str(payload["endpoint"]),
        listen_interface=str(payload["listen_interface"]),
        listen_address=str(payload["listen_address"]),
        port=int(payload["port"]),
        depot_store_path=str(payload["depot_store_path"]),
        tool_archive_name=str(payload["tool_archive_name"]),
        tool_version=str(payload["tool_version"]),
        download_token_present=download_token_present,
        activation_code_present=activation_code_present,
        profile_count=len([profile for profile in profiles if profile.enabled]),
        config_path=str(payload["config_path"]),
        valid=not validation_errors,
        dry_run=get_settings().dry_run_system_adapters,
    )


@router.get(
    "/vcf-offline-depot/status",
    response_model=VcfOfflineDepotStatusResponse,
    tags=["VCF Offline Depot"],
    operation_id="getVcfOfflineDepotStatus",
)
def get_vcf_offline_depot_status(
    identity: Annotated[Identity, Depends(require_scope("read:repository"))],
    db: Session = Depends(get_db),
) -> VcfOfflineDepotStatusResponse:
    return build_vcf_offline_depot_status(db)


@router.get(
    "/repository/status",
    response_model=VcfOfflineDepotStatusResponse,
    tags=["VCF Offline Depot"],
    operation_id="getRepositoryStatus",
)
def get_repository_status_alias(
    identity: Annotated[Identity, Depends(require_scope("read:repository"))],
    db: Session = Depends(get_db),
) -> VcfOfflineDepotStatusResponse:
    return build_vcf_offline_depot_status(db)


@router.get(
    "/vcf-private-registry/status",
    response_model=VcfPrivateRegistryStatusResponse,
    tags=["VCF Private Registry"],
    operation_id="getVcfPrivateRegistryStatus",
)
def get_vcf_private_registry_status(
    identity: Annotated[Identity, Depends(require_scope("read:vcf-registry"))],
    db: Session = Depends(get_db),
) -> VcfPrivateRegistryStatusResponse:
    settings = get_vcf_private_registry_settings(db)
    bundles = db.execute(select(VcfRegistryBundle).order_by(VcfRegistryBundle.name)).scalars().all()
    row = db.execute(select(ServiceState).where(ServiceState.service == "vcf-private-registry")).scalar_one_or_none()
    ca_bundle_source, ca_bundle_available = vcf_registry_ca_bundle_status(db)
    validation_errors, _warnings = validate_vcf_registry_state(
        settings,
        bundles,
        ca_bundle_source=ca_bundle_source,
        ca_bundle_available=ca_bundle_available,
    )
    payload = vcf_registry_settings_to_dict(settings)
    return VcfPrivateRegistryStatusResponse(
        enabled=settings.enabled,
        service=ServiceStateResponse.model_validate(row) if row else None,
        hostname=str(payload["hostname"]),
        endpoint=str(payload["endpoint"]),
        listen_interface=str(payload["listen_interface"]),
        listen_address=str(payload["listen_address"]),
        port=int(payload["port"]),
        harbor_project=str(payload["harbor_project"]),
        storage_path=str(payload["storage_path"]),
        config_path=str(payload["config_path"]),
        bundle_count=len([bundle for bundle in bundles if bundle.enabled]),
        valid=not validation_errors,
        dry_run=get_settings().dry_run_system_adapters,
    )


def add_placeholder_resource_routes() -> None:
    placeholder_specs = [
        ("ldap", "LDAP", "read:dashboard"),
        ("ca", "CA", "read:ca"),
        ("backup", "Backup Restore", "write:backup"),
    ]

    for prefix, tag, scope in placeholder_specs:
        async def placeholder(identity: Annotated[Identity, Depends(require_scope(scope))], resource: str = prefix) -> dict[str, str]:
            return {"resource": resource, "status": "scaffolded", "mode": "dry-run"}

        router.add_api_route(
            f"/{prefix}/status" if prefix not in {"backup"} else f"/{prefix}",
            placeholder,
            methods=["GET"],
            response_model=dict[str, str],
            tags=[tag],
            operation_id=f"get{tag.replace(' ', '').replace('/', '')}Status",
        )


add_placeholder_resource_routes()

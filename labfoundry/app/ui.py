import json
from ipaddress import ip_address, ip_interface, ip_network
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from labfoundry.app.audit import record_audit
from labfoundry.app.adapters.system import SystemAdapter
from labfoundry.app.config import get_settings
from labfoundry.app.database import get_db
from labfoundry.app.models import (
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
    FirewallRule,
    FirewallSettings,
    Job,
    JobStatus,
    KmsClient,
    KmsKey,
    KmsSettings,
    PhysicalInterface,
    Role,
    Route,
    ServiceState,
    User,
    VcfBackupSettings,
    VlanInterface,
    WanPolicy,
    utcnow,
)
from labfoundry.app.schemas import ApiTokenCreate, WanPolicyCreate
from labfoundry.app.security import (
    Identity,
    authenticate_user,
    get_session_identity,
    hash_password,
    require_session_identity,
)
from labfoundry.app.services.dnsmasq import (
    dns_domain_warnings,
    dns_reverse_records,
    dhcp_option_to_dict,
    dhcp_scope_to_dict,
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
    split_domains,
    split_interfaces,
    split_servers,
    validate_dns_record,
    validate_dhcp_settings,
    validate_dns_settings,
)
from labfoundry.app.services.ca import (
    ca_certificate_to_dict,
    ca_profile_to_dict,
    ensure_development_root_ca,
    join_multiline,
    render_ca_config,
    split_multiline,
    validate_ca_state,
)
from labfoundry.app.services.networking import (
    INTERFACE_MODES,
    INTERFACE_ROLES,
    VLAN_ROLES,
    normalize_interface_mode,
    physical_interface_to_dict,
    render_network_config,
    validate_network_state,
    vlan_interface_to_dict,
)
from labfoundry.app.services.routes_wan import (
    WAN_CONFIG_PATH,
    WAN_MODES,
    render_wan_config,
    route_to_dict,
    validate_wan_state,
    wan_policy_to_dict,
)
from labfoundry.app.services.firewall import (
    FIREWALL_ACTIONS,
    FIREWALL_DIRECTIONS,
    FIREWALL_POLICIES,
    FIREWALL_PROTOCOLS,
    firewall_rule_to_dict,
    render_nftables_config,
    validate_firewall_rule,
    validate_firewall_state,
)
from labfoundry.app.services.kms import (
    KMS_BACKENDS,
    KMS_CLIENT_ROLES,
    KMS_KEY_ALGORITHMS,
    KMS_KEY_STATES,
    join_csv,
    kms_client_to_dict,
    kms_key_to_dict,
    render_kms_config,
    split_csv,
    validate_kms_state,
)
from labfoundry.app.services.vcf_backups import (
    VCF_BACKUP_DEFAULT_VOLUME_MOUNT,
    render_vcf_backup_config,
    validate_vcf_backup_state,
    vcf_backup_remote_directory,
    vcf_backup_settings_to_dict,
)
from labfoundry.app.token_service import create_token_for_user

templates = Jinja2Templates(directory="labfoundry/app/templates")
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
    identity = context.pop("identity", None)
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


def user_to_dict(user: User, current_user_id: int | None = None) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "enabled": user.enabled,
        "created_at": user.created_at.strftime("%Y-%m-%d"),
        "is_current": user.id == current_user_id,
        "is_new": False,
    }


def users_context(db: Session, identity: Identity) -> dict:
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    return {
        "users": users,
        "users_json": [user_to_dict(user, identity.user_id) for user in users],
        "roles": [role.value for role in Role],
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


def get_ca_settings_row(db: Session) -> CaSettings:
    settings = db.execute(select(CaSettings)).scalar_one_or_none()
    if settings is None:
        settings = CaSettings()
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


def get_firewall_settings_row(db: Session) -> FirewallSettings:
    settings = db.execute(select(FirewallSettings)).scalar_one_or_none()
    if settings is None:
        settings = FirewallSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_vcf_backup_settings_row(db: Session) -> VcfBackupSettings:
    settings = db.execute(select(VcfBackupSettings).options(selectinload(VcfBackupSettings.sftp_user))).scalar_one_or_none()
    if settings is None:
        first_admin = db.execute(select(User).where(User.role == Role.ADMIN.value, User.enabled.is_(True)).order_by(User.username)).scalar_one_or_none()
        settings = VcfBackupSettings(sftp_user_id=first_admin.id if first_admin else None)
        db.add(settings)
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


def vcf_backup_listen_options(db: Session) -> list[dict[str, str]]:
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


def vcf_backup_context(db: Session) -> dict:
    settings = get_vcf_backup_settings_row(db)
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    available_interfaces = vcf_backup_listen_options(db)
    config_preview = render_vcf_backup_config(settings)
    validation_errors = validate_vcf_backup_state(settings, users, {interface["name"] for interface in available_interfaces})
    return {
        "vcf_backup_settings": settings,
        "vcf_backup_settings_json": vcf_backup_settings_to_dict(settings),
        "vcf_backup_users": users,
        "available_interfaces": available_interfaces,
        "vcf_backup_remote_directory": vcf_backup_remote_directory(settings),
        "vcf_backup_config_preview": config_preview,
        "vcf_backup_validation_errors": validation_errors,
    }


def firewall_context(db: Session) -> dict:
    settings = get_firewall_settings_row(db)
    rules = db.execute(select(FirewallRule).order_by(FirewallRule.priority, FirewallRule.name)).scalars().all()
    config_preview = render_nftables_config(settings, rules)
    validation_errors = validate_firewall_state(settings, rules)
    return {
        "firewall_settings": settings,
        "firewall_rules": rules,
        "firewall_rules_json": [firewall_rule_to_dict(rule) for rule in rules],
        "firewall_config_preview": config_preview,
        "firewall_validation_errors": validation_errors,
        "firewall_directions": FIREWALL_DIRECTIONS,
        "firewall_actions": FIREWALL_ACTIONS,
        "firewall_protocols": FIREWALL_PROTOCOLS,
        "firewall_policies": FIREWALL_POLICIES,
        "physical_interfaces": db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all(),
    }


def ca_context(db: Session) -> dict:
    settings = get_ca_settings_row(db)
    profiles = db.execute(select(CaProfile).order_by(CaProfile.name)).scalars().all()
    certificates = (
        db.execute(select(CaCertificate).options(selectinload(CaCertificate.profile)).order_by(CaCertificate.common_name))
        .scalars()
        .all()
    )
    config_preview = render_ca_config(settings=settings, profiles=profiles, certificates=certificates)
    validation_errors = validate_ca_state(settings=settings, profiles=profiles, certificates=certificates)
    return {
        "ca_settings": settings,
        "ca_profiles": profiles,
        "ca_profile_rows": [ca_profile_to_dict(profile) for profile in profiles],
        "ca_certificate_rows": [ca_certificate_to_dict(certificate) for certificate in certificates],
        "ca_profile_choices": [{"id": profile.id, "label": profile.name} for profile in profiles if profile.enabled],
        "ca_certificates": certificates,
        "ca_config_preview": config_preview,
        "ca_validation_errors": validation_errors,
    }


def kms_context(db: Session) -> dict:
    settings = get_kms_settings_row(db)
    clients = db.execute(select(KmsClient).order_by(KmsClient.name)).scalars().all()
    keys = db.execute(select(KmsKey).options(selectinload(KmsKey.owner_client)).order_by(KmsKey.name)).scalars().all()
    interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    config_preview = render_kms_config(settings=settings, clients=clients, keys=keys)
    validation_errors = validate_kms_state(settings=settings, clients=clients, keys=keys)
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
        "available_interfaces": interfaces,
        "kms_config_preview": config_preview,
        "kms_validation_errors": validation_errors,
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
    return {
        "physical_interfaces": interfaces,
        "physical_interface_rows": [physical_interface_to_dict(interface, vlan_counts.get(interface.name, 0)) for interface in interfaces],
        "vlan_interfaces": vlans,
        "vlan_interface_rows": [vlan_interface_to_dict(vlan) for vlan in vlans],
        "interface_names": [interface.name for interface in interfaces],
        "trunk_interface_names": [interface.name for interface in interfaces if normalize_interface_mode(interface.mode) == "trunk"],
        "interface_roles": INTERFACE_ROLES,
        "interface_modes": INTERFACE_MODES,
        "vlan_roles": VLAN_ROLES,
        "network_config_preview": config_preview,
        "network_validation_errors": validation_errors,
        "network_config_path": "/etc/labfoundry/network/labfoundry-network.conf",
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
                "label": f"{interface.name} - physical / {interface.role} / {interface.ip_cidr}",
            }
        )
    for vlan in vlans:
        if not vlan.enabled or not vlan.ip_cidr:
            continue
        targets.append(
            {
                "name": vlan.name,
                "label": f"{vlan.name} - VLAN {vlan.vlan_id} on {vlan.parent_interface} / {vlan.role} / {vlan.ip_cidr}",
            }
        )
    return targets


def routes_wan_context(db: Session) -> dict:
    routes = db.execute(select(Route).options(selectinload(Route.wan_policy)).order_by(Route.destination_cidr)).scalars().all()
    policies = db.execute(select(WanPolicy).order_by(WanPolicy.name)).scalars().all()
    targets = wan_route_targets(db)
    validation_errors = validate_wan_state(routes, policies, {target["name"] for target in targets})
    config_preview = render_wan_config(routes)
    return {
        "routes": routes,
        "policies": policies,
        "route_rows": [route_to_dict(route) for route in routes],
        "policy_rows": [wan_policy_to_dict(policy) for policy in policies],
        "wan_route_targets": targets,
        "wan_route_target_names": [target["name"] for target in targets],
        "wan_policy_options": [{"id": policy.id, "label": policy.name} for policy in policies],
        "wan_modes": WAN_MODES,
        "wan_config_path": WAN_CONFIG_PATH,
        "wan_config_preview": config_preview,
        "wan_validation_errors": validation_errors,
    }


def dnsmasq_context(db: Session) -> dict:
    dns_settings = get_dns_settings_row(db)
    dns_records = db.execute(select(DnsRecord).order_by(DnsRecord.hostname)).scalars().all()
    dhcp_settings = get_dhcp_settings_row(db)
    dhcp_scopes = db.execute(select(DhcpScope).order_by(DhcpScope.name)).scalars().all()
    dhcp_options = db.execute(select(DhcpOption).order_by(DhcpOption.scope_id, DhcpOption.option_code)).scalars().all()
    dhcp_reservations = db.execute(select(DhcpReservation).order_by(DhcpReservation.hostname)).scalars().all()
    available_interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
    vlan_interfaces = db.execute(select(VlanInterface).order_by(VlanInterface.name)).scalars().all()
    config_preview = render_dnsmasq_config(
        dns_settings=dns_settings,
        dns_records=dns_records,
        dhcp_settings=dhcp_settings,
        dhcp_reservations=dhcp_reservations,
        dhcp_scopes=dhcp_scopes,
        dhcp_options=dhcp_options,
    )
    validation_errors = validate_dns_settings(dns_settings, dns_records) + validate_dhcp_settings(
        dhcp_settings,
        dhcp_reservations,
        dhcp_scopes,
        dhcp_options,
    )
    dns_domains = split_domains(dns_settings.domain) or ["labfoundry.internal"]
    dns_warnings = dns_domain_warnings(dns_domains)
    dns_record_groups = dns_records_by_domain(dns_records, dns_domains)
    reverse_zone_groups = reverse_records_by_zone(dns_reverse_records(dns_records))
    lease_result = SystemAdapter().read_dhcp_leases()
    dhcp_leases = parse_dnsmasq_leases(lease_result.stdout)
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
        "dhcp_reservations": dhcp_reservations,
        "dhcp_reservation_rows": [dhcp_reservation_payload(item) for item in dhcp_reservations],
        "dhcp_leases": dhcp_leases,
        "dhcp_lease_dry_run": lease_result.dry_run,
        "dhcp_lease_command": " ".join(lease_result.command),
        "available_interfaces": available_interfaces,
        "available_dns_addresses": available_dns_listen_addresses(dns_settings, dhcp_settings, vlan_interfaces),
        "selected_dns_interfaces": split_interfaces(dns_settings.listen_interface),
        "selected_dns_addresses": split_addresses(dns_settings.listen_address),
        "config_preview": config_preview,
        "dns_domains": "\n".join(dns_domains),
        "hosts_editor_text": render_hosts_records(dns_records),
        "validation_errors": validation_errors,
        "dns_warnings": dns_warnings,
        "upstream_servers": "\n".join(split_servers(dns_settings.upstream_servers)),
        "dns_domain_options": dns_domains,
    }


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


def available_dns_listen_addresses(
    dns_settings: DnsSettings,
    dhcp_settings: DhcpSettings,
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
    add(dhcp_settings.site_address, "SiteA gateway")
    for vlan in vlan_interfaces:
        if vlan.ip_cidr:
            try:
                add(str(ip_interface(vlan.ip_cidr).ip), vlan.name)
            except ValueError:
                add(vlan.ip_cidr, vlan.name)
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
) -> RedirectResponse | HTMLResponse:
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


@router.get("/routes-wan", response_class=HTMLResponse, response_model=None)
def routes_wan(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "routes_wan.html", {"identity": identity, **routes_wan_context(db)})


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
    mode_value = wan_mode.strip() if wan_mode.strip() in WAN_MODES else "interface"
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


@router.post("/routes-wan/apply-task", response_model=None)
def create_routes_wan_apply_task(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    context = routes_wan_context(db)
    validation_errors = context["wan_validation_errors"]
    if validation_errors:
        return render(
            request,
            "routes_wan.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )
    adapter = SystemAdapter()
    validate_result = adapter.validate_wan_config(context["wan_config_path"])
    apply_result = adapter.apply_wan_config(context["wan_config_path"])
    now = utcnow()
    job_result = {
        "config_path": context["wan_config_path"],
        "dry_run": apply_result.dry_run,
        "commands": [validate_result.command, apply_result.command],
        "config_preview": context["wan_config_preview"],
        "route_count": len(context["routes"]),
        "policy_count": len(context["policies"]),
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="wan-apply",
        status=JobStatus.SUCCEEDED.value if validate_result.returncode == 0 and apply_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 else "WAN apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_wan_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(validate_result.command + [";"] + apply_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    return render(
        request,
        "routes_wan.html",
        {
            "identity": identity,
            **routes_wan_context(db),
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


@router.get("/firewall", response_class=HTMLResponse, response_model=None)
def firewall(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "firewall.html", {"identity": identity, **firewall_context(db)})


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
    record_audit(db, actor=identity.username, action="update_firewall_settings", resource_type="firewall", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave"):
        return JSONResponse({"updated_at": settings.updated_at.isoformat()})
    return RedirectResponse("/firewall", status_code=303)


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
    errors = validate_firewall_rule(rule)
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
    errors = validate_firewall_rule(rule)
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


@router.post("/firewall/apply-task", response_model=None)
def create_firewall_apply_task(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    context = firewall_context(db)
    settings = context["firewall_settings"]
    errors = context["firewall_validation_errors"]
    validate_result = SystemAdapter().validate_firewall_config(settings.config_path)
    apply_result = SystemAdapter().apply_firewall_config(settings.config_path)
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="firewall-apply",
        status=JobStatus.SUCCEEDED.value if not errors else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=utcnow(),
        finished_at=utcnow(),
        progress_percent=100,
        result="\n".join([context["firewall_config_preview"], " ".join(validate_result.command), " ".join(apply_result.command)]),
        error="\n".join(errors) if errors else None,
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_firewall_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(validate_result.command + [";"] + apply_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    return render(
        request,
        "firewall.html",
        {
            "identity": identity,
            **firewall_context(db),
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


@router.get("/physical-interfaces", response_class=HTMLResponse, response_model=None)
def physical_interfaces_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "physical_interfaces.html", {"identity": identity, **network_context(db)})


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
    db.commit()
    record_audit(db, actor=identity.username, action="update_physical_interface", resource_type="interface", resource_id=interface.name)
    return RedirectResponse("/physical-interfaces", status_code=303)


@router.post("/physical-interfaces/apply-task", response_model=None)
def create_physical_interfaces_apply_task_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    context = network_context(db)
    validation_errors = context["network_validation_errors"]
    if validation_errors:
        return render(
            request,
            "physical_interfaces.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )
    adapter = SystemAdapter()
    validate_result = adapter.validate_network_config(context["network_config_path"])
    apply_result = adapter.apply_network_config(context["network_config_path"])
    now = utcnow()
    job_result = {
        "config_path": context["network_config_path"],
        "dry_run": apply_result.dry_run,
        "commands": [validate_result.command, apply_result.command],
        "config_preview": context["network_config_preview"],
        "physical_interface_count": len(context["physical_interfaces"]),
        "vlan_count": len(context["vlan_interfaces"]),
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="network-apply",
        status=JobStatus.SUCCEEDED.value if validate_result.returncode == 0 and apply_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 else "Network apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_network_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(validate_result.command + [";"] + apply_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    return render(
        request,
        "physical_interfaces.html",
        {
            "identity": identity,
            **network_context(db),
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


@router.get("/vlan-interfaces", response_class=HTMLResponse, response_model=None)
def vlan_interfaces_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "vlan_interfaces.html", {"identity": identity, **network_context(db)})


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
) -> RedirectResponse | HTMLResponse:
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
) -> RedirectResponse | HTMLResponse:
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


@router.post("/vlan-interfaces/apply-task", response_model=None)
def create_vlan_interfaces_apply_task_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    context = network_context(db)
    validation_errors = context["network_validation_errors"]
    if validation_errors:
        return render(
            request,
            "vlan_interfaces.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )
    adapter = SystemAdapter()
    validate_result = adapter.validate_network_config(context["network_config_path"])
    apply_result = adapter.apply_network_config(context["network_config_path"])
    now = utcnow()
    job_result = {
        "config_path": context["network_config_path"],
        "dry_run": apply_result.dry_run,
        "commands": [validate_result.command, apply_result.command],
        "config_preview": context["network_config_preview"],
        "physical_interface_count": len(context["physical_interfaces"]),
        "vlan_count": len(context["vlan_interfaces"]),
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="network-apply",
        status=JobStatus.SUCCEEDED.value if validate_result.returncode == 0 and apply_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 else "Network apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_network_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(validate_result.command + [";"] + apply_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    return render(
        request,
        "vlan_interfaces.html",
        {
            "identity": identity,
            **network_context(db),
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


@router.get("/dns", response_class=HTMLResponse, response_model=None)
def dns_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "dns.html", {"identity": identity, **dnsmasq_context(db)})


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
    cache_size: int = Form(1000),
    expand_hosts: str | None = Form(None),
    authoritative: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_dns_settings_row(db)
    available_names = {item.name for item in db.execute(select(PhysicalInterface)).scalars().all()}
    selected_interfaces = [interface.strip() for interface in listen_interfaces if interface.strip()]
    if available_names:
        selected_interfaces = [interface for interface in selected_interfaces if interface in available_names]
    if not selected_interfaces:
        selected_interfaces = split_interfaces(settings.listen_interface) or ["eth1"]
    selected_addresses = split_addresses(join_addresses(listen_addresses))
    if listen_addresses_present is None and not selected_addresses:
        selected_addresses = split_addresses(settings.listen_address)
    settings.enabled = enabled == "on"
    settings.listen_interface = join_interfaces(selected_interfaces)
    settings.listen_address = join_addresses(selected_addresses) or None
    if domains is not None:
        settings.domain = join_domains(split_domains(domains))
    settings.upstream_servers = join_servers(split_servers(upstream_servers))
    settings.cache_size = cache_size
    settings.expand_hosts = expand_hosts == "on"
    settings.authoritative = authoritative == "on"
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_dns_settings", resource_type="dns", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": settings.updated_at.isoformat(),
                "listen_interfaces": split_interfaces(settings.listen_interface),
                "listen_addresses": split_addresses(settings.listen_address),
            }
        )
    return RedirectResponse("/dns", status_code=303)


@router.post("/dns/apply-task", response_model=None)
def create_dns_apply_task_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    context = dnsmasq_context(db)
    validation_errors = context["validation_errors"]
    if validation_errors:
        return render(
            request,
            "dns.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )

    adapter = SystemAdapter()
    apply_result = adapter.apply_dnsmasq_config(context["dns_settings"].config_path)
    reload_result = adapter.reload_dnsmasq()
    now = utcnow()
    commands = [apply_result.command, reload_result.command]
    job_result = {
        "config_path": context["dns_settings"].config_path,
        "dry_run": apply_result.dry_run,
        "commands": commands,
        "config_preview": context["config_preview"],
        "warnings": context["dns_warnings"],
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="dns-apply",
        status=JobStatus.SUCCEEDED.value if apply_result.returncode == 0 and reload_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 and reload_result.returncode == 0 else "DNS apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_dns_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(apply_result.command + [";"] + reload_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    refreshed_context = dnsmasq_context(db)
    return render(
        request,
        "dns.html",
        {
            "identity": identity,
            **refreshed_context,
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


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
    return render(request, "dhcp.html", {"identity": identity, **dnsmasq_context(db)})


@router.post("/dhcp/settings", response_model=None)
def update_dhcp_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    interface_name: str = Form(...),
    site_address: str = Form(...),
    prefix_length: int = Form(...),
    range_start: str = Form(...),
    range_end: str = Form(...),
    lease_time: str = Form(...),
    domain_name: str = Form(...),
    dns_server: str = Form(...),
    authoritative: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_dhcp_settings_row(db)
    settings.enabled = enabled == "on"
    settings.interface_name = interface_name
    settings.site_address = site_address
    settings.prefix_length = prefix_length
    settings.range_start = range_start
    settings.range_end = range_end
    settings.lease_time = lease_time
    settings.domain_name = domain_name
    settings.dns_server = dns_server
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


@router.post("/dhcp/apply-task", response_model=None)
def create_dhcp_apply_task_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    verify_csrf(request, csrf)
    context = dnsmasq_context(db)
    validation_errors = context["validation_errors"]
    if validation_errors:
        return render(
            request,
            "dhcp.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )

    adapter = SystemAdapter()
    apply_result = adapter.apply_dnsmasq_config(context["dhcp_settings"].config_path)
    reload_result = adapter.reload_dnsmasq()
    now = utcnow()
    job_result = {
        "config_path": context["dhcp_settings"].config_path,
        "dry_run": apply_result.dry_run,
        "commands": [apply_result.command, reload_result.command],
        "config_preview": context["config_preview"],
        "scope_count": len(context["dhcp_scopes"]),
        "option_count": len(context["dhcp_options"]),
        "reservation_count": len(context["dhcp_reservations"]),
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="dhcp-apply",
        status=JobStatus.SUCCEEDED.value if apply_result.returncode == 0 and reload_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 and reload_result.returncode == 0 else "DHCP apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_dhcp_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(apply_result.command + [";"] + reload_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    refreshed_context = dnsmasq_context(db)
    return render(
        request,
        "dhcp.html",
        {
            "identity": identity,
            **refreshed_context,
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


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
    return render(request, "certificate_authority.html", {"identity": identity, **ca_context(db)})


@router.get("/certificate-authority/downloads/root-ca.pem", response_model=None)
def download_root_ca(
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    settings = get_ca_settings_row(db)
    cert_bytes, _key_bytes = ensure_development_root_ca(settings)
    record_audit(db, actor=identity.username, action="download_ca_root_certificate", resource_type="ca", resource_id=str(settings.id))
    return Response(
        cert_bytes,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="labfoundry-root-ca.pem"'},
    )


@router.get("/certificate-authority/downloads/ca-bundle.pem", response_model=None)
def download_ca_bundle(
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> Response:
    settings = get_ca_settings_row(db)
    cert_bytes, _key_bytes = ensure_development_root_ca(settings)
    record_audit(db, actor=identity.username, action="download_ca_bundle", resource_type="ca", resource_id=str(settings.id))
    return Response(
        cert_bytes,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="labfoundry-ca-bundle.pem"'},
    )


@router.post("/certificate-authority/settings", response_model=None)
def update_ca_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
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
    storage_path: str = Form("/etc/labfoundry/ca"),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_ca_settings_row(db)
    settings.enabled = enabled == "on"
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
    settings.storage_path = storage_path.strip() or "/etc/labfoundry/ca"
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_ca_settings", resource_type="ca", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        return JSONResponse({"status": "saved", "updated_at": settings.updated_at.isoformat()})
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


@router.post("/certificate-authority/apply-task", response_model=None)
def create_ca_apply_task_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    context = ca_context(db)
    validation_errors = context["ca_validation_errors"]
    if validation_errors:
        return render(
            request,
            "certificate_authority.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )

    adapter = SystemAdapter()
    config_path = f"{context['ca_settings'].storage_path}/labfoundry-ca.conf"
    validate_result = adapter.validate_ca_config(config_path)
    apply_result = adapter.apply_ca_config(config_path)
    now = utcnow()
    job_result = {
        "config_path": config_path,
        "dry_run": apply_result.dry_run,
        "commands": [validate_result.command, apply_result.command],
        "config_preview": context["ca_config_preview"],
        "profile_count": len(context["ca_profiles"]),
        "certificate_count": len(context["ca_certificates"]),
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="ca-apply",
        status=JobStatus.SUCCEEDED.value if validate_result.returncode == 0 and apply_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 else "CA apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_ca_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(validate_result.command + [";"] + apply_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    return render(
        request,
        "certificate_authority.html",
        {
            "identity": identity,
            **ca_context(db),
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


@router.get("/kms", response_class=HTMLResponse, response_model=None)
def kms_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "kms.html", {"identity": identity, **kms_context(db)})


@router.post("/kms/settings", response_model=None)
def update_kms_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    backend: str = Form("pykmip"),
    listen_interface: str = Form("eth1"),
    listen_address: str = Form("192.168.50.1"),
    port: int = Form(5696),
    hostname: str = Form("kms.labfoundry.internal"),
    server_certificate: str = Form("kms.labfoundry.internal"),
    ca_certificate_path: str = Form("/etc/labfoundry/ca/root.crt"),
    database_path: str = Form("/var/lib/labfoundry/kms/pykmip.db"),
    config_path: str = Form("/etc/labfoundry/kms/pykmip.conf"),
    require_client_cert: str | None = Form(None),
    allow_register: str | None = Form(None),
    allow_destroy: str | None = Form(None),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_kms_settings_row(db)
    settings.enabled = enabled == "on"
    settings.backend = backend.strip().lower() or "pykmip"
    settings.listen_interface = listen_interface.strip() or "eth1"
    settings.listen_address = listen_address.strip() or "192.168.50.1"
    settings.port = port
    settings.hostname = hostname.strip() or "kms.labfoundry.internal"
    settings.server_certificate = server_certificate.strip() or settings.hostname
    settings.ca_certificate_path = ca_certificate_path.strip() or "/etc/labfoundry/ca/root.crt"
    settings.database_path = database_path.strip() or "/var/lib/labfoundry/kms/pykmip.db"
    settings.config_path = config_path.strip() or "/etc/labfoundry/kms/pykmip.conf"
    settings.require_client_cert = require_client_cert == "on"
    settings.allow_register = allow_register == "on"
    settings.allow_destroy = allow_destroy == "on"
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_kms_settings", resource_type="kms", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        return JSONResponse({"status": "saved", "updated_at": settings.updated_at.isoformat()})
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


@router.post("/kms/apply-task", response_model=None)
def create_kms_apply_task_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    context = kms_context(db)
    validation_errors = context["kms_validation_errors"]
    if validation_errors:
        return render(
            request,
            "kms.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )

    adapter = SystemAdapter()
    validate_result = adapter.validate_kms_config(context["kms_settings"].config_path)
    apply_result = adapter.apply_kms_config(context["kms_settings"].config_path)
    now = utcnow()
    job_result = {
        "backend": context["kms_settings"].backend,
        "config_path": context["kms_settings"].config_path,
        "dry_run": apply_result.dry_run,
        "commands": [validate_result.command, apply_result.command],
        "config_preview": context["kms_config_preview"],
        "client_count": len(context["kms_clients"]),
        "key_count": len(context["kms_keys"]),
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="kms-apply",
        status=JobStatus.SUCCEEDED.value if validate_result.returncode == 0 and apply_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 else "KMS apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_kms_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(validate_result.command + [";"] + apply_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    return render(
        request,
        "kms.html",
        {
            "identity": identity,
            **kms_context(db),
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


@router.get("/vcf-backups", response_class=HTMLResponse, response_model=None)
def vcf_backups_page(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "vcf_backups.html", {"identity": identity, **vcf_backup_context(db)})


@router.post("/vcf-backups/settings", response_model=None)
def update_vcf_backup_settings_from_ui(
    request: Request,
    enabled: str | None = Form(None),
    listen_interface: str = Form("eth2"),
    port: int = Form(22),
    sftp_user_id: str = Form(""),
    chroot_enabled: str | None = Form(None),
    allow_password_auth: str | None = Form(None),
    allow_public_key_auth: str | None = Form(None),
    max_sessions: int = Form(4),
    config_path: str = Form("/etc/labfoundry/ssh/sshd_config.d/labfoundry-vcf-backups.conf"),
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> RedirectResponse | JSONResponse:
    verify_csrf(request, csrf)
    settings = get_vcf_backup_settings_row(db)
    user_id = int(sftp_user_id) if str(sftp_user_id).strip() else None
    if user_id and not db.get(User, user_id):
        raise HTTPException(status_code=400, detail="Selected SFTP user does not exist.")
    listen_options = {option["name"]: option for option in vcf_backup_listen_options(db)}
    selected_interface = listen_interface.strip() or "eth2"
    if selected_interface not in listen_options:
        raise HTTPException(status_code=400, detail="Select an access physical interface or VLAN interface with an IP address.")
    settings.enabled = enabled == "on"
    settings.listen_interface = selected_interface
    settings.listen_address = listen_options[selected_interface]["address"]
    settings.port = port
    settings.sftp_user_id = user_id
    settings.storage_path = VCF_BACKUP_DEFAULT_VOLUME_MOUNT
    settings.chroot_enabled = chroot_enabled == "on"
    settings.allow_password_auth = allow_password_auth == "on"
    settings.allow_public_key_auth = allow_public_key_auth == "on"
    settings.max_sessions = max_sessions
    settings.config_path = config_path.strip() or "/etc/labfoundry/ssh/sshd_config.d/labfoundry-vcf-backups.conf"
    settings.updated_at = utcnow()
    db.commit()
    record_audit(db, actor=identity.username, action="update_vcf_backup_settings", resource_type="vcf_backups", resource_id=str(settings.id))
    if request.headers.get("X-LabFoundry-Autosave") == "1":
        context = vcf_backup_context(db)
        saved_settings = context["vcf_backup_settings"]
        validation_errors = context["vcf_backup_validation_errors"]
        return JSONResponse(
            {
                "status": "saved",
                "updated_at": saved_settings.updated_at.isoformat(),
                "listen_interface": saved_settings.listen_interface,
                "listen_address": saved_settings.listen_address,
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


@router.post("/vcf-backups/apply-task", response_model=None)
def create_vcf_backup_apply_task_from_ui(
    request: Request,
    csrf: str = Form(...),
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    context = vcf_backup_context(db)
    validation_errors = context["vcf_backup_validation_errors"]
    if validation_errors:
        return render(
            request,
            "vcf_backups.html",
            {
                "identity": identity,
                **context,
                "apply_task_error": "Resolve validation errors before creating an appliance apply task.",
            },
            status_code=422,
        )

    adapter = SystemAdapter()
    settings = context["vcf_backup_settings"]
    validate_result = adapter.validate_vcf_backup_config(settings.config_path)
    apply_result = adapter.apply_vcf_backup_config(settings.config_path)
    now = utcnow()
    job_result = {
        "config_path": settings.config_path,
        "storage_path": settings.storage_path,
        "sftp_user": settings.sftp_user.username if settings.sftp_user else "",
        "dry_run": apply_result.dry_run,
        "commands": [validate_result.command, apply_result.command],
        "config_preview": context["vcf_backup_config_preview"],
    }
    job = Job(
        id=f"job_{uuid4().hex[:12]}",
        type="vcf-backups-apply",
        status=JobStatus.SUCCEEDED.value if validate_result.returncode == 0 and apply_result.returncode == 0 else JobStatus.FAILED.value,
        created_by=identity.username,
        started_at=now,
        finished_at=now,
        progress_percent=100,
        result=json.dumps(job_result, indent=2),
        error=None if apply_result.returncode == 0 else "VCF backup SFTP apply adapter reported a failure.",
    )
    db.add(job)
    db.commit()
    record_audit(
        db,
        actor=identity.username,
        action="create_vcf_backup_apply_task",
        resource_type="job",
        resource_id=job.id,
        detail=" ".join(validate_result.command + [";"] + apply_result.command),
        success=job.status == JobStatus.SUCCEEDED.value,
    )
    return render(
        request,
        "vcf_backups.html",
        {
            "identity": identity,
            **vcf_backup_context(db),
            "apply_task": job,
            "apply_task_dry_run": apply_result.dry_run,
        },
    )


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
    return render(request, "users.html", {"identity": identity, **users_context(db, identity)})


@router.post("/users", response_model=None)
def create_user_from_ui(
    request: Request,
    username: str = Form(...),
    role: str = Form(Role.VIEWER.value),
    password: str = Form(...),
    enabled: str | None = Form(None),
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
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Temporary password must be at least 8 characters.")
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"User {username} already exists.")
    user = User(username=username, role=role, password_hash=hash_password(password), enabled=enabled == "on")
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
    enabled: str | None = Form(None),
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
    next_enabled = enabled == "on"
    if user.id == identity.user_id and not next_enabled:
        raise HTTPException(status_code=400, detail="You cannot disable your own active session account.")
    protect_last_admin(db, user, next_role=role, next_enabled=next_enabled)
    existing = db.execute(select(User).where(User.username == username, User.id != user.id)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"User {username} already exists.")
    old_username = user.username
    user.username = username
    user.role = role
    user.enabled = next_enabled
    if old_username != username:
        tokens = db.execute(select(ApiToken).where(ApiToken.owner_user_id == user.id)).scalars().all()
        for token in tokens:
            token.owner_username = username
            db.add(token)
    if not next_enabled:
        revoke_user_tokens(db, user, identity.username)
    db.add(user)
    db.commit()
    record_audit(db, actor=identity.username, action="update_local_user", resource_type="user", resource_id=str(user.id))
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
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="Password confirmation does not match.")
    user.password_hash = hash_password(password)
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


@router.get("/audit-log", response_class=HTMLResponse, response_model=None)
def audit_log(
    request: Request,
    identity: Identity = Depends(require_session_identity),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    events = db.execute(select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(100)).scalars().all()
    return render(request, "audit.html", {"identity": identity, "events": events})


@router.get("/settings", response_class=HTMLResponse, response_model=None)
def settings_page(request: Request, identity: Identity = Depends(require_session_identity)) -> HTMLResponse:
    settings = get_settings()
    return render(request, "settings.html", {"identity": identity, "settings": settings})


@router.get("/{page}", response_class=HTMLResponse, response_model=None)
def placeholder_page(page: str, request: Request, identity: Identity = Depends(require_session_identity)) -> HTMLResponse:
    known = {
        "physical-interfaces": "Physical Interfaces",
        "vlan-interfaces": "VLAN Interfaces",
        "certificate-authority": "Certificate Authority",
        "https-repository": "HTTPS Repository",
        "vcf-backups": "VCF Backups",
        "logs": "Logs",
        "backup-restore": "Backup / Restore",
    }
    if page not in known:
        raise HTTPException(status_code=404, detail="Page not found")
    return render(request, "placeholder.html", {"identity": identity, "title": known[page]})

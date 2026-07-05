from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime as SqlDateTime
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from labfoundry import __version__
from labfoundry.app.models import (
    ApplianceSettings,
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
    KmsClient,
    KmsKey,
    KmsSettings,
    NatRule,
    ChronySettings,
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
)
from labfoundry.app.seed import SEED_EXAMPLES_SETTING_KEY, seed_initial_data
from labfoundry.app.services.dnsmasq import DNS_CONDITIONAL_FORWARDERS_SETTING_KEY
from labfoundry.app.services.esxi_pxe import host_variables_json, normalize_host_mac, normalize_host_variables
from labfoundry.app.services.firewall import FIREWALL_SOURCE_GROUPS_SETTING_KEY
from labfoundry.app.services.local_users import LOCAL_USERS_PASSWORD_POLICY_KEY

ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_KIND = "labfoundry-settings-archive"
SAFE_SETTING_KEYS = {
    DNS_CONDITIONAL_FORWARDERS_SETTING_KEY,
    FIREWALL_SOURCE_GROUPS_SETTING_KEY,
    LOCAL_USERS_PASSWORD_POLICY_KEY,
}

SCALAR_TABLES = {
    "physical_interfaces": PhysicalInterface,
    "vlan_interfaces": VlanInterface,
    "wan_policies": WanPolicy,
    "nat_rules": NatRule,
    "service_states": ServiceState,
    "appliance_settings": ApplianceSettings,
    "chrony_settings": ChronySettings,
    "dns_settings": DnsSettings,
    "dns_records": DnsRecord,
    "dhcp_settings": DhcpSettings,
    "dhcp_scopes": DhcpScope,
    "dhcp_reservations": DhcpReservation,
    "firewall_settings": FirewallSettings,
    "firewall_rules": FirewallRule,
    "ca_settings": CaSettings,
    "ca_profiles": CaProfile,
    "kms_settings": KmsSettings,
    "kms_clients": KmsClient,
    "vcf_private_registry_settings": VcfPrivateRegistrySettings,
    "vcf_registry_bundles": VcfRegistryBundle,
    "vcf_offline_depot_settings": VcfOfflineDepotSettings,
    "vcf_depot_download_profiles": VcfDepotDownloadProfile,
    "esxi_kickstarts": EsxiKickstart,
}

RESTORE_DELETE_MODELS = [
    EsxiPxeHost,
    EsxiKickstart,
    VcfRegistryBundle,
    VcfDepotDownloadProfile,
    VcfOfflineDepotSettings,
    VcfPrivateRegistrySettings,
    VcfBackupSettings,
    KmsKey,
    KmsClient,
    KmsSettings,
    CaCertificate,
    CaProfile,
    CaSettings,
    FirewallRule,
    FirewallSettings,
    DhcpOption,
    DhcpReservation,
    DhcpScope,
    DhcpSettings,
    DnsRecord,
    DnsSettings,
    Route,
    NatRule,
    WanPolicy,
    VlanInterface,
    PhysicalInterface,
    ServiceState,
    ChronySettings,
    ApplianceSettings,
    Setting,
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: object, *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = {"id", "created_at", "updated_at", *(exclude or set())}
    payload: dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.name in excluded or isinstance(column.type, SqlDateTime):
            continue
        payload[column.name] = getattr(row, column.name)
    return payload


def _settings_rows(db: Session) -> list[dict[str, str]]:
    rows = db.execute(select(Setting).where(Setting.key.in_(SAFE_SETTING_KEYS)).order_by(Setting.key)).scalars().all()
    return [_row_to_dict(row) for row in rows]


def export_settings_archive(db: Session, *, actor: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": ARCHIVE_KIND,
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "app_version": __version__,
        "exported_at": _utc_iso(),
        "exported_by": actor,
        "notes": [
            "Contains LabFoundry desired-state configuration only.",
            "Audit events, jobs, API tokens, password hashes, and uploaded secret bodies are not included; encrypted CA private-key material is included for trust portability.",
            "Restoring usable CA private-key material requires the same LABFOUNDRY_SECRETS_KEY.",
            "Restore updates the control-plane database; host services change only after global appliance apply.",
        ],
        "data": {},
    }
    data = payload["data"]
    for key, model in SCALAR_TABLES.items():
        rows = db.execute(select(model)).scalars().all()
        data[key] = [_row_to_dict(row) for row in rows]

    data["routes"] = _routes_to_archive(db)
    data["dhcp_options"] = _dhcp_options_to_archive(db)
    data["ca_certificates"] = _ca_certificates_to_archive(db)
    data["kms_keys"] = _kms_keys_to_archive(db)
    data["vcf_backup_settings"] = _vcf_backup_settings_to_archive(db)
    data["vcf_offline_depot_settings"] = _vcf_offline_depot_settings_to_archive(db)
    data["esxi_pxe_hosts"] = _esxi_pxe_hosts_to_archive(db)
    data["settings"] = _settings_rows(db)
    return payload


def _routes_to_archive(db: Session) -> list[dict[str, Any]]:
    policies = {policy.id: policy.name for policy in db.execute(select(WanPolicy)).scalars().all()}
    rows = []
    for route in db.execute(select(Route)).scalars().all():
        payload = _row_to_dict(route, exclude={"wan_policy_id"})
        payload["wan_policy_name"] = policies.get(route.wan_policy_id) if route.wan_policy_id else ""
        rows.append(payload)
    return rows


def _dhcp_options_to_archive(db: Session) -> list[dict[str, Any]]:
    scopes = {scope.id: scope.name for scope in db.execute(select(DhcpScope)).scalars().all()}
    rows = []
    for option in db.execute(select(DhcpOption)).scalars().all():
        payload = _row_to_dict(option, exclude={"scope_id"})
        payload["scope_name"] = scopes.get(option.scope_id) if option.scope_id else ""
        rows.append(payload)
    return rows


def _ca_certificates_to_archive(db: Session) -> list[dict[str, Any]]:
    profiles = {profile.id: profile.name for profile in db.execute(select(CaProfile)).scalars().all()}
    rows = []
    for certificate in db.execute(select(CaCertificate)).scalars().all():
        payload = _row_to_dict(certificate, exclude={"profile_id", "issued_at", "expires_at"})
        payload["profile_name"] = profiles.get(certificate.profile_id) if certificate.profile_id else ""
        rows.append(payload)
    return rows


def _kms_keys_to_archive(db: Session) -> list[dict[str, Any]]:
    clients = {client.id: client.name for client in db.execute(select(KmsClient)).scalars().all()}
    rows = []
    for key in db.execute(select(KmsKey)).scalars().all():
        payload = _row_to_dict(key, exclude={"owner_client_id"})
        payload["owner_client_name"] = clients.get(key.owner_client_id) if key.owner_client_id else ""
        rows.append(payload)
    return rows


def _vcf_backup_settings_to_archive(db: Session) -> list[dict[str, Any]]:
    users = {user.id: user.username for user in db.execute(select(User)).scalars().all()}
    rows = []
    for settings in db.execute(select(VcfBackupSettings)).scalars().all():
        payload = _row_to_dict(settings, exclude={"sftp_user_id"})
        payload["sftp_username"] = users.get(settings.sftp_user_id) if settings.sftp_user_id else ""
        rows.append(payload)
    return rows


def _vcf_offline_depot_settings_to_archive(db: Session) -> list[dict[str, Any]]:
    users = {user.id: user.username for user in db.execute(select(User)).scalars().all()}
    rows = []
    for settings in db.execute(select(VcfOfflineDepotSettings)).scalars().all():
        payload = _row_to_dict(settings, exclude={"http_user_id"})
        payload["http_username"] = users.get(settings.http_user_id) if settings.http_user_id else ""
        rows.append(payload)
    return rows


def _esxi_pxe_hosts_to_archive(db: Session) -> list[dict[str, Any]]:
    kickstarts = {row.id: row.name for row in db.execute(select(EsxiKickstart)).scalars().all()}
    rows = []
    for host in db.execute(select(EsxiPxeHost)).scalars().all():
        payload = _row_to_dict(host, exclude={"kickstart_id", "variables_json"})
        payload["kickstart_name"] = kickstarts.get(host.kickstart_id) if host.kickstart_id else ""
        payload["variables"] = normalize_host_variables(host.variables_json or "{}")
        rows.append(payload)
    return rows


def restore_settings_archive(db: Session, archive: dict[str, Any]) -> dict[str, int]:
    _validate_archive(archive)
    data = archive["data"]
    _clear_desired_state(db)

    counts: dict[str, int] = {}
    for key in ["physical_interfaces", "vlan_interfaces", "wan_policies", "nat_rules"]:
        counts[key] = _insert_rows(db, SCALAR_TABLES[key], data.get(key, []))
    db.flush()

    counts["routes"] = _restore_routes(db, data.get("routes", []))
    for key in [
        "service_states",
        "appliance_settings",
        "chrony_settings",
        "dns_settings",
        "dns_records",
        "dhcp_settings",
        "dhcp_scopes",
    ]:
        counts[key] = _insert_rows(db, SCALAR_TABLES[key], data.get(key, []))
    db.flush()
    _force_services_stopped_unconfigured(db)

    counts["dhcp_options"] = _restore_dhcp_options(db, data.get("dhcp_options", []))
    for key in [
        "dhcp_reservations",
        "firewall_settings",
        "firewall_rules",
        "ca_settings",
        "ca_profiles",
    ]:
        counts[key] = _insert_rows(db, SCALAR_TABLES[key], data.get(key, []))
    db.flush()

    counts["ca_certificates"] = _restore_ca_certificates(db, data.get("ca_certificates", []))
    counts["kms_settings"] = _insert_rows(db, KmsSettings, data.get("kms_settings", []))
    counts["kms_clients"] = _insert_rows(db, KmsClient, data.get("kms_clients", []))
    db.flush()
    counts["kms_keys"] = _restore_kms_keys(db, data.get("kms_keys", []))
    counts["vcf_backup_settings"] = _restore_vcf_backup_settings(db, data.get("vcf_backup_settings", []))
    counts["vcf_offline_depot_settings"] = _restore_vcf_offline_depot_settings(db, data.get("vcf_offline_depot_settings", []))
    for key in [
        "vcf_private_registry_settings",
        "vcf_registry_bundles",
        "vcf_depot_download_profiles",
        "esxi_kickstarts",
    ]:
        counts[key] = _insert_rows(db, SCALAR_TABLES[key], data.get(key, []))
    db.flush()
    counts["esxi_pxe_hosts"] = _restore_esxi_pxe_hosts(db, data.get("esxi_pxe_hosts", []))
    counts["settings"] = _insert_rows(db, Setting, [row for row in data.get("settings", []) if row.get("key") in SAFE_SETTING_KEYS])
    _disable_startup_example_seed(db)
    db.commit()
    return counts


def factory_reset_desired_state(db: Session) -> dict[str, int]:
    _clear_desired_state(db)
    seed_initial_data(db, include_examples=False)
    _disable_startup_example_seed(db)
    _force_services_stopped_unconfigured(db)
    db.commit()
    return desired_state_counts(db)


def desired_state_counts(db: Session) -> dict[str, int]:
    counts = {key: len(db.execute(select(model)).scalars().all()) for key, model in SCALAR_TABLES.items()}
    counts["routes"] = len(db.execute(select(Route)).scalars().all())
    counts["dhcp_options"] = len(db.execute(select(DhcpOption)).scalars().all())
    counts["ca_certificates"] = len(db.execute(select(CaCertificate)).scalars().all())
    counts["kms_keys"] = len(db.execute(select(KmsKey)).scalars().all())
    counts["vcf_backup_settings"] = len(db.execute(select(VcfBackupSettings)).scalars().all())
    counts["esxi_pxe_hosts"] = len(db.execute(select(EsxiPxeHost)).scalars().all())
    counts["settings"] = len(db.execute(select(Setting).where(Setting.key.in_(SAFE_SETTING_KEYS))).scalars().all())
    return counts


def archive_summary(archive: dict[str, Any]) -> dict[str, Any]:
    _validate_archive(archive)
    data = archive["data"]
    table_counts = {key: len(value) for key, value in data.items() if isinstance(value, list)}
    return {
        "exported_at": archive.get("exported_at", ""),
        "exported_by": archive.get("exported_by", ""),
        "app_version": archive.get("app_version", ""),
        "table_counts": table_counts,
        "total_rows": sum(table_counts.values()),
    }


def _clear_desired_state(db: Session) -> None:
    for model in RESTORE_DELETE_MODELS:
        db.execute(delete(model))
    db.flush()


def _force_services_stopped_unconfigured(db: Session) -> None:
    service_rows = db.execute(select(ServiceState)).scalars().all()
    for service in service_rows:
        service.running = False
        service.enabled = False
        service.health = "unconfigured"
        service.detail = "Stopped after settings restore or factory reset."
        db.add(service)
    db.flush()


def _disable_startup_example_seed(db: Session) -> None:
    existing = db.execute(select(Setting).where(Setting.key == SEED_EXAMPLES_SETTING_KEY)).scalar_one_or_none()
    if existing is None:
        db.add(Setting(key=SEED_EXAMPLES_SETTING_KEY, value="false"))
    else:
        existing.value = "false"
    db.flush()


def _validate_archive(archive: dict[str, Any]) -> None:
    if archive.get("kind") != ARCHIVE_KIND:
        raise ValueError("Upload a LabFoundry settings archive.")
    if archive.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise ValueError("This settings archive schema is not supported by this LabFoundry build.")
    if not isinstance(archive.get("data"), dict):
        raise ValueError("The settings archive is missing its data section.")


def _insert_rows(db: Session, model: type, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        db.add(model(**_model_kwargs(model, row)))
    db.flush()
    return len(rows)


def _model_kwargs(model: type, row: dict[str, Any], *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = {"id", "created_at", "updated_at", *(exclude or set())}
    column_names = {column.name for column in model.__table__.columns if not isinstance(column.type, SqlDateTime)}
    return {key: value for key, value in row.items() if key in column_names and key not in excluded}


def _restore_routes(db: Session, rows: list[dict[str, Any]]) -> int:
    policies = {policy.name: policy.id for policy in db.execute(select(WanPolicy)).scalars().all()}
    for row in rows:
        payload = _model_kwargs(Route, row, exclude={"wan_policy_id"})
        policy_name = str(row.get("wan_policy_name") or "")
        payload["wan_policy_id"] = policies.get(policy_name) if policy_name else None
        db.add(Route(**payload))
    db.flush()
    return len(rows)


def _restore_dhcp_options(db: Session, rows: list[dict[str, Any]]) -> int:
    scopes = {scope.name: scope.id for scope in db.execute(select(DhcpScope)).scalars().all()}
    for row in rows:
        payload = _model_kwargs(DhcpOption, row, exclude={"scope_id"})
        scope_name = str(row.get("scope_name") or "")
        payload["scope_id"] = scopes.get(scope_name) if scope_name else None
        db.add(DhcpOption(**payload))
    db.flush()
    return len(rows)


def _restore_ca_certificates(db: Session, rows: list[dict[str, Any]]) -> int:
    profiles = {profile.name: profile.id for profile in db.execute(select(CaProfile)).scalars().all()}
    for row in rows:
        payload = _model_kwargs(CaCertificate, row, exclude={"profile_id", "issued_at", "expires_at"})
        profile_name = str(row.get("profile_name") or "")
        payload["profile_id"] = profiles.get(profile_name) if profile_name else None
        db.add(CaCertificate(**payload))
    db.flush()
    return len(rows)


def _restore_kms_keys(db: Session, rows: list[dict[str, Any]]) -> int:
    clients = {client.name: client.id for client in db.execute(select(KmsClient)).scalars().all()}
    for row in rows:
        payload = _model_kwargs(KmsKey, row, exclude={"owner_client_id"})
        client_name = str(row.get("owner_client_name") or "")
        payload["owner_client_id"] = clients.get(client_name) if client_name else None
        db.add(KmsKey(**payload))
    db.flush()
    return len(rows)


def _restore_vcf_backup_settings(db: Session, rows: list[dict[str, Any]]) -> int:
    users = {user.username: user.id for user in db.execute(select(User)).scalars().all()}
    for row in rows:
        payload = _model_kwargs(VcfBackupSettings, row, exclude={"sftp_user_id"})
        username = str(row.get("sftp_username") or "")
        payload["sftp_user_id"] = users.get(username) if username else None
        db.add(VcfBackupSettings(**payload))
    db.flush()
    return len(rows)


def _restore_vcf_offline_depot_settings(db: Session, rows: list[dict[str, Any]]) -> int:
    users = {user.username: user.id for user in db.execute(select(User)).scalars().all()}
    for row in rows:
        payload = _model_kwargs(VcfOfflineDepotSettings, row, exclude={"http_user_id"})
        username = str(row.get("http_username") or "")
        payload["http_user_id"] = users.get(username) if username else None
        db.add(VcfOfflineDepotSettings(**payload))
    db.flush()
    return len(rows)


def _restore_esxi_pxe_hosts(db: Session, rows: list[dict[str, Any]]) -> int:
    kickstarts = {row.name: row.id for row in db.execute(select(EsxiKickstart)).scalars().all()}
    for row in rows:
        payload = _model_kwargs(EsxiPxeHost, row, exclude={"kickstart_id"})
        kickstart_name = str(row.get("kickstart_name") or "")
        payload["kickstart_id"] = kickstarts.get(kickstart_name) if kickstart_name else None
        payload["mac_address"] = normalize_host_mac(str(row.get("mac_address") or ""))
        payload["variables_json"] = host_variables_json(row.get("variables", row.get("variables_json", {})))
        db.add(EsxiPxeHost(**payload))
    db.flush()
    return len(rows)

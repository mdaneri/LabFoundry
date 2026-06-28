from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    error_code: str
    request_id: str


class IdentityResponse(BaseModel):
    username: str
    role: str
    scopes: list[str]
    auth_type: str


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=lambda: ["read:dashboard"])


class ApiTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    jti: str
    name: str
    description: str | None
    owner_user_id: int
    owner_username: str
    token_type: str
    role: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None
    enabled: bool
    signing_key_id: str | None


class ApiTokenCreated(BaseModel):
    token: ApiTokenResponse
    raw_token: str


class ServiceStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service: str
    display_name: str
    running: bool
    enabled: bool
    health: str
    detail: str | None


class FirewallSettingsUpdate(BaseModel):
    enabled: bool = False
    default_input_policy: str = "drop"
    default_forward_policy: str = "drop"
    default_output_policy: str = "accept"
    allow_established: bool = True
    allow_loopback: bool = True
    allow_icmp: bool = True
    log_dropped: bool = False


class FirewallSettingsResponse(FirewallSettingsUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    config_path: str
    updated_at: datetime


class FirewallRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    direction: str = "input"
    action: str = "accept"
    protocol: str = "tcp"
    source: str = "any"
    destination: str = "any"
    destination_port: str = ""
    interface_name: str = ""
    priority: int = 100
    enabled: bool = True
    description: str | None = None


class FirewallRuleResponse(FirewallRuleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class FirewallStatusResponse(BaseModel):
    enabled: bool
    service: ServiceStateResponse | None
    rule_count: int
    config_path: str
    dry_run: bool


class VcfBackupStatusResponse(BaseModel):
    enabled: bool
    service: ServiceStateResponse | None
    listen_interface: str
    listen_address: str
    port: int
    sftp_username: str | None
    storage_path: str
    remote_directory: str
    config_path: str
    dry_run: bool


class VcfPrivateRegistryStatusResponse(BaseModel):
    enabled: bool
    service: ServiceStateResponse | None
    hostname: str
    endpoint: str
    listen_interface: str
    listen_address: str
    port: int
    harbor_project: str
    storage_path: str
    config_path: str
    bundle_count: int
    valid: bool
    dry_run: bool


class VcfOfflineDepotStatusResponse(BaseModel):
    enabled: bool
    service: ServiceStateResponse | None
    hostname: str
    endpoint: str
    listen_interface: str
    listen_address: str
    port: int
    depot_store_path: str
    tool_archive_name: str
    tool_version: str
    software_depot_id: str
    software_depot_id_generated_at: str
    software_depot_id_error: str
    download_token_present: bool
    activation_code_present: bool
    profile_count: int
    config_path: str
    valid: bool
    dry_run: bool


class EsxiKickstartCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    content: str = Field(min_length=1)
    enabled: bool = True


class EsxiKickstartUpdate(EsxiKickstartCreate):
    pass


class EsxiKickstartResponse(BaseModel):
    id: int
    name: str
    description: str
    content_hash: str
    rendered_hash: str
    http_path: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_rendered_at: datetime | None
    last_applied_at: datetime | None
    redacted_preview: str
    drift_state: str
    content: str | None = None


class EsxiKickstartValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    redacted_preview: str


class EsxiKickstartPreviewResponse(BaseModel):
    id: int
    redacted_preview: str
    content_hash: str
    drift_state: str


class EsxiKickstartDuplicateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)


class EsxiPxeHostCreate(BaseModel):
    hostname: str = Field(min_length=1, max_length=120)
    mac_address: str = Field(min_length=1, max_length=32)
    kickstart_id: int | None = None
    installer_iso_path: str = ""
    enabled: bool = True


class EsxiPxeHostResponse(EsxiPxeHostCreate):
    id: int
    kickstart_name: str = ""
    installer_iso_name: str = ""
    created_at: datetime
    updated_at: datetime


class EsxiInstallerIsoResponse(BaseModel):
    name: str
    path: str
    relative_path: str
    size_bytes: int
    updated_at: str


class PhysicalInterfaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    mac_address: str
    driver: str | None
    speed: str | None
    host_ip_cidr: str | None
    host_mtu: int | None
    host_admin_state: str | None
    ip_cidr: str | None
    mtu: int
    admin_state: str
    oper_state: str
    role: str
    mode: str
    inventory_source: str
    desired_state_source: str
    last_seen_at: datetime | None
    missing_since: datetime | None


class VlanCreate(BaseModel):
    parent_interface: str
    vlan_id: int = Field(ge=1, le=4094)
    ip_cidr: str = Field(min_length=1)
    mtu: int = Field(default=1500, ge=576, le=9000)
    role: str = "access"
    enabled: bool = True


class VlanResponse(VlanCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class WanPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    enabled: bool = True
    latency_ms: int = Field(default=0, ge=0)
    jitter_ms: int = Field(default=0, ge=0)
    packet_loss_percent: float = Field(default=0.0, ge=0, le=100)
    bandwidth_mbit: int | None = Field(default=None, ge=1)
    corrupt_percent: float | None = Field(default=0.0, ge=0, le=100)
    duplicate_percent: float | None = Field(default=0.0, ge=0, le=100)
    reorder_percent: float | None = Field(default=0.0, ge=0, le=100)


class WanPolicyResponse(WanPolicyCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int


class RouteCreate(BaseModel):
    destination_cidr: str
    gateway: str | None = None
    interface_name: str
    metric: int = Field(default=100, ge=0)
    enabled: bool = True
    wan_policy_id: int | None = None
    wan_mode: Literal["interface"] = "interface"


class RouteResponse(RouteCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wan_policy: WanPolicyResponse | None = None


class NatRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    source: str = Field(default="any", min_length=1, max_length=240)
    outbound_interface: str = Field(min_length=1, max_length=80)
    masquerade: bool = True
    priority: int = Field(default=100, ge=0)
    description: str | None = None


class NatRuleResponse(NatRuleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int


class WanStatusResponse(BaseModel):
    active_policy_count: int
    managed_interfaces: list[str]
    dry_run: bool


class DnsConditionalForwarder(BaseModel):
    domain: str = Field(min_length=1, max_length=120)
    server: str = Field(min_length=1, max_length=120)


class DnsSettingsUpdate(BaseModel):
    enabled: bool = False
    listen_interface: str = Field(default="eth2", min_length=1, max_length=80)
    listen_address: str | None = Field(default=None, max_length=240)
    domain: str = Field(default="labfoundry.internal", min_length=1, max_length=500)
    upstream_servers: list[str] = Field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    conditional_forwarders: list[DnsConditionalForwarder] = Field(default_factory=list)
    cache_size: int = Field(default=1000, ge=0, le=100000)
    expand_hosts: bool = True
    authoritative: bool = True


class DnsSettingsResponse(DnsSettingsUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    config_path: str
    updated_at: datetime


class DnsRecordCreate(BaseModel):
    hostname: str = Field(min_length=1, max_length=120)
    record_type: str = Field(default="A", min_length=1, max_length=20)
    address: str = Field(min_length=1, max_length=120)
    description: str | None = None
    enabled: bool = True


class DnsRecordResponse(DnsRecordCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class DnsHostsImportRequest(BaseModel):
    hosts_text: str = Field(min_length=1)
    replace_existing: bool = True


class DnsHostsImportResponse(BaseModel):
    imported_count: int
    replaced_existing: bool
    errors: list[str] = Field(default_factory=list)
    records: list[DnsRecordResponse]


class DhcpSettingsUpdate(BaseModel):
    enabled: bool = False
    interface_name: str = Field(default="eth2", min_length=1, max_length=80)
    site_address: str = Field(default="192.168.50.1", min_length=1, max_length=64)
    prefix_length: int = Field(default=24, ge=1, le=32)
    range_start: str = Field(default="192.168.50.100", min_length=1, max_length=64)
    range_end: str = Field(default="192.168.50.200", min_length=1, max_length=64)
    lease_time: str = Field(default="12h", min_length=1, max_length=40)
    domain_name: str = Field(default="labfoundry.internal", min_length=1, max_length=120)
    dns_server: str = Field(default="192.168.50.1", min_length=1, max_length=64)
    authoritative: bool = True


class DhcpSettingsResponse(DhcpSettingsUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    config_path: str
    updated_at: datetime


class DhcpScopeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    interface_name: str
    site_address: str
    prefix_length: int
    range_start: str
    range_end: str
    lease_time: str
    domain_name: str
    dns_server: str
    ntp_server: str = ""
    enabled: bool
    description: str | None = None


class DhcpScopeResponse(DhcpScopeCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class DhcpOptionCreate(BaseModel):
    scope_id: int | None = None
    option_code: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=240)
    description: str | None = None
    enabled: bool = True


class DhcpOptionResponse(DhcpOptionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class DhcpReservationCreate(BaseModel):
    hostname: str = Field(min_length=1, max_length=120)
    mac_address: str = Field(min_length=1, max_length=32)
    ip_address: str = Field(min_length=1, max_length=64)
    description: str | None = None
    enabled: bool = True


class DhcpReservationResponse(DhcpReservationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class DhcpLeaseResponse(BaseModel):
    expires_at: datetime | None
    mac_address: str
    ip_address: str
    hostname: str
    client_id: str
    status: str


class ConfigValidationResponse(BaseModel):
    valid: bool
    dry_run: bool
    command: list[str]
    config_path: str
    config_preview: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConfigApplyResponse(ConfigValidationResponse):
    reloaded: bool = False


class DnsStatusResponse(BaseModel):
    enabled: bool
    service: ServiceStateResponse | None
    listen_interface: str
    listen_address: str | None
    domain: str
    record_count: int
    config_path: str
    dry_run: bool


class DhcpStatusResponse(BaseModel):
    enabled: bool
    service: ServiceStateResponse | None
    interface_name: str
    range_start: str
    range_end: str
    reservation_count: int
    config_path: str
    dry_run: bool


class DashboardResponse(BaseModel):
    appliance: dict[str, Any]
    service_health: list[ServiceStateResponse]
    interfaces: list[PhysicalInterfaceResponse]
    active_wan_policies: list[WanPolicyResponse]
    disk_usage: dict[str, Any]
    recent_audit_events: list[dict[str, Any]]


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    actor: str
    action: str
    resource_type: str
    resource_id: str | None
    success: bool
    detail: str | None
    request_id: str | None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    status: str
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress_percent: int
    result: str | None
    error: str | None


class ServiceActionResponse(BaseModel):
    service: str
    action: str
    dry_run: bool
    command: list[str]


class SettingsResponse(BaseModel):
    app_name: str
    appliance_hostname: str
    dry_run_system_adapters: bool
    repository_path: str
    vcf_backup_path: str
    appliance_fqdn: str
    management_https_enabled: bool = False
    management_https_cert_available: bool = False
    root_ssh_enabled: bool = False
    external_dns_servers: list[str]
    ntp_servers: list[str]
    appliance_settings_config_path: str
    local_dns_enabled: bool
    management_interface: str
    management_ip: str
    valid: bool
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    config_preview: str


class SettingsUpdate(BaseModel):
    appliance_fqdn: str = Field(default="labfoundry.labfoundry.internal", min_length=1, max_length=180)
    management_https_enabled: bool = False
    root_ssh_enabled: bool = False
    external_dns_servers: list[str] = Field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    ntp_servers: list[str] = Field(
        default_factory=lambda: ["time1.google.com", "time2.google.com", "time3.google.com", "time4.google.com"]
    )

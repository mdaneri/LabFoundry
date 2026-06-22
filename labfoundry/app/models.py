from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from labfoundry.app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(StrEnum):
    ADMIN = "admin"
    NETWORK_ADMIN = "network-admin"
    SERVICE_ADMIN = "service-admin"
    VIEWER = "viewer"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(50), default=Role.ADMIN.value)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tokens: Mapped[list["ApiToken"]] = relationship(back_populates="owner")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    owner_username: Mapped[str] = mapped_column(String(100))
    token_type: Mapped[str] = mapped_column(String(20), default="bearer")
    role: Mapped[str] = mapped_column(String(50))
    scopes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signing_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    owner: Mapped[User] = relationship(back_populates="tokens")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PhysicalInterface(Base):
    __tablename__ = "physical_interfaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    mac_address: Mapped[str] = mapped_column(String(32))
    driver: Mapped[str | None] = mapped_column(String(80), nullable=True)
    speed: Mapped[str | None] = mapped_column(String(50), nullable=True)
    host_ip_cidr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    host_mtu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host_admin_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ip_cidr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mtu: Mapped[int] = mapped_column(Integer, default=1500)
    admin_state: Mapped[str] = mapped_column(String(20), default="up")
    oper_state: Mapped[str] = mapped_column(String(20), default="up")
    role: Mapped[str] = mapped_column(String(40), default="unused")
    mode: Mapped[str] = mapped_column(String(40), default="unused")
    inventory_source: Mapped[str] = mapped_column(String(40), default="seed")
    desired_state_source: Mapped[str] = mapped_column(String(40), default="seed")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VlanInterface(Base):
    __tablename__ = "vlan_interfaces"
    __table_args__ = (UniqueConstraint("parent_interface", "vlan_id", name="uq_vlan_parent_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    parent_interface: Mapped[str] = mapped_column(String(50), index=True)
    vlan_id: Mapped[int] = mapped_column(Integer)
    ip_cidr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mtu: Mapped[int] = mapped_column(Integer, default=1500)
    role: Mapped[str] = mapped_column(String(40), default="access")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class WanPolicy(Base):
    __tablename__ = "wan_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    jitter_ms: Mapped[int] = mapped_column(Integer, default=0)
    packet_loss_percent: Mapped[float] = mapped_column(default=0.0)
    bandwidth_mbit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corrupt_percent: Mapped[float | None] = mapped_column(default=0.0, nullable=True)
    duplicate_percent: Mapped[float | None] = mapped_column(default=0.0, nullable=True)
    reorder_percent: Mapped[float | None] = mapped_column(default=0.0, nullable=True)

    routes: Mapped[list["Route"]] = relationship(back_populates="wan_policy")


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destination_cidr: Mapped[str] = mapped_column(String(64), index=True)
    gateway: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interface_name: Mapped[str] = mapped_column(String(80), index=True)
    metric: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    wan_policy_id: Mapped[int | None] = mapped_column(ForeignKey("wan_policies.id"), nullable=True)
    wan_mode: Mapped[str] = mapped_column(String(40), default="interface")

    wan_policy: Mapped[WanPolicy | None] = relationship(back_populates="routes")


class ServiceState(Base):
    __tablename__ = "service_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    health: Mapped[str] = mapped_column(String(40), default="unknown")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApplianceSettings(Base):
    __tablename__ = "appliance_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fqdn: Mapped[str] = mapped_column(String(180), default="labfoundry.labfoundry.internal")
    external_dns_servers: Mapped[str] = mapped_column(Text, default="1.1.1.1\n9.9.9.9")
    ntp_servers: Mapped[str] = mapped_column(Text, default="time1.google.com\ntime2.google.com\ntime3.google.com\ntime4.google.com")
    config_path: Mapped[str] = mapped_column(String(240), default="/var/lib/labfoundry/apply/appliance-settings/labfoundry-settings.json")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FirewallSettings(Base):
    __tablename__ = "firewall_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    default_input_policy: Mapped[str] = mapped_column(String(20), default="drop")
    default_forward_policy: Mapped[str] = mapped_column(String(20), default="drop")
    default_output_policy: Mapped[str] = mapped_column(String(20), default="accept")
    allow_established: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_loopback: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_icmp: Mapped[bool] = mapped_column(Boolean, default=True)
    log_dropped: Mapped[bool] = mapped_column(Boolean, default=False)
    config_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/nftables.d/labfoundry.nft")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FirewallRule(Base):
    __tablename__ = "firewall_rules"
    __table_args__ = (UniqueConstraint("name", name="uq_firewall_rule_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(20), default="input")
    action: Mapped[str] = mapped_column(String(20), default="accept")
    protocol: Mapped[str] = mapped_column(String(20), default="tcp")
    source: Mapped[str] = mapped_column(String(120), default="any")
    destination: Mapped[str] = mapped_column(String(120), default="any")
    destination_port: Mapped[str] = mapped_column(String(120), default="")
    interface_name: Mapped[str] = mapped_column(String(80), default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DnsSettings(Base):
    __tablename__ = "dns_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    listen_interface: Mapped[str] = mapped_column(String(80), default="eth2")
    listen_address: Mapped[str | None] = mapped_column(String(240), nullable=True)
    domain: Mapped[str] = mapped_column(String(500), default="labfoundry.internal")
    upstream_servers: Mapped[str] = mapped_column(Text, default="1.1.1.1\n9.9.9.9")
    cache_size: Mapped[int] = mapped_column(Integer, default=1000)
    expand_hosts: Mapped[bool] = mapped_column(Boolean, default=True)
    authoritative: Mapped[bool] = mapped_column(Boolean, default=True)
    config_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/dnsmasq.d/labfoundry.conf")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DnsRecord(Base):
    __tablename__ = "dns_records"
    __table_args__ = (UniqueConstraint("hostname", "record_type", name="uq_dns_record_hostname_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(120), index=True)
    record_type: Mapped[str] = mapped_column(String(20), default="A")
    address: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DhcpSettings(Base):
    __tablename__ = "dhcp_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interface_name: Mapped[str] = mapped_column(String(80), default="eth1")
    site_address: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    prefix_length: Mapped[int] = mapped_column(Integer, default=24)
    range_start: Mapped[str] = mapped_column(String(64), default="192.168.50.100")
    range_end: Mapped[str] = mapped_column(String(64), default="192.168.50.200")
    lease_time: Mapped[str] = mapped_column(String(40), default="12h")
    domain_name: Mapped[str] = mapped_column(String(120), default="labfoundry.internal")
    dns_server: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    authoritative: Mapped[bool] = mapped_column(Boolean, default=True)
    config_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/dnsmasq.d/labfoundry.conf")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DhcpScope(Base):
    __tablename__ = "dhcp_scopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    interface_name: Mapped[str] = mapped_column(String(80), default="eth1")
    site_address: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    prefix_length: Mapped[int] = mapped_column(Integer, default=24)
    range_start: Mapped[str] = mapped_column(String(64), default="192.168.50.100")
    range_end: Mapped[str] = mapped_column(String(64), default="192.168.50.200")
    lease_time: Mapped[str] = mapped_column(String(40), default="12h")
    domain_name: Mapped[str] = mapped_column(String(120), default="labfoundry.internal")
    dns_server: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    ntp_server: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DhcpOption(Base):
    __tablename__ = "dhcp_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_id: Mapped[int | None] = mapped_column(ForeignKey("dhcp_scopes.id"), nullable=True, index=True)
    option_code: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DhcpReservation(Base):
    __tablename__ = "dhcp_reservations"
    __table_args__ = (UniqueConstraint("mac_address", name="uq_dhcp_reservation_mac"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(120))
    mac_address: Mapped[str] = mapped_column(String(32), index=True)
    ip_address: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaSettings(Base):
    __tablename__ = "ca_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    root_common_name: Mapped[str] = mapped_column(String(180), default="LabFoundry Internal Root CA")
    organization: Mapped[str] = mapped_column(String(180), default="LabFoundry")
    organizational_unit: Mapped[str] = mapped_column(String(180), default="Lab Infrastructure")
    country: Mapped[str] = mapped_column(String(2), default="US")
    state: Mapped[str] = mapped_column(String(120), default="")
    locality: Mapped[str] = mapped_column(String(120), default="")
    key_algorithm: Mapped[str] = mapped_column(String(20), default="RSA")
    key_size: Mapped[int] = mapped_column(Integer, default=4096)
    digest_algorithm: Mapped[str] = mapped_column(String(40), default="sha256")
    root_valid_days: Mapped[int] = mapped_column(Integer, default=3650)
    intermediate_valid_days: Mapped[int] = mapped_column(Integer, default=1825)
    publish_crl: Mapped[bool] = mapped_column(Boolean, default=True)
    ocsp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/ca")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaProfile(Base):
    __tablename__ = "ca_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    certificate_type: Mapped[str] = mapped_column(String(40), default="server")
    validity_days: Mapped[int] = mapped_column(Integer, default=825)
    key_algorithm: Mapped[str] = mapped_column(String(20), default="RSA")
    key_size: Mapped[int] = mapped_column(Integer, default=2048)
    key_usage: Mapped[str] = mapped_column(String(240), default="digitalSignature,keyEncipherment")
    extended_key_usage: Mapped[str] = mapped_column(String(240), default="serverAuth")
    san_required: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    certificates: Mapped[list["CaCertificate"]] = relationship(back_populates="profile")


class CaCertificate(Base):
    __tablename__ = "ca_certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    common_name: Mapped[str] = mapped_column(String(180), index=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("ca_profiles.id"), nullable=True, index=True)
    subject_alt_names: Mapped[str] = mapped_column(Text, default="")
    ip_addresses: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="planned")
    serial_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    csr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    profile: Mapped[CaProfile | None] = relationship(back_populates="certificates")


class KmsSettings(Base):
    __tablename__ = "kms_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    backend: Mapped[str] = mapped_column(String(40), default="pykmip")
    listen_interface: Mapped[str] = mapped_column(String(80), default="eth1")
    listen_address: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    port: Mapped[int] = mapped_column(Integer, default=5696)
    hostname: Mapped[str] = mapped_column(String(180), default="kms.labfoundry.internal")
    server_certificate: Mapped[str] = mapped_column(String(180), default="kms.labfoundry.internal")
    ca_certificate_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/ca/root.crt")
    database_path: Mapped[str] = mapped_column(String(240), default="/var/lib/labfoundry/kms/pykmip.db")
    config_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/kms/pykmip.conf")
    require_client_cert: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_register: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_destroy: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KmsClient(Base):
    __tablename__ = "kms_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    certificate_subject: Mapped[str] = mapped_column(String(240))
    role: Mapped[str] = mapped_column(String(40), default="service")
    allowed_operations: Mapped[str] = mapped_column(Text, default="locate,get,register,create")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    keys: Mapped[list["KmsKey"]] = relationship(back_populates="owner_client")


class KmsKey(Base):
    __tablename__ = "kms_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    algorithm: Mapped[str] = mapped_column(String(40), default="AES")
    length: Mapped[int] = mapped_column(Integer, default=256)
    usage: Mapped[str] = mapped_column(String(240), default="encrypt,decrypt")
    state: Mapped[str] = mapped_column(String(40), default="active")
    owner_client_id: Mapped[int | None] = mapped_column(ForeignKey("kms_clients.id"), nullable=True, index=True)
    exportable: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner_client: Mapped[KmsClient | None] = relationship(back_populates="keys")


class VcfBackupSettings(Base):
    __tablename__ = "vcf_backup_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    listen_interface: Mapped[str] = mapped_column(String(80), default="eth1")
    listen_address: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    port: Mapped[int] = mapped_column(Integer, default=22)
    sftp_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(240), default="/mnt/labfoundry-vcf-backups")
    chroot_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_password_auth: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_public_key_auth: Mapped[bool] = mapped_column(Boolean, default=True)
    max_sessions: Mapped[int] = mapped_column(Integer, default=4)
    config_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/ssh/sshd_config.d/labfoundry-vcf-backups.conf")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sftp_user: Mapped[User | None] = relationship()


class VcfPrivateRegistrySettings(Base):
    __tablename__ = "vcf_private_registry_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    hostname: Mapped[str] = mapped_column(String(180), default="registry.labfoundry.internal")
    listen_interface: Mapped[str] = mapped_column(String(80), default="eth2")
    listen_address: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    port: Mapped[int] = mapped_column(Integer, default=443)
    harbor_project: Mapped[str] = mapped_column(String(120), default="vcf-supervisor-services")
    storage_path: Mapped[str] = mapped_column(String(240), default="/mnt/labfoundry-vcf-registry")
    config_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/harbor/harbor.yml")
    ca_bundle_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/ca/ca-bundle.pem")
    server_certificate: Mapped[str] = mapped_column(String(180), default="registry.labfoundry.internal")
    robot_account: Mapped[str] = mapped_column(String(120), default="robot$vcf-supervisor-services")
    relocation_dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VcfOfflineDepotSettings(Base):
    __tablename__ = "vcf_offline_depot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    hostname: Mapped[str] = mapped_column(String(180), default="depot.labfoundry.internal")
    listen_interface: Mapped[str] = mapped_column(String(80), default="eth2")
    listen_address: Mapped[str] = mapped_column(String(64), default="192.168.50.1")
    port: Mapped[int] = mapped_column(Integer, default=443)
    server_certificate: Mapped[str] = mapped_column(String(180), default="depot.labfoundry.internal")
    depot_store_path: Mapped[str] = mapped_column(String(240), default="/mnt/labfoundry-vcf-offline-depot")
    tool_archive_path: Mapped[str] = mapped_column(String(500), default="")
    tool_version: Mapped[str] = mapped_column(String(80), default="")
    telemetry_choice: Mapped[str] = mapped_column(String(20), default="DISABLE")
    config_path: Mapped[str] = mapped_column(String(240), default="/etc/labfoundry/nginx/sites.d/vcf-offline-depot.conf")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VcfDepotDownloadProfile(Base):
    __tablename__ = "vcf_depot_download_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_vcf_depot_download_profile_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    profile_type: Mapped[str] = mapped_column(String(40), default="binaries")
    sku: Mapped[str] = mapped_column(String(20), default="VCF")
    vcf_version: Mapped[str] = mapped_column(String(40), default="9.1.0")
    binary_type: Mapped[str] = mapped_column(String(20), default="INSTALL")
    automated_install: Mapped[bool] = mapped_column(Boolean, default=True)
    upgrades_only: Mapped[bool] = mapped_column(Boolean, default=False)
    component: Mapped[str] = mapped_column(String(80), default="")
    component_version: Mapped[str] = mapped_column(String(80), default="")
    disabled_platforms: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(40), default="planned")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VcfRegistryBundle(Base):
    __tablename__ = "vcf_registry_bundles"
    __table_args__ = (UniqueConstraint("name", name="uq_vcf_registry_bundle_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    source_reference: Mapped[str] = mapped_column(String(500), default="")
    target_reference: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(40), default="planned")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default=JobStatus.PENDING.value)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

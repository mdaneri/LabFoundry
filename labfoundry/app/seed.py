from sqlalchemy import select, text
from sqlalchemy.orm import Session

from labfoundry.app.config import get_settings
from labfoundry.app.models import (
    ApplianceSettings,
    CaCertificate,
    CaProfile,
    CaSettings,
    DhcpReservation,
    DhcpScope,
    DhcpSettings,
    DnsRecord,
    DnsSettings,
    FirewallRule,
    FirewallSettings,
    KmsClient,
    KmsKey,
    KmsSettings,
    PhysicalInterface,
    Route,
    ServiceState,
    Setting,
    User,
    VcfBackupSettings,
    VcfDepotDownloadProfile,
    VcfOfflineDepotSettings,
    VcfPrivateRegistrySettings,
    VlanInterface,
    WanPolicy,
)
from labfoundry.app.services.local_users import stage_user_os_password
from labfoundry.app.services.networking import normalize_interface_mode
from labfoundry.app.services.vcf_backups import VCF_BACKUP_DEFAULT_USERNAME


VCF_BACKUP_USERNAME = VCF_BACKUP_DEFAULT_USERNAME
SEED_EXAMPLES_SETTING_KEY = "seed.include_examples"


SERVICE_STATE_DEFAULTS = [
    {"service": "routing", "display_name": "Routing", "running": True, "enabled": True, "health": "healthy"},
    {"service": "firewall", "display_name": "Firewall", "running": True, "enabled": True, "health": "healthy"},
    {"service": "dns", "display_name": "DNS", "running": False, "enabled": False, "health": "disabled"},
    {"service": "dhcp", "display_name": "DHCP", "running": False, "enabled": False, "health": "disabled"},
    {
        "service": "kms",
        "display_name": "KMS / KMIP",
        "running": False,
        "enabled": False,
        "health": "planned",
        "detail": "PyKMIP lab backend",
    },
    {
        "service": "repository",
        "display_name": "VCF Offline Depot",
        "running": False,
        "enabled": False,
        "health": "planned",
        "detail": "/mnt/labfoundry-vcf-offline-depot",
    },
    {
        "service": "vcf-private-registry",
        "display_name": "VCF Private Registry",
        "running": False,
        "enabled": False,
        "health": "planned",
        "detail": "Harbor / vcf-supervisor-services",
    },
    {
        "service": "vcf-backups",
        "display_name": "VCF Backup SFTP",
        "running": True,
        "enabled": True,
        "health": "healthy",
        "detail": "/mnt/labfoundry-vcf-backups",
    },
    {"service": "ca", "display_name": "Certificate Authority", "running": False, "enabled": False, "health": "planned"},
    {"service": "ldap", "display_name": "LDAP", "running": False, "enabled": False, "health": "planned"},
    {"service": "auth", "display_name": "Authentication", "running": True, "enabled": True, "health": "healthy"},
]


def seed_initial_data(db: Session, *, include_examples: bool = True) -> None:
    if include_examples:
        seed_examples_setting = db.execute(select(Setting).where(Setting.key == SEED_EXAMPLES_SETTING_KEY)).scalar_one_or_none()
        if seed_examples_setting is not None and seed_examples_setting.value.strip().lower() in {"0", "false", "no"}:
            include_examples = False
    settings = get_settings()
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        columns = {row[1] for row in db.execute(text("PRAGMA table_info(users)")).all()}
        if {"pending_os_password_encrypted", "os_password_pending_at"}.issubset(columns):
            db.execute(
                text(
                    "UPDATE users SET pending_os_password_encrypted = NULL, os_password_pending_at = NULL "
                    "WHERE pending_os_password_encrypted IS NOT NULL"
                )
            )
    bootstrap_user = db.execute(select(User).where(User.username == settings.bootstrap_admin_username)).scalar_one_or_none()
    if db.execute(select(User)).first() is None:
        bootstrap_user = User(
            username=settings.bootstrap_admin_username,
            role="admin",
        )
        stage_user_os_password(bootstrap_user, settings.bootstrap_admin_password)
        db.add(bootstrap_user)
        db.flush()
    vcf_backup_user = db.execute(select(User).where(User.username == VCF_BACKUP_USERNAME)).scalar_one_or_none()
    if vcf_backup_user is None:
        vcf_backup_user = User(
            username=VCF_BACKUP_USERNAME,
            role="viewer",
            enabled=False,
        )
        db.add(vcf_backup_user)
        db.flush()

    if db.execute(select(PhysicalInterface)).first() is None:
        db.add_all(
            [
                PhysicalInterface(
                    name="eth0",
                    mac_address="02:15:5d:00:10:01",
                    driver="hv_netvsc",
                    speed="10 Gbps",
                    host_ip_cidr="192.168.49.1/24",
                    host_mtu=1500,
                    host_admin_state="up",
                    ip_cidr="192.168.49.1/24",
                    mtu=1500,
                    role="management",
                    mode="access",
                    inventory_source="seed",
                    desired_state_source="seed",
                ),
                PhysicalInterface(
                    name="eth1",
                    mac_address="02:15:5d:00:10:02",
                    driver="hv_netvsc",
                    speed="10 Gbps",
                    host_mtu=1500,
                    host_admin_state="up" if include_examples else "down",
                    mtu=1500,
                    admin_state="up" if include_examples else "down",
                    role="access",
                    mode="trunk" if include_examples else "access",
                    inventory_source="seed",
                    desired_state_source="seed",
                ),
                PhysicalInterface(
                    name="eth2",
                    mac_address="02:15:5d:00:10:03",
                    driver="hv_netvsc",
                    speed="10 Gbps",
                    host_ip_cidr="192.168.50.1/24" if include_examples else None,
                    host_mtu=1500,
                    host_admin_state="up" if include_examples else "down",
                    ip_cidr="192.168.50.1/24" if include_examples else None,
                    mtu=1500,
                    admin_state="up" if include_examples else "down",
                    role="access",
                    mode="access",
                    inventory_source="seed",
                    desired_state_source="seed",
                ),
            ]
        )
        db.flush()

    eth1_parent = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == "eth1")).scalar_one_or_none()
    seed_sample_vlan = include_examples and eth1_parent is not None and normalize_interface_mode(eth1_parent.mode) == "trunk"
    if seed_sample_vlan and db.execute(select(VlanInterface).where(VlanInterface.name == "eth1.20")).scalar_one_or_none() is None:
        db.add(
            VlanInterface(
                name="eth1.20",
                parent_interface="eth1",
                vlan_id=20,
                ip_cidr="192.168.20.1/24",
                mtu=1500,
                role="wan",
                enabled=True,
            )
        )
        db.flush()

    if include_examples and db.execute(select(WanPolicy)).first() is None:
        policy = WanPolicy(
            name="Europe WAN",
            description="Training-lab WAN profile for transatlantic latency.",
            latency_ms=150,
            jitter_ms=20,
            packet_loss_percent=0.5,
            bandwidth_mbit=100,
            corrupt_percent=0.01,
            duplicate_percent=0.0,
            reorder_percent=0.0,
        )
        db.add(policy)
        db.flush()
        if seed_sample_vlan and db.execute(select(VlanInterface).where(VlanInterface.name == "eth1.20")).scalar_one_or_none() is not None:
            db.add(
                Route(
                    destination_cidr="192.168.20.0/24",
                    gateway=None,
                    interface_name="eth1.20",
                    metric=100,
                    wan_policy_id=policy.id,
                )
            )

    existing_services = {row.service for row in db.execute(select(ServiceState)).scalars().all()}
    for service_state in SERVICE_STATE_DEFAULTS:
        existing_service = db.execute(select(ServiceState).where(ServiceState.service == service_state["service"])).scalar_one_or_none()
        if existing_service is None:
            db.add(ServiceState(**service_state))
        elif service_state["service"] == "repository":
            existing_service.display_name = service_state["display_name"]
            existing_service.detail = service_state["detail"]
            if existing_service.health == "healthy":
                existing_service.health = service_state["health"]
            existing_service.enabled = service_state["enabled"]
            existing_service.running = service_state["running"]

    if db.execute(select(ApplianceSettings)).first() is None:
        db.add(ApplianceSettings())

    if db.execute(select(DnsSettings)).first() is None:
        db.add(
            DnsSettings(
                enabled=False,
                listen_interface="eth2" if include_examples else "",
                listen_address="192.168.50.1" if include_examples else "",
                domain="labfoundry.internal",
                upstream_servers="1.1.1.1\n9.9.9.9",
            )
        )

    if include_examples and db.execute(select(DnsRecord)).first() is None:
        db.add(
            DnsRecord(
                hostname="labfoundry.labfoundry.internal",
                record_type="A",
                address="192.168.50.1",
                description="LabFoundry app-owned appliance FQDN record.",
            )
        )

    if db.execute(select(DhcpSettings)).first() is None:
        db.add(
            DhcpSettings(
                enabled=False,
                interface_name="eth2" if include_examples else "",
                site_address="192.168.50.1" if include_examples else "",
                prefix_length=24,
                range_start="192.168.50.100" if include_examples else "",
                range_end="192.168.50.200" if include_examples else "",
                lease_time="12h",
                domain_name="labfoundry.internal",
                dns_server="192.168.50.1" if include_examples else "",
            )
        )

    if include_examples and db.execute(select(DhcpScope)).first() is None:
        db.add(
            DhcpScope(
                name="SiteA",
                interface_name="eth2",
                site_address="192.168.50.1",
                prefix_length=24,
                range_start="192.168.50.100",
                range_end="192.168.50.200",
                lease_time="12h",
                domain_name="labfoundry.internal",
                dns_server="192.168.50.1",
                ntp_server="192.168.50.1",
                enabled=True,
                description="Default SiteA DHCP IP zone.",
            )
        )

    if include_examples and db.execute(select(DhcpReservation)).first() is None:
        db.add(
            DhcpReservation(
                hostname="test-client",
                mac_address="02:15:5d:00:20:10",
                ip_address="192.168.50.120",
                description="Sample SiteA reservation for smoke tests.",
                enabled=False,
            )
        )

    if db.execute(select(FirewallSettings)).first() is None:
        db.add(FirewallSettings(enabled=True, default_input_policy="drop", default_forward_policy="drop", default_output_policy="accept"))

    if include_examples and db.execute(select(FirewallRule)).first() is None:
        db.add_all(
            [
                FirewallRule(
                    name="mgmt-console",
                    direction="input",
                    action="accept",
                    protocol="tcp",
                    source="192.168.49.0/24",
                    destination="any",
                    destination_port="22,443,8000",
                    interface_name="eth0",
                    priority=10,
                    description="Allow management access to SSH, HTTPS, and the development console.",
                ),
                FirewallRule(
                    name="sitea-dns-dhcp",
                    direction="input",
                    action="accept",
                    protocol="udp",
                    source="192.168.50.0/24",
                    destination="any",
                    destination_port="53,67",
                    interface_name="eth2",
                    priority=20,
                    description="Allow SiteA clients to reach LabFoundry DNS and DHCP.",
                ),
            ]
        )

    if db.execute(select(CaSettings)).first() is None:
        db.add(
            CaSettings(
                enabled=False,
                root_common_name="LabFoundry Internal Root CA",
                organization="LabFoundry",
                organizational_unit="Lab Infrastructure",
                country="US",
                storage_path="/etc/labfoundry/ca",
            )
        )

    if include_examples and db.execute(select(CaProfile)).first() is None:
        server_profile = CaProfile(
            name="VCF service TLS",
            certificate_type="server",
            validity_days=825,
            key_algorithm="RSA",
            key_size=2048,
            key_usage="digitalSignature,keyEncipherment",
            extended_key_usage="serverAuth",
            san_required=True,
            description="Default profile for VCF lab services and appliance endpoints.",
        )
        db.add(server_profile)
        db.flush()
        db.add(
            CaCertificate(
                common_name="labfoundry.labfoundry.internal",
                profile_id=server_profile.id,
                subject_alt_names="labfoundry.labfoundry.internal\nlabfoundry.internal",
                ip_addresses="192.168.50.1",
                description="Sample appliance console certificate request.",
                enabled=True,
            )
        )

    if db.execute(select(KmsSettings)).first() is None:
        db.add(
            KmsSettings(
                enabled=False,
                backend="pykmip",
                listen_interface="eth1" if include_examples else "",
                listen_address="192.168.50.1" if include_examples else "",
                port=5696,
                hostname="kms.labfoundry.internal",
                server_certificate="kms.labfoundry.internal",
                ca_certificate_path="/etc/labfoundry/ca/root.crt",
                database_path="/var/lib/labfoundry/kms/pykmip.db",
                config_path="/etc/labfoundry/kms/pykmip.conf",
                require_client_cert=True,
                allow_register=True,
                allow_destroy=False,
            )
        )

    if include_examples and db.execute(select(KmsClient)).first() is None:
        client = KmsClient(
            name="vcf-management",
            certificate_subject="CN=vcf-management.labfoundry.internal,O=LabFoundry",
            role="service",
            allowed_operations="locate,get,register,create,activate",
            description="Sample VCF management KMIP client.",
            enabled=True,
        )
        db.add(client)
        db.flush()
        db.add(
            KmsKey(
                name="vcf-sddc-manager-aes",
                algorithm="AES",
                length=256,
                usage="encrypt,decrypt",
                state="active",
                owner_client_id=client.id,
                exportable=False,
                description="Sample AES key desired state for VCF lab encryption.",
                enabled=True,
            )
        )
    if db.execute(select(VcfBackupSettings)).first() is None:
        db.add(
            VcfBackupSettings(
                enabled=False,
                listen_interface="eth2" if include_examples else "",
                listen_address="192.168.50.1" if include_examples else "",
                port=22,
                sftp_user_id=vcf_backup_user.id if vcf_backup_user else None,
                storage_path="/mnt/labfoundry-vcf-backups",
                chroot_enabled=True,
                allow_password_auth=True,
                allow_public_key_auth=True,
                max_sessions=4,
            )
        )
    if db.execute(select(VcfPrivateRegistrySettings)).first() is None:
        db.add(VcfPrivateRegistrySettings())
    if db.execute(select(VcfOfflineDepotSettings)).first() is None:
        db.add(VcfOfflineDepotSettings())
    if include_examples and db.execute(select(VcfDepotDownloadProfile)).first() is None:
        db.add_all(
            [
                VcfDepotDownloadProfile(
                    name="VCF 9.1 install binaries",
                    profile_type="binaries",
                    sku="VCF",
                    vcf_version="9.1.0",
                    binary_type="INSTALL",
                    automated_install=True,
                    enabled=True,
                    status="planned",
                ),
                VcfDepotDownloadProfile(
                    name="VCF metadata",
                    profile_type="metadata",
                    enabled=False,
                    status="planned",
                ),
                VcfDepotDownloadProfile(
                    name="ESX patches",
                    profile_type="esx",
                    enabled=False,
                    status="planned",
                    disabled_platforms="esxio-9.1-INTL\narmEsx-9.1-INTL",
                ),
            ]
        )
    db.commit()

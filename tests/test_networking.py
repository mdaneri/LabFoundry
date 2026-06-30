import json
import logging

from labfoundry.app.models import AuditEvent, CaSettings, DhcpScope, DhcpSettings, DnsSettings, Job, KmsSettings, NatRule, PhysicalInterface, Route, Setting, VlanInterface
from labfoundry.app.services.networking import (
    HostPhysicalInterface,
    NETWORK_INVENTORY_CLEANUP_WARNING_KEY,
    parse_linux_ip_interfaces,
    reconcile_host_physical_interfaces,
    render_network_config,
    sync_host_physical_interfaces,
)


def test_parse_linux_ip_interfaces_skips_loopback_and_vlans():
    payload = json.dumps(
        [
            {"ifname": "lo", "link_type": "loopback", "address": "00:00:00:00:00:00"},
            {
                "ifname": "eth0",
                "link_type": "ether",
                "address": "00:15:5d:aa:bb:01",
                "mtu": 1500,
                "operstate": "UP",
                "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
                "addr_info": [{"family": "inet", "local": "192.168.49.22", "prefixlen": 24, "scope": "global"}],
            },
            {
                "ifname": "eth0.20",
                "link_type": "ether",
                "linkinfo": {"info_kind": "vlan"},
                "address": "00:15:5d:aa:bb:01",
                "mtu": 1500,
                "operstate": "UP",
                "flags": ["UP"],
                "addr_info": [{"family": "inet", "local": "192.168.20.1", "prefixlen": 24, "scope": "global"}],
            },
        ]
    )

    interfaces = parse_linux_ip_interfaces(payload)

    assert [interface.name for interface in interfaces] == ["eth0"]
    assert interfaces[0].host_ip_cidr == "192.168.49.22/24"
    assert interfaces[0].host_mtu == 1500
    assert interfaces[0].host_admin_state == "up"


def test_reconcile_host_inventory_replaces_seed_but_preserves_user_desired_state():
    seed = PhysicalInterface(
        name="eth0",
        mac_address="old",
        ip_cidr="192.168.49.1/24",
        mtu=1500,
        admin_state="up",
        role="management",
        mode="access",
        inventory_source="seed",
        desired_state_source="seed",
    )
    user_owned = PhysicalInterface(
        name="eth1",
        mac_address="00:15:5d:aa:bb:02",
        ip_cidr="192.168.50.1/24",
        mtu=9000,
        admin_state="down",
        role="access",
        mode="access",
        inventory_source="host",
        desired_state_source="user",
    )

    reconciled = reconcile_host_physical_interfaces(
        [seed, user_owned],
        [
            HostPhysicalInterface(
                name="eth0",
                mac_address="00:15:5d:aa:bb:01",
                driver="hv_netvsc",
                speed="10000 Mbps",
                host_ip_cidr="192.168.49.22/24",
                host_mtu=1500,
                host_admin_state="up",
                oper_state="up",
            ),
            HostPhysicalInterface(
                name="eth1",
                mac_address="00:15:5d:aa:bb:02",
                driver="hv_netvsc",
                speed="10000 Mbps",
                host_ip_cidr="",
                host_mtu=1500,
                host_admin_state="up",
                oper_state="up",
            ),
        ],
    )

    by_name = {interface.name: interface for interface in reconciled}
    assert by_name["eth0"].inventory_source == "host"
    assert by_name["eth0"].ip_cidr == "192.168.49.22/24"
    assert by_name["eth0"].host_ip_cidr == "192.168.49.22/24"
    assert by_name["eth1"].ip_cidr == "192.168.50.1/24"
    assert by_name["eth1"].mtu == 9000
    assert by_name["eth1"].admin_state == "down"
    assert by_name["eth1"].host_mtu == 1500


def test_reconcile_host_inventory_keeps_seed_non_management_down():
    reconciled = reconcile_host_physical_interfaces(
        [],
        [
            HostPhysicalInterface(
                name="eth1",
                mac_address="00:15:5d:aa:bb:02",
                driver="hv_netvsc",
                speed="10000 Mbps",
                host_ip_cidr="192.168.50.22/24",
                host_mtu=1500,
                host_admin_state="up",
                oper_state="up",
            )
        ],
    )

    assert len(reconciled) == 1
    assert reconciled[0].name == "eth1"
    assert reconciled[0].host_ip_cidr == "192.168.50.22/24"
    assert reconciled[0].ip_cidr is None
    assert reconciled[0].admin_state == "down"


def test_reconcile_host_inventory_tracks_renumbered_nics_by_mac():
    removed = PhysicalInterface(
        name="eth1",
        mac_address="00:15:5d:01:1d:14",
        ip_cidr=None,
        mtu=1500,
        admin_state="up",
        role="access",
        mode="trunk",
        inventory_source="host",
        desired_state_source="user",
    )
    survivor = PhysicalInterface(
        name="eth2",
        mac_address="00:15:5d:01:1d:15",
        ip_cidr="192.168.20.1/24",
        mtu=1500,
        admin_state="up",
        role="access",
        mode="access",
        inventory_source="host",
        desired_state_source="user",
    )
    renames: dict[str, str] = {}

    reconciled = reconcile_host_physical_interfaces(
        [removed, survivor],
        [
            HostPhysicalInterface(
                name="eth1",
                mac_address="00:15:5d:01:1d:15",
                driver="hv_netvsc",
                speed="10000 Mbps",
                host_ip_cidr="192.168.20.1/24",
                host_mtu=1500,
                host_admin_state="up",
                oper_state="up",
            )
        ],
        renames=renames,
    )

    by_mac = {interface.mac_address: interface for interface in reconciled}
    assert by_mac["00:15:5d:01:1d:15"].name == "eth1"
    assert by_mac["00:15:5d:01:1d:15"].ip_cidr == "192.168.20.1/24"
    assert by_mac["00:15:5d:01:1d:15"].mode == "access"
    assert by_mac["00:15:5d:01:1d:14"].name.startswith("missing_")
    assert by_mac["00:15:5d:01:1d:14"].oper_state == "missing"
    assert renames["eth2"] == "eth1"
    assert renames["eth1"].startswith("missing_")


def test_sync_host_inventory_cleans_removed_nic_bindings_and_retargets_survivors(monkeypatch, tmp_path, caplog):
    from sqlalchemy import select

    import labfoundry.app.database as database
    from labfoundry.app.config import get_settings

    db_path = tmp_path / "labfoundry-renumber.db"
    monkeypatch.setenv("LABFOUNDRY_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LABFOUNDRY_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setenv("LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD", "labfoundry-admin")
    get_settings.cache_clear()
    database.engine.dispose()
    database.engine = database.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.SessionLocal.configure(bind=database.engine)
    database.init_db()

    def fake_discover():
        return [
            HostPhysicalInterface(
                name="eth1",
                mac_address="00:15:5d:01:1d:15",
                driver="hv_netvsc",
                speed="10000 Mbps",
                host_ip_cidr="192.168.20.1/24",
                host_mtu=1500,
                host_admin_state="up",
                oper_state="up",
            )
        ]

    monkeypatch.setattr("labfoundry.app.services.networking.discover_host_physical_interfaces", fake_discover)

    with database.SessionLocal() as db:
        db.add_all(
            [
                PhysicalInterface(
                    name="eth1",
                    mac_address="00:15:5d:01:1d:14",
                    role="access",
                    mode="trunk",
                    inventory_source="host",
                    desired_state_source="user",
                ),
                PhysicalInterface(
                    name="eth2",
                    mac_address="00:15:5d:01:1d:15",
                    role="access",
                    mode="access",
                    ip_cidr="192.168.20.1/24",
                    inventory_source="host",
                    desired_state_source="user",
                ),
                VlanInterface(parent_interface="eth1", name="eth1.22", vlan_id=22, ip_cidr="192.168.22.1/24"),
                VlanInterface(parent_interface="eth2", name="eth2.50", vlan_id=50, ip_cidr="192.168.50.1/24"),
                Route(destination_cidr="10.50.0.0/24", interface_name="eth2.50"),
                Route(destination_cidr="10.22.0.0/24", interface_name="eth1.22"),
                NatRule(name="removed outbound", source="192.168.22.0/24", outbound_interface="eth1.22"),
                DhcpSettings(enabled=True),
                DhcpScope(name="removed-zone", interface_name="eth1.22", site_address="192.168.22.1", range_start="192.168.22.100", range_end="192.168.22.200"),
                DnsSettings(enabled=True, listen_interface="eth1.22\neth2.50", listen_address="192.168.22.1\n192.168.50.1"),
                CaSettings(enabled=True, listen_interface="eth1.22", listen_address="192.168.22.1\n10.0.0.99"),
                KmsSettings(enabled=True, listen_interface="eth1.22", listen_address="192.168.22.1"),
            ]
        )
        db.commit()

        with caplog.at_level(logging.WARNING, logger="labfoundry.networking"):
            sync_host_physical_interfaces(db)

        survivor = db.execute(select(PhysicalInterface).where(PhysicalInterface.mac_address == "00:15:5d:01:1d:15")).scalar_one()
        removed = db.execute(select(PhysicalInterface).where(PhysicalInterface.mac_address == "00:15:5d:01:1d:14")).scalar_one()
        assert survivor.name == "eth1"
        assert survivor.ip_cidr == "192.168.20.1/24"
        assert removed.name.startswith("missing_")
        assert removed.oper_state == "missing"
        assert removed.mode == "unused"
        assert removed.admin_state == "down"
        assert removed.ip_cidr is None
        survivor_vlan = db.execute(select(VlanInterface).where(VlanInterface.vlan_id == 50)).scalar_one()
        removed_vlan = db.execute(select(VlanInterface).where(VlanInterface.vlan_id == 22)).scalar_one()
        assert survivor_vlan.parent_interface == "eth1"
        assert survivor_vlan.name == "eth1.50"
        assert removed_vlan.parent_interface == removed.name
        assert removed_vlan.name == f"{removed.name}.22"
        assert removed_vlan.enabled is False
        survivor_route = db.execute(select(Route).where(Route.destination_cidr == "10.50.0.0/24")).scalar_one()
        removed_route = db.execute(select(Route).where(Route.destination_cidr == "10.22.0.0/24")).scalar_one()
        assert survivor_route.interface_name == "eth1.50"
        assert survivor_route.enabled is True
        assert removed_route.interface_name == f"{removed.name}.22"
        assert removed_route.enabled is False
        nat_rule = db.execute(select(NatRule).where(NatRule.name == "removed outbound")).scalar_one()
        assert nat_rule.enabled is False
        assert nat_rule.outbound_interface == ""
        dhcp_settings = db.execute(select(DhcpSettings)).scalar_one()
        assert dhcp_settings.enabled is False
        dhcp_scope = db.execute(select(DhcpScope).where(DhcpScope.name == "removed-zone")).scalar_one()
        assert dhcp_scope.enabled is False
        assert dhcp_scope.interface_name == ""
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        assert dns_settings.listen_interface == "eth1.50"
        assert dns_settings.listen_address == "192.168.50.1"
        assert dns_settings.enabled is True
        ca_settings = db.execute(select(CaSettings)).scalar_one()
        assert ca_settings.listen_interface == ""
        assert ca_settings.listen_address == "10.0.0.99"
        assert ca_settings.enabled is True
        kms_settings = db.execute(select(KmsSettings)).scalar_one()
        assert kms_settings.listen_interface == ""
        assert kms_settings.listen_address == ""
        assert kms_settings.enabled is False
        rendered = render_network_config(interfaces=[survivor, removed], vlans=[survivor_vlan, removed_vlan])
        assert removed.name not in rendered
        assert removed_vlan.name not in rendered
        warning = db.execute(select(Setting).where(Setting.key == NETWORK_INVENTORY_CLEANUP_WARNING_KEY)).scalar_one()
        assert "Missing physical interface cleanup" in warning.value
        audit = db.execute(select(AuditEvent).where(AuditEvent.action == "cleanup_missing_physical_interface_bindings")).scalar_one()
        assert "disabled VLAN eth1.22" in (audit.detail or "")
        assert "disabled KMS / KMIP" in (audit.detail or "")
        assert "Missing physical interface cleanup" in caplog.text

    get_settings.cache_clear()


def test_startup_host_inventory_refreshes_appliance_seed_without_apply_job(monkeypatch, tmp_path):
    from sqlalchemy import select

    import labfoundry.app.database as database
    from labfoundry.app.config import get_settings
    from labfoundry.app.main import refresh_startup_host_inventory
    from labfoundry.app.seed import seed_initial_data

    db_path = tmp_path / "labfoundry-startup.db"
    monkeypatch.setenv("LABFOUNDRY_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LABFOUNDRY_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setenv("LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD", "labfoundry-admin")
    get_settings.cache_clear()
    database.engine.dispose()
    database.engine = database.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.SessionLocal.configure(bind=database.engine)
    database.init_db()

    def fake_discover():
        return [
            HostPhysicalInterface(
                name="ens192",
                mac_address="00:15:5d:aa:bb:cc",
                driver="hv_netvsc",
                speed="10000 Mbps",
                host_ip_cidr="192.168.49.22/24",
                host_mtu=1500,
                host_admin_state="up",
                oper_state="up",
            )
        ]

    monkeypatch.setattr("labfoundry.app.services.networking.discover_host_physical_interfaces", fake_discover)

    with database.SessionLocal() as db:
        seed_initial_data(db, include_examples=False)
        refresh_startup_host_inventory(db, environment="appliance")
        interface = db.execute(select(PhysicalInterface)).scalar_one()
        assert interface.name == "ens192"
        assert interface.inventory_source == "host"
        assert interface.desired_state_source == "seed"
        assert interface.ip_cidr is None
        assert interface.admin_state == "down"
        assert db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one_or_none() is None

    get_settings.cache_clear()


def test_render_network_config_includes_physical_roles_for_networkd_apply():
    config = render_network_config(
        interfaces=[
            PhysicalInterface(
                name="eth0",
                mac_address="00:15:5d:aa:bb:01",
                ip_cidr="192.168.49.1/24",
                role="management",
                mode="access",
            )
        ],
        vlans=[],
    )

    assert "interface=eth0" in config
    assert "  role=management" in config

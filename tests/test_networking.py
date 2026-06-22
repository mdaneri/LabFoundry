import json

from labfoundry.app.models import PhysicalInterface
from labfoundry.app.services.networking import (
    HostPhysicalInterface,
    parse_linux_ip_interfaces,
    reconcile_host_physical_interfaces,
    render_network_config,
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
        mac_address="old",
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

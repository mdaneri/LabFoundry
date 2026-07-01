from labfoundry.app.models import NatRule, Route
from labfoundry.app.services.routes_wan import render_wan_config, validate_nat_source, validate_wan_state


def test_render_wan_config_uses_ipv6_route_commands():
    route = Route(
        destination_cidr="2001:db8:100::/64",
        gateway="2001:db8:50::fe",
        interface_name="eth2.50",
        metric=120,
        enabled=True,
    )

    config = render_wan_config(
        [route],
        targets=[
            {
                "name": "eth2.50",
                "kind": "vlan",
                "role": "wan",
                "ip_cidr": "192.168.50.1/24",
                "ipv6_cidr": "2001:db8:50::1/64",
                "wan": True,
            }
        ],
    )

    assert "  ipv6_cidr=2001:db8:50::1/64" in config
    assert "ip -6 route replace 2001:db8:100::/64 via 2001:db8:50::fe dev eth2.50 metric 120" in config


def test_validate_wan_state_rejects_ipv6_nat_sources_and_gateway_family_mismatch():
    groups = [
        {"id": "any", "name": "Any", "entries": ["any"]},
        {"id": "custom:dual", "name": "Dual", "entries": ["192.168.50.0/24", "2001:db8:50::/64"]},
    ]
    nat = NatRule(name="dual source", source="group:custom:dual", outbound_interface="eth2.50", masquerade=True, priority=100, enabled=True)
    route = Route(destination_cidr="2001:db8:100::/64", gateway="192.168.50.254", interface_name="eth2.50", metric=100, enabled=True)

    errors = validate_wan_state([route], [], {"eth2.50"}, [nat], {"eth2.50"}, groups)

    assert any("same IP family" in error for error in errors)
    assert any("NAT v1 supports IPv4 source CIDRs only" in error for error in errors)
    assert any("NAT v1 supports IPv4 source CIDRs only" in error for error in validate_nat_source("group:custom:dual", {"custom:dual"}, groups))

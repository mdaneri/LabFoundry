from labfoundry.app.models import (
    DhcpScope,
    DhcpSettings,
    DnsSettings,
    FirewallRule,
    FirewallSettings,
    KmsSettings,
    VcfBackupSettings,
    VcfOfflineDepotSettings,
    VcfPrivateRegistrySettings,
)
from labfoundry.app.services.firewall import dhcp_firewall_rules, managed_service_firewall_rules, render_nftables_config


def test_dhcp_firewall_rules_follow_scope_interface_and_replace_legacy_rule():
    settings = FirewallSettings(enabled=True, default_input_policy="drop")
    legacy_rule = FirewallRule(
        name="sitea-dns-dhcp",
        direction="input",
        action="accept",
        protocol="udp",
        source="192.168.50.0/24",
        destination="any",
        destination_port="53,67",
        interface_name="eth1",
        priority=20,
        enabled=True,
        description="Allow SiteA clients to reach LabFoundry DNS and DHCP.",
    )
    scopes = [
        DhcpScope(
            name="SiteA",
            interface_name="eth2.50",
            site_address="192.168.50.1",
            prefix_length=24,
            enabled=True,
        )
    ]
    generated_rules = dhcp_firewall_rules(DhcpSettings(enabled=True), scopes)

    config = render_nftables_config(settings, [legacy_rule], generated_rules, replace_labfoundry_dhcp_rules=True)

    assert 'iifname "eth2.50" ip saddr 192.168.50.0/24 udp dport { 53, 67 } accept comment "sitea-dns-dhcp"' in config
    assert 'iifname "eth1" ip saddr 192.168.50.0/24 udp dport { 53, 67 } accept comment "sitea-dns-dhcp"' not in config


def test_disabled_dhcp_removes_labfoundry_managed_dns_dhcp_rule():
    settings = FirewallSettings(enabled=True, default_input_policy="drop")
    legacy_rule = FirewallRule(
        name="sitea-dns-dhcp",
        direction="input",
        action="accept",
        protocol="udp",
        source="192.168.50.0/24",
        destination="any",
        destination_port="53,67",
        interface_name="eth1",
        priority=20,
        enabled=True,
        description="Allow SiteA clients to reach LabFoundry DNS and DHCP.",
    )
    generated_rules = dhcp_firewall_rules(DhcpSettings(enabled=False), [])

    config = render_nftables_config(settings, [legacy_rule], generated_rules, replace_labfoundry_dhcp_rules=True)

    assert "sitea-dns-dhcp" not in config
    assert 'iifname "eth1"' not in config


def test_managed_service_firewall_rules_include_all_enabled_service_listeners():
    rules = managed_service_firewall_rules(
        dns_settings=DnsSettings(enabled=True, listen_interface="eth2.50"),
        dhcp_settings=DhcpSettings(enabled=True),
        dhcp_scopes=[
            DhcpScope(
                name="SiteA",
                interface_name="eth2.50",
                site_address="192.168.50.1",
                prefix_length=24,
                enabled=True,
            )
        ],
        kms_settings=KmsSettings(enabled=True, listen_interface="eth2.50", port=5696),
        vcf_backup_settings=VcfBackupSettings(enabled=True, listen_interface="eth2.50", port=22),
        vcf_depot_settings=VcfOfflineDepotSettings(enabled=True, listen_interface="eth2.50", port=8443),
        vcf_registry_settings=VcfPrivateRegistrySettings(enabled=True, listen_interface="eth2.50", port=9443),
        interface_networks={"eth0": "192.168.49.0/24", "eth2.50": "192.168.50.0/24"},
    )

    by_name = {rule.name: rule for rule in rules}

    assert by_name["mgmt-console"].interface_name == "eth0"
    assert by_name["eth2-50-dns-tcp"].destination_port == "53"
    assert by_name["eth2-50-dns-udp"].destination_port == "53"
    assert by_name["sitea-dns-dhcp"].interface_name == "eth2.50"
    assert by_name["kms-kmip"].destination_port == "5696"
    assert by_name["vcf-backups-sftp"].destination_port == "22"
    assert by_name["vcf-offline-depot"].destination_port == "8443"
    assert by_name["vcf-private-registry"].destination_port == "9443"

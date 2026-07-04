from labfoundry.app.models import CaSettings, PhysicalInterface, VcfOfflineDepotSettings, VcfPrivateRegistrySettings
from labfoundry.app.services.public_services import public_service_entries, render_public_services_nginx_config


def test_public_service_entries_scope_services_to_matching_address():
    interfaces = [
        PhysicalInterface(name="eth0", role="management", mode="access", ip_cidr="192.168.167.10/24"),
        PhysicalInterface(name="eth2", role="access", mode="access", ip_cidr="192.168.87.32/24"),
        PhysicalInterface(name="eth3", role="access", mode="access", ip_cidr="192.168.88.32/24"),
    ]
    ca_settings = CaSettings(enabled=True, listen_interface="eth2", listen_address="192.168.87.32", root_certificate_pem="root")
    depot_settings = VcfOfflineDepotSettings(enabled=True, listen_interface="eth2", listen_address="192.168.87.32")
    registry_settings = VcfPrivateRegistrySettings(
        enabled=True,
        hostname="registry.labfoundry.internal",
        listen_interface="eth3",
        listen_address="192.168.88.32",
        port=9443,
    )

    entries = public_service_entries(
        interfaces=interfaces,
        vlans=[],
        ca_settings=ca_settings,
        esxi_pxe_boot={"enabled": True, "listen_interface": "eth2", "listen_address": "192.168.87.32"},
        vcf_depot_settings=depot_settings,
        vcf_registry_settings=registry_settings,
    )

    by_address = {entry["address"]: {service["id"] for service in entry["services"]} for entry in entries}
    assert "192.168.167.10" not in by_address
    assert by_address["192.168.87.32"] == {"ca", "esxi_pxe", "vcf_offline_depot"}
    assert by_address["192.168.88.32"] == {"vcf_private_registry"}


def test_public_services_nginx_config_contains_per_ip_scoped_locations():
    config = render_public_services_nginx_config(
        [
            {
                "interface": "eth2",
                "role": "access",
                "address": "192.168.87.32",
                "services": [
                    {"id": "ca"},
                    {"id": "esxi_pxe"},
                    {"id": "vcf_offline_depot"},
                ],
            },
            {
                "interface": "eth3",
                "role": "access",
                "address": "192.168.88.32",
                "services": [{"id": "vcf_private_registry"}],
            },
        ],
        depot_store_path="/mnt/labfoundry-vcf-offline-depot",
    )

    assert "listen 192.168.87.32:80;" in config
    assert "listen 192.168.88.32:80;" in config
    assert "location /ca {" in config
    assert "location /requests {" in config
    assert "location /pxe/esxi/ks/" in config
    assert "alias /var/lib/labfoundry/pxe/http/esxi/;" in config
    assert "location = /PROD" in config
    assert "return 301 /PROD/;" in config
    assert "alias /mnt/labfoundry-vcf-offline-depot/PROD/;" in config
    assert "/registry" not in config

    registry_block = config.split("listen 192.168.88.32:80;", 1)[1]
    assert "location /ca {" not in registry_block
    assert "location = /PROD" not in registry_block
    assert "location /pxe/esxi/" not in registry_block

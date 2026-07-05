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
    assert "server_name _ 192.168.87.32;" in config
    assert "server_name _ 192.168.88.32;" in config
    assert "location ^~ /static/ {" in config
    assert "location = /favicon.ico {" in config
    assert "location = /manifest.webmanifest {" in config
    assert "location = /requests/login {" in config
    assert "location = /requests/logout {" in config
    assert "\n  location = /login {" not in config
    assert "\n  location = /logout {" not in config
    assert "location /ca {" in config
    assert "location /requests {" in config
    assert "location /pxe/esxi/ks/" in config
    assert "alias /var/lib/labfoundry/pxe/http/esxi/;" in config
    assert "location = /PROD" in config
    assert "return 301 /PROD/;" in config
    assert "location = /PROD/login {" in config
    assert "location = /PROD/logout {" in config
    assert "location = /_labfoundry_depot_auth {" in config
    assert "internal;" in config
    assert "proxy_pass http://127.0.0.1:8000/PROD/auth-check;" in config
    assert "location @labfoundry_depot_login {" in config
    assert "return 303 /PROD/login?next=$request_uri;" in config
    assert "location = /PROD/ {" in config
    assert "location ~ ^/PROD/.*/$ {" in config
    assert "location ~ ^/PROD/(?!login$|logout$)(.+[^/])$ {" in config
    assert "auth_request /_labfoundry_depot_auth;" in config
    assert "error_page 401 = @labfoundry_depot_login;" in config
    assert "auth_basic" not in config
    assert "auth_basic_user_file" not in config
    assert "alias /mnt/labfoundry-vcf-offline-depot/PROD/$1;" in config
    assert "autoindex off;" in config
    assert "/registry" not in config

    depot_login_block = config.split("location = /PROD/login {", 1)[1].split("  }", 1)[0]
    depot_directory_block = config.split("location = /PROD/ {", 1)[1].split("  }", 1)[0]
    depot_static_block = config.split("location ~ ^/PROD/(?!login$|logout$)(.+[^/])$ {", 1)[1].split("  }", 1)[0]
    assert "auth_basic" not in depot_login_block
    assert "auth_basic" not in depot_directory_block
    assert "auth_request /_labfoundry_depot_auth;" in depot_static_block

    registry_block = config.split("listen 192.168.88.32:80;", 1)[1]
    assert "location = /requests/login {" not in registry_block
    assert "location = /requests/logout {" not in registry_block
    assert "location /ca {" not in registry_block
    assert "location = /PROD" not in registry_block
    assert "location /pxe/esxi/" not in registry_block


def test_public_services_nginx_config_respects_unauthenticated_depot_access():
    config = render_public_services_nginx_config(
        [
            {
                "interface": "eth2",
                "role": "access",
                "address": "192.168.87.32",
                "services": [{"id": "vcf_offline_depot", "allow_unauthenticated_access": True}],
            },
        ],
        depot_store_path="/mnt/labfoundry-vcf-offline-depot",
    )

    assert "location ~ ^/PROD/(?!login$|logout$)(.+[^/])$ {" in config
    assert "auth_request" not in config
    assert "auth_basic" not in config

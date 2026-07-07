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
    services_by_id = {service["id"]: service for entry in entries for service in entry["services"]}
    assert services_by_id["ca"]["dns_names"] == ["ca.labfoundry.internal"]
    assert services_by_id["esxi_pxe"]["dns_names"] == ["esxi-pxe.labfoundry.internal"]
    assert services_by_id["vcf_offline_depot"]["dns_names"] == ["depot.labfoundry.internal"]
    assert services_by_id["vcf_offline_depot"]["allow_unauthenticated_access"] is False
    assert "allow_unauthenticated_access" not in services_by_id["esxi_pxe"]
    assert services_by_id["vcf_private_registry"]["dns_names"] == ["registry.labfoundry.internal"]

    depot_settings.allow_unauthenticated_access = True
    open_entries = public_service_entries(
        interfaces=interfaces,
        vlans=[],
        ca_settings=ca_settings,
        esxi_pxe_boot={"enabled": True, "listen_interface": "eth2", "listen_address": "192.168.87.32"},
        vcf_depot_settings=depot_settings,
        vcf_registry_settings=registry_settings,
    )
    open_services_by_id = {service["id"]: service for entry in open_entries for service in entry["services"]}
    assert open_services_by_id["vcf_offline_depot"]["allow_unauthenticated_access"] is True
    assert "allow_unauthenticated_access" not in open_services_by_id["esxi_pxe"]


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
    assert "server_name _ 192.168.87.32;" in config
    assert "listen 192.168.88.32:80;" not in config
    assert "server_name _ 192.168.88.32;" not in config
    assert "location ^~ /static/ {" not in config
    assert "location = /favicon.ico {" not in config
    assert "location = /manifest.webmanifest {" not in config
    assert "location = /requests/login {" not in config
    assert "location = /requests/logout {" not in config
    assert "\n  location = /login {" not in config
    assert "\n  location = /logout {" not in config
    assert "location /ca {" not in config
    assert "location /requests {" not in config
    assert "location /pxe/esxi/ks/" in config
    assert "alias /var/lib/labfoundry/pxe/http/esxi/;" in config
    assert "location = /PROD" not in config
    assert "return 301 /PROD/;" not in config
    assert "location = /PROD/login {" not in config
    assert "location = /PROD/logout {" not in config
    assert "location = /_labfoundry_depot_auth {" not in config
    assert "proxy_pass http://127.0.0.1:8000/PROD/auth-check;" not in config
    assert "location @labfoundry_depot_login {" not in config
    assert "return 303 /PROD/login?next=$request_uri;" not in config
    assert "location = /PROD/ {" not in config
    assert "location ~ ^/PROD/.*/$ {" not in config
    assert "location ~ ^/PROD/(?!login$|logout$)(.+[^/])$ {" not in config
    assert "auth_request /_labfoundry_depot_auth;" not in config
    assert "error_page 401 = @labfoundry_depot_login;" not in config
    assert 'auth_basic "LabFoundry VCF Offline Depot";' not in config
    assert "auth_basic_user_file /etc/labfoundry/nginx/htpasswd/vcf-offline-depot.htpasswd;" not in config
    assert "alias /mnt/labfoundry-vcf-offline-depot/PROD/$1;" not in config
    assert "autoindex off;" in config
    assert "/registry" not in config


def test_public_services_nginx_config_skips_non_pxe_http_services():
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

    assert "server {" not in config
    assert "/PROD/" not in config
    assert "auth_basic" not in config

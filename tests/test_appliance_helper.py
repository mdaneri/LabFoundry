import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import hashlib
import re
from pathlib import Path


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "appliance" / "labfoundry-helper"


def load_helper_module():
    loader = importlib.machinery.SourceFileLoader("labfoundry_helper", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader("labfoundry_helper", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def kms_config_text(managed_root: Path, *, enabled: bool = True, database_path: Path | None = None) -> str:
    database_path = database_path or Path("/var/lib/labfoundry/kms/pykmip.db")
    return "\n".join(
        [
            "# Managed by LabFoundry. Local changes may be overwritten.",
            f"# LabFoundry KMS enabled: {str(enabled).lower()}",
            "# LabFoundry KMS endpoint hostname: kms.labfoundry.internal",
            "# Backend: PyKMIP lab KMIP server desired state.",
            "[server]",
            "hostname=192.168.50.1",
            "port=5696",
            f"certificate_path={managed_root / 'kms' / 'certs' / 'kms.labfoundry.internal.crt'}",
            f"key_path={managed_root / 'kms' / 'certs' / 'kms.labfoundry.internal.key'}",
            f"ca_path={managed_root / 'ca' / 'root.crt'}",
            "auth_suite=TLS1.2",
            f"policy_path={managed_root / 'kms' / 'policies'}",
            "enable_tls_client_auth=True",
            "logging_level=INFO",
            f"database_path={database_path}",
            "",
        ]
    )


def network_config_text(
    *,
    eth2_mode: str = "trunk",
    eth2_admin_state: str = "up",
    include_vlan: bool = True,
    include_removed_vlan: bool = False,
    dual_stack: bool = False,
) -> str:
    lines = [
        "[physical_interfaces]",
        "interface=eth0",
        "  role=management",
        "  mode=access",
        "  ipv4_method=static",
        "  ip_cidr=192.168.49.1/24",
        f"  ipv6_cidr={'2001:db8:49::1/64' if dual_stack else ''}",
        "  admin_state=up",
        "  mtu=1500",
        "interface=eth2",
        "  role=access",
        f"  mode={eth2_mode}",
        "  ipv4_method=static",
        "  ip_cidr=",
        f"  ipv6_cidr={'2001:db8:60::1/64' if dual_stack else ''}",
        f"  admin_state={eth2_admin_state}",
        "  mtu=1500",
        "",
        "[vlan_interfaces]",
    ]
    if include_vlan:
        lines.extend(
            [
                "vlan=eth2.20",
                "  parent=eth2",
                "  vlan_id=20",
                "  ip_cidr=192.168.20.1/24",
                f"  ipv6_cidr={'2001:db8:20::1/64' if dual_stack else ''}",
                "  mtu=1500",
                "  role=services",
            ]
        )
    if include_removed_vlan:
        lines.extend(
            [
                "",
                "[removed_vlan_interfaces]",
                "vlan=eth2.20",
                "  parent=eth2",
                "  vlan_id=20",
            ]
        )
    return "\n".join(lines)


def public_services_config_text() -> str:
    return "\n".join(
        [
            "# Managed by LabFoundry. Local changes may be overwritten.",
            "# IP-scoped public service front door for non-management interfaces.",
            "server {",
            "  listen 192.168.87.32:80;",
            "  server_name _;",
            "  location /pxe/esxi/ks/ {",
            "    proxy_pass http://127.0.0.1:8000;",
            "  }",
            "  location /pxe/esxi/ {",
            "    alias /var/lib/labfoundry/pxe/http/esxi/;",
            "    autoindex off;",
            "  }",
            "  location / {",
            "    return 404;",
            "  }",
            "}",
            "",
        ]
    )


def public_services_ca_https_config_text(cert_path: Path, key_path: Path) -> str:
    return "\n".join(
        [
            "# Managed by LabFoundry. Local changes may be overwritten.",
            "# IP-scoped public service front door for non-management interfaces.",
            "server {",
            "  # CA portal HTTPS front door.",
            "  listen 192.168.87.32:443 ssl;",
            "  server_name ca.labfoundry.internal;",
            f"  ssl_certificate {cert_path};",
            f"  ssl_certificate_key {key_path};",
            "  location = / {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location = /ca {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location ^~ /ca/ {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location = /requests {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location ^~ /requests/ {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location ^~ /static/ {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location = /favicon.ico {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location = /manifest.webmanifest {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location = /service-worker.js {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location / {",
            "    return 404;",
            "  }",
            "}",
            "",
        ]
    )


def public_services_ip_https_depot_config_text(cert_path: Path, key_path: Path) -> str:
    return "\n".join(
        [
            "# Managed by LabFoundry. Local changes may be overwritten.",
            "# IP-scoped public service front door for non-management interfaces.",
            "server {",
            "  # IP-scoped HTTPS public services front door.",
            "  listen 192.168.87.32:443 ssl;",
            "  server_name _ 192.168.87.32;",
            f"  ssl_certificate {cert_path};",
            f"  ssl_certificate_key {key_path};",
            "  location = /PROD {",
            "    return 301 /PROD/;",
            "  }",
            "  location = /PROD/login {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location = /PROD/logout {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location = /PROD/ {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location ~ ^/PROD/.*/$ {",
            "    proxy_pass http://127.0.0.1:8000;",
            "    proxy_set_header X-Forwarded-Proto https;",
            "  }",
            "  location ~ ^/PROD/(?!login$|logout$|auth-check$)(.+[^/])$ {",
            "    alias /mnt/labfoundry-vcf-offline-depot/PROD/$1;",
            "  }",
            "}",
            "",
        ]
    )


def test_public_services_helper_validates_staged_nginx_config(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "public-services"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-public-services.conf"
    config_path.write_text(public_services_config_text(), encoding="utf-8")
    monkeypatch.setattr(helper, "PUBLIC_SERVICES_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "VCF_DEPOT_PROD_PATH", Path("/mnt/labfoundry-vcf-offline-depot/PROD"))

    result = helper._handle_public_services("validate", [str(config_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert "validation ok" in captured.out


def test_public_services_helper_allows_ip_scoped_depot_https_paths(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "public-services"
    managed_root = tmp_path / "managed"
    cert_path = managed_root / "ca-portal" / "certs" / "ca.labfoundry.internal.crt"
    key_path = managed_root / "ca-portal" / "certs" / "ca.labfoundry.internal.key"
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n", encoding="utf-8")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-public-services.conf"
    config_path.write_text(public_services_ip_https_depot_config_text(cert_path, key_path), encoding="utf-8")
    monkeypatch.setattr(helper, "PUBLIC_SERVICES_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "VCF_DEPOT_PROD_PATH", Path("/mnt/labfoundry-vcf-offline-depot/PROD"))

    result = helper._handle_public_services("validate", [str(config_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert "validation ok" in captured.out


def test_public_services_helper_validates_ca_https_sni_config(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "public-services"
    managed_root = tmp_path / "managed"
    cert_path = managed_root / "ca-portal" / "certs" / "ca.labfoundry.internal.crt"
    key_path = managed_root / "ca-portal" / "certs" / "ca.labfoundry.internal.key"
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n", encoding="utf-8")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-public-services.conf"
    config_path.write_text(public_services_ca_https_config_text(cert_path, key_path), encoding="utf-8")
    monkeypatch.setattr(helper, "PUBLIC_SERVICES_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)

    result = helper._handle_public_services("validate", [str(config_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert "validation ok" in captured.out


def test_nginx_site_conflict_detects_duplicate_sni_name_on_same_listener(monkeypatch, tmp_path):
    helper = load_helper_module()
    sites_dir = tmp_path / "sites.d"
    sites_dir.mkdir()
    existing = sites_dir / "vcf-offline-depot.conf"
    existing.write_text(
        "\n".join(
            [
                "server {",
                "  listen 192.168.87.32:443 ssl;",
                "  server_name ca.labfoundry.internal;",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    candidate = "\n".join(
        [
            "server {",
            "  listen 192.168.87.32:443 ssl;",
            "  server_name ca.labfoundry.internal;",
            "}",
        ]
    )
    monkeypatch.setattr(helper, "NGINX_SITES_DIR", sites_dir)

    assert "duplicates server_name ca.labfoundry.internal" in helper._nginx_site_conflict(sites_dir / "public-services.conf", candidate)


def test_public_services_helper_rejects_broad_root_and_registry_proxy(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "public-services"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-public-services.conf"
    config_path.write_text(
        public_services_config_text().replace("  location / {", "  root /mnt/labfoundry-vcf-offline-depot;\n  location /registry {\n    proxy_pass http://127.0.0.1:8080;\n  }\n  location / {"),
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "PUBLIC_SERVICES_APPLY_DIR", apply_dir)

    result = helper._handle_public_services("validate", [str(config_path)])

    captured = capsys.readouterr()
    assert result == 2
    assert "must not expose a broad server root" in captured.err
    assert "must not add registry proxy locations" in captured.err


def test_public_services_helper_apply_installs_site(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "public-services"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-public-services.conf"
    config_text = public_services_config_text()
    config_path.write_text(config_text, encoding="utf-8")
    site_path = tmp_path / "sites" / "public-services.conf"
    calls: list[tuple[Path, str]] = []

    def fake_install(path, text):
        calls.append((path, text))
        return 0

    monkeypatch.setattr(helper, "PUBLIC_SERVICES_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "NGINX_PUBLIC_SERVICES_SITE_PATH", site_path)
    monkeypatch.setattr(helper, "_install_nginx_site", fake_install)

    result = helper._handle_public_services("apply", [str(config_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert calls == [(site_path, config_text)]
    assert "apply complete" in captured.out


def wan_config_text(
    *,
    bad_nat_source: bool = False,
    bad_target: bool = False,
    wan_mode: str = "interface",
    target_role: str = "wan",
    target_wan: bool = True,
    ipv6_route: bool = False,
    ipv6_only_target: bool = False,
) -> str:
    source = "not-a-cidr" if bad_nat_source else "192.168.50.0/24"
    outbound = "eth9" if bad_target else "eth1.20"
    ipv4_cidr = "" if ipv6_only_target else "192.168.20.1/24"
    ipv6_cidr = "2001:db8:20::1/64" if ipv6_route or ipv6_only_target else ""
    destination = "2001:db8:100::/64" if ipv6_route else "10.20.0.0/24"
    gateway = "2001:db8:20::fe" if ipv6_route else ""
    return "\n".join(
        [
            "[targets]",
            "target=eth1.20",
            "  kind=vlan",
            f"  role={target_role}",
            f"  ip_cidr={ipv4_cidr}",
            f"  ipv6_cidr={ipv6_cidr}",
            f"  wan={str(target_wan).lower()}",
            "",
            "[routes]",
            f"route={destination}",
            f"  gateway={gateway}",
            "  interface=eth1.20",
            "  metric=120",
            "  enabled=true",
            "  wan_policy=Slow WAN",
            f"  wan_mode={wan_mode}",
            "",
            "[nat_rules]",
            "nat=SiteA outbound WAN",
            "  enabled=true",
            f"  source={source}",
            f"  source_resolved={source}",
            f"  outbound_interface={outbound}",
            "  masquerade=true",
            "  priority=100",
            "  description=demo",
            "",
            "[wan_policies]",
            "policy=Slow WAN",
            "  enabled=true",
            "  latency_ms=100",
            "  jitter_ms=10",
            "  packet_loss_percent=0.5",
            "  bandwidth_mbit=100",
            "  corrupt_percent=0",
            "  duplicate_percent=0",
            "  reorder_percent=0",
        ]
    )


def esxi_pxe_manifest(http_root: Path, *, enabled: bool = True, stale_id: int = 99, iso_root: Path | None = None) -> dict:
    content = "install\nnetwork --bootproto=dhcp\nrootpw VMware01!\nreboot\n%firstboot\n%end\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    kickstart_http_path = f"/pxe/esxi/ks/{content_hash[:12]}.cfg"
    kickstart_url = f"http://192.168.50.1:8080{kickstart_http_path}"
    iso_root = iso_root or http_root.parent / "iso"
    iso_path = iso_root / "VMware-VMvisor-Installer-8.0U3.iso"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", iso_path.stem).strip("-._").lower()
    image_key = f"{slug}-{hashlib.sha1(str(iso_path).encode('utf-8')).hexdigest()[:10]}"
    http_base = http_root.parent
    image_path = http_base / "images" / image_key
    mac_key = "01-00-50-56-aa-bb-cc"
    kickstart_url = f"{kickstart_url}?mac={mac_key}"
    return {
        "kind": "labfoundry-esxi-pxe",
        "schema_version": 2,
        "http_root": str(http_root),
        "http_base": str(http_base),
        "image_http_root": str(http_base / "images"),
        "installer_iso_root": str(iso_root),
        "installer_isos": [
            {
                "name": iso_path.name,
                "path": str(iso_path),
                "relative_path": iso_path.name,
                "size_bytes": 12,
                "updated_at": "2026-06-28T00:00:00+00:00",
            }
        ],
        "kickstarts": [
            {
                "id": 7,
                "name": "ESXi install",
                "enabled": enabled,
                "content": content,
                "content_hash": content_hash,
                "http_path": kickstart_http_path,
                "generated_path": str(http_root / f"{content_hash[:12]}.cfg"),
            }
        ],
        "hosts": [
            {
                "id": 1,
                "hostname": "esxi-01",
                "mac_address": "00:50:56:aa:bb:cc",
                "kickstart_id": 7 if enabled else None,
                "installer_iso_path": str(iso_path),
                "installer_iso_name": iso_path.name,
                "enabled": True,
            }
        ],
        "artifacts": [
            {
                "host_id": 1,
                "hostname": "esxi-01",
                "mac_address": "00:50:56:aa:bb:cc",
                "mac_key": mac_key,
                "image_key": image_key,
                "installer_iso_path": str(iso_path),
                "installer_iso_name": iso_path.name,
                "image_http_path": f"/pxe/esxi/images/{image_key}",
                "image_http_url": f"http://192.168.50.1:8080/pxe/esxi/images/{image_key}",
                "image_generated_path": str(image_path),
                "kickstart_id": 7 if enabled else None,
                "kickstart_http_path": kickstart_http_path if enabled else "",
                "kickstart_url": kickstart_url if enabled else "",
                "pxelinux_config_path": str(http_root.parents[2] / "tftp" / "pxelinux.cfg" / mac_key),
                "uefi_tftp_boot_cfg_path": str(http_root.parents[2] / "tftp" / mac_key / "boot.cfg"),
                "http_boot_cfg_path": str(http_base / mac_key / "boot.cfg"),
            }
        ],
        "stale_id": stale_id,
    }


def ca_payload_text(root_dir: Path) -> str:
    root_cert = "-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----\n"
    cert = "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"
    key = "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n"
    crl = "-----BEGIN X509 CRL-----\ncrl\n-----END X509 CRL-----\n"
    return json.dumps(
        {
            "enabled": True,
            "root": {
                "common_name": "LabFoundry Internal Root CA",
                "certificate_pem": root_cert,
                "private_key_pem": key,
                "root_cert_path": str(root_dir / "ca" / "root-ca.pem"),
                "legacy_root_cert_path": str(root_dir / "ca" / "root.crt"),
                "ca_bundle_path": str(root_dir / "ca" / "ca-bundle.pem"),
                "crl_path": str(root_dir / "ca" / "labfoundry-ca.crl"),
                "crl_pem": crl,
            },
            "certificates": [
                {
                    "common_name": "kms.labfoundry.internal",
                    "managed_owner": "kms:server",
                    "certificate_pem": cert,
                    "chain_pem": cert + root_cert,
                    "private_key_pem": key,
                    "cert_path": str(root_dir / "kms" / "certs" / "kms.labfoundry.internal.crt"),
                    "key_path": str(root_dir / "kms" / "certs" / "kms.labfoundry.internal.key"),
                    "chain_path": str(root_dir / "kms" / "certs" / "kms.labfoundry.internal-chain.pem"),
                }
            ],
        }
    )


def test_network_helper_validates_vlan_parent_must_be_trunk(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(eth2_mode="access"), encoding="utf-8")

    errors = helper._network_config_errors(config_path)

    assert "VLAN eth2.20 parent eth2 is not marked trunk." in errors


def test_network_helper_accepts_valid_vlan_config(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(), encoding="utf-8")

    assert helper._network_config_errors(config_path) == []


def test_network_helper_rejects_static_management_without_ipv4(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text().replace("  ip_cidr=192.168.49.1/24", "  ip_cidr=", 1), encoding="utf-8")

    errors = helper._network_config_errors(config_path)

    assert "Interface eth0 must set an IPv4 CIDR when IPv4 method is static." in errors


def test_network_helper_requires_eth0_management(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text().replace("interface=eth0", "interface=eth1", 1), encoding="utf-8")

    errors = helper._network_config_errors(config_path)

    assert "Network config must keep eth0 as the management physical interface." in errors


def test_network_helper_renders_dual_stack_networkd_addresses(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(dual_stack=True), encoding="utf-8")

    assert helper._network_config_errors(config_path) == []
    files, _reconfigure_links, _admin_down_links = helper._systemd_networkd_files(config_path)

    assert "Address=192.168.49.1/24" in files["00-labfoundry-mgmt.network"]
    assert "Address=2001:db8:49::1/64" in files["00-labfoundry-mgmt.network"]
    vlan_network = files["10-labfoundry-eth2.20.network"]
    assert "Address=192.168.20.1/24" in vlan_network
    assert "Address=2001:db8:20::1/64" in vlan_network


def test_network_helper_renders_management_dhcp_networkd(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(
        "\n".join(
            [
                "[physical_interfaces]",
                "interface=eth0",
                "  role=management",
                "  mode=access",
                "  ipv4_method=dhcp",
                "  ip_cidr=",
                "  ipv6_cidr=",
                "  admin_state=up",
                "  mtu=1500",
                "",
                "[vlan_interfaces]",
            ]
        ),
        encoding="utf-8",
    )

    assert helper._network_config_errors(config_path) == []
    files, _reconfigure_links, _admin_down_links = helper._systemd_networkd_files(config_path)

    management_network = files["00-labfoundry-mgmt.network"]
    assert "DHCP=ipv4" in management_network
    assert "Address=" not in management_network


def test_wan_helper_rejects_config_outside_apply_dir(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-wan.conf"
    config_path.write_text(wan_config_text(), encoding="utf-8")

    try:
        helper._validate_wan_config_path(str(config_path))
    except ValueError as exc:
        assert "WAN config must be staged under" in str(exc)
    else:
        raise AssertionError("WAN config outside apply directory should be rejected")


def test_wan_helper_validates_routes_nat_and_netem(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-wan.conf"
    config_path.write_text(wan_config_text(), encoding="utf-8")

    assert helper._wan_config_errors(config_path) == []
    nat_config = helper._render_wan_nat_config(helper._parse_wan_config(config_path)["nat_rules"])
    assert "table ip labfoundry_nat" in nat_config
    assert 'ip saddr 192.168.50.0/24 oifname "eth1.20" masquerade' in nat_config


def test_wan_helper_rejects_bad_nat_source_and_target(tmp_path):
    helper = load_helper_module()
    bad_source = tmp_path / "bad-source.conf"
    bad_source.write_text(wan_config_text(bad_nat_source=True), encoding="utf-8")
    bad_target = tmp_path / "bad-target.conf"
    bad_target.write_text(wan_config_text(bad_target=True), encoding="utf-8")

    assert any("source not-a-cidr is not a valid CIDR" in error for error in helper._wan_config_errors(bad_source))
    assert any("must use an access physical interface or enabled VLAN" in error for error in helper._wan_config_errors(bad_target))


def test_wan_helper_rejects_route_wan_mode(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "route-mode.conf"
    config_path.write_text(wan_config_text(wan_mode="route"), encoding="utf-8")

    assert any("WAN mode route is planned but not supported in v1" in error for error in helper._wan_config_errors(config_path))


def test_wan_helper_ignores_disabled_routing_rule_missing_targets(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "disabled-routing-rule.conf"
    config_path.write_text(
        wan_config_text()
        + "\n".join(
            [
                "",
                "[routing_rules]",
                "routing=Stale disabled rule",
                "  enabled=false",
                "  source_interface=missing-source",
                "  destination_interface=missing-destination",
                "  priority=100",
            ]
        ),
        encoding="utf-8",
    )

    assert helper._wan_config_errors(config_path) == []


def test_wan_helper_rejects_enabled_routing_rule_missing_targets(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "enabled-routing-rule.conf"
    config_path.write_text(
        wan_config_text()
        + "\n".join(
            [
                "",
                "[routing_rules]",
                "routing=Stale enabled rule",
                "  enabled=true",
                "  source_interface=missing-source",
                "  destination_interface=missing-destination",
                "  priority=100",
            ]
        ),
        encoding="utf-8",
    )

    errors = helper._wan_config_errors(config_path)
    assert any("references missing source target missing-source" in error for error in errors)
    assert any("references missing destination target missing-destination" in error for error in errors)


def test_wan_helper_allows_nat_on_non_wan_role_target(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "nat-access-target.conf"
    config_path.write_text(wan_config_text(target_role="access", target_wan=False), encoding="utf-8")

    assert helper._wan_config_errors(config_path) == []


def test_wan_helper_accepts_ipv6_routes_and_rejects_ipv6_only_nat_targets(tmp_path):
    helper = load_helper_module()
    ipv6_route = tmp_path / "ipv6-route.conf"
    ipv6_route.write_text(wan_config_text(ipv6_route=True), encoding="utf-8")
    ipv6_only_nat = tmp_path / "ipv6-only-nat.conf"
    ipv6_only_nat.write_text(wan_config_text(ipv6_route=True, ipv6_only_target=True), encoding="utf-8")

    assert helper._wan_config_errors(ipv6_route) == []
    parsed = helper._parse_wan_config(ipv6_route)
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    helper._run = fake_run
    helper.shutil.which = lambda command: f"/usr/sbin/{command}" if command in {"ip", "tc"} else None
    assert helper._apply_wan_routes_and_qdiscs(parsed) == 0
    assert ["ip", "-6", "route", "replace", "2001:db8:100::/64", "via", "2001:db8:20::fe", "dev", "eth1.20", "metric", "120", "table", "200"] in commands
    assert any("outbound interface with an IPv4 CIDR" in error for error in helper._wan_config_errors(ipv6_only_nat))


def test_wan_helper_cleans_managed_policy_rule_windows_before_apply(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "policy-rules.conf"
    config_path.write_text(wan_config_text(), encoding="utf-8")
    parsed = helper._parse_wan_config(config_path)
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    helper._run = fake_run
    helper.shutil.which = lambda command: f"/usr/sbin/{command}" if command == "ip" else None

    assert helper._apply_wan_policy_rules(parsed) == 0
    assert ["ip", "rule", "del", "priority", "1000"] in commands
    assert ["ip", "-6", "rule", "del", "priority", "1000"] in commands
    assert ["ip", "rule", "del", "priority", "2099"] in commands
    assert ["ip", "-6", "rule", "del", "priority", "2099"] in commands
    assert ["ip", "rule", "add", "from", "192.168.20.0/24", "table", "200", "priority", "2000"] in commands


def test_wan_helper_preserves_management_default_gateway(monkeypatch, tmp_path):
    helper = load_helper_module()
    networkd_dir = tmp_path / "systemd-network"
    networkd_dir.mkdir()
    management_network = networkd_dir / "00-labfoundry-mgmt.network"
    management_network.write_text(
        "\n".join(
            [
                "[Match]",
                "Name=eth0",
                "",
                "[Network]",
                "Address=192.168.49.10/24",
                "",
                "[Route]",
                "Gateway=192.168.49.254",
                "Table=100",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "management-default.conf"
    config_path.write_text(
        "\n".join(
            [
                "[targets]",
                "target=eth0",
                "  kind=physical",
                "  role=management",
                "  ip_cidr=192.168.49.10/24",
                "  ipv6_cidr=",
                "  wan=false",
                "  routing_domain=management",
                "  route_allowed=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parsed = helper._parse_wan_config(config_path)
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", management_network)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper.shutil, "which", lambda command: f"/usr/sbin/{command}" if command == "ip" else None)

    assert helper._apply_wan_target_routes(parsed) == 0
    assert ["ip", "route", "replace", "192.168.49.0/24", "dev", "eth0", "table", "100"] in commands
    assert ["ip", "route", "replace", "default", "via", "192.168.49.254", "dev", "eth0", "table", "100"] in commands


def test_staging_prepare_repairs_apply_directory_ownership(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_root = tmp_path / "apply"
    config_path = apply_root / "wan" / "labfoundry-wan.conf"
    chowned: list[tuple[Path, str, str]] = []
    chmodded: list[tuple[Path, int]] = []

    monkeypatch.setattr(helper, "LABFOUNDRY_APPLY_DIR", apply_root)
    monkeypatch.setattr(helper.shutil, "chown", lambda path, user, group: chowned.append((Path(path), user, group)))
    monkeypatch.setattr(helper.os, "chmod", lambda path, mode: chmodded.append((Path(path), mode)))

    assert helper.main(["labfoundry-helper", "staging", "prepare", "--real", str(config_path)]) == 0

    assert config_path.parent.is_dir()
    assert (apply_root, "labfoundry", "labfoundry") in chowned
    assert (config_path.parent, "labfoundry", "labfoundry") in chowned
    assert (apply_root, 0o755) in chmodded
    assert (config_path.parent, 0o750) in chmodded


def test_esxi_pxe_helper_validates_and_writes_generated_kickstarts(monkeypatch, tmp_path):
    helper = load_helper_module()
    http_root = tmp_path / "pxe" / "http" / "esxi" / "ks"
    http_base = http_root.parent
    tftp_root = tmp_path / "pxe" / "tftp"
    ipxe_binary_dir = tmp_path / "usr" / "share" / "ipxe"
    iso_root = tmp_path / "vcf-depot" / "PROD" / "COMP" / "ESX_HOST"
    apply_dir = tmp_path / "apply" / "esxi-pxe"
    apply_dir.mkdir(parents=True)
    http_root.mkdir(parents=True)
    ipxe_binary_dir.mkdir(parents=True)
    iso_root.mkdir(parents=True)
    iso_tree = iso_root / "VMware-VMvisor-Installer-8.0U3.iso"
    (iso_tree / "efi" / "boot").mkdir(parents=True)
    (iso_tree / "boot.cfg").write_text(
        "title=ESXi\n"
        "kernel=/b.b00\n"
        "kernelopt=cdromBoot runweasel\n"
        "modules=/jumpstrt.gz---/useropts.gz\n",
        encoding="utf-8",
    )
    (iso_tree / "mboot.c32").write_bytes(b"mboot c32")
    (iso_tree / "efi" / "boot" / "bootx64.efi").write_bytes(b"mboot efi")
    (iso_tree / "efi" / "boot" / "crypto64.efi").write_bytes(b"crypto")
    (ipxe_binary_dir / "undionly.kpxe").write_bytes(b"bios ipxe")
    (ipxe_binary_dir / "snponly.efi").write_bytes(b"uefi ipxe")
    (ipxe_binary_dir / "pxelinux.0").write_bytes(b"pxelinux")
    (ipxe_binary_dir / "ldlinux.c32").write_bytes(b"ldlinux")
    (ipxe_binary_dir / "ldlinux.c32").write_bytes(b"ldlinux")
    (http_base / "boot.ipxe").write_text("old ipxe script", encoding="utf-8")
    (tftp_root / "bootx64.efi").parent.mkdir(parents=True, exist_ok=True)
    (tftp_root / "bootx64.efi").write_bytes(b"old uefi first stage")
    (tftp_root / "esxi.ipxe").write_text("old tftp script", encoding="utf-8")
    stale_mac = "01-aa-bb-cc-dd-ee-ff"
    (tftp_root / "pxelinux.cfg").mkdir(parents=True, exist_ok=True)
    (tftp_root / "pxelinux.cfg" / stale_mac).write_text("old pxelinux", encoding="utf-8")
    (tftp_root / stale_mac).mkdir(parents=True, exist_ok=True)
    (tftp_root / stale_mac / "boot.cfg").write_text("old tftp boot cfg", encoding="utf-8")
    (http_base / stale_mac).mkdir(parents=True, exist_ok=True)
    (http_base / stale_mac / "boot.cfg").write_text("old http boot cfg", encoding="utf-8")
    stale = http_root / "99.cfg"
    stale.write_text("old", encoding="utf-8")
    manifest = esxi_pxe_manifest(http_root, iso_root=iso_root)
    default_artifact = dict(manifest["artifacts"][0])
    default_artifact.update(
        {
            "host_id": None,
            "hostname": "Default / undefined MACs",
            "mac_address": "*",
            "mac_key": "default",
            "is_default": True,
            "kickstart_id": None,
            "kickstart_http_path": "",
            "kickstart_url": "",
            "pxelinux_config_path": str(tftp_root / "pxelinux.cfg" / "default"),
            "uefi_tftp_boot_cfg_path": str(tftp_root / "boot.cfg"),
            "http_boot_cfg_path": str(http_base / "boot.cfg"),
        }
    )
    manifest["artifacts"].append(default_artifact)
    manifest["boot"] = {
        "enabled": True,
        "hostname": "esxi-pxe.labfoundry.internal",
        "listen_interface": "eth1",
        "listen_address": "192.168.50.1",
        "tftp_root": str(tftp_root),
        "bios_bootfile": "undionly.kpxe",
        "uefi_bootfile": "snponly.efi",
        "bios_second_stage_bootfile": "pxelinux.0",
        "uefi_second_stage_bootfile": "mboot.efi",
        "native_uefi_bootfile": "mboot.efi",
        "http_port": 8080,
        "http_base_url": "http://192.168.50.1:8080/pxe/esxi",
        "native_uefi_http_enabled": True,
        "effective_native_uefi_http_url": "http://192.168.50.1:8080/pxe/esxi/mboot.efi",
        "ipxe_script": "#!ipxe\necho LabFoundry PXE ready\nshell\n",
    }
    config_path = apply_dir / "labfoundry-esxi-pxe.json"
    config_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_ROOT", http_root)
    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_BASE", http_base)
    monkeypatch.setattr(helper, "ESXI_PXE_IMAGE_HTTP_ROOT", http_base / "images")
    monkeypatch.setattr(helper, "ESXI_IPXE_HTTP_SCRIPT_PATH", http_base / "boot.ipxe")
    monkeypatch.setattr(helper, "ESXI_TFTP_ROOT", tftp_root)
    monkeypatch.setattr(helper, "PXE_BOOT_BINARY_DIRS", [ipxe_binary_dir])
    monkeypatch.setattr(helper, "ESXI_PXE_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "ESXI_INSTALLER_ISO_ROOT", iso_root)
    monkeypatch.setattr(helper, "ESXI_PXE_NGINX_SITE_PATH", tmp_path / "nginx" / "sites.d" / "esxi-pxe.conf")
    monkeypatch.setattr(helper, "_install_nginx_site", lambda path, text: (path.parent.mkdir(parents=True, exist_ok=True), path.write_text(text, encoding="utf-8"), 0)[2])

    payload = helper._load_esxi_pxe_manifest(helper._validate_esxi_pxe_config_path(str(config_path)))
    assert helper._esxi_pxe_manifest_errors(payload) == []
    assert helper._apply_esxi_pxe_manifest(payload) == 0
    generated_kickstart = Path(manifest["kickstarts"][0]["generated_path"])
    assert generated_kickstart.read_text(encoding="utf-8") == manifest["kickstarts"][0]["content"]
    assert (tftp_root / "undionly.kpxe").read_bytes() == b"bios ipxe"
    assert (tftp_root / "snponly.efi").read_bytes() == b"uefi ipxe"
    assert (tftp_root / "pxelinux.0").read_bytes() == b"pxelinux"
    assert (tftp_root / "ldlinux.c32").read_bytes() == b"ldlinux"
    assert (tftp_root / "mboot.efi").read_bytes() == b"mboot efi"
    assert (http_base / "mboot.efi").read_bytes() == b"mboot efi"
    assert (http_base / "boot.ipxe").read_text(encoding="utf-8") == "#!ipxe\necho LabFoundry PXE ready\nshell\n"
    assert not (tftp_root / "bootx64.efi").exists()
    assert not (tftp_root / "esxi.ipxe").exists()
    assert not (tftp_root / "pxelinux.cfg" / stale_mac).exists()
    assert not (tftp_root / stale_mac / "boot.cfg").exists()
    assert not (http_base / stale_mac / "boot.cfg").exists()
    assert (tftp_root / "images" / manifest["artifacts"][0]["image_key"] / "mboot.c32").read_bytes() == b"mboot c32"
    assert (tftp_root / "01-00-50-56-aa-bb-cc" / "mboot.efi").read_bytes() == b"mboot efi"
    assert (tftp_root / "01-00-50-56-aa-bb-cc" / "crypto64.efi").read_bytes() == b"crypto"
    assert (http_base / "01-00-50-56-aa-bb-cc" / "mboot.efi").read_bytes() == b"mboot efi"
    assert (http_base / "01-00-50-56-aa-bb-cc" / "crypto64.efi").read_bytes() == b"crypto"
    boot_cfg = (tftp_root / "01-00-50-56-aa-bb-cc" / "boot.cfg").read_text(encoding="utf-8")
    http_boot_cfg = (http_base / "01-00-50-56-aa-bb-cc" / "boot.cfg").read_text(encoding="utf-8")
    assert f"prefix={manifest['artifacts'][0]['image_http_url']}" in boot_cfg
    assert http_boot_cfg == boot_cfg
    assert "kernel=b.b00" in boot_cfg
    assert f"kernelopt=runweasel ks={manifest['artifacts'][0]['kickstart_url']} BOOTIF=01-00-50-56-aa-bb-cc" in boot_cfg
    assert "modules=jumpstrt.gz---useropts.gz" in boot_cfg
    default_boot_cfg = (tftp_root / "boot.cfg").read_text(encoding="utf-8")
    assert "kernelopt=runweasel netdevice=vmnic0" in default_boot_cfg
    assert "ks=" not in default_boot_cfg
    assert "BOOTIF=" not in default_boot_cfg
    assert (http_base / "boot.cfg").read_text(encoding="utf-8") == default_boot_cfg
    pxelinux = (tftp_root / "pxelinux.cfg" / "01-00-50-56-aa-bb-cc").read_text(encoding="utf-8")
    assert "KERNEL images/" in pxelinux
    assert "IPAPPEND 2" in pxelinux
    nginx_site = (tmp_path / "nginx" / "sites.d" / "esxi-pxe.conf").read_text(encoding="utf-8")
    assert nginx_site.count("listen 8080;") == 1
    assert "location /pxe/esxi/ks/" in nginx_site
    assert "proxy_pass http://127.0.0.1:8000;" in nginx_site
    assert f"alias {http_base}/;" in nginx_site
    assert not stale.exists()

    manifest["hosts"][0]["installer_iso_path"] = str(tmp_path / "escape.iso")
    assert any("installer ISO must be under" in error for error in helper._esxi_pxe_manifest_errors(manifest))


def test_esxi_pxe_helper_writes_http_ipxe_script_without_profiles(monkeypatch, tmp_path):
    helper = load_helper_module()
    http_root = tmp_path / "pxe" / "http" / "esxi" / "ks"
    http_base = http_root.parent
    tftp_root = tmp_path / "pxe" / "tftp"
    apply_dir = tmp_path / "apply" / "esxi-pxe"
    iso_root = tmp_path / "vcf-depot" / "PROD" / "COMP" / "ESX_HOST"
    ipxe_binary_dir = tmp_path / "bootloaders"
    http_root.mkdir(parents=True)
    apply_dir.mkdir(parents=True)
    iso_root.mkdir(parents=True)
    ipxe_binary_dir.mkdir(parents=True)
    (ipxe_binary_dir / "undionly.kpxe").write_bytes(b"bios ipxe")
    (ipxe_binary_dir / "snponly.efi").write_bytes(b"uefi ipxe")
    (ipxe_binary_dir / "pxelinux.0").write_bytes(b"pxelinux")
    (ipxe_binary_dir / "ldlinux.c32").write_bytes(b"ldlinux")
    manifest = {
        "kind": "labfoundry-esxi-pxe",
        "schema_version": 2,
        "http_root": str(http_root),
        "http_base": str(http_base),
        "image_http_root": str(http_base / "images"),
        "installer_iso_root": str(iso_root),
        "installer_isos": [],
        "boot": {
            "enabled": True,
            "hostname": "esxi-pxe.labfoundry.internal",
            "listen_interface": "eth1",
            "listen_address": "192.168.50.1",
            "tftp_root": str(tftp_root),
            "bios_bootfile": "undionly.kpxe",
            "uefi_bootfile": "snponly.efi",
            "bios_second_stage_bootfile": "pxelinux.0",
            "uefi_second_stage_bootfile": "mboot.efi",
            "native_uefi_bootfile": "mboot.efi",
            "http_port": 8080,
            "http_base_url": "http://192.168.50.1:8080/pxe/esxi",
            "native_uefi_http_enabled": True,
            "effective_native_uefi_http_url": "http://192.168.50.1:8080/pxe/esxi/mboot.efi",
            "ipxe_script": "#!ipxe\necho No profiles yet\nshell\n",
        },
        "kickstarts": [],
        "hosts": [],
        "artifacts": [],
    }
    config_path = apply_dir / "labfoundry-esxi-pxe.json"
    config_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_ROOT", http_root)
    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_BASE", http_base)
    monkeypatch.setattr(helper, "ESXI_PXE_IMAGE_HTTP_ROOT", http_base / "images")
    monkeypatch.setattr(helper, "ESXI_IPXE_HTTP_SCRIPT_PATH", http_base / "boot.ipxe")
    monkeypatch.setattr(helper, "ESXI_TFTP_ROOT", tftp_root)
    monkeypatch.setattr(helper, "PXE_BOOT_BINARY_DIRS", [ipxe_binary_dir])
    monkeypatch.setattr(helper, "ESXI_PXE_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "ESXI_INSTALLER_ISO_ROOT", iso_root)
    monkeypatch.setattr(helper, "ESXI_PXE_NGINX_SITE_PATH", tmp_path / "nginx" / "sites.d" / "esxi-pxe.conf")
    monkeypatch.setattr(helper, "_install_nginx_site", lambda path, text: (path.parent.mkdir(parents=True, exist_ok=True), path.write_text(text, encoding="utf-8"), 0)[2])

    payload = helper._load_esxi_pxe_manifest(helper._validate_esxi_pxe_config_path(str(config_path)))
    assert helper._esxi_pxe_manifest_errors(payload) == []
    assert helper._apply_esxi_pxe_manifest(payload) == 0

    assert (http_base / "boot.ipxe").read_text(encoding="utf-8") == "#!ipxe\necho No profiles yet\nshell\n"
    assert (tftp_root / "undionly.kpxe").read_bytes() == b"bios ipxe"
    assert (tftp_root / "snponly.efi").read_bytes() == b"uefi ipxe"
    assert (tftp_root / "pxelinux.0").read_bytes() == b"pxelinux"
    assert (tftp_root / "ldlinux.c32").read_bytes() == b"ldlinux"


def test_esxi_pxe_helper_does_not_copy_host_artifact_to_default_fallback(monkeypatch, tmp_path):
    helper = load_helper_module()
    http_root = tmp_path / "pxe" / "http" / "esxi" / "ks"
    http_base = http_root.parent
    tftp_root = tmp_path / "pxe" / "tftp"
    apply_dir = tmp_path / "apply" / "esxi-pxe"
    iso_root = tmp_path / "vcf-depot" / "PROD" / "COMP" / "ESX_HOST"
    ipxe_binary_dir = tmp_path / "bootloaders"
    http_root.mkdir(parents=True)
    http_base.mkdir(parents=True, exist_ok=True)
    tftp_root.mkdir(parents=True)
    (tftp_root / "pxelinux.cfg").mkdir(parents=True)
    apply_dir.mkdir(parents=True)
    iso_root.mkdir(parents=True)
    ipxe_binary_dir.mkdir(parents=True)
    (ipxe_binary_dir / "undionly.kpxe").write_bytes(b"bios ipxe")
    (ipxe_binary_dir / "snponly.efi").write_bytes(b"uefi ipxe")
    (ipxe_binary_dir / "pxelinux.0").write_bytes(b"pxelinux")
    (ipxe_binary_dir / "ldlinux.c32").write_bytes(b"ldlinux")
    (tftp_root / "boot.cfg").write_text("stale default", encoding="utf-8")
    (http_base / "boot.cfg").write_text("stale default", encoding="utf-8")
    (tftp_root / "pxelinux.cfg" / "default").write_text("stale default", encoding="utf-8")
    iso_tree = iso_root / "VMware-VMvisor-Installer-8.0U3.iso"
    iso_tree.mkdir()
    (iso_tree / "boot.cfg").write_text(
        "kernel=b.b00\nkernelopt=runweasel\nmodules=jumpstrt.gz --- useropts.gz\n",
        encoding="utf-8",
    )
    (iso_tree / "mboot.c32").write_bytes(b"mboot c32")
    (iso_tree / "EFI" / "BOOT").mkdir(parents=True)
    (iso_tree / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"mboot efi")
    manifest = esxi_pxe_manifest(http_root, iso_root=iso_root)
    manifest["boot"] = {
        "enabled": True,
        "hostname": "esxi-pxe.labfoundry.internal",
        "listen_interface": "eth1",
        "listen_address": "192.168.50.1",
        "tftp_root": str(tftp_root),
        "bios_bootfile": "undionly.kpxe",
        "uefi_bootfile": "snponly.efi",
        "bios_second_stage_bootfile": "pxelinux.0",
        "uefi_second_stage_bootfile": "mboot.efi",
        "native_uefi_bootfile": "mboot.efi",
        "http_port": 8080,
        "http_base_url": "http://192.168.50.1:8080/pxe/esxi",
        "native_uefi_http_enabled": True,
        "effective_native_uefi_http_url": "http://192.168.50.1:8080/pxe/esxi/mboot.efi",
        "ipxe_script": "#!ipxe\necho LabFoundry PXE ready\nshell\n",
    }
    config_path = apply_dir / "labfoundry-esxi-pxe.json"
    config_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_ROOT", http_root)
    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_BASE", http_base)
    monkeypatch.setattr(helper, "ESXI_PXE_IMAGE_HTTP_ROOT", http_base / "images")
    monkeypatch.setattr(helper, "ESXI_IPXE_HTTP_SCRIPT_PATH", http_base / "boot.ipxe")
    monkeypatch.setattr(helper, "ESXI_TFTP_ROOT", tftp_root)
    monkeypatch.setattr(helper, "PXE_BOOT_BINARY_DIRS", [ipxe_binary_dir])
    monkeypatch.setattr(helper, "ESXI_PXE_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "ESXI_INSTALLER_ISO_ROOT", iso_root)
    monkeypatch.setattr(helper, "ESXI_PXE_NGINX_SITE_PATH", tmp_path / "nginx" / "sites.d" / "esxi-pxe.conf")
    monkeypatch.setattr(helper, "_install_nginx_site", lambda path, text: (path.parent.mkdir(parents=True, exist_ok=True), path.write_text(text, encoding="utf-8"), 0)[2])

    payload = helper._load_esxi_pxe_manifest(helper._validate_esxi_pxe_config_path(str(config_path)))
    assert helper._esxi_pxe_manifest_errors(payload) == []
    assert helper._apply_esxi_pxe_manifest(payload) == 0

    assert not (tftp_root / "boot.cfg").exists()
    assert not (http_base / "boot.cfg").exists()
    assert not (tftp_root / "pxelinux.cfg" / "default").exists()
    host_boot_cfg = (tftp_root / "01-00-50-56-aa-bb-cc" / "boot.cfg").read_text(encoding="utf-8")
    assert "?mac=01-00-50-56-aa-bb-cc" in host_boot_cfg


def test_esxi_pxe_helper_rejects_disabled_kickstart_references(monkeypatch, tmp_path):
    helper = load_helper_module()
    http_root = tmp_path / "pxe" / "http" / "esxi" / "ks"
    http_base = http_root.parent
    tftp_root = tmp_path / "pxe" / "tftp"
    iso_root = tmp_path / "vcf-depot" / "PROD" / "COMP" / "ESX_HOST"
    http_root.mkdir(parents=True)
    tftp_root.mkdir(parents=True)
    iso_root.mkdir(parents=True)
    iso_tree = iso_root / "VMware-VMvisor-Installer-8.0U3.iso"
    iso_tree.mkdir()

    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_ROOT", http_root)
    monkeypatch.setattr(helper, "ESXI_PXE_HTTP_BASE", http_base)
    monkeypatch.setattr(helper, "ESXI_PXE_IMAGE_HTTP_ROOT", http_base / "images")
    monkeypatch.setattr(helper, "ESXI_TFTP_ROOT", tftp_root)
    monkeypatch.setattr(helper, "ESXI_INSTALLER_ISO_ROOT", iso_root)
    manifest = esxi_pxe_manifest(http_root, enabled=True, iso_root=iso_root)
    manifest["kickstarts"][0]["enabled"] = False
    manifest["hosts"][0]["kickstart_id"] = 7
    manifest["artifacts"][0]["kickstart_id"] = 7

    errors = helper._esxi_pxe_manifest_errors(manifest)

    assert any("references disabled or missing Kickstart 7" in error for error in errors)


def test_esxi_boot_cfg_rewrite_uses_http_prefix_and_kickstart():
    helper = load_helper_module()
    source = "\n".join(
        [
            "title=ESXi",
            "kernel=/b.b00",
            "kernelopt=cdromBoot runweasel systemMediaSize=max",
            "modules=jumpstrt.gz --- /useropts.gz --- /features.gz",
            "",
        ]
    )

    rendered = helper._render_esxi_boot_cfg(
        source,
        prefix_url="http://192.168.50.1:8080/pxe/esxi/images/esx-9",
        kickstart_url="http://192.168.50.1:8080/pxe/esxi/ks/7.cfg",
        bootif="BOOTIF=01-00-50-56-aa-bb-cc",
    )

    assert "prefix=http://192.168.50.1:8080/pxe/esxi/images/esx-9" in rendered
    assert "kernel=b.b00" in rendered
    assert "cdromBoot" not in rendered
    assert "kernelopt=runweasel systemMediaSize=max ks=http://192.168.50.1:8080/pxe/esxi/ks/7.cfg BOOTIF=01-00-50-56-aa-bb-cc" in rendered
    assert "modules=jumpstrt.gz---useropts.gz---features.gz" in rendered

    default_rendered = helper._render_esxi_boot_cfg(
        source,
        prefix_url="http://192.168.50.1:8080/pxe/esxi/images/esx-9",
        kickstart_url="http://192.168.50.1:8080/pxe/esxi/ks/7.cfg",
        fallback_netdevice="vmnic0",
    )

    assert "BOOTIF=" not in default_rendered
    assert "kernelopt=runweasel systemMediaSize=max ks=http://192.168.50.1:8080/pxe/esxi/ks/7.cfg netdevice=vmnic0" in default_rendered


def test_esxi_uefi_bootloader_must_come_from_iso_efi_boot(tmp_path):
    helper = load_helper_module()
    image_root = tmp_path / "image"
    (image_root / "random").mkdir(parents=True)
    (image_root / "random" / "mboot.efi").write_bytes(b"wrong")

    assert helper._find_esxi_uefi_bootloader(image_root) is None

    (image_root / "EFI" / "BOOT").mkdir(parents=True)
    expected = image_root / "EFI" / "BOOT" / "BOOTX64.EFI"
    expected.write_bytes(b"right")

    assert helper._find_esxi_uefi_bootloader(image_root) == expected


def test_ca_helper_rejects_config_outside_apply_dir(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-ca.json"
    config_path.write_text(ca_payload_text(tmp_path / "etc" / "labfoundry"), encoding="utf-8")

    try:
        helper._validate_ca_config_path(str(config_path))
    except ValueError as exc:
        assert "CA config must be staged under" in str(exc)
    else:
        raise AssertionError("CA config outside apply directory should be rejected")


def test_ca_helper_validates_and_writes_managed_files(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "ca"
    managed_root = tmp_path / "etc" / "labfoundry"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-ca.json"
    config_path.write_text(ca_payload_text(managed_root), encoding="utf-8")

    monkeypatch.setattr(helper, "CA_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "_ca_key_matches_certificate", lambda certificate_pem, private_key_pem: True)

    assert helper._handle_ca("validate", [str(config_path)]) == 0
    assert helper._handle_ca("apply", [str(config_path)]) == 0

    root_ca = managed_root / "ca" / "root-ca.pem"
    crl_path = managed_root / "ca" / "labfoundry-ca.crl"
    key_path = managed_root / "kms" / "certs" / "kms.labfoundry.internal.key"
    assert root_ca.read_text(encoding="utf-8").startswith("-----BEGIN CERTIFICATE-----")
    assert crl_path.read_text(encoding="utf-8").startswith("-----BEGIN X509 CRL-----")
    assert key_path.read_text(encoding="utf-8").startswith("-----BEGIN PRIVATE KEY-----")
    if os.name != "nt":
        assert oct(key_path.stat().st_mode & 0o777) == "0o600"


def test_ca_helper_removes_stale_crl_when_publication_is_empty(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "ca"
    managed_root = tmp_path / "etc" / "labfoundry"
    apply_dir.mkdir(parents=True)
    payload = json.loads(ca_payload_text(managed_root))
    crl_path = managed_root / "ca" / "labfoundry-ca.crl"
    crl_path.parent.mkdir(parents=True)
    crl_path.write_text("-----BEGIN X509 CRL-----\nstale\n-----END X509 CRL-----\n", encoding="utf-8")
    payload["root"]["crl_pem"] = ""
    config_path = apply_dir / "labfoundry-ca.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(helper, "CA_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "_ca_key_matches_certificate", lambda certificate_pem, private_key_pem: True)

    assert helper._handle_ca("validate", [str(config_path)]) == 0
    assert helper._handle_ca("apply", [str(config_path)]) == 0
    assert not crl_path.exists()


def test_ca_helper_allows_csr_certificate_without_private_key(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "ca"
    managed_root = tmp_path / "etc" / "labfoundry"
    apply_dir.mkdir(parents=True)
    payload = json.loads(ca_payload_text(managed_root))
    payload["certificates"].append(
        {
            "common_name": "client-a.labfoundry.internal",
            "managed_owner": "",
            "certificate_pem": "-----BEGIN CERTIFICATE-----\nclient\n-----END CERTIFICATE-----\n",
            "chain_pem": "-----BEGIN CERTIFICATE-----\nclient\n-----END CERTIFICATE-----\n",
            "private_key_pem": "",
            "cert_path": str(managed_root / "ca" / "client-a.crt"),
            "key_path": "",
            "chain_path": str(managed_root / "ca" / "client-a-chain.pem"),
        }
    )
    config_path = apply_dir / "labfoundry-ca.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(helper, "CA_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "_ca_key_matches_certificate", lambda certificate_pem, private_key_pem: True)

    assert helper._handle_ca("validate", [str(config_path)]) == 0
    assert helper._handle_ca("apply", [str(config_path)]) == 0

    assert (managed_root / "ca" / "client-a.crt").read_text(encoding="utf-8").startswith("-----BEGIN CERTIFICATE-----")
    assert not (managed_root / "ca" / "client-a.key").exists()


def test_ca_helper_rejects_key_path_without_private_key(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "ca"
    managed_root = tmp_path / "etc" / "labfoundry"
    apply_dir.mkdir(parents=True)
    payload = json.loads(ca_payload_text(managed_root))
    payload["certificates"][0]["private_key_pem"] = ""
    config_path = apply_dir / "labfoundry-ca.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(helper, "CA_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)

    errors = helper._ca_payload_errors(config_path)

    assert "certificate kms.labfoundry.internal key_path requires a private key." in errors


def test_wan_helper_apply_routes_nat_and_netem(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "wan"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-wan.conf"
    config_path.write_text(wan_config_text(), encoding="utf-8")
    nat_dir = tmp_path / "nftables.d"
    service_path = tmp_path / "labfoundry-nat.service"
    sysctl_path = tmp_path / "90-labfoundry-routing-wan.conf"
    commands: list[list[str]] = []
    input_commands: list[tuple[list[str], str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_run_with_input(command: list[str], input_text: str) -> subprocess.CompletedProcess[str]:
        input_commands.append((command, input_text))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "WAN_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "WAN_NAT_CONFIG_DIR", nat_dir)
    monkeypatch.setattr(helper, "WAN_NAT_CONFIG_PATH", nat_dir / "labfoundry-nat.nft")
    monkeypatch.setattr(helper, "WAN_NAT_SERVICE_PATH", service_path)
    monkeypatch.setattr(helper, "WAN_SYSCTL_PATH", sysctl_path)
    monkeypatch.setattr(helper.shutil, "which", lambda command: f"/usr/sbin/{command}")
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_run_with_input", fake_run_with_input)

    assert helper._handle_wan("apply", [str(config_path)]) == 0

    assert input_commands[0][0] == ["nft", "-c", "-f", "-"]
    assert 'oifname "eth1.20" masquerade' in input_commands[0][1]
    assert ["sysctl", "-w", "net.ipv4.ip_forward=1"] in commands
    assert ["nft", "-f", str(nat_dir / "labfoundry-nat.nft")] in commands
    assert ["ip", "route", "replace", "192.168.20.0/24", "dev", "eth1.20", "table", "200"] in commands
    assert ["ip", "rule", "add", "from", "192.168.20.0/24", "table", "200", "priority", "2000"] in commands
    assert ["ip", "route", "replace", "10.20.0.0/24", "dev", "eth1.20", "metric", "120", "table", "200"] in commands
    assert ["tc", "qdisc", "replace", "dev", "eth1.20", "root", "netem", "delay", "100ms", "10ms", "loss", "0.5%", "rate", "100mbit"] in commands
    assert service_path.exists()
    assert sysctl_path.read_text(encoding="utf-8") == "net.ipv4.ip_forward = 1\n"


def test_real_mutating_helper_action_escapes_service_mount_namespace(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry.conf"
    config_path.write_text("# staged dnsmasq config\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return "/usr/bin/systemd-run" if command == "systemd-run" else None

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "child helper output\n", "")

    monkeypatch.setenv("LABFOUNDRY_HELPER_USE_SYSTEMD_RUN", "1")
    monkeypatch.delenv(helper.SYSTEMD_RUN_CHILD_ENV, raising=False)
    monkeypatch.setattr(helper.shutil, "which", fake_which)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_handle_dnsmasq", lambda action, args: (_ for _ in ()).throw(AssertionError("handler should run in child")))

    assert helper.main(["labfoundry-helper", "dnsmasq", "apply", "--real", str(config_path)]) == 0

    out = capsys.readouterr().out
    assert out == "child helper output\n"
    assert len(commands) == 1
    assert commands[0][:7] == [
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--service-type=exec",
        f"--setenv={helper.SYSTEMD_RUN_CHILD_ENV}=1",
    ]
    assert commands[0][-4:] == ["dnsmasq", "apply", "--real", str(config_path)]


def test_network_helper_renders_systemd_networkd_files(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(), encoding="utf-8")

    files, links, admin_down_links = helper._systemd_networkd_files(config_path)

    assert "00-labfoundry-mgmt.network" in files
    assert "Name=eth0" in files["00-labfoundry-mgmt.network"]
    assert "Name=eth*" not in files["00-labfoundry-mgmt.network"]
    assert "Address=192.168.49.1/24" in files["00-labfoundry-mgmt.network"]
    assert "[RoutingPolicyRule]" in files["00-labfoundry-mgmt.network"]
    assert "From=192.168.49.0/24" in files["00-labfoundry-mgmt.network"]
    assert "Table=100" in files["00-labfoundry-mgmt.network"]
    assert "10-labfoundry-eth2.network" in files
    assert "VLAN=eth2.20" in files["10-labfoundry-eth2.network"]
    assert "10-labfoundry-eth2.20.netdev" in files
    assert "Id=20" in files["10-labfoundry-eth2.20.netdev"]
    assert "10-labfoundry-eth2.20.network" in files
    assert "Address=192.168.20.1/24" in files["10-labfoundry-eth2.20.network"]
    assert links == ["eth2", "eth2.20"]
    assert admin_down_links == []


def test_network_helper_keeps_admin_down_physical_links_unmanaged(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(eth2_mode="access", eth2_admin_state="down", include_vlan=False), encoding="utf-8")

    files, links, admin_down_links = helper._systemd_networkd_files(config_path)

    assert "00-labfoundry-mgmt.network" in files
    assert "10-labfoundry-eth2.network" not in files
    assert links == []
    assert admin_down_links == ["eth2"]


def test_network_helper_installs_networkd_files_and_reconfigures_non_management(monkeypatch, tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(), encoding="utf-8")
    networkd_dir = tmp_path / "systemd-network"
    networkd_dir.mkdir()
    old_managed = networkd_dir / "10-labfoundry-old.network"
    old_managed.write_text("old", encoding="utf-8")
    old_default = networkd_dir / "99-dhcp-en.network"
    old_default.write_text("old default", encoding="utf-8")
    commands: list[list[str]] = []
    stdin_commands: list[tuple[list[str], str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_run_with_input(command: list[str], stdin_text: str) -> subprocess.CompletedProcess[str]:
        stdin_commands.append((command, stdin_text))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "NETWORKD_CONFIG_DIR", networkd_dir)
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", networkd_dir / "00-labfoundry-mgmt.network")
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/bin/networkctl" if command == "networkctl" else None)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_link_exists", lambda name: True)

    returncode, installed, links, admin_down_links = helper._install_systemd_networkd_files(config_path)

    assert returncode == 0
    assert not old_managed.exists()
    assert not old_default.exists()
    assert (networkd_dir / "00-labfoundry-mgmt.network").is_file()
    assert (networkd_dir / "10-labfoundry-eth2.network").is_file()
    assert (networkd_dir / "10-labfoundry-eth2.20.netdev").is_file()
    assert ["networkctl", "reload"] in commands
    assert ["networkctl", "reconfigure", "eth2"] in commands
    assert ["networkctl", "reconfigure", "eth2.20"] in commands
    assert ["networkctl", "reconfigure", "eth0"] not in commands
    assert any(path.endswith("00-labfoundry-mgmt.network") for path in installed)
    assert links == ["eth2", "eth2.20"]
    assert admin_down_links == []


def test_network_helper_sets_admin_down_links_down_after_reload(monkeypatch, tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(eth2_mode="access", eth2_admin_state="down", include_vlan=False), encoding="utf-8")
    networkd_dir = tmp_path / "systemd-network"
    networkd_dir.mkdir()
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in {"networkctl", "ip"} else None

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "NETWORKD_CONFIG_DIR", networkd_dir)
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", networkd_dir / "00-labfoundry-mgmt.network")
    monkeypatch.setattr(helper.shutil, "which", fake_which)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_link_exists", lambda name: True)

    returncode, _installed, links, admin_down_links = helper._install_systemd_networkd_files(config_path)

    assert returncode == 0
    assert links == []
    assert admin_down_links == ["eth2"]
    assert ["ip", "link", "set", "dev", "eth2", "down"] in commands


def test_network_helper_sets_vlan_ip_after_link_up_and_flush(monkeypatch, tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/ip" if command == "ip" else None)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._apply_vlan_interfaces(config_path) == 0

    assert ["ip", "link", "set", "dev", "eth2.20", "up"] in commands
    assert ["ip", "address", "flush", "dev", "eth2.20", "scope", "global"] in commands
    assert ["ip", "address", "replace", "192.168.20.1/24", "dev", "eth2.20"] in commands
    assert commands.index(["ip", "link", "set", "dev", "eth2.20", "up"]) < commands.index(
        ["ip", "address", "flush", "dev", "eth2.20", "scope", "global"]
    )
    assert commands.index(["ip", "address", "flush", "dev", "eth2.20", "scope", "global"]) < commands.index(
        ["ip", "address", "replace", "192.168.20.1/24", "dev", "eth2.20"]
    )


def test_network_helper_deletes_removed_vlan_links(monkeypatch, tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(include_vlan=False, include_removed_vlan=True), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:5] == ["ip", "-j", "-d", "link", "show"]:
            return subprocess.CompletedProcess(command, 0, '[{"linkinfo":{"info_kind":"vlan"}}]', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/ip" if command == "ip" else None)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._apply_vlan_interfaces(config_path) == 0

    assert ["ip", "link", "show", "dev", "eth2.20"] in commands
    assert ["ip", "-j", "-d", "link", "show", "dev", "eth2.20"] in commands
    assert ["ip", "link", "delete", "dev", "eth2.20"] in commands


def test_network_helper_refuses_to_delete_non_vlan_link(monkeypatch, tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(include_vlan=False, include_removed_vlan=True), encoding="utf-8")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:5] == ["ip", "-j", "-d", "link", "show"]:
            return subprocess.CompletedProcess(command, 0, '[{"linkinfo":{"info_kind":"dummy"}}]', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/ip" if command == "ip" else None)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._apply_vlan_interfaces(config_path) == 2


def test_kms_helper_rejects_config_outside_apply_dir(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "pykmip.conf"
    config_path.write_text(kms_config_text(tmp_path), encoding="utf-8")

    assert helper._handle_kms("validate", [str(config_path)]) == 2


def test_kms_helper_validates_disabled_staged_config(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "kms"
    state_dir = tmp_path / "state" / "kms"
    managed_root = tmp_path / "etc" / "labfoundry"
    apply_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    config_path = apply_dir / "pykmip.conf"
    config_path.write_text(kms_config_text(managed_root, enabled=False, database_path=state_dir / "pykmip.db"), encoding="utf-8")

    monkeypatch.setattr(helper, "KMS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "KMS_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "KMS_CONFIG_DIR", managed_root / "kms")
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)

    assert helper._handle_kms("validate", [str(config_path)]) == 0


def test_kms_helper_apply_installs_pykmip_service(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "kms"
    state_dir = tmp_path / "state" / "kms"
    log_dir = tmp_path / "log" / "kms"
    managed_root = tmp_path / "etc" / "labfoundry"
    pykmip_dir = tmp_path / "etc" / "pykmip"
    service_path = tmp_path / "systemd" / "labfoundry-kms.service"
    config_path = apply_dir / "pykmip.conf"
    cert_path = managed_root / "kms" / "certs" / "kms.labfoundry.internal.crt"
    key_path = managed_root / "kms" / "certs" / "kms.labfoundry.internal.key"
    ca_path = managed_root / "ca" / "root.crt"
    apply_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    cert_path.parent.mkdir(parents=True)
    ca_path.parent.mkdir(parents=True)
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n", encoding="utf-8")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    ca_path.write_text("-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----\n", encoding="utf-8")
    config_path.write_text(kms_config_text(managed_root, database_path=state_dir / "pykmip.db"), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "KMS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "KMS_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "KMS_LOG_DIR", log_dir)
    monkeypatch.setattr(helper, "KMS_CONFIG_DIR", managed_root / "kms")
    monkeypatch.setattr(helper, "KMS_CONFIG_PATH", managed_root / "kms" / "pykmip.conf")
    monkeypatch.setattr(helper, "KMS_POLICY_DIR", managed_root / "kms" / "policies")
    monkeypatch.setattr(helper, "KMS_SERVICE_PATH", service_path)
    monkeypatch.setattr(helper, "PYKMIP_CONFIG_DIR", pykmip_dir)
    monkeypatch.setattr(helper, "PYKMIP_CONFIG_PATH", pykmip_dir / "server.conf")
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/opt/labfoundry/.venv/bin/pykmip-server" if command == "pykmip-server" else None)

    assert helper._handle_kms("apply", [str(config_path)]) == 0

    assert (managed_root / "kms" / "pykmip.conf").is_file()
    assert (pykmip_dir / "server.conf").is_file()
    assert "pykmip_compat_server.py" in service_path.read_text(encoding="utf-8")
    assert ["systemctl", "daemon-reload"] in commands
    assert ["systemctl", "enable", "labfoundry-kms.service"] in commands
    assert ["systemctl", "restart", "labfoundry-kms.service"] in commands


def test_dnsmasq_helper_validates_staged_config(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "dnsmasq"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry.conf"
    config_path.write_text("domain=labfoundry.internal\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "dnsmasq: syntax check OK.\n", "")

    monkeypatch.setattr(helper, "DNSMASQ_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/dnsmasq" if command == "dnsmasq" else None)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_dnsmasq("validate", [str(config_path)]) == 0

    assert commands == [["/usr/sbin/dnsmasq", "--test", f"--conf-file={config_path}"]]


def test_dnsmasq_helper_apply_installs_config_dropin_and_enables_service(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "dnsmasq"
    config_dir = tmp_path / "etc" / "labfoundry" / "dnsmasq.d"
    dropin_dir = tmp_path / "etc" / "systemd" / "system" / "dnsmasq.service.d"
    networkd_dir = tmp_path / "etc" / "systemd" / "network"
    apply_dir.mkdir(parents=True)
    networkd_dir.mkdir(parents=True)
    mgmt_network = networkd_dir / "00-labfoundry-mgmt.network"
    mgmt_network.write_text(
        "\n".join(
            [
                "[Match]",
                "Name=eth0",
                "",
                "[Network]",
                "Address=192.168.49.1/24",
                "Gateway=192.168.49.254",
                "DNS=1.1.1.1",
                "DNS=9.9.9.9",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = apply_dir / "labfoundry.conf"
    config_path.write_text("domain=labfoundry.internal\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "DNSMASQ_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "DNSMASQ_CONFIG_DIR", config_dir)
    monkeypatch.setattr(helper, "DNSMASQ_CONFIG_PATH", config_dir / "labfoundry.conf")
    monkeypatch.setattr(helper, "DNSMASQ_SERVICE_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "DNSMASQ_SERVICE_DROPIN_PATH", dropin_dir / "labfoundry.conf")
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", mgmt_network)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/dnsmasq" if command == "dnsmasq" else None)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_dnsmasq("apply", [str(config_path)]) == 0

    assert (config_dir / "labfoundry.conf").read_text(encoding="utf-8") == "domain=labfoundry.internal\n"
    dropin = (dropin_dir / "labfoundry.conf").read_text(encoding="utf-8")
    assert "ExecStart=" in dropin
    assert f"--conf-file={config_dir / 'labfoundry.conf'}" in dropin
    assert ["/usr/sbin/dnsmasq", "--test", f"--conf-file={config_path}"] in commands
    assert ["systemctl", "daemon-reload"] in commands
    assert ["systemctl", "enable", "dnsmasq"] in commands
    assert ["systemctl", "reload-or-restart", "dnsmasq"] in commands
    assert ["resolvectl", "dns", "eth0", "127.0.0.1"] not in commands
    assert ["resolvectl", "domain", "eth0", "~."] not in commands
    assert "DNS=1.1.1.1" in mgmt_network.read_text(encoding="utf-8")
    assert "DNS=127.0.0.1" not in mgmt_network.read_text(encoding="utf-8")
    assert "Domains=~." not in mgmt_network.read_text(encoding="utf-8")


def test_dnsmasq_helper_apply_creates_allowlisted_tftp_root(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "dnsmasq"
    config_dir = tmp_path / "etc" / "labfoundry" / "dnsmasq.d"
    dropin_dir = tmp_path / "etc" / "systemd" / "system" / "dnsmasq.service.d"
    tftp_root = tmp_path / "var" / "lib" / "labfoundry" / "pxe" / "tftp"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry.conf"
    config_path.write_text(f"enable-tftp\ntftp-root={tftp_root}\n", encoding="utf-8")
    commands: list[list[str]] = []
    chowned: list[Path] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "DNSMASQ_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "DNSMASQ_CONFIG_DIR", config_dir)
    monkeypatch.setattr(helper, "DNSMASQ_CONFIG_PATH", config_dir / "labfoundry.conf")
    monkeypatch.setattr(helper, "DNSMASQ_SERVICE_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "DNSMASQ_SERVICE_DROPIN_PATH", dropin_dir / "labfoundry.conf")
    monkeypatch.setattr(helper, "ESXI_TFTP_ROOT", tftp_root)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/dnsmasq" if command == "dnsmasq" else None)
    monkeypatch.setattr(helper.shutil, "chown", lambda path, user, group: chowned.append(Path(path)))
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_dnsmasq("apply", [str(config_path)]) == 0

    assert tftp_root.is_dir()
    assert chowned == [tftp_root]
    assert ["systemctl", "reload-or-restart", "dnsmasq"] in commands


def test_dnsmasq_helper_apply_rejects_unexpected_tftp_root(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "dnsmasq"
    allowed_root = tmp_path / "var" / "lib" / "labfoundry" / "pxe" / "tftp"
    unexpected_root = tmp_path / "tmp" / "not-labfoundry"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry.conf"
    config_path.write_text(f"enable-tftp\ntftp-root={unexpected_root}\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "DNSMASQ_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "ESXI_TFTP_ROOT", allowed_root)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/dnsmasq" if command == "dnsmasq" else None)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_dnsmasq("apply", [str(config_path)]) == 2

    captured = capsys.readouterr()
    assert f"dnsmasq TFTP root must be {allowed_root}" in captured.err
    assert not unexpected_root.exists()
    assert ["systemctl", "reload-or-restart", "dnsmasq"] not in commands


def test_dnsmasq_helper_reload_restarts_service(monkeypatch):
    helper = load_helper_module()
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_dnsmasq("reload", []) == 0

    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "reload-or-restart", "dnsmasq"],
    ]


def test_dnsmasq_helper_reads_allowlisted_lease_file(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    state_dir = tmp_path / "var" / "lib" / "labfoundry" / "dnsmasq"
    state_dir.mkdir(parents=True)
    lease_file = state_dir / "dhcp.leases"
    lease_file.write_text("1893456000 02:15:5d:00:20:30 192.168.50.130 api-client *\n", encoding="utf-8")

    monkeypatch.setattr(helper, "DNSMASQ_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "DNSMASQ_LEASE_FILE_PATH", lease_file)

    assert helper._handle_dnsmasq("leases", []) == 0
    captured = capsys.readouterr()
    assert "api-client" in captured.out
    assert captured.err == ""


def test_dnsmasq_helper_missing_lease_file_is_empty_success(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    state_dir = tmp_path / "var" / "lib" / "labfoundry" / "dnsmasq"
    state_dir.mkdir(parents=True)

    monkeypatch.setattr(helper, "DNSMASQ_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "DNSMASQ_LEASE_FILE_PATH", state_dir / "dhcp.leases")

    assert helper._handle_dnsmasq("leases", []) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_dnsmasq_helper_rejects_lease_paths_outside_allowlisted_state(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    state_dir = tmp_path / "var" / "lib" / "labfoundry" / "dnsmasq"
    outside_file = tmp_path / "elsewhere" / "dhcp.leases"
    state_dir.mkdir(parents=True)
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("1893456000 02:15:5d:00:20:30 192.168.50.130 api-client *\n", encoding="utf-8")

    monkeypatch.setattr(helper, "DNSMASQ_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "DNSMASQ_LEASE_FILE_PATH", outside_file)

    assert helper._handle_dnsmasq("leases", []) == 2
    captured = capsys.readouterr()
    assert "dnsmasq lease file must stay under" in captured.err


def local_users_json(*, username: str = "sync-user", enabled: bool = True, password: str | None = "BridgeStrong1!") -> str:
    row = {
        "username": username,
        "role": "viewer",
        "enabled": enabled,
        "home": f"/var/lib/labfoundry/users/{username}",
        "shell": "/sbin/nologin",
        "password_pending": bool(password),
        "password_pending_since": "2026-06-23T12:00:00+00:00" if password else "",
    }
    if password:
        row["password"] = password
    return json.dumps({"managed_by": "LabFoundry", "version": 1, "scope": "Photon OS local users", "users": [row]})


def test_local_users_helper_validates_staged_config(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    config_path.write_text(local_users_json(), encoding="utf-8")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)

    assert helper._handle_local_users("validate", [str(config_path)]) == 0
    captured = capsys.readouterr()
    assert '"local_users": "validation ok"' in captured.out
    assert '"passwords_pending": 1' in captured.out
    assert "BridgeStrong1!" not in captured.out


def test_local_users_helper_rejects_reserved_username(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    config_path.write_text(local_users_json(username="root"), encoding="utf-8")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)

    assert helper._handle_local_users("validate", [str(config_path)]) == 2
    captured = capsys.readouterr()
    assert "local user root is reserved" in captured.err


def test_local_users_helper_creates_deletes_and_sets_password_without_leaking(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    home_base = tmp_path / "users"
    pwquality_path = tmp_path / "etc" / "security" / "pwquality.conf"
    pam_path = tmp_path / "etc" / "pam.d" / "system-password"
    pam_path.parent.mkdir(parents=True)
    pam_path.write_text("password  required    pam_unix.so       sha512 shadow use_authtok\n", encoding="utf-8")
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    payload = json.loads(local_users_json())
    payload["users"][0]["home"] = (home_base / "sync-user").as_posix()
    payload["users"].append(
        {
            "username": "disabled-user",
            "role": "viewer",
            "enabled": False,
            "home": (home_base / "disabled-user").as_posix(),
            "shell": "/sbin/nologin",
            "password_pending": False,
            "password_pending_since": "",
        }
    )
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    commands: list[list[str]] = []
    stdin_values: list[str] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command == ["id", "sync-user"]:
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_run_with_input(command: list[str], input_text: str) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdin_values.append(input_text)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "LOCAL_USERS_HOME_BASE", home_base)
    monkeypatch.setattr(helper, "LOCAL_USERS_PWQUALITY_PATH", pwquality_path)
    monkeypatch.setattr(helper, "LOCAL_USERS_SYSTEM_PASSWORD_PAM_PATH", pam_path)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_run_with_input", fake_run_with_input)

    assert helper._handle_local_users("apply", [str(config_path)]) == 0
    captured = capsys.readouterr()

    assert ["useradd", "--home-dir", (home_base / "sync-user").as_posix(), "--create-home", "--shell", "/sbin/nologin", "sync-user"] in commands
    assert ["usermod", "--shell", "/sbin/nologin", "sync-user"] in commands
    assert ["passwd", "-u", "sync-user"] in commands
    assert ["userdel", "-r", "disabled-user"] in commands
    assert ["passwd", "-l", "disabled-user"] not in commands
    assert stdin_values == ["sync-user:BridgeStrong1!\n"]
    assert all("BridgeStrong1!" not in arg for command in commands for arg in command)
    assert "BridgeStrong1!" not in captured.out
    assert "BridgeStrong1!" not in captured.err
    assert "pam_pwquality.so" in pam_path.read_text(encoding="utf-8")
    assert "minlen = 12" in pwquality_path.read_text(encoding="utf-8")


def test_local_users_helper_applies_per_user_shell(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    home_base = tmp_path / "users"
    pwquality_path = tmp_path / "etc" / "security" / "pwquality.conf"
    pam_path = tmp_path / "etc" / "pam.d" / "system-password"
    pam_path.parent.mkdir(parents=True)
    pam_path.write_text("password  required    pam_pwquality.so  retry=3\npassword  required    pam_unix.so\n", encoding="utf-8")
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    payload = json.loads(local_users_json(password=None))
    payload["users"][0]["home"] = (home_base / "sync-user").as_posix()
    payload["users"][0]["shell"] = "/bin/bash"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "LOCAL_USERS_HOME_BASE", home_base)
    monkeypatch.setattr(helper, "LOCAL_USERS_PWQUALITY_PATH", pwquality_path)
    monkeypatch.setattr(helper, "LOCAL_USERS_SYSTEM_PASSWORD_PAM_PATH", pam_path)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_local_users("apply", [str(config_path)]) == 0
    assert ["usermod", "--shell", "/bin/bash", "sync-user"] in commands


def test_local_users_helper_keeps_admin_role_sudo_capable(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    home_base = tmp_path / "users"
    pwquality_path = tmp_path / "etc" / "security" / "pwquality.conf"
    pam_path = tmp_path / "etc" / "pam.d" / "system-password"
    pam_path.parent.mkdir(parents=True)
    pam_path.write_text("password  required    pam_pwquality.so  retry=3\npassword  required    pam_unix.so\n", encoding="utf-8")
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    payload = json.loads(local_users_json(username="admin", password=None))
    payload["users"][0]["role"] = "admin"
    payload["users"][0]["home"] = (home_base / "admin").as_posix()
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "LOCAL_USERS_HOME_BASE", home_base)
    monkeypatch.setattr(helper, "LOCAL_USERS_PWQUALITY_PATH", pwquality_path)
    monkeypatch.setattr(helper, "LOCAL_USERS_SYSTEM_PASSWORD_PAM_PATH", pam_path)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_local_users("apply", [str(config_path)]) == 0
    assert ["usermod", "--shell", "/sbin/nologin", "admin"] in commands
    assert ["usermod", "--append", "--groups", "wheel", "admin"] in commands


def test_local_users_helper_removes_wheel_on_admin_downgrade(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    home_base = tmp_path / "users"
    pwquality_path = tmp_path / "etc" / "security" / "pwquality.conf"
    pam_path = tmp_path / "etc" / "pam.d" / "system-password"
    pam_path.parent.mkdir(parents=True)
    pam_path.write_text("password  required    pam_pwquality.so  retry=3\npassword  required    pam_unix.so\n", encoding="utf-8")
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    payload = json.loads(local_users_json(username="downgraded-user", password=None))
    payload["users"][0]["role"] = "viewer"
    payload["users"][0]["home"] = (home_base / "downgraded-user").as_posix()
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command == ["id", "-nG", "downgraded-user"]:
            return subprocess.CompletedProcess(command, 0, "downgraded-user wheel", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "LOCAL_USERS_HOME_BASE", home_base)
    monkeypatch.setattr(helper, "LOCAL_USERS_PWQUALITY_PATH", pwquality_path)
    monkeypatch.setattr(helper, "LOCAL_USERS_SYSTEM_PASSWORD_PAM_PATH", pam_path)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_local_users("apply", [str(config_path)]) == 0
    assert ["gpasswd", "--delete", "downgraded-user", "wheel"] in commands


def test_local_users_helper_allows_powershell_shell(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    payload = json.loads(local_users_json(password=None))
    payload["users"][0]["shell"] = "/usr/bin/pwsh"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)

    assert helper._handle_local_users("validate", [str(config_path)]) == 0
    captured = capsys.readouterr()
    assert '"local_users": "validation ok"' in captured.out


def test_local_users_helper_unlock_request_resets_passwd_and_faillock(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    home_base = tmp_path / "users"
    pwquality_path = tmp_path / "etc" / "security" / "pwquality.conf"
    pam_path = tmp_path / "etc" / "pam.d" / "system-password"
    pam_path.parent.mkdir(parents=True)
    pam_path.write_text("password  required    pam_pwquality.so  retry=3\npassword  required    pam_unix.so\n", encoding="utf-8")
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    payload = json.loads(local_users_json(password=None))
    payload["users"][0]["home"] = (home_base / "sync-user").as_posix()
    payload["users"][0]["unlock_requested"] = True
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "LOCAL_USERS_HOME_BASE", home_base)
    monkeypatch.setattr(helper, "LOCAL_USERS_PWQUALITY_PATH", pwquality_path)
    monkeypatch.setattr(helper, "LOCAL_USERS_SYSTEM_PASSWORD_PAM_PATH", pam_path)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_local_users("apply", [str(config_path)]) == 0
    assert ["passwd", "-u", "sync-user"] in commands
    assert ["faillock", "--user", "sync-user", "--reset"] in commands
    assert ["chpasswd"] not in commands


def test_local_users_helper_status_reports_faillock_blocked(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    config_path.write_text(local_users_json(password=None), encoding="utf-8")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command == ["id", "sync-user"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["passwd", "-S", "sync-user"]:
            return subprocess.CompletedProcess(command, 0, "sync-user L 2026-06-23 0 99999 7 -1\n", "")
        if command == ["faillock", "--user", "sync-user"]:
            return subprocess.CompletedProcess(command, 0, "sync-user:\nWhen                Type  Source                                           Valid\n2026-06-23 10:00   TTY   ssh                                              V\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_local_users("status", [str(config_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["users"][0]["username"] == "sync-user"
    assert payload["users"][0]["state"] == "faillock blocked"


def test_local_users_helper_status_does_not_block_on_zero_faillock_failures(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-users.json"
    config_path.write_text(local_users_json(password=None), encoding="utf-8")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command == ["id", "sync-user"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["passwd", "-S", "sync-user"]:
            return subprocess.CompletedProcess(command, 0, "sync-user P 2026-06-23 0 99999 7 -1\n", "")
        if command == ["faillock", "--user", "sync-user"]:
            return subprocess.CompletedProcess(command, 0, "Login           Failures    Latest failure         From\nsync-user           0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_local_users("status", [str(config_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["users"][0]["state"] == "present"


def vcf_backups_config_text(*, enabled: bool = True) -> str:
    if not enabled:
        return "\n".join(
            [
                "# Managed by LabFoundry. Local changes may be overwritten.",
                "# LabFoundry VCF Backups enabled: false",
                "# LabFoundry VCF Backups user: vcf-backup",
                "# Backup volume mount: /mnt/labfoundry-vcf-backups",
                "# VCF remote directory: /backups",
                "# VCF Backup SFTP desired state is disabled.",
                "",
            ]
        )
    return "\n".join(
        [
            "# Managed by LabFoundry. Local changes may be overwritten.",
            "# LabFoundry VCF Backups enabled: true",
            "# LabFoundry VCF Backups user: vcf-backup",
            "# Backup volume mount: /mnt/labfoundry-vcf-backups",
            "# VCF remote directory: /backups",
            "# The selected listen target is enforced by the LabFoundry firewall apply unit.",
            "",
            "# Service listener target: 192.168.50.1:22",
            "Match User vcf-backup",
            "  AuthorizedKeysFile /etc/labfoundry/ssh/authorized_keys/%u",
            "  ChrootDirectory /mnt/labfoundry-vcf-backups",
            "  ForceCommand internal-sftp -d /backups",
            "  PasswordAuthentication yes",
            "  PubkeyAuthentication yes",
            "  MaxSessions 4",
            "  PermitTTY no",
            "  PermitTunnel no",
            "  AllowAgentForwarding no",
            "  AllowTcpForwarding no",
            "  X11Forwarding no",
            "",
        ]
    )


def test_vcf_backups_helper_validates_staged_config(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-backups"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-vcf-backups-sshd.conf"
    config_path.write_text(vcf_backups_config_text(), encoding="utf-8")

    monkeypatch.setattr(helper, "VCF_BACKUPS_APPLY_DIR", apply_dir)

    assert helper._handle_vcf_backups("validate", [str(config_path)]) == 0
    captured = capsys.readouterr()
    assert '"vcf_backups": "validation ok"' in captured.out
    assert '"username": "vcf-backup"' in captured.out


def test_vcf_backups_helper_rejects_unmanaged_config(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-vcf-backups-sshd.conf"
    config_path.write_text("Match User root\n", encoding="utf-8")

    errors = helper._vcf_backups_config_errors(config_path)

    assert "VCF backups config must be rendered by LabFoundry." in errors


def test_vcf_backups_helper_apply_installs_sshd_dropin_and_storage(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-backups"
    config_dir = tmp_path / "etc" / "ssh" / "sshd_config.d"
    labfoundry_ssh_dir = tmp_path / "etc" / "labfoundry" / "ssh" / "authorized_keys"
    storage_path = tmp_path / "mnt" / "labfoundry-vcf-backups"
    sshd_config = tmp_path / "etc" / "ssh" / "sshd_config"
    apply_dir.mkdir(parents=True)
    sshd_config.parent.mkdir(parents=True)
    sshd_config.write_text("Subsystem sftp internal-sftp\n", encoding="utf-8")
    config_path = apply_dir / "labfoundry-vcf-backups-sshd.conf"
    config_path.write_text(vcf_backups_config_text(), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "VCF_BACKUPS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "VCF_BACKUPS_CONFIG_DIR", config_dir)
    monkeypatch.setattr(helper, "VCF_BACKUPS_CONFIG_PATH", config_dir / "labfoundry-vcf-backups.conf")
    monkeypatch.setattr(helper, "VCF_BACKUPS_AUTHORIZED_KEYS_DIR", labfoundry_ssh_dir)
    def fake_path(value):
        if value == "/etc/ssh/sshd_config":
            return sshd_config
        if value == "/mnt/labfoundry-vcf-backups":
            return storage_path
        return Path(value)

    monkeypatch.setattr(helper, "Path", fake_path)
    monkeypatch.setattr(helper, "_chown_path", lambda path, uid, gid: None)
    monkeypatch.setattr(helper.shutil, "which", lambda command: {"id": "id", "sshd": "sshd"}.get(command))
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_vcf_backups("apply", [str(config_path)]) == 0

    assert (config_dir / "labfoundry-vcf-backups.conf").is_file()
    assert "Match User vcf-backup" in (config_dir / "labfoundry-vcf-backups.conf").read_text(encoding="utf-8")
    assert (storage_path / "backups").is_dir()
    assert (labfoundry_ssh_dir / "vcf-backup").is_file()
    assert sshd_config.read_text(encoding="utf-8").startswith("Include /etc/ssh/sshd_config.d/*.conf\n")
    assert ["id", "vcf-backup"] in commands
    assert all(arg != "labfoundry-vcf-backup" for command in commands for arg in command)
    assert ["sshd", "-t"] in commands
    assert ["systemctl", "restart", "sshd"] in commands


def test_vcf_backups_helper_apply_requires_existing_os_user(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-backups"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-vcf-backups-sshd.conf"
    config_path.write_text(vcf_backups_config_text(), encoding="utf-8")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command == ["id", "vcf-backup"]:
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "VCF_BACKUPS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "id" if command == "id" else None)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_vcf_backups("apply", [str(config_path)]) == 2
    captured = capsys.readouterr()
    assert "Apply the Local Users unit before VCF Backups" in captured.err


def test_vcf_offline_depot_helper_applies_nginx_site(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-offline-depot"
    managed_root = tmp_path / "etc" / "labfoundry"
    site_dir = managed_root / "nginx" / "sites.d"
    cert_path = managed_root / "vcf-offline-depot" / "certs" / "depot.crt"
    key_path = managed_root / "vcf-offline-depot" / "certs" / "depot.key"
    nginx_include = tmp_path / "nginx" / "conf.d" / "labfoundry.conf"
    apply_dir.mkdir(parents=True)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n", encoding="utf-8")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    config_path = apply_dir / "labfoundry-vcf-offline-depot.conf"
    config_path.write_text(
        "\n".join(
            [
                "# Managed by LabFoundry. Local changes may be overwritten.",
                "server {",
                "  listen 192.168.50.1:443 ssl;",
                "  server_name depot.labfoundry.internal;",
                f"  ssl_certificate {cert_path};",
                f"  ssl_certificate_key {key_path};",
                "",
                "  location = / {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location ^~ /static/ {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /favicon.ico {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /manifest.webmanifest {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /service-worker.js {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /PROD {",
                "    return 301 /PROD/;",
                "  }",
                "",
                "  location = /PROD/login {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /PROD/logout {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /PROD/ {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location ~ ^/PROD/.*/$ {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location ~ ^/PROD/(?!login$|logout$|auth-check$)(.+[^/])$ {",
                "    alias /mnt/labfoundry-vcf-offline-depot/PROD/$1;",
                "    sendfile on;",
                "    default_type application/octet-stream;",
                "  }",
                "",
                "  location / {",
                "    return 404;",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "VCF_DEPOT_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "NGINX_CONF_INCLUDE_PATH", nginx_include)
    monkeypatch.setattr(helper, "NGINX_SITES_DIR", site_dir)
    monkeypatch.setattr(helper, "VCF_DEPOT_SITE_PATH", site_dir / "vcf-offline-depot.conf")
    monkeypatch.setattr(helper, "_prepare_vcf_depot_web_tree", lambda text: None)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/nginx" if command == "nginx" else None)

    assert helper._handle_vcf_offline_depot("validate", [str(config_path)]) == 0
    assert helper._handle_vcf_offline_depot("apply-https", [str(config_path)]) == 0

    site_text = (site_dir / "vcf-offline-depot.conf").read_text(encoding="utf-8")
    assert "server_name depot.labfoundry.internal;" in site_text
    assert "alias /mnt/labfoundry-vcf-offline-depot/PROD/$1;" in site_text
    assert "root /mnt/labfoundry-vcf-offline-depot;" not in site_text
    assert "sendfile on;" in site_text
    assert nginx_include.read_text(encoding="utf-8").strip().endswith(f"include {site_dir}/*.conf;")
    assert ["/usr/sbin/nginx", "-t"] in commands
    assert ["systemctl", "enable", "--now", "nginx"] in commands


def test_vcf_offline_depot_helper_writes_htpasswd_for_authenticated_site(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-offline-depot"
    managed_root = tmp_path / "etc" / "labfoundry"
    site_dir = managed_root / "nginx" / "sites.d"
    cert_path = managed_root / "vcf-offline-depot" / "certs" / "depot.crt"
    key_path = managed_root / "vcf-offline-depot" / "certs" / "depot.key"
    htpasswd_path = managed_root / "nginx" / "htpasswd" / "vcf-offline-depot.htpasswd"
    shadow_path = tmp_path / "shadow"
    nginx_include = tmp_path / "nginx" / "conf.d" / "labfoundry.conf"
    apply_dir.mkdir(parents=True)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n", encoding="utf-8")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    shadow_path.write_text("vcf-depot:$6$rounds=5000$salty$hashvalue:19000:0:99999:7:::\n", encoding="utf-8")
    config_path = apply_dir / "labfoundry-vcf-offline-depot.conf"
    config_path.write_text(
        "\n".join(
            [
                "# Managed by LabFoundry. Local changes may be overwritten.",
                "# LabFoundry VCF Offline Depot unauthenticated access: false",
                "# LabFoundry VCF Offline Depot user: vcf-depot",
                "server {",
                "  listen 192.168.50.1:443 ssl;",
                "  server_name depot.labfoundry.internal;",
                f"  ssl_certificate {cert_path};",
                f"  ssl_certificate_key {key_path};",
                "",
                "  location = / {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location ^~ /static/ {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /favicon.ico {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /manifest.webmanifest {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /service-worker.js {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /PROD {",
                "    return 301 /PROD/;",
                "  }",
                "",
                "  location = /PROD/login {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /PROD/logout {",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location = /PROD/ {",
                "    auth_request /_labfoundry_depot_auth;",
                "    error_page 401 = @labfoundry_depot_login;",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location ~ ^/PROD/.*/$ {",
                "    auth_request /_labfoundry_depot_auth;",
                "    error_page 401 = @labfoundry_depot_login;",
                "    proxy_pass http://127.0.0.1:8000;",
                "  }",
                "",
                "  location ~ ^/PROD/(?!login$|logout$|auth-check$)(.+[^/])$ {",
                '    auth_basic "LabFoundry VCF Offline Depot";',
                f"    auth_basic_user_file {htpasswd_path};",
                "    alias /mnt/labfoundry-vcf-offline-depot/PROD/$1;",
                "    sendfile on;",
                "    default_type application/octet-stream;",
                "  }",
                "",
                "  location / {",
                "    return 404;",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(helper, "VCF_DEPOT_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "NGINX_CONF_INCLUDE_PATH", nginx_include)
    monkeypatch.setattr(helper, "NGINX_SITES_DIR", site_dir)
    monkeypatch.setattr(helper, "VCF_DEPOT_SITE_PATH", site_dir / "vcf-offline-depot.conf")
    monkeypatch.setattr(helper, "VCF_DEPOT_HTPASSWD_PATH", htpasswd_path)
    monkeypatch.setattr(helper, "VCF_DEPOT_SHADOW_PATH", shadow_path)
    monkeypatch.setattr(helper, "_prepare_vcf_depot_web_tree", lambda text: None)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/nginx" if command == "nginx" else None)
    monkeypatch.setattr(helper.shutil, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(helper.pwd, "getpwnam", lambda username: object())
    monkeypatch.setattr(helper.grp, "getgrnam", lambda group: (_ for _ in ()).throw(KeyError(group)))
    monkeypatch.setattr(helper, "_run", lambda command: subprocess.CompletedProcess(command, 0, "", ""))

    assert helper._handle_vcf_offline_depot("apply-https", [str(config_path)]) == 0

    assert htpasswd_path.read_text(encoding="utf-8") == "vcf-depot:$6$rounds=5000$salty$hashvalue\n"
    site_text = (site_dir / "vcf-offline-depot.conf").read_text(encoding="utf-8")
    assert 'auth_basic "LabFoundry VCF Offline Depot";' in site_text
    assert f"auth_basic_user_file {htpasswd_path};" in site_text


def test_vcf_offline_depot_helper_prepares_prod_tree_permissions(monkeypatch, tmp_path):
    helper = load_helper_module()
    prod_path = tmp_path / "depot" / "PROD"
    nested_dir = prod_path / "COMP"
    nested_file = nested_dir / "artifact.json"
    nested_dir.mkdir(parents=True)
    nested_file.write_text("{}", encoding="utf-8")
    prod_path.chmod(0o750)
    nested_dir.chmod(0o750)
    nested_file.chmod(0o640)
    monkeypatch.setattr(helper, "VCF_DEPOT_PROD_PATH", prod_path)

    helper._prepare_vcf_depot_web_tree(f"alias {prod_path}/;\n")

    assert prod_path.stat().st_mode & 0o005 == 0o005
    assert nested_dir.stat().st_mode & 0o005 == 0o005
    assert nested_file.stat().st_mode & 0o004 == 0o004


def test_vcf_offline_depot_helper_rejects_broad_nginx_root(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-offline-depot"
    managed_root = tmp_path / "etc" / "labfoundry"
    cert_path = managed_root / "vcf-offline-depot" / "certs" / "depot.crt"
    key_path = managed_root / "vcf-offline-depot" / "certs" / "depot.key"
    apply_dir.mkdir(parents=True)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n", encoding="utf-8")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    config_path = apply_dir / "labfoundry-vcf-offline-depot.conf"
    config_path.write_text(
        "\n".join(
            [
                "server {",
                "  listen 192.168.50.1:443 ssl;",
                "  server_name depot.labfoundry.internal;",
                "  root /mnt/labfoundry-vcf-offline-depot;",
                "  sendfile on;",
                "  default_type application/octet-stream;",
                f"  ssl_certificate {cert_path};",
                f"  ssl_certificate_key {key_path};",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(helper, "VCF_DEPOT_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)

    assert helper._handle_vcf_offline_depot("validate", [str(config_path)]) == 2
    captured = capsys.readouterr()
    assert "must not expose the depot store as a broad server root" in captured.err
    assert "must include a /PROD/ alias" in captured.err


def test_vcf_offline_depot_helper_extracts_vcfdt_tool(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    archive_path = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    payload = b"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 'vcf-download-tool 9.1.0.0100.25429019'; else echo software depot id 8c9506c6-7bdf-44d5-b2e9-50d829d66b99; fi\n"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("vcfdt/bin/vcf-download-tool")
        info.mode = 0o644
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    tool_dir = tmp_path / "opt" / "labfoundry" / "vcf-download-tool"
    monkeypatch.setattr(helper, "VCF_DEPOT_TOOL_DIR", tool_dir)
    monkeypatch.setattr(helper, "_run", lambda command: subprocess.CompletedProcess(command, 0, "vcf-download-tool 9.1.0.0100.25429019\n", ""))

    assert helper._handle_vcf_offline_depot("stage-tool", [str(archive_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["vcf_offline_depot"] == "stage-tool complete"
    assert payload["executable"] == str(tool_dir / "vcf-download-tool")
    assert payload["tool_version"] == "9.1.0.0100.25429019"
    assert payload["version_command"] == "vcf-download-tool --version"
    wrapper = tool_dir / "vcf-download-tool"
    extracted = tool_dir / "extracted" / "vcfdt" / "bin" / "vcf-download-tool"
    assert wrapper.is_file()
    assert extracted.is_file()
    assert os.access(wrapper, os.X_OK)
    assert os.access(extracted, os.X_OK)
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert f"cd '{extracted.parent.parent}' || exit 1" in wrapper_text
    assert str(extracted) in wrapper_text


def test_vcf_offline_depot_helper_generates_software_depot_id(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    tool_dir = tmp_path / "opt" / "labfoundry" / "vcf-download-tool"
    wrapper = tool_dir / "vcf-download-tool"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    commands: list[tuple[list[str], str]] = []

    def fake_run_with_input(command: list[str], text: str) -> subprocess.CompletedProcess[str]:
        commands.append((command, text))
        return subprocess.CompletedProcess(command, 0, "Software Depot ID: 8c9506c6-7bdf-44d5-b2e9-50d829d66b99\n", "")

    monkeypatch.setattr(helper, "VCF_DEPOT_TOOL_DIR", tool_dir)
    monkeypatch.setattr(helper, "_run_with_input", fake_run_with_input)

    assert helper._handle_vcf_offline_depot("generate-software-depot-id", []) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert commands == [([str(wrapper), "configuration", "generate", "--software-depot-id"], "Y\n")]
    assert payload["software_depot_id"] == "8c9506c6-7bdf-44d5-b2e9-50d829d66b99"
    assert payload["command"] == "vcf-download-tool configuration generate --software-depot-id"


def test_vcf_offline_depot_generate_software_depot_id_main_allows_no_path(monkeypatch):
    helper = load_helper_module()
    calls: list[tuple[str, list[str]]] = []

    def fake_handle(action: str, args: list[str]) -> int:
        calls.append((action, args))
        return 0

    monkeypatch.delenv("LABFOUNDRY_HELPER_USE_SYSTEMD_RUN", raising=False)
    monkeypatch.setattr(helper, "_handle_vcf_offline_depot", fake_handle)

    assert helper.main(["labfoundry-helper", "vcf-offline-depot", "generate-software-depot-id", "--real"]) == 0
    assert calls == [("generate-software-depot-id", [])]


def test_vcf_offline_depot_helper_applies_vcfdt_application_properties(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-offline-depot"
    properties_path = apply_dir / "application-prodv2.properties"
    apply_dir.mkdir(parents=True)
    properties_path.write_text("spring.profiles.active=depot\nlcm.depot.adapter.host=stage.example.test\n", encoding="utf-8")
    tool_dir = tmp_path / "opt" / "labfoundry" / "vcf-download-tool"
    tool_bin = tool_dir / "extracted" / "vcfdt" / "bin" / "vcf-download-tool"
    tool_bin.parent.mkdir(parents=True)
    tool_bin.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(helper, "VCF_DEPOT_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "VCF_DEPOT_TOOL_DIR", tool_dir)

    assert helper._handle_vcf_offline_depot("apply-properties", [str(properties_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["vcf_offline_depot"] == "application properties apply complete"
    target = tool_dir / "extracted" / "vcfdt" / "conf" / "application-prodv2.properties"
    assert target.read_text(encoding="utf-8") == properties_path.read_text(encoding="utf-8")

    outside_path = tmp_path / "application-prodv2.properties"
    outside_path.write_text("spring.profiles.active=depot\n", encoding="utf-8")
    assert helper._handle_vcf_offline_depot("apply-properties", [str(outside_path)]) == 2


def test_vcf_offline_depot_helper_removes_disabled_nginx_site(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "vcf-offline-depot"
    site_dir = tmp_path / "sites.d"
    config_path = apply_dir / "labfoundry-vcf-offline-depot.conf"
    site_path = site_dir / "vcf-offline-depot.conf"
    apply_dir.mkdir(parents=True)
    site_dir.mkdir(parents=True)
    config_path.write_text("# VCF Offline Depot HTTPS endpoint is disabled.\n", encoding="utf-8")
    site_path.write_text("server { listen 443 ssl; }\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "VCF_DEPOT_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "NGINX_CONF_INCLUDE_PATH", tmp_path / "nginx" / "conf.d" / "labfoundry.conf")
    monkeypatch.setattr(helper, "NGINX_SITES_DIR", site_dir)
    monkeypatch.setattr(helper, "VCF_DEPOT_SITE_PATH", site_path)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/sbin/nginx" if command == "nginx" else None)

    assert helper._handle_vcf_offline_depot("apply-https", [str(config_path)]) == 0

    assert not site_path.exists()
    assert ["/usr/sbin/nginx", "-t"] in commands


def patch_appliance_settings_nginx_paths(monkeypatch, helper, tmp_path):
    nginx_include = tmp_path / "nginx" / "conf.d" / "labfoundry.conf"
    nginx_main = tmp_path / "nginx" / "nginx.conf"
    nginx_sites = tmp_path / "nginx" / "sites.d"
    nginx_management_site = nginx_sites / "management.conf"
    nginx_main.parent.mkdir(parents=True, exist_ok=True)
    nginx_main.write_text(
        "\n".join(
            [
                "events { worker_connections 1024; }",
                "",
                "http {",
                "    include mime.types;",
                "    server {",
                "        listen 80;",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "NGINX_CONF_INCLUDE_PATH", nginx_include)
    monkeypatch.setattr(helper, "NGINX_MAIN_CONFIG_PATH", nginx_main)
    monkeypatch.setattr(helper, "NGINX_SITES_DIR", nginx_sites)
    monkeypatch.setattr(helper, "NGINX_MANAGEMENT_SITE_PATH", nginx_management_site)
    sshd_config_dir = tmp_path / "ssh" / "sshd_config.d"
    sshd_root_login = sshd_config_dir / "labfoundry-root-login.conf"
    sshd_main = tmp_path / "ssh" / "sshd_config"
    sshd_main.parent.mkdir(parents=True, exist_ok=True)
    sshd_main.write_text("PermitRootLogin no\nPasswordAuthentication no\nSubsystem sftp /usr/libexec/sftp-server\n", encoding="utf-8")
    monkeypatch.setattr(helper, "SSHD_CONFIG_DIR", sshd_config_dir)
    monkeypatch.setattr(helper, "SSHD_MAIN_CONFIG_PATH", sshd_main)
    monkeypatch.setattr(helper, "SSHD_ROOT_LOGIN_CONFIG_PATH", sshd_root_login)
    return {
        "include": nginx_include,
        "main": nginx_main,
        "sites": nginx_sites,
        "management_site": nginx_management_site,
        "sshd_main": sshd_main,
        "sshd_root_login": sshd_root_login,
    }


def appliance_settings_json(
    *,
    resolver_mode: str = "local_dns",
    resolver_servers: list[str] | None = None,
    local_dns_enabled: bool = True,
    management_https_enabled: bool = False,
    management_https_cert_path: str = "",
    management_https_key_path: str = "",
    root_ssh_enabled: bool = False,
    ntp_servers: list[str] | None = None,
) -> str:
    import json

    payload = {
        "fqdn": "labfoundry.labfoundry.internal",
        "resolver_mode": resolver_mode,
        "resolver_servers": ["127.0.0.1"] if resolver_servers is None else resolver_servers,
        "local_dns_enabled": local_dns_enabled,
        "management_interface": "eth0",
        "management_ip": "192.168.49.1",
        "management_ip_cidr": "192.168.49.1/24",
        "management_https_enabled": management_https_enabled,
        "root_ssh_enabled": root_ssh_enabled,
        "management_http_port": 8000,
        "management_public_http_port": 80,
        "management_public_https_port": 443,
        "management_upstream_host": "127.0.0.1",
        "management_upstream_port": 8000,
        "management_https_cert_path": management_https_cert_path,
        "management_https_key_path": management_https_key_path,
    }
    if ntp_servers is not None:
        payload["time_sync_mode"] = "systemd-timesyncd"
        payload["ntp_servers"] = ntp_servers
    return json.dumps(payload)


def chronyd_config_text(*, enabled: bool = True, server: str = "time1.google.com", listen_address: str = "192.168.50.1", allow_clients: str = "192.168.50.0/24") -> str:
    allow_directives = []
    for entry in allow_clients.replace(",", "\n").splitlines():
        value = entry.strip()
        if value:
            allow_directives.append(f"allow {value}")
    return "\n".join(
        [
            "# Managed by LabFoundry. Local changes may be overwritten.",
            f"# LabFoundry Chrony enabled: {str(enabled).lower()}",
            "# LabFoundry Chrony hostname: ntp.labfoundry.internal",
            "# LabFoundry Chrony listen interfaces: eth2.50",
            f"# LabFoundry Chrony listen addresses: {listen_address if listen_address else 'none'}",
            f"# LabFoundry Chrony client allow list: {allow_clients}",
            "driftfile /var/lib/chrony/drift",
            "makestep 1.0 3",
            "rtcsync",
            *([f"server {server} iburst"] if server else []),
            *([f"bindaddress {listen_address}"] if listen_address else []),
            *allow_directives,
            "",
        ]
    )


def test_appliance_settings_helper_validates_staged_json(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-settings.json"
    config_path.write_text(appliance_settings_json(), encoding="utf-8")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)

    assert helper._handle_appliance_settings("validate", [str(config_path)]) == 0


def test_appliance_settings_helper_requires_https_cert_files(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-settings.json"
    config_path.write_text(appliance_settings_json(management_https_enabled=True), encoding="utf-8")

    errors = helper._appliance_settings_config_errors(config_path)

    assert "management_https_cert_path is required when management HTTPS is enabled." in errors
    assert "management_https_key_path is required when management HTTPS is enabled." in errors


def test_appliance_settings_helper_rejects_invalid_json(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-settings.json"
    config_path.write_text('{"fqdn": "bad name"}', encoding="utf-8")

    errors = helper._appliance_settings_config_errors(config_path)

    assert "fqdn must be a valid fully qualified DNS name." in errors
    assert "resolver_mode must be local_dns, external, or dhcp." in errors


def test_appliance_settings_helper_accepts_dhcp_resolver_mode(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-settings.json"
    config_path.write_text(
        appliance_settings_json(resolver_mode="dhcp", resolver_servers=[], local_dns_enabled=False),
        encoding="utf-8",
    )

    errors = helper._appliance_settings_config_errors(config_path)

    assert errors == []


def test_appliance_settings_helper_rejects_invalid_ntp_server(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-settings.json"
    config_path.write_text(appliance_settings_json(ntp_servers=["bad_name"]), encoding="utf-8")

    errors = helper._appliance_settings_config_errors(config_path)

    assert "ntp server bad_name must be a valid DNS name or IP address." in errors


def test_appliance_settings_helper_writes_management_nginx_proxy(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    managed_root = tmp_path / "etc" / "labfoundry"
    cert_path = managed_root / "https" / "certs" / "labfoundry.labfoundry.internal.crt"
    key_path = managed_root / "https" / "certs" / "labfoundry.labfoundry.internal.key"
    dropin_dir = tmp_path / "systemd" / "labfoundry.service.d"
    redirect_script = tmp_path / "bin" / "labfoundry-http-redirect"
    redirect_service = tmp_path / "systemd" / "labfoundry-http-redirect.service"
    nginx_include = tmp_path / "nginx" / "conf.d" / "labfoundry.conf"
    nginx_main = tmp_path / "nginx" / "nginx.conf"
    nginx_sites = tmp_path / "nginx" / "sites.d"
    nginx_management_site = nginx_sites / "management.conf"
    sshd_config_dir = tmp_path / "ssh" / "sshd_config.d"
    sshd_root_login = sshd_config_dir / "labfoundry-root-login.conf"
    sshd_main = tmp_path / "ssh" / "sshd_config"
    apply_dir.mkdir(parents=True)
    cert_path.parent.mkdir(parents=True)
    nginx_main.parent.mkdir(parents=True)
    sshd_main.parent.mkdir(parents=True)
    sshd_main.write_text("PermitRootLogin no\nPasswordAuthentication no\nSubsystem sftp /usr/libexec/sftp-server\n", encoding="utf-8")
    nginx_main.write_text(
        "\n".join(
            [
                "events { worker_connections 1024; }",
                "",
                "http {",
                "    include mime.types;",
                "    server {",
                "        listen 80;",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    redirect_script.parent.mkdir(parents=True)
    redirect_script.write_text("legacy redirect", encoding="utf-8")
    redirect_service.parent.mkdir(parents=True)
    redirect_service.write_text("legacy redirect service", encoding="utf-8")
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n", encoding="utf-8")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    config_path = apply_dir / "labfoundry-settings.json"
    config_path.write_text(
        appliance_settings_json(
            management_https_enabled=True,
            management_https_cert_path=str(cert_path),
            management_https_key_path=str(key_path),
            root_ssh_enabled=True,
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CA_MANAGED_PATH_BASE", managed_root)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_HTTPS_DROPIN_PATH", dropin_dir / "management-https.conf")
    monkeypatch.setattr(helper, "LABFOUNDRY_HTTP_REDIRECT_SCRIPT_PATH", redirect_script)
    monkeypatch.setattr(helper, "LABFOUNDRY_HTTP_REDIRECT_SERVICE_PATH", redirect_service)
    monkeypatch.setattr(helper, "NGINX_CONF_INCLUDE_PATH", nginx_include)
    monkeypatch.setattr(helper, "NGINX_MAIN_CONFIG_PATH", nginx_main)
    monkeypatch.setattr(helper, "NGINX_SITES_DIR", nginx_sites)
    monkeypatch.setattr(helper, "NGINX_MANAGEMENT_SITE_PATH", nginx_management_site)
    monkeypatch.setattr(helper, "SSHD_CONFIG_DIR", sshd_config_dir)
    monkeypatch.setattr(helper, "SSHD_MAIN_CONFIG_PATH", sshd_main)
    monkeypatch.setattr(helper, "SSHD_ROOT_LOGIN_CONFIG_PATH", sshd_root_login)
    monkeypatch.setattr(helper, "_ca_key_matches_certificate", lambda certificate_pem, private_key_pem: True)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(
        helper.shutil,
        "which",
        lambda command: {
            "hostnamectl": "/usr/bin/hostnamectl",
            "systemd-run": "/usr/bin/systemd-run",
            "nginx": "/usr/sbin/nginx",
            "sshd": "/usr/sbin/sshd",
        }.get(command),
    )

    assert helper._handle_appliance_settings("apply", [str(config_path)]) == 0

    dropin = (dropin_dir / "management-https.conf").read_text(encoding="utf-8")
    assert "--host 127.0.0.1 --port 8000" in dropin
    assert "--ssl-certfile" not in dropin
    assert nginx_include.read_text(encoding="utf-8").strip().endswith(f"include {nginx_sites}/*.conf;")
    assert f"include {nginx_include};" in nginx_main.read_text(encoding="utf-8")
    management_site = nginx_management_site.read_text(encoding="utf-8")
    assert "listen 80 default_server;" in management_site
    assert "return 308 https://$host$request_uri;" in management_site
    assert "listen 443 ssl default_server;" in management_site
    assert "client_max_body_size 1g;" in management_site
    assert "client_max_body_size 512m;" not in management_site
    assert f"ssl_certificate {cert_path};" in management_site
    assert f"ssl_certificate_key {key_path};" in management_site
    assert "proxy_pass http://127.0.0.1:8000;" in management_site
    root_login = sshd_root_login.read_text(encoding="utf-8")
    assert "PermitRootLogin yes" in root_login
    assert "PasswordAuthentication yes" in root_login
    sshd_main_text = sshd_main.read_text(encoding="utf-8")
    assert "Include /etc/ssh/sshd_config.d/*.conf" in sshd_main_text
    assert "# LabFoundry manages this directive through labfoundry-root-login.conf: PermitRootLogin no" in sshd_main_text
    assert "# LabFoundry manages this directive through labfoundry-root-login.conf: PasswordAuthentication no" in sshd_main_text
    assert not redirect_script.exists()
    assert not redirect_service.exists()
    assert ["systemctl", "daemon-reload"] in commands
    assert ["systemctl", "disable", "--now", "labfoundry-http-redirect.service"] in commands
    assert ["systemctl", "enable", "--now", "nginx"] in commands
    assert ["/usr/sbin/nginx", "-t"] in commands
    assert ["/usr/sbin/sshd", "-t"] in commands
    assert ["systemctl", "restart", "sshd"] in commands
    assert any(command[:5] == ["/usr/bin/systemd-run", "--quiet", "--collect", "--on-active=3", "--unit=labfoundry-management-ui-restart"] for command in commands)


def test_appliance_settings_helper_writes_http_management_proxy_without_https(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    dropin_dir = tmp_path / "systemd" / "labfoundry.service.d"
    apply_dir.mkdir(parents=True)
    nginx_paths = patch_appliance_settings_nginx_paths(monkeypatch, helper, tmp_path)
    config_path = apply_dir / "labfoundry-settings.json"
    config_path.write_text(appliance_settings_json(management_https_enabled=False), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_HTTPS_DROPIN_PATH", dropin_dir / "management-https.conf")
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(
        helper.shutil,
        "which",
        lambda command: {
            "hostnamectl": "/usr/bin/hostnamectl",
            "systemd-run": "/usr/bin/systemd-run",
            "nginx": "/usr/sbin/nginx",
            "sshd": "/usr/sbin/sshd",
        }.get(command),
    )

    assert helper._handle_appliance_settings("apply", [str(config_path)]) == 0

    dropin = (dropin_dir / "management-https.conf").read_text(encoding="utf-8")
    assert "--host 127.0.0.1 --port 8000" in dropin
    management_site = nginx_paths["management_site"].read_text(encoding="utf-8")
    assert "listen 80 default_server;" in management_site
    assert "return 308 https://$host$request_uri;" not in management_site
    assert "listen 443" not in management_site
    assert "ssl_certificate" not in management_site
    assert "proxy_pass http://127.0.0.1:8000;" in management_site
    assert "proxy_set_header X-Forwarded-Proto http;" in management_site
    root_login = nginx_paths["sshd_root_login"].read_text(encoding="utf-8")
    assert "PermitRootLogin no" in root_login
    assert "PasswordAuthentication yes" not in root_login
    assert "Include /etc/ssh/sshd_config.d/*.conf" in nginx_paths["sshd_main"].read_text(encoding="utf-8")
    assert ["systemctl", "enable", "--now", "nginx"] in commands
    assert ["/usr/sbin/nginx", "-t"] in commands
    assert ["/usr/sbin/sshd", "-t"] in commands
    assert ["systemctl", "restart", "sshd"] in commands
    assert any(command[:5] == ["/usr/bin/systemd-run", "--quiet", "--collect", "--on-active=3", "--unit=labfoundry-management-ui-restart"] for command in commands)


def test_appliance_settings_helper_applies_local_resolver_without_timesyncd(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    networkd_dir = tmp_path / "etc" / "systemd" / "network"
    dropin_dir = tmp_path / "systemd" / "labfoundry.service.d"
    apply_dir.mkdir(parents=True)
    networkd_dir.mkdir(parents=True)
    mgmt_network = networkd_dir / "00-labfoundry-mgmt.network"
    mgmt_network.write_text(
        "\n".join(
            [
                "[Match]",
                "Name=eth0",
                "",
                "[Network]",
                "Address=192.168.49.1/24",
                "DNS=1.1.1.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = apply_dir / "labfoundry-settings.json"
    config_path.write_text(appliance_settings_json(), encoding="utf-8")
    patch_appliance_settings_nginx_paths(monkeypatch, helper, tmp_path)
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", mgmt_network)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_HTTPS_DROPIN_PATH", dropin_dir / "management-https.conf")
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(
        helper.shutil,
        "which",
        lambda command: {
            "hostnamectl": "/usr/bin/hostnamectl",
            "nginx": "/usr/sbin/nginx",
            "sshd": "/usr/sbin/sshd",
        }.get(command),
    )

    assert helper._handle_appliance_settings("apply", [str(config_path)]) == 0

    assert ["/usr/bin/hostnamectl", "set-hostname", "labfoundry.labfoundry.internal"] in commands
    assert ["resolvectl", "dns", "eth0", "127.0.0.1"] in commands
    assert ["resolvectl", "domain", "eth0", "~."] in commands
    assert ["systemctl", "enable", "--now", "systemd-timesyncd"] not in commands
    assert ["systemctl", "restart", "systemd-timesyncd"] not in commands
    network_text = mgmt_network.read_text(encoding="utf-8")
    assert "DNS=1.1.1.1" not in network_text
    assert "DNS=127.0.0.1" in network_text
    assert "Domains=~." in network_text


def test_appliance_settings_helper_applies_external_resolver_without_catchall(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    networkd_dir = tmp_path / "etc" / "systemd" / "network"
    dropin_dir = tmp_path / "systemd" / "labfoundry.service.d"
    apply_dir.mkdir(parents=True)
    networkd_dir.mkdir(parents=True)
    mgmt_network = networkd_dir / "00-labfoundry-mgmt.network"
    mgmt_network.write_text(
        "\n".join(["[Match]", "Name=eth0", "", "[Network]", "Address=192.168.49.1/24", "DNS=127.0.0.1", "Domains=~."]) + "\n",
        encoding="utf-8",
    )
    config_path = apply_dir / "labfoundry-settings.json"
    config_path.write_text(
        appliance_settings_json(resolver_mode="external", resolver_servers=["1.1.1.1", "9.9.9.9"], local_dns_enabled=False),
        encoding="utf-8",
    )
    patch_appliance_settings_nginx_paths(monkeypatch, helper, tmp_path)
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", mgmt_network)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_HTTPS_DROPIN_PATH", dropin_dir / "management-https.conf")
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(
        helper.shutil,
        "which",
        lambda command: {
            "hostnamectl": "/usr/bin/hostnamectl",
            "nginx": "/usr/sbin/nginx",
            "sshd": "/usr/sbin/sshd",
        }.get(command),
    )

    assert helper._handle_appliance_settings("apply", [str(config_path)]) == 0

    assert ["/usr/bin/hostnamectl", "set-hostname", "labfoundry.labfoundry.internal"] in commands
    assert ["resolvectl", "dns", "eth0", "1.1.1.1", "9.9.9.9"] in commands
    assert ["resolvectl", "domain", "eth0", ""] in commands
    network_text = mgmt_network.read_text(encoding="utf-8")
    assert "DNS=127.0.0.1" not in network_text
    assert "Domains=~." not in network_text
    assert "DNS=1.1.1.1" in network_text
    assert "DNS=9.9.9.9" in network_text


def test_appliance_settings_helper_configures_timesyncd_when_ntp_servers_present(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    networkd_dir = tmp_path / "etc" / "systemd" / "network"
    dropin_dir = tmp_path / "systemd" / "labfoundry.service.d"
    timesyncd_dir = tmp_path / "etc" / "systemd" / "timesyncd.conf.d"
    timesyncd_path = timesyncd_dir / "labfoundry.conf"
    apply_dir.mkdir(parents=True)
    networkd_dir.mkdir(parents=True)
    mgmt_network = networkd_dir / "00-labfoundry-mgmt.network"
    mgmt_network.write_text("\n".join(["[Match]", "Name=eth0", "", "[Network]", "Address=192.168.49.1/24"]) + "\n", encoding="utf-8")
    config_path = apply_dir / "labfoundry-settings.json"
    config_path.write_text(appliance_settings_json(ntp_servers=["time.cloudflare.com", "192.0.2.10"]), encoding="utf-8")
    patch_appliance_settings_nginx_paths(monkeypatch, helper, tmp_path)
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", mgmt_network)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "LABFOUNDRY_SERVICE_HTTPS_DROPIN_PATH", dropin_dir / "management-https.conf")
    monkeypatch.setattr(helper, "TIMESYNCD_CONFIG_DIR", timesyncd_dir)
    monkeypatch.setattr(helper, "TIMESYNCD_CONFIG_PATH", timesyncd_path)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(
        helper.shutil,
        "which",
        lambda command: {
            "hostnamectl": "/usr/bin/hostnamectl",
            "nginx": "/usr/sbin/nginx",
            "sshd": "/usr/sbin/sshd",
        }.get(command),
    )

    assert helper._handle_appliance_settings("apply", [str(config_path)]) == 0

    timesyncd_text = timesyncd_path.read_text(encoding="utf-8")
    assert "NTP=time.cloudflare.com 192.0.2.10" in timesyncd_text
    assert ["systemctl", "disable", "--now", "chronyd.service"] in commands
    assert ["systemctl", "enable", "--now", "systemd-timesyncd"] in commands
    assert ["systemctl", "restart", "systemd-timesyncd"] in commands


def test_chronyd_helper_rejects_invalid_staged_config(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "chronyd"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-chrony.conf"
    config_path.write_text(chronyd_config_text(server="bad_name", listen_address="not-an-ip", allow_clients="all, 192.168.50.0/24"), encoding="utf-8")

    monkeypatch.setattr(helper, "CHRONY_APPLY_DIR", apply_dir)

    errors = helper._chronyd_config_errors(config_path)

    assert "chronyd server bad_name must be a valid DNS name or IP address." in errors
    assert "chronyd bindaddress not-an-ip must be a valid IP address." in errors
    assert "chronyd client allow list can use 'all' only by itself." in errors


def test_chronyd_helper_apply_installs_config_and_switches_from_timesyncd(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "chronyd"
    config_path = apply_dir / "labfoundry-chrony.conf"
    chrony_conf = tmp_path / "etc" / "chrony.conf"
    state_dir = tmp_path / "var" / "lib" / "chrony"
    apply_dir.mkdir(parents=True)
    config_path.write_text(chronyd_config_text(), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "CHRONY_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CHRONY_CONFIG_PATH", chrony_conf)
    monkeypatch.setattr(helper, "CHRONY_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_chronyd("apply", [str(config_path)]) == 0

    assert chrony_conf.read_text(encoding="utf-8") == config_path.read_text(encoding="utf-8")
    assert state_dir.exists()
    assert ["systemctl", "disable", "--now", "systemd-timesyncd"] in commands
    assert ["systemctl", "enable", "chronyd.service"] in commands
    assert ["systemctl", "restart", "chronyd.service"] in commands


def test_chronyd_helper_disabled_apply_stops_chronyd_without_installing_config(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "chronyd"
    config_path = apply_dir / "labfoundry-chrony.conf"
    chrony_conf = tmp_path / "etc" / "chrony.conf"
    apply_dir.mkdir(parents=True)
    config_path.write_text(chronyd_config_text(enabled=False, listen_address="", allow_clients="all"), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "CHRONY_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CHRONY_CONFIG_PATH", chrony_conf)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_chronyd("apply", [str(config_path)]) == 0

    assert not chrony_conf.exists()
    assert commands == [["systemctl", "disable", "--now", "chronyd.service"]]


def test_chronyd_helper_disabled_apply_allows_empty_upstream_list(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "chronyd"
    config_path = apply_dir / "labfoundry-chrony.conf"
    chrony_conf = tmp_path / "etc" / "chrony.conf"
    apply_dir.mkdir(parents=True)
    config_path.write_text(chronyd_config_text(enabled=False, server="", listen_address="", allow_clients="all"), encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "CHRONY_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "CHRONY_CONFIG_PATH", chrony_conf)
    monkeypatch.setattr(helper, "_run", fake_run)

    assert helper._handle_chronyd("apply", [str(config_path)]) == 0

    assert not chrony_conf.exists()
    assert commands == [["systemctl", "disable", "--now", "chronyd.service"]]


def test_appliance_settings_hostname_fallback_writes_etc_hostname(monkeypatch, tmp_path):
    helper = load_helper_module()
    hostname_path = tmp_path / "hostname"
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/bin/hostname" if command == "hostname" else None)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "Path", lambda value: hostname_path if value == "/etc/hostname" else Path(value))

    assert helper._apply_hostname("fallback.labfoundry.internal") == 0

    assert hostname_path.read_text(encoding="utf-8") == "fallback.labfoundry.internal\n"
    assert commands == [["/usr/bin/hostname", "fallback.labfoundry.internal"]]

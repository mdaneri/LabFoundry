from pathlib import Path
import importlib.util
import sys


def load_customizer():
    path = Path("scripts/appliance/labfoundry-vmware-ovf-customize.py")
    spec = importlib.util.spec_from_file_location("labfoundry_vmware_ovf_customize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["labfoundry_vmware_ovf_customize"] = module
    spec.loader.exec_module(module)
    return module


OVF_ENV = """<?xml version="1.0" encoding="UTF-8"?>
<Environment
  xmlns="http://schemas.dmtf.org/ovf/environment/1"
  xmlns:oe="http://schemas.dmtf.org/ovf/environment/1">
  <PropertySection>
    <Property oe:key="labfoundry.management_mode" oe:value="static" />
    <Property oe:key="labfoundry.cidr" oe:value="192.168.10.10/24" />
    <Property oe:key="labfoundry.gateway" oe:value="192.168.10.1" />
    <Property oe:key="labfoundry.fqdn" oe:value="appliance.labfoundry.internal" />
    <Property oe:key="labfoundry.dns_servers" oe:value="192.168.10.2,192.168.10.3" />
    <Property oe:key="labfoundry.ntp_servers" oe:value="ntp.labfoundry.internal time1.google.com" />
    <Property oe:key="labfoundry.admin_password" oe:value="admin-secret" />
    <Property oe:key="labfoundry.root_password" oe:value="root-secret" />
  </PropertySection>
</Environment>
"""


def test_vmware_ovf_customizer_parses_and_validates_properties_without_logging_secrets():
    customizer = load_customizer()

    properties = customizer.parse_ovf_environment(OVF_ENV)
    config = customizer.validate_properties(properties)
    summary = customizer.redacted_summary(config)

    assert config["management_mode"] == "static"
    assert config["cidr"] == "192.168.10.10/24"
    assert config["gateway"] == "192.168.10.1"
    assert config["fqdn"] == "appliance.labfoundry.internal"
    assert config["dns_servers"] == ["192.168.10.2", "192.168.10.3"]
    assert config["ntp_servers"] == ["ntp.labfoundry.internal", "time1.google.com"]
    assert config["management_source_cidr"] == "192.168.10.0/24"
    assert summary["admin_password_set"] is True
    assert summary["root_password_set"] is True
    assert "admin-secret" not in str(summary)
    assert "root-secret" not in str(summary)


def test_vmware_ovf_customizer_supports_dhcp_management_by_default():
    customizer = load_customizer()
    properties = customizer.parse_ovf_environment(OVF_ENV)
    properties.pop("labfoundry.management_mode")
    properties.pop("labfoundry.cidr")
    properties.pop("labfoundry.gateway")
    properties.pop("labfoundry.dns_servers")

    config = customizer.validate_properties(properties)

    assert config["management_mode"] == "dhcp"
    assert config["cidr"] == "dhcp"
    assert config["gateway"] == ""
    assert config["dns_servers"] == []
    assert config["management_source_cidr"] == ""


def test_vmware_ovf_customizer_requires_static_network_properties_only_for_static_mode():
    customizer = load_customizer()
    properties = customizer.parse_ovf_environment(OVF_ENV)
    properties.pop("labfoundry.ntp_servers")

    config = customizer.validate_properties(properties)

    assert config["ntp_servers"] == []

    properties.pop("labfoundry.cidr")
    try:
        customizer.validate_properties(properties)
    except customizer.OvfCustomizationError as exc:
        assert "labfoundry.cidr" in str(exc)
    else:
        raise AssertionError("missing static CIDR should fail validation")


def test_vmware_ovf_customizer_renders_initial_firewall_for_ovf_subnet(tmp_path):
    customizer = load_customizer()
    firewall_path = tmp_path / "labfoundry.nft"
    customizer.FIREWALL_CONFIG_PATH = firewall_path
    properties = customizer.parse_ovf_environment(OVF_ENV)
    config = customizer.validate_properties(properties)

    customizer.write_initial_firewall_config(config)

    rendered = firewall_path.read_text(encoding="utf-8")
    assert "ip saddr 192.168.10.0/24 tcp dport { 22, 80, 443 } accept" in rendered
    assert "192.168.49.0/24" not in rendered
    assert "flush ruleset" in rendered
    assert "policy drop" in rendered


def test_vmware_ovf_customizer_renders_dhcp_network_and_interface_scoped_firewall(tmp_path):
    customizer = load_customizer()
    customizer.NETWORKD_PATH = tmp_path / "00-labfoundry-mgmt.network"
    customizer.FIREWALL_CONFIG_PATH = tmp_path / "labfoundry.nft"
    properties = customizer.parse_ovf_environment(OVF_ENV)
    properties["labfoundry.management_mode"] = "dhcp"
    properties.pop("labfoundry.cidr")
    properties.pop("labfoundry.gateway")
    config = customizer.validate_properties(properties)

    customizer.write_networkd_config(config)
    customizer.write_initial_firewall_config(config)

    networkd = customizer.NETWORKD_PATH.read_text(encoding="utf-8")
    firewall = customizer.FIREWALL_CONFIG_PATH.read_text(encoding="utf-8")
    assert "DHCP=ipv4" in networkd
    assert "Address=" not in networkd
    assert 'iifname "eth0" tcp dport { 22, 80, 443 } accept' in firewall


def test_vmware_ovf_customizer_rotates_clone_specific_env_secrets(tmp_path):
    customizer = load_customizer()
    customizer.ENV_PATH = tmp_path / "labfoundry.env"
    customizer.NETWORKD_PATH = tmp_path / "00-labfoundry-mgmt.network"
    customizer.RESOLV_CONF_PATH = tmp_path / "resolv.conf"
    customizer.NGINX_MANAGEMENT_PATH = tmp_path / "management.conf"
    customizer.FIREWALL_CONFIG_PATH = tmp_path / "labfoundry.nft"
    customizer.MARKER_PATH = tmp_path / "marker.json"
    customizer.NGINX_MANAGEMENT_PATH.write_text("server_name labfoundry.internal _;\n", encoding="utf-8")
    generated = iter(["rotated-secret-key", "rotated-secrets-key"])
    customizer.generate_secret_key = lambda: next(generated)
    customizer.set_password = lambda username, password: None
    customizer.set_hostname = lambda fqdn: None
    customizer.ENV_PATH.write_text(
        "\n".join(
            [
                'LABFOUNDRY_SECRET_KEY="baked-secret"',
                'LABFOUNDRY_SECRETS_KEY="baked-secrets-key"',
                'LABFOUNDRY_BOOTSTRAP_ADMIN_USERNAME="admin"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    properties = customizer.parse_ovf_environment(OVF_ENV)
    config = customizer.validate_properties(properties)

    summary = customizer.apply_customization(config)

    rendered = customizer.ENV_PATH.read_text(encoding="utf-8")
    assert 'LABFOUNDRY_SECRET_KEY="rotated-secret-key"' in rendered
    assert 'LABFOUNDRY_SECRETS_KEY="rotated-secrets-key"' in rendered
    assert "baked-secret" not in rendered
    assert "baked-secrets-key" not in rendered
    assert "rotated-secret-key" not in str(summary)
    assert "rotated-secrets-key" not in str(summary)


def test_vmware_ovf_export_and_image_plumbing_are_present():
    export_script = Path("scripts/windows/vmware/export-ovf.ps1").read_text(encoding="utf-8")
    provision_script = Path("image/common/scripts/provision-labfoundry.sh").read_text(encoding="utf-8")
    bootstrap_script = Path("scripts/appliance/labfoundry-bootstrap-https").read_text(encoding="utf-8")
    vmware_unit = Path("image/vmware-workstation/systemd/labfoundry-vmware-ovf-customize.service").read_text(encoding="utf-8")
    docs = Path("image/vmware-workstation/README.md").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    for key in (
        "labfoundry.management_mode",
        "labfoundry.cidr",
        "labfoundry.gateway",
        "labfoundry.fqdn",
        "labfoundry.dns_servers",
        "labfoundry.ntp_servers",
        "labfoundry.admin_password",
        "labfoundry.root_password",
    ):
        assert key in export_script
        assert key in docs

    assert "ovftool was not found" in export_script
    assert "VMware Workstation\\OVFTool\\ovftool.exe" in export_script
    assert "Join-Path $Path 'ovftool.exe'" in export_script
    assert "Add-LabFoundryOvfProperties" in export_script
    assert "Ensure-LabFoundryOvfNetworks" in export_script
    assert "SelectSingleNode('/ovf:Envelope/ovf:NetworkSection'" in export_script
    assert "envelope.InsertBefore($networkSection, $VirtualSystem)" in export_script
    assert "VirtualSystem.InsertBefore($networkSection, $HardwareSection)" not in export_script
    assert "Add-LabFoundryOvfCategory" in export_script
    assert "Management network" in export_script
    assert "Appliance identity and time" in export_script
    assert "Initial credentials" in export_script
    assert "-DefaultValue 'dhcp'" in export_script
    assert "LabFoundry Management Network" in export_script
    assert "LabFoundry Services Network" in export_script
    assert "$serviceAdapter = $networkAdapters[1]" in export_script
    assert "Network adapter 2" in export_script
    assert "Remove-NamespacedChildElement -Parent $serviceAdapter -LocalName 'Address'" in export_script
    assert "Update-OvfManifest" in export_script
    assert "New-OvaArchive" in export_script
    assert "Get-OvfDescriptorPath" in export_script
    assert "-Recurse" in export_script
    assert "$ovfPackageDirectory = Split-Path -Parent $ovfPath" in export_script
    assert "New-OvaArchive -OvfDirectory $ovfPackageDirectory" in export_script
    assert "'transport' -Value 'com.vmware.guestInfo'" in export_script
    assert "com.vmware.guestInfo" in export_script
    assert "vmw:password" not in docs
    assert "labfoundry-vmware-ovf-customize.py" in provision_script
    assert "labfoundry-bootstrap-https" in provision_script
    assert "labfoundry-bootstrap-https.service" in provision_script
    assert 'for action in ("validate", "apply")' in bootstrap_script
    assert 'str(HELPER_PATH), "ca", action, str(CA_STAGED_CONFIG_PATH), "--real"' in bootstrap_script
    assert "systemctl enable labfoundry-vmware-ovf-customize.service" in provision_script
    assert "systemctl enable labfoundry-bootstrap-https.service" in provision_script
    assert "Before=network-pre.target" in vmware_unit
    assert "labfoundry-bootstrap-https.service" in vmware_unit
    assert "/image/vmware-workstation/ovf" in gitignore
    assert "VMware Workstation\\OVFTool" in docs
    assert "LabFoundry Management Network" in docs
    assert "LabFoundry Services Network" in docs

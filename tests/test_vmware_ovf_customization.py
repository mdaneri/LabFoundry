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


def test_vmware_ovf_customizer_requires_all_non_ntp_deployment_properties():
    customizer = load_customizer()
    properties = customizer.parse_ovf_environment(OVF_ENV)
    properties.pop("labfoundry.ntp_servers")

    config = customizer.validate_properties(properties)

    assert config["ntp_servers"] == []

    properties.pop("labfoundry.root_password")
    try:
        customizer.validate_properties(properties)
    except customizer.OvfCustomizationError as exc:
        assert "labfoundry.root_password" in str(exc)
    else:
        raise AssertionError("missing root password should fail validation")


def test_vmware_ovf_export_and_image_plumbing_are_present():
    export_script = Path("scripts/windows/vmware/export-ovf.ps1").read_text(encoding="utf-8")
    provision_script = Path("image/common/scripts/provision-labfoundry.sh").read_text(encoding="utf-8")
    vmware_unit = Path("image/vmware-workstation/systemd/labfoundry-vmware-ovf-customize.service").read_text(encoding="utf-8")
    docs = Path("image/vmware-workstation/README.md").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    for key in (
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
    assert "Update-OvfManifest" in export_script
    assert "New-OvaArchive" in export_script
    assert "'transport' -Value 'com.vmware.guestInfo'" in export_script
    assert "com.vmware.guestInfo" in export_script
    assert "vmw:password" not in docs
    assert "labfoundry-vmware-ovf-customize.py" in provision_script
    assert "systemctl enable labfoundry-vmware-ovf-customize.service" in provision_script
    assert "Before=network-pre.target" in vmware_unit
    assert "/image/vmware-workstation/ovf" in gitignore
    assert "VMware Workstation\\OVFTool" in docs

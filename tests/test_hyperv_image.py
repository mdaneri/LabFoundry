from pathlib import Path

import importlib.util
import sys


def load_lifecycle_runner():
    path = Path("scripts/interop/lifecycle_test.py")
    spec = importlib.util.spec_from_file_location("lifecycle_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lifecycle_test"] = module
    spec.loader.exec_module(module)
    return module


def test_photon_provisioning_management_network_matches_eth0_only():
    script = Path("image/hyperv/scripts/provision-labfoundry.sh").read_text(encoding="utf-8")

    assert 'LABFOUNDRY_MGMT_INTERFACE="${LABFOUNDRY_MGMT_INTERFACE:-eth0}"' in script
    assert 'printf \'Name=%s\\n\\n\' "$LABFOUNDRY_MGMT_INTERFACE"' in script
    assert "Name=eth* en*" not in script
    assert "rm -f /etc/systemd/network/50-static-en.network /etc/systemd/network/99-dhcp-en.network" in script


def test_packer_build_uses_labfoundry_management_network_by_default():
    template = Path("image/hyperv/labfoundry-photon.pkr.hcl").read_text(encoding="utf-8")
    docs = Path("image/hyperv/README.md").read_text(encoding="utf-8")
    root_docs = Path("README.md").read_text(encoding="utf-8")
    wrapper = Path("scripts/windows/build-photon-hyperv-image.ps1").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert 'default = "LabFoundry-Mgmt"' in template
    assert 'default     = "192.168.49.30/24"' in template
    assert 'default     = "255.255.255.0"' in template
    assert 'default     = "192.168.49.254"' in template
    assert 'variable "iso_contains_kickstart"' in template
    assert "Iso_contains_kickstart must be true" in template
    assert "secondary_iso_images" not in template
    assert 'ks=cdrom:/photon-ks.json' in template
    assert "photon.media=cdrom" in template
    assert "http_content" not in template
    assert "http_port_min" not in template
    assert 'default = "Default Switch"' not in template
    assert "build-photon-hyperv-image.ps1" in root_docs
    assert "create-hyperv-switches.ps1" in docs
    assert "builder_static_ip=192.168.49.30/24" in docs
    assert "labfoundry-photon-with-kickstart.iso" in docs
    assert "Using remastered Photon ISO" in docs
    assert "[string]$SshPassword = 'VMware01!'" in wrapper
    assert "[string]$BootstrapAdminPassword = 'VMware01!'" in wrapper
    assert "create_photon_kickstart_iso.py" in wrapper
    assert "Using remastered Photon ISO" in wrapper
    assert "Packer will boot a single DVD with embedded photon-ks.json." in wrapper
    assert "Write-PackerVarFile" in wrapper
    assert "Using Packer var-file" in wrapper
    assert "[switch]$KeepExistingOutput" in wrapper
    assert "Packer build will replace any existing output directory for this build." in wrapper
    assert "$packerArgs += '-force'" in wrapper
    assert "'-var-file', $varFilePath" in wrapper
    assert "builder_static_dns       = $BuilderStaticDns" in wrapper
    assert "UseHttpKickstartFallback" not in wrapper
    assert "/image/hyperv/build" in gitignore
    remaster_helper = Path("scripts/interop/create_photon_kickstart_iso.py").read_text(encoding="utf-8")
    assert "iso.add_file" in remaster_helper
    assert 'rr_name="photon-ks.json"' in remaster_helper


def test_lifecycle_hyperv_script_uses_separate_vm_set_by_default():
    script = Path("scripts/windows/run-hyperv-lifecycle-test.ps1").read_text(encoding="utf-8")

    assert "[string]$LabName = 'LabFoundryLifecycle'" in script
    assert "[string]$SiteInterface = 'eth1.12'" in script
    assert "[string]$SiteCidr = '192.168.12.1/24'" in script
    assert "[int]$SiteVlanId = 12" in script
    assert "$applianceName = \"$LabName-Appliance\"" in script
    assert "$clientAName = \"$LabName-ClientA\"" in script
    assert "$clientBName = \"$LabName-ClientB\"" in script
    assert "Refusing to use reserved VM name" in script
    assert "@('LabFoundry', 'LabFoundry-Photon-Builder')" in script
    assert "image\\hyperv\\clients\\alpine-cloud\\labfoundry-tiny-linux-client.vhdx" in script
    assert "Running lifecycle appliance VM(s) may already own ${ApplianceIPAddress}" in script
    assert "-CleanupVmsOnly" in script


def test_lifecycle_hyperv_script_does_not_cleanup_without_explicit_flag():
    script = Path("scripts/windows/run-hyperv-lifecycle-test.ps1").read_text(encoding="utf-8")

    assert "[switch]$CleanupCreatedLab" in script
    assert "if ($CleanupCreatedLab)" in script
    assert "Lifecycle VMs were left in place" in script
    assert "Remove-VM -Name $name -Force" in script
    assert "Remove-VM -Name 'LabFoundry'" not in script


def test_lifecycle_single_command_wrapper_prepares_runs_and_cleans_up_by_default():
    script = Path("scripts/windows/invoke-hyperv-lifecycle-test.ps1").read_text(encoding="utf-8")

    assert "DefaultParameterSetName = 'Run'" in script
    assert "ParameterSetName = 'PrepareNetworks'" in script
    assert "ParameterSetName = 'CleanupNetworks'" in script
    assert "ParameterSetName = 'CleanupVms'" in script
    assert "[string]$AdminPassword = 'VMware01!'" in script
    assert "[string]$SshPassword = 'VMware01!'" in script
    assert "[string]$VcfBackupPassword = 'VMware01!Test'" in script
    assert "'-VcfBackupPassword', $VcfBackupPassword" in script
    assert "[string]$SiteInterface = 'eth1.12'" in script
    assert "[string]$SiteCidr = '192.168.12.1/24'" in script
    assert "[int]$SiteVlanId = 12" in script
    assert "prepare-tiny-linux-client.ps1" in script
    assert "Find-LatestApplianceVhdx" in script
    assert "run-hyperv-lifecycle-test.ps1" in script
    assert "$arguments += '-CleanupCreatedLab'" in script
    assert "[switch]$KeepVms" in script
    assert "[switch]$PrepareNetworksOnly" in script
    assert "[switch]$CleanupNetworksOnly" in script
    assert "[switch]$CleanupVmsOnly" in script
    assert "remove-hyperv-lifecycle-networks.ps1" in script
    assert "remove-hyperv-lifecycle-vms.ps1" in script
    assert "LabFoundryLifecycle-$(Get-Date -Format 'yyyyMMddHHmmss')" in script
    assert "$singlePurposeActions" not in script


def test_lifecycle_cleanup_scripts_are_scoped_to_labfoundry_assets():
    network_script = Path("scripts/windows/remove-hyperv-lifecycle-networks.ps1").read_text(encoding="utf-8")
    vm_script = Path("scripts/windows/remove-hyperv-lifecycle-vms.ps1").read_text(encoding="utf-8")

    assert "LabFoundry-Mgmt-NAT" in network_script
    assert "LabFoundry-Mgmt" in network_script
    assert "LabFoundry-SiteA" in network_script
    assert "Get-VMNetworkAdapter -All" in network_script
    assert "Refusing to remove switch" in network_script
    assert "LabFoundryLifecycle*" in vm_script
    assert "LabFoundry-Photon-Builder" in vm_script
    assert "Refusing VM cleanup" in vm_script
    assert "Remove-VM -Name $vm.Name -Force" in vm_script


def test_lifecycle_hyperv_script_finds_alpine_ips_and_pins_plink_hostkeys():
    script = Path("scripts/windows/run-hyperv-lifecycle-test.ps1").read_text(encoding="utf-8")

    assert "Get-NetNeighbor -AddressFamily IPv4" in script
    assert "ConvertTo-HyphenMac" in script
    assert "Wait-GuestIPv4 -Name $clientAName" in script
    assert "function Test-TcpPort" in script
    assert "Get-PlinkHostKey" in script
    assert "(Get-Date).AddMinutes(4)" in script
    assert "$ErrorActionPreference = 'Continue'" in script
    assert "Timed out waiting for SSH host key" in script
    assert "Set-VMNetworkAdapterVlan -VMName $applianceName -VMNetworkAdapterName 'SiteA' -Trunk" in script
    assert "Set-VMNetworkAdapterVlan -VMName $clientAName -VMNetworkAdapterName 'SiteA-Test' -Access -VlanId $SiteVlanId" in script
    assert "Appliance-Mgmt-Test" in script
    assert "'--client-a-hostkey'" in script
    assert "'--client-b-hostkey'" in script


def test_lifecycle_hyperv_script_seeds_alpine_clients_for_ssh():
    script = Path("scripts/windows/run-hyperv-lifecycle-test.ps1").read_text(encoding="utf-8")

    assert "[string]$ClientSshUser = 'alpine'" in script
    assert "New-CloudInitSeedIso" in script
    assert "create_nocloud_seed_iso.py" in script
    assert "Add-VMDvdDrive" in script
    assert "pycdlib" in script
    assert "ssh-keygen" not in script
    assert "Client SSH access requires -SshPassword or an existing -SshKeyPath." in script


def test_nocloud_seed_helper_writes_client_cloud_init_contract():
    script = Path("scripts/interop/create_nocloud_seed_iso.py").read_text(encoding="utf-8")

    assert 'vol_ident="cidata"' in script
    assert "ssh_authorized_keys:" in script
    assert 'parser.add_argument("--public-key", default="")' in script
    assert "Either --public-key or --password is required" in script
    assert "openssl" in script
    assert "sshpass" in script
    assert "labfoundry-refresh-test-dhcp" in script
    assert "joliet_path=f\"/{name}\"" in script


def test_prepare_tiny_linux_client_downloads_verifies_and_converts_alpine():
    script = Path("scripts/windows/prepare-tiny-linux-client.ps1").read_text(encoding="utf-8")

    assert "dl-cdn.alpinelinux.org/alpine/latest-stable/releases/cloud" in script
    assert "generic_alpine-3.24.1-x86_64-uefi-cloudinit-r0.qcow2" in script
    assert "Get-FileHash -Algorithm SHA512" in script
    assert "qemu-img convert -p -f qcow2 -O vhdx -o subformat=dynamic" in script
    assert "labfoundry-tiny-linux-client.vhdx" in script


def test_lifecycle_runner_plan_includes_ca_and_global_apply_units():
    module = load_lifecycle_runner()
    args = module.parse_args(
        [
            "--password",
            "test",
            "--plan-only",
        ]
    )

    plan = module.lifecycle_plan(args)

    assert plan["apply_units"] == ["local_users", "network", "firewall", "wan", "dnsmasq", "ca", "vcf_backups"]
    assert plan["interfaces"]["vlan"]["name"] == "eth2.50"
    assert plan["interfaces"]["client_ca_request"]["name"] == "eth3"
    assert plan["interfaces"]["client_ca_request"]["ip_cidr"] == "192.168.49.20/24"
    assert "CA desired state, root certificate download, client CSR request, issued certificate download, and client-side verification" in plan["checks"]
    assert "VCF Backup desired state, local user sync, SFTP listener, and client probe" in plan["checks"]


def test_lifecycle_runner_uses_supported_network_roles():
    script = Path("scripts/interop/lifecycle_test.py").read_text(encoding="utf-8")

    assert '"mode": "access", "role": "access"' in script
    assert '"mode": "trunk", "role": "unused"' in script
    assert 'role="access"' in script
    assert '"role": "lab"' not in script
    assert '"role": "trunk"' not in script


def test_lifecycle_runner_supports_alpine_doas_and_plink_hostkeys():
    script = Path("scripts/interop/lifecycle_test.py").read_text(encoding="utf-8")

    assert "--client-a-hostkey" in script
    assert "--client-b-hostkey" in script
    assert '"-hostkey", hostkey' in script
    assert "command -v doas" in script
    assert "ip route replace {wan.network} via {site_ip} dev eth1" in script
    assert "traceroute -n {wan_peer_ip}" in script


def test_lifecycle_runner_covers_ca_vcf_backups_wan_noise_and_console_summary():
    script = Path("scripts/interop/lifecycle_test.py").read_text(encoding="utf-8")

    assert "--vcf-backup-password" in script
    assert "--client-ca-request-interface" in script
    assert "configure-vcf-backups" in script
    assert "vcf-backup-client-check" in script
    assert "sshpass -p" in script
    assert "redact_text" in script
    assert '"local_users", "network", "firewall", "wan", "dnsmasq", "ca", "vcf_backups"' in script
    assert "certificate_summary" in script
    assert "root_ca" in script
    assert "ca-client-certificate-request" in script
    assert "ca-client-certificate-check" in script
    assert "create_client_csr" in script
    assert "ca_request_url" in script
    assert "Cookie: " in script
    assert "--connect-timeout 10 --max-time 30" in script
    assert "except subprocess.TimeoutExpired" in script
    assert "SSH command timed out after 120 seconds." in script
    assert "VCF KMIP client" in script
    assert "verify_certificate_signed_by_root" in script
    assert "client_a_download" in script
    assert "-o /dev/null -w '%{http_code}'" in script
    assert "apply-connectivity-units" in script
    assert "apply-ca-unit" in script
    assert "tc qdisc show dev {args.wan_interface} | grep -E 'netem.*delay 25ms'" in script
    assert "Lifecycle summary" in script
    assert "Result JSON:" in script


def test_lifecycle_roadmap_splits_pester_and_pytest_ownership():
    doc = Path("docs/hyperv-lifecycle-testing.md").read_text(encoding="utf-8")

    assert "Invoke-Pester tests/pester/HyperVLifecycle.Tests.ps1" in doc
    assert "Python appliance and guest assertions must remain pytest-covered" in doc
    assert "scripts/interop/lifecycle_test.py" in doc

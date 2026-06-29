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
    main = Path("labfoundry/app/main.py").read_text(encoding="utf-8")
    seed = Path("labfoundry/app/seed.py").read_text(encoding="utf-8")

    assert 'LABFOUNDRY_MGMT_INTERFACE="${LABFOUNDRY_MGMT_INTERFACE:-eth0}"' in script
    assert 'printf \'Name=%s\\n\\n\' "$LABFOUNDRY_MGMT_INTERFACE"' in script
    assert "Name=eth* en*" not in script
    assert "rm -f /etc/systemd/network/50-static-en.network /etc/systemd/network/99-dhcp-en.network" in script
    assert 'seed_initial_data(db, include_examples=settings.environment != "appliance")' in main
    assert "if include_examples:" in seed


def test_photon_provisioning_installs_default_nginx_management_proxy():
    script = Path("image/hyperv/scripts/provision-labfoundry.sh").read_text(encoding="utf-8")
    systemd_unit = Path("image/hyperv/systemd/labfoundry.service").read_text(encoding="utf-8")
    sudoers = Path("image/hyperv/sudoers.d/labfoundry-helper").read_text(encoding="utf-8")
    docs = Path("image/hyperv/README.md").read_text(encoding="utf-8")
    root_docs = Path("README.md").read_text(encoding="utf-8")

    assert "tdnf -y install" in script and "nginx" in script
    assert "tdnf -y install" in script and "powershell" in script
    assert "tdnf -y install" in script and "ipxe" in script
    assert "tdnf -y install" in script and "syslinux" in script
    assert 'BOOTSTRAP_SHELL="${LABFOUNDRY_BOOTSTRAP_ADMIN_SHELL:-/usr/bin/pwsh}"' in script
    assert '--shell "$BOOTSTRAP_SHELL"' in script
    assert "touch /etc/shells" in script
    assert 'grep -qxF "$BOOTSTRAP_SHELL" /etc/shells' in script
    assert "labfoundry-bootstrap-admin" in script
    assert "$BOOTSTRAP_USERNAME ALL=(ALL) ALL" in script
    assert "visudo -cf /etc/sudoers.d/labfoundry-bootstrap-admin" in script
    assert 'chmod 0711 "$LABFOUNDRY_STATE"' in script
    assert 'chown "$BOOTSTRAP_USERNAME:$(id -gn "$BOOTSTRAP_USERNAME")" "$LABFOUNDRY_STATE/users/$BOOTSTRAP_USERNAME"' in script
    assert 'chmod 0750 "$LABFOUNDRY_STATE/users/$BOOTSTRAP_USERNAME"' in script
    assert "UMask=0027" in systemd_unit
    assert "configuring default LabFoundry management nginx proxy" in script
    assert "install -d -o root -g root -m 0755 /etc/nginx/conf.d" in script
    assert "/etc/nginx/conf.d/labfoundry.conf" in script
    assert "/etc/labfoundry/nginx/sites.d/management.conf" in script
    assert "rm -f /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default_server.conf" in script
    assert "listen 80 default_server;" in script
    assert "client_max_body_size 1g;" in script
    assert "client_max_body_size 512m;" not in script
    assert "proxy_pass http://127.0.0.1:8000;" in script
    assert "nginx -t" in script
    assert "systemctl enable --now nginx" in script
    assert 'LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS="${LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS:-true}"' in script
    assert 'log_step "system adapter dry-run mode: $LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS"' in script
    assert "LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS=$LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS" in script
    assert 'install -o root -g root -m 0440 "$LABFOUNDRY_HOME/image/hyperv/sudoers.d/labfoundry-helper" /etc/sudoers.d/labfoundry-helper' in script
    assert "labfoundry ALL=(root) NOPASSWD: /opt/labfoundry/bin/labfoundry-helper\n" in sudoers
    assert "/opt/labfoundry/bin/labfoundry-helper *" not in sudoers
    assert "labfoundry-root-login.conf" in script
    assert "PermitRootLogin no" in script
    assert "HTTP/80, proxied to uvicorn on `127.0.0.1:8000`" in docs
    assert "proxying HTTP/80 to" in root_docs
    assert "-PipGlobalIndex" in root_docs
    assert "-PipGlobalIndexUrl" in root_docs
    assert "Leave both options empty to keep" in root_docs
    assert "standard pip behavior" in root_docs


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
    assert 'variable "dry_run_system_adapters"' in template
    assert 'variable "pip_global_index"' in template
    assert 'description = "Optional pip global.index value. Empty keeps default pip behavior."' in template
    assert 'variable "pip_global_index_url"' in template
    assert 'description = "Optional pip global.index-url value. Empty keeps default pip behavior."' in template
    assert 'builder_static_dns_text      = join(" ", var.builder_static_dns)' in template
    assert 'dry_run_system_adapters_text = var.dry_run_system_adapters ? "true" : "false"' in template
    assert '"LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS=${local.dry_run_system_adapters_text}"' in template
    assert '"LABFOUNDRY_MGMT_DNS=${local.builder_static_dns_text}"' in template
    assert '"LABFOUNDRY_PIP_GLOBAL_INDEX=${var.pip_global_index}"' in template
    assert '"LABFOUNDRY_PIP_GLOBAL_INDEX_URL=${var.pip_global_index_url}"' in template
    assert "Iso_contains_kickstart must be true" in template
    assert "secondary_iso_images" not in template
    assert "boot_command" not in template
    assert "boot_keygroup_interval" not in template
    assert "Packer should not race" in template
    assert "http_content" not in template
    assert "http_port_min" not in template
    assert 'default = "Default Switch"' not in template
    assert "build-photon-hyperv-image.ps1" in root_docs
    assert "create-hyperv-switches.ps1" in docs
    assert "builder_static_ip=192.168.49.30/24" in docs
    assert "discovers the host's active IPv4 DNS" in docs
    assert "labfoundry-photon-with-kickstart.iso" in docs
    assert "Using remastered Photon ISO" in docs
    assert "without Packer typing boot commands" in docs
    assert "-PipGlobalIndex" in docs
    assert "-PipGlobalIndexUrl" in docs
    assert "Omit both pip options for standard/default pip behavior." in docs
    assert "[string]$SshPassword = 'VMware01!'" in wrapper
    assert "[string]$BootstrapAdminPassword = 'VMware01!'" in wrapper
    assert "[string[]]$BuilderStaticDns = @()" in wrapper
    assert "[string]$PipGlobalIndex = ''" in wrapper
    assert "[string]$PipGlobalIndexUrl = ''" in wrapper
    assert "function Get-HostIpv4DnsServers" in wrapper
    assert "Get-DnsClientServerAddress -AddressFamily IPv4" in wrapper
    assert "Using host IPv4 DNS for Photon builder/appliance" in wrapper
    assert "falling back to public DNS" in wrapper
    assert "create_photon_kickstart_iso.py" in wrapper
    assert "Using remastered Photon ISO" in wrapper
    assert "Packer will boot a single DVD with embedded photon-ks.json and a GRUB auto-install entry." in wrapper
    assert "Write-PackerVarFile" in wrapper
    assert "Using Packer var-file" in wrapper
    assert "[ValidateSet('cleanup', 'abort', 'ask', 'run-cleanup-provisioner')]" in wrapper
    assert "[string]$PackerOnError = 'cleanup'" in wrapper
    assert "[switch]$KeepExistingOutput" in wrapper
    assert "[switch]$EnableRealSystemAdapters" in wrapper
    assert "Packer build will replace any existing output directory for this build." in wrapper
    assert "$packerArgs += '-force'" in wrapper
    assert '$packerArgs += "-on-error=$PackerOnError"' in wrapper
    assert "'-var-file', $varFilePath" in wrapper
    assert "builder_static_dns       = $BuilderStaticDns" in wrapper
    assert "pip_global_index         = $PipGlobalIndex" in wrapper
    assert "pip_global_index_url     = $PipGlobalIndexUrl" in wrapper
    assert "dry_run_system_adapters  = -not $EnableRealSystemAdapters" in wrapper
    assert "UseHttpKickstartFallback" not in wrapper
    assert "/image/hyperv/build" in gitignore
    remaster_helper = Path("scripts/interop/create_photon_kickstart_iso.py").read_text(encoding="utf-8")
    assert "iso.add_file" in remaster_helper
    assert 'rr_name="photon-ks.json"' in remaster_helper
    assert "GRUB_BOOT_CONFIG" in remaster_helper
    assert "GRUB_CONFIG_TARGETS" in remaster_helper
    assert '"/BOOT/GRUB2/GRUB.CFG;1"' in remaster_helper
    assert "ks=cdrom:/photon-ks.json" in remaster_helper
    assert "photon.media=cdrom" in remaster_helper
    assert '"/EFI/BOOT/GRUB.CFG;1"' in remaster_helper
    assert '"/BOOT/GRUB2/GRUB.CFG;1", "grub.cfg"' in remaster_helper
    assert '"/EFI/BOOT/GRUB.CFG;1", "grub.cfg"' in remaster_helper
    assert "iso.add_fp" in remaster_helper
    assert "iso.rm_file" in remaster_helper
    assert "Could not embed LabFoundry GRUB config" in remaster_helper


def test_photon_image_optional_pip_global_index_configuration():
    wrapper = Path("scripts/windows/build-photon-hyperv-image.ps1").read_text(encoding="utf-8")
    template = Path("image/hyperv/labfoundry-photon.pkr.hcl").read_text(encoding="utf-8")
    script = Path("image/hyperv/scripts/provision-labfoundry.sh").read_text(encoding="utf-8")

    assert "[string[]]$BuilderStaticDns = @(),\n    [string]$PipGlobalIndex = '',\n    [string]$PipGlobalIndexUrl = ''," in wrapper
    assert "pip_global_index         = $PipGlobalIndex" in wrapper
    assert "pip_global_index_url     = $PipGlobalIndexUrl" in wrapper

    assert 'variable "pip_global_index" {\n  type        = string\n  default     = ""' in template
    assert 'variable "pip_global_index_url" {\n  type        = string\n  default     = ""' in template
    assert '"LABFOUNDRY_PIP_GLOBAL_INDEX=${var.pip_global_index}"' in template
    assert '"LABFOUNDRY_PIP_GLOBAL_INDEX_URL=${var.pip_global_index_url}"' in template

    assert 'LABFOUNDRY_PIP_GLOBAL_INDEX="${LABFOUNDRY_PIP_GLOBAL_INDEX:-}"' in script
    assert 'LABFOUNDRY_PIP_GLOBAL_INDEX_URL="${LABFOUNDRY_PIP_GLOBAL_INDEX_URL:-}"' in script
    assert 'PIP_CACHE_DIR="${PIP_CACHE_DIR:-/var/cache/labfoundry-pip}"' in script
    assert "write_pip_config() {" in script
    assert 'printf \'index = %s\\n\' "$LABFOUNDRY_PIP_GLOBAL_INDEX"' in script
    assert 'printf \'index-url = %s\\n\' "$LABFOUNDRY_PIP_GLOBAL_INDEX_URL"' in script
    assert 'printf \'cache-dir = %s\\n\' "$PIP_CACHE_DIR"' in script
    assert 'write_pip_config /etc/pip.conf' in script
    assert 'write_pip_config "$LABFOUNDRY_HOME/.venv/pip.conf"' in script
    assert 'export PIP_DISABLE_PIP_VERSION_CHECK=1' in script
    assert 'export PIP_INDEX_URL="$LABFOUNDRY_PIP_GLOBAL_INDEX_URL"' in script
    assert "pip install --upgrade pip setuptools wheel" not in script
    assert "packages.vcfd.broadcom.net/artifactory" not in wrapper
    assert "packages.vcfd.broadcom.net/artifactory" not in template
    assert "packages.vcfd.broadcom.net/artifactory" not in script


def test_lifecycle_hyperv_script_uses_separate_vm_set_by_default():
    script = Path("scripts/windows/run-hyperv-lifecycle-test.ps1").read_text(encoding="utf-8")
    wrapper = Path("scripts/windows/invoke-hyperv-lifecycle-test.ps1").read_text(encoding="utf-8")
    runner = Path("scripts/interop/lifecycle_test.py").read_text(encoding="utf-8")

    assert "[string]$LabName = 'LabFoundryLifecycle'" in script
    assert "[string]$ApplianceUrl = ''" in script
    assert '$ApplianceUrl = "http://${ApplianceIPAddress}"' in script
    assert "'--appliance-url', $ApplianceUrl" in script
    assert '"http://${ApplianceIPAddress}:8000"' not in script
    assert "[string]$ApplianceUrl = ''" in wrapper
    assert '"http://${ApplianceIPAddress}"' in wrapper
    assert '"http://${ApplianceIPAddress}:8000"' not in wrapper
    assert "'-ApplianceUrl', $effectiveApplianceUrl" in wrapper
    assert 'parser.add_argument("--appliance-url", default="http://192.168.49.1")' in runner
    assert "[string]$SiteInterface = 'eth1.12'" in script
    assert "[string]$SiteCidr = '192.168.12.1/24'" in script
    assert "[int]$SiteVlanId = 12" in script
    assert "$applianceName = \"$LabName-Appliance\"" in script
    assert "$clientAName = \"$LabName-ClientA\"" in script
    assert "$clientBName = \"$LabName-ClientB\"" in script
    assert "$pxeClientName = \"$LabName-PxeBoot\"" in script
    assert "New-LifecyclePxeVm -Name $pxeClientName -SwitchName 'LabFoundry-SiteA'" in script
    assert "Invoke-PxeBootSmoke -Name $pxeClientName -MacAddress $pxeClientMac" in script
    assert "[string]$EsxIsoPath = ''" in script
    assert "[string]$EsxIsoPath = ''" in wrapper
    assert "'-EsxIsoPath', $EsxIsoPath" in wrapper
    assert "--pxe-test-mode" in runner
    assert "--pxe-client-mac" in runner
    assert "--pxe-installer-iso-path" in runner
    assert '"esxi_pxe"' in runner
    assert "configure-esxi-pxe" in runner
    assert "Refusing to use reserved VM name" in script
    assert "@('LabFoundry', 'LabFoundry-Photon-Builder')" in script
    assert "image\\hyperv\\clients\\alpine-cloud\\labfoundry-tiny-linux-client.vhdx" in script
    assert "Running lifecycle appliance VM(s) may already own ${ApplianceIPAddress}" in script
    assert "-CleanupVmsOnly" in script


def test_create_labfoundry_test_vm_wrapper_is_safe_and_simple():
    script = Path("scripts/windows/create-labfoundry-test-vm.ps1").read_text(encoding="utf-8")
    vm_script = Path("scripts/windows/create-labfoundry-vm.ps1").read_text(encoding="utf-8")
    docs = Path("image/hyperv/README.md").read_text(encoding="utf-8")

    assert "[string]$Name = 'LabFoundry'" in script
    assert "[switch]$Redeploy" in script
    assert "[switch]$ResetDataDisks" in script
    assert "[switch]$SkipLabNetworkAdapters" in script
    assert "[int]$SiteVlanId = 12" in script
    assert "[int]$TaggedVlanId = 50" in script
    assert "[switch]$WaitForIp" in script
    assert "Find-LatestApplianceVhdx" in script
    assert "Remove-ExistingDataDisks" in script
    assert "LabFoundry-Depot.vhdx" in script
    assert "LabFoundry-Backups.vhdx" in script
    assert "Refusing to remove OS disk as a data disk" in script
    assert "create-hyperv-switches.ps1" in script
    assert "create-labfoundry-vm.ps1" in script
    assert "-SkipLabNetworkAdapters:$SkipLabNetworkAdapters" in script
    assert "-SiteVlanId $SiteVlanId" in script
    assert "-TaggedVlanId $TaggedVlanId" in script
    assert "start-labfoundry-vm.ps1" in script
    assert "get-labfoundry-vm-ip.ps1" in script
    assert "VM already exists: $Name. Pass -Redeploy" in script
    assert "Remove-VM -Name $Name -Force" in script
    assert "Run this script from an elevated PowerShell session." not in script
    assert "create-labfoundry-test-vm.ps1 -WaitForIp" in docs
    assert "pass `-Redeploy` to remove and recreate only that VM" in docs
    assert "same appliance-side lab NIC layout as the lifecycle" in docs
    assert "SiteA` on `LabFoundry-SiteA` as trunk VLAN 12" in docs
    assert "`Trunk` on" in docs and "`LabFoundry-Trunk` as trunk VLAN 50" in docs
    assert "WAN-Test` on `LabFoundry-SiteB` as" in docs
    assert "`/var/lib/labfoundry/users/<admin>` with `/usr/bin/pwsh`" in docs
    assert "`powershell` package" in docs
    assert "[switch]$SkipLabNetworkAdapters" in vm_script
    assert "[int]$SiteVlanId = 12" in vm_script
    assert "[int]$TaggedVlanId = 50" in vm_script
    assert "Add-LabNetworkAdapters" in vm_script
    assert "Add-VMNetworkAdapter -VMName $VMName -Name 'SiteA' -SwitchName 'LabFoundry-SiteA'" in vm_script
    assert "Set-VMNetworkAdapterVlan -VMName $VMName -VMNetworkAdapterName 'SiteA' -Trunk -AllowedVlanIdList \"$SiteTag\" -NativeVlanId 0" in vm_script
    assert "Add-VMNetworkAdapter -VMName $VMName -Name 'Trunk' -SwitchName 'LabFoundry-Trunk'" in vm_script
    assert "Set-VMNetworkAdapterVlan -VMName $VMName -VMNetworkAdapterName 'Trunk' -Trunk -AllowedVlanIdList \"$TaggedVlanTag\" -NativeVlanId 0" in vm_script
    assert "Add-VMNetworkAdapter -VMName $VMName -Name 'WAN-Test' -SwitchName 'LabFoundry-SiteB'" in vm_script


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
    assert "[string]$ApplianceSshUser = 'admin'" in script
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

    assert "[string]$ApplianceSshUser = 'admin'" in script
    assert "Get-NetNeighbor -AddressFamily IPv4" in script
    assert "ConvertTo-HyphenMac" in script
    assert "Wait-GuestIPv4 -Name $clientAName" in script
    assert "function Test-TcpPort" in script
    assert "Get-PlinkHostKey" in script
    assert "$applianceHostKey = Get-PlinkHostKey -HostName $ApplianceIPAddress" in script
    assert "(Get-Date).AddMinutes(4)" in script
    assert "$ErrorActionPreference = 'Continue'" in script
    assert "Timed out waiting for SSH host key" in script
    assert "Set-VMNetworkAdapterVlan -VMName $applianceName -VMNetworkAdapterName 'SiteA' -Trunk" in script
    assert "Set-VMNetworkAdapterVlan -VMName $clientAName -VMNetworkAdapterName 'SiteA-Test' -Access -VlanId $SiteVlanId" in script
    assert "Appliance-Mgmt-Test" in script
    assert "'--appliance-ssh-hostkey'" in script
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

    assert plan["apply_units"] == [
        "local_users",
        "network",
        "firewall",
        "wan",
        "dnsmasq",
        "esxi_pxe",
        "ca",
        "kms",
        "appliance_settings",
        "vcf_backups",
    ]
    assert plan["interfaces"]["vlan"]["name"] == "eth2.50"
    assert plan["interfaces"]["client_ca_request"]["name"] == "eth3"
    assert plan["interfaces"]["client_ca_request"]["ip_cidr"] == "192.168.49.20/24"
    assert "CA desired state, root certificate download, client CSR request, issued certificate download, and client-side verification" in plan["checks"]
    assert "VCF Backup desired state, local user sync, SFTP listener, and client probe" in plan["checks"]
    assert plan["pxe_boot"]["enabled"] is False
    assert plan["pxe_boot"]["mode"] == "linux"


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

    assert "direct_dns_a_query_command" in script
    assert "base64 -d | python3 -" in script
    assert '"dnsmasq": "test -f /etc/labfoundry/dnsmasq.d/labfoundry.conf && getent hosts interop-appliance.labfoundry.internal"' not in script
    assert "--vcf-backup-password" in script
    assert "--client-ca-request-interface" in script
    assert "configure_management_https" in script
    assert "management_https_check" in script
    assert "apply-appliance-settings-unit" in script
    assert "HTTP management endpoint should redirect after HTTPS apply" in script
    assert "https_request_unverified" in script
    assert "configure-vcf-backups" in script
    assert "configure-kms" in script
    assert '"/kms/clients"' in script
    assert '"name": "vcf-management"' in script
    assert "Hyper-V lifecycle KMIP client" in script
    assert "labfoundry-kms.service" in script
    assert "kms_files" in script
    assert "kms_service" in script
    assert "kms_tls" in script
    assert "apply-kms-unit" in script
    assert "Appliance apply task failed" in script
    assert "/etc/labfoundry/kms/clients/certs/vcf-management.crt" in script
    assert "/etc/labfoundry/kms/clients/vcf-management.crt" not in script
    assert "missing $path" in script
    assert "stderr:" in script
    assert "stdout:" in script
    assert "vcf-backup-client-check" in script
    assert "sshpass -p" in script
    assert "redact_text" in script
    assert '"local_users", "network", "firewall", "wan", "dnsmasq", "esxi_pxe", "vcf_backups"' in script
    assert "certificate_summary" in script
    assert "root_ca" in script
    assert "ca-client-certificate-request" in script
    assert "ca-client-certificate-check" in script
    assert "create_client_csr" in script
    assert '"listen_interfaces_present": "1"' in script
    assert '"listen_interfaces": [args.site_interface]' in script
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
    assert "tc qdisc show dev {args.wan_interface} | grep netem | grep delay | grep 25ms" in script
    assert "Lifecycle summary" in script
    assert "Result JSON:" in script


def test_lifecycle_runner_summarizes_apply_validation_html():
    module = load_lifecycle_runner()
    summary = module.summarize_html_response(
        """
        <!doctype html>
        <html>
          <body>
            <aside>LabFoundry navigation noise</aside>
            <div class="alert error">Resolve validation errors before submitting appliance changes.</div>
            <article>
              <strong>Certificate Authority</strong>
              <div class="alert error"><div>CA service requires at least one listen interface.</div></div>
            </article>
          </body>
        </html>
        """
    )

    assert summary.startswith("Resolve validation errors before submitting appliance changes.")
    assert "CA service requires at least one listen interface." in summary
    assert "doctype" not in summary.lower()


def test_lifecycle_roadmap_splits_pester_and_pytest_ownership():
    doc = Path("docs/hyperv-lifecycle-testing.md").read_text(encoding="utf-8")

    assert "Invoke-Pester tests/pester/HyperVLifecycle.Tests.ps1" in doc
    assert "Python appliance and guest assertions must remain pytest-covered" in doc
    assert "scripts/interop/lifecycle_test.py" in doc

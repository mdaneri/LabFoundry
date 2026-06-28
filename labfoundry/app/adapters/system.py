from dataclasses import dataclass
from pathlib import Path
import subprocess

from labfoundry.app.config import get_settings


@dataclass(frozen=True)
class AdapterResult:
    command: list[str]
    dry_run: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class SystemAdapter:
    """Safe MVP adapter that records approved command intent without executing it."""

    HELPER_PATH = "/opt/labfoundry/bin/labfoundry-helper"

    def __init__(self, dry_run: bool | None = None) -> None:
        settings = get_settings()
        self.dry_run = settings.dry_run_system_adapters if dry_run is None else dry_run

    def apply_wan_policy(self, interface_name: str, policy_name: str) -> AdapterResult:
        return self._record_only_result(["tc", "qdisc", "replace", "dev", interface_name, "root", "netem", "policy", policy_name], "dry-run: WAN policy command recorded")

    def clear_wan_policy(self, interface_name: str) -> AdapterResult:
        return self._record_only_result(["tc", "qdisc", "del", "dev", interface_name, "root"], "dry-run: WAN policy clear command recorded")

    def validate_wan_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("wan", "validate", config_path, dry_run_message="dry-run: WAN config validation command recorded")

    def apply_wan_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("wan", "apply", config_path, dry_run_message="dry-run: WAN config apply command recorded")

    def service_action(self, service: str, action: str) -> AdapterResult:
        return self._record_only_result(["systemctl", action, service], f"dry-run: service {action} recorded for {service}")

    def validate_dnsmasq_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("dnsmasq", "validate", config_path, dry_run_message="dry-run: dnsmasq validation command recorded")

    def validate_local_users_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("local-users", "validate", config_path, dry_run_message="dry-run: local users validation command recorded")

    def apply_local_users_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("local-users", "apply", config_path, dry_run_message="dry-run: local users apply command recorded")

    def local_users_status(self, config_path: str) -> AdapterResult:
        if self.dry_run:
            return AdapterResult(command=["labfoundry-helper", "local-users", "status", config_path], dry_run=True, stdout='{"users":[],"status":"dry-run"}')
        return self._helper_result("local-users", "status", config_path, dry_run_message="dry-run: local users status command recorded")

    def apply_dnsmasq_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("dnsmasq", "apply", config_path, dry_run_message="dry-run: dnsmasq apply command recorded")

    def reload_dnsmasq(self) -> AdapterResult:
        return self._helper_result("dnsmasq", "reload", dry_run_message="dry-run: dnsmasq reload command recorded")

    def validate_esxi_pxe_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("esxi-pxe", "validate", config_path, dry_run_message="dry-run: ESXi PXE validation command recorded")

    def apply_esxi_pxe_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("esxi-pxe", "apply", config_path, dry_run_message="dry-run: ESXi PXE apply command recorded")

    def read_dhcp_leases(self) -> AdapterResult:
        if self.dry_run:
            return self._record_only_result(
                ["labfoundry-helper", "dnsmasq", "leases"],
                (
                    "1893456000 02:15:5d:00:20:30 192.168.50.130 api-client.labfoundry.internal 01:02:15:5d:00:20:30\n"
                    "1893459600 02:15:5d:00:20:31 192.168.50.131 vcsa.labfoundry.internal 01:02:15:5d:00:20:31"
                ),
            )
        helper_result = self._helper_result("dnsmasq", "leases", dry_run_message="dry-run: dnsmasq lease read command recorded", use_sudo=False)
        if helper_result.returncode == 0:
            return helper_result
        helper_result = self._helper_result("dnsmasq", "leases", dry_run_message="dry-run: dnsmasq lease read command recorded")
        if "sudo:" in helper_result.stderr and "password is required" in helper_result.stderr:
            return AdapterResult(
                command=helper_result.command,
                dry_run=False,
                stdout=helper_result.stdout,
                stderr=(
                    "DHCP lease readback needs the updated LabFoundry sudoers helper rule. "
                    "Reinstall the appliance helper/sudoers configuration, then restart labfoundry.service."
                ),
                returncode=helper_result.returncode,
            )
        return helper_result

    def apply_ca_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("ca", "apply", config_path, dry_run_message="dry-run: CA apply command recorded")

    def validate_ca_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("ca", "validate", config_path, dry_run_message="dry-run: CA validation command recorded")

    def apply_kms_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("kms", "apply", config_path, dry_run_message="dry-run: KMS apply command recorded")

    def validate_kms_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("kms", "validate", config_path, dry_run_message="dry-run: KMS validation command recorded")

    def apply_network_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("network", "apply", config_path, dry_run_message="dry-run: network apply command recorded")

    def validate_network_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("network", "validate", config_path, dry_run_message="dry-run: network validation command recorded")

    def apply_appliance_settings_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("appliance-settings", "apply", config_path, dry_run_message="dry-run: appliance settings apply command recorded")

    def validate_appliance_settings_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("appliance-settings", "validate", config_path, dry_run_message="dry-run: appliance settings validation command recorded")

    def apply_firewall_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("firewall", "apply", config_path, dry_run_message="dry-run: firewall apply command recorded")

    def validate_firewall_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("firewall", "validate", config_path, dry_run_message="dry-run: firewall validation command recorded")

    def apply_vcf_backup_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("vcf-backups", "apply", config_path, dry_run_message="dry-run: VCF backup SFTP apply command recorded")

    def validate_vcf_backup_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("vcf-backups", "validate", config_path, dry_run_message="dry-run: VCF backup SFTP validation command recorded")

    def validate_vcf_private_registry_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-private-registry", "validate", config_path], "dry-run: VCF private registry validation command recorded")

    def apply_vcf_private_registry_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-private-registry", "apply", config_path], "dry-run: VCF private registry apply command recorded")

    def relocate_vcf_private_registry_bundles(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-private-registry", "relocate-bundles", config_path], "dry-run: VCF private registry bundle relocation command recorded")

    def validate_vcf_offline_depot_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("vcf-offline-depot", "validate", config_path, dry_run_message="dry-run: VCF Offline Depot validation command recorded")

    def stage_vcf_offline_depot_tool(self, archive_path: str) -> AdapterResult:
        helper_archive_path = str(Path(archive_path).resolve()) if not archive_path.startswith("/") else archive_path
        return self._helper_result("vcf-offline-depot", "stage-tool", helper_archive_path, dry_run_message="dry-run: VCF Download Tool extraction command recorded")

    def sync_vcf_offline_depot(self, config_path: str) -> AdapterResult:
        return self._helper_result("vcf-offline-depot", "sync", config_path, dry_run_message="dry-run: VCF Offline Depot sync command recorded")

    def apply_vcf_offline_depot_https_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("vcf-offline-depot", "apply-https", config_path, dry_run_message="dry-run: VCF Offline Depot HTTPS apply command recorded")

    def _record_only_result(self, command: list[str], stdout: str) -> AdapterResult:
        return AdapterResult(command=command, dry_run=True, stdout=stdout)

    def _helper_result(self, group: str, action: str, *args: str, dry_run_message: str, use_sudo: bool = True) -> AdapterResult:
        display_command = ["labfoundry-helper", group, action, *args]
        if self.dry_run:
            return AdapterResult(command=display_command, dry_run=True, stdout=dry_run_message)

        command = [self.HELPER_PATH, group, action, "--real", *args]
        if use_sudo:
            command = ["sudo", "-n", *command]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return AdapterResult(
                command=command,
                dry_run=False,
                stderr=f"Unable to execute {' '.join(command)}: {exc}",
                returncode=127,
            )
        return AdapterResult(
            command=command,
            dry_run=False,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

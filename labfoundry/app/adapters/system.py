from dataclasses import dataclass
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
        return self._record_only_result(["labfoundry-helper", "wan", "validate", config_path], "dry-run: WAN config validation command recorded")

    def apply_wan_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "wan", "apply", config_path], "dry-run: WAN config apply command recorded")

    def service_action(self, service: str, action: str) -> AdapterResult:
        return self._record_only_result(["systemctl", action, service], f"dry-run: service {action} recorded for {service}")

    def validate_dnsmasq_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("dnsmasq", "validate", config_path, dry_run_message="dry-run: dnsmasq validation command recorded")

    def apply_dnsmasq_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("dnsmasq", "apply", config_path, dry_run_message="dry-run: dnsmasq apply command recorded")

    def reload_dnsmasq(self) -> AdapterResult:
        return self._helper_result("dnsmasq", "reload", dry_run_message="dry-run: dnsmasq reload command recorded")

    def read_dhcp_leases(self) -> AdapterResult:
        return self._record_only_result(
            ["labfoundry-helper", "dnsmasq", "leases"],
            (
                "1893456000 02:15:5d:00:20:30 192.168.50.130 api-client.labfoundry.internal 01:02:15:5d:00:20:30\n"
                "1893459600 02:15:5d:00:20:31 192.168.50.131 vcsa.labfoundry.internal 01:02:15:5d:00:20:31"
            ),
        )

    def apply_ca_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "ca", "apply", config_path], "dry-run: CA apply command recorded")

    def validate_ca_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "ca", "validate", config_path], "dry-run: CA validation command recorded")

    def apply_kms_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "kms", "apply", config_path], "dry-run: KMS apply command recorded")

    def validate_kms_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "kms", "validate", config_path], "dry-run: KMS validation command recorded")

    def apply_network_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("network", "apply", config_path, dry_run_message="dry-run: network apply command recorded")

    def validate_network_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("network", "validate", config_path, dry_run_message="dry-run: network validation command recorded")

    def apply_firewall_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("firewall", "apply", config_path, dry_run_message="dry-run: firewall apply command recorded")

    def validate_firewall_config(self, config_path: str) -> AdapterResult:
        return self._helper_result("firewall", "validate", config_path, dry_run_message="dry-run: firewall validation command recorded")

    def apply_vcf_backup_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-backups", "apply", config_path], "dry-run: VCF backup SFTP apply command recorded")

    def validate_vcf_backup_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-backups", "validate", config_path], "dry-run: VCF backup SFTP validation command recorded")

    def validate_vcf_private_registry_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-private-registry", "validate", config_path], "dry-run: VCF private registry validation command recorded")

    def apply_vcf_private_registry_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-private-registry", "apply", config_path], "dry-run: VCF private registry apply command recorded")

    def relocate_vcf_private_registry_bundles(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-private-registry", "relocate-bundles", config_path], "dry-run: VCF private registry bundle relocation command recorded")

    def validate_vcf_offline_depot_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-offline-depot", "validate", config_path], "dry-run: VCF Offline Depot validation command recorded")

    def stage_vcf_offline_depot_tool(self, archive_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-offline-depot", "stage-tool", archive_path], "dry-run: VCF Download Tool staging command recorded")

    def sync_vcf_offline_depot(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-offline-depot", "sync", config_path], "dry-run: VCF Offline Depot sync command recorded")

    def apply_vcf_offline_depot_https_config(self, config_path: str) -> AdapterResult:
        return self._record_only_result(["labfoundry-helper", "vcf-offline-depot", "apply-https", config_path], "dry-run: VCF Offline Depot HTTPS apply command recorded")

    def _record_only_result(self, command: list[str], stdout: str) -> AdapterResult:
        return AdapterResult(command=command, dry_run=True, stdout=stdout)

    def _helper_result(self, group: str, action: str, *args: str, dry_run_message: str) -> AdapterResult:
        display_command = ["labfoundry-helper", group, action, *args]
        if self.dry_run:
            return AdapterResult(command=display_command, dry_run=True, stdout=dry_run_message)

        command = ["sudo", "-n", self.HELPER_PATH, group, action, "--real", *args]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        return AdapterResult(
            command=command,
            dry_run=False,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "appliance" / "labfoundry-helper"


def load_helper_module():
    loader = importlib.machinery.SourceFileLoader("labfoundry_helper", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader("labfoundry_helper", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def network_config_text(*, eth2_mode: str = "trunk", include_vlan: bool = True, include_removed_vlan: bool = False) -> str:
    lines = [
        "[physical_interfaces]",
        "interface=eth0",
        "  role=management",
        "  mode=access",
        "  ip_cidr=192.168.49.1/24",
        "  admin_state=up",
        "  mtu=1500",
        "interface=eth2",
        "  role=access",
        f"  mode={eth2_mode}",
        "  ip_cidr=",
        "  admin_state=up",
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


def test_network_helper_renders_systemd_networkd_files(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(), encoding="utf-8")

    files, links = helper._systemd_networkd_files(config_path)

    assert "00-labfoundry-mgmt.network" in files
    assert "Name=eth0" in files["00-labfoundry-mgmt.network"]
    assert "Name=eth*" not in files["00-labfoundry-mgmt.network"]
    assert "Address=192.168.49.1/24" in files["00-labfoundry-mgmt.network"]
    assert "10-labfoundry-eth2.network" in files
    assert "VLAN=eth2.20" in files["10-labfoundry-eth2.network"]
    assert "10-labfoundry-eth2.20.netdev" in files
    assert "Id=20" in files["10-labfoundry-eth2.20.netdev"]
    assert "10-labfoundry-eth2.20.network" in files
    assert "Address=192.168.20.1/24" in files["10-labfoundry-eth2.20.network"]
    assert links == ["eth2", "eth2.20"]


def test_network_helper_installs_networkd_files_and_reconfigures_non_management(monkeypatch, tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-network.conf"
    config_path.write_text(network_config_text(), encoding="utf-8")
    networkd_dir = tmp_path / "systemd-network"
    networkd_dir.mkdir()
    old_managed = networkd_dir / "10-labfoundry-old.network"
    old_managed.write_text("old", encoding="utf-8")
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

    returncode, installed, links = helper._install_systemd_networkd_files(config_path)

    assert returncode == 0
    assert not old_managed.exists()
    assert (networkd_dir / "00-labfoundry-mgmt.network").is_file()
    assert (networkd_dir / "10-labfoundry-eth2.network").is_file()
    assert (networkd_dir / "10-labfoundry-eth2.20.netdev").is_file()
    assert ["networkctl", "reload"] in commands
    assert ["networkctl", "reconfigure", "eth2"] in commands
    assert ["networkctl", "reconfigure", "eth2.20"] in commands
    assert ["networkctl", "reconfigure", "eth0"] not in commands
    assert any(path.endswith("00-labfoundry-mgmt.network") for path in installed)
    assert links == ["eth2", "eth2.20"]


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


def test_local_users_helper_creates_locks_and_sets_password_without_leaking(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "local-users"
    home_base = tmp_path / "users"
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
        if command in (["id", "sync-user"], ["id", "disabled-user"]):
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_run_with_input(command: list[str], input_text: str) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdin_values.append(input_text)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "LOCAL_USERS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "LOCAL_USERS_HOME_BASE", home_base)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper, "_run_with_input", fake_run_with_input)

    assert helper._handle_local_users("apply", [str(config_path)]) == 0
    captured = capsys.readouterr()

    assert ["useradd", "--home-dir", (home_base / "sync-user").as_posix(), "--create-home", "--shell", "/sbin/nologin", "sync-user"] in commands
    assert ["passwd", "-u", "sync-user"] in commands
    assert ["passwd", "-l", "disabled-user"] in commands
    assert stdin_values == ["sync-user:BridgeStrong1!\n"]
    assert all("BridgeStrong1!" not in arg for command in commands for arg in command)
    assert "BridgeStrong1!" not in captured.out
    assert "BridgeStrong1!" not in captured.err


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


def appliance_settings_json(
    *,
    resolver_mode: str = "local_dns",
    resolver_servers: list[str] | None = None,
    local_dns_enabled: bool = True,
) -> str:
    import json

    return json.dumps(
        {
            "fqdn": "labfoundry.labfoundry.internal",
            "resolver_mode": resolver_mode,
            "resolver_servers": resolver_servers or ["127.0.0.1"],
            "local_dns_enabled": local_dns_enabled,
            "management_interface": "eth0",
            "management_ip": "192.168.49.1",
            "management_ip_cidr": "192.168.49.1/24",
            "ntp_servers": ["time1.google.com", "time2.google.com"],
        }
    )


def test_appliance_settings_helper_validates_staged_json(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-settings.json"
    config_path.write_text(appliance_settings_json(), encoding="utf-8")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)

    assert helper._handle_appliance_settings("validate", [str(config_path)]) == 0


def test_appliance_settings_helper_rejects_invalid_json(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-settings.json"
    config_path.write_text('{"fqdn": "bad name"}', encoding="utf-8")

    errors = helper._appliance_settings_config_errors(config_path)

    assert "fqdn must be a valid fully qualified DNS name." in errors
    assert "resolver_mode must be local_dns or external." in errors
    assert "ntp_servers must include at least one server." in errors


def test_appliance_settings_helper_applies_local_resolver_and_timesyncd(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    networkd_dir = tmp_path / "etc" / "systemd" / "network"
    timesyncd_dir = tmp_path / "etc" / "systemd" / "timesyncd.conf.d"
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
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", mgmt_network)
    monkeypatch.setattr(helper, "TIMESYNCD_DROPIN_DIR", timesyncd_dir)
    monkeypatch.setattr(helper, "TIMESYNCD_DROPIN_PATH", timesyncd_dir / "labfoundry.conf")
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/bin/hostnamectl" if command == "hostnamectl" else None)

    assert helper._handle_appliance_settings("apply", [str(config_path)]) == 0

    assert ["/usr/bin/hostnamectl", "set-hostname", "labfoundry.labfoundry.internal"] in commands
    assert ["resolvectl", "dns", "eth0", "127.0.0.1"] in commands
    assert ["resolvectl", "domain", "eth0", "~."] in commands
    assert ["systemctl", "enable", "--now", "systemd-timesyncd"] in commands
    assert ["systemctl", "restart", "systemd-timesyncd"] in commands
    network_text = mgmt_network.read_text(encoding="utf-8")
    assert "DNS=1.1.1.1" not in network_text
    assert "DNS=127.0.0.1" in network_text
    assert "Domains=~." in network_text
    timesyncd = (timesyncd_dir / "labfoundry.conf").read_text(encoding="utf-8")
    assert "NTP=time1.google.com time2.google.com" in timesyncd


def test_appliance_settings_helper_applies_external_resolver_without_catchall(monkeypatch, tmp_path):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-settings"
    networkd_dir = tmp_path / "etc" / "systemd" / "network"
    timesyncd_dir = tmp_path / "etc" / "systemd" / "timesyncd.conf.d"
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
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "APPLIANCE_SETTINGS_APPLY_DIR", apply_dir)
    monkeypatch.setattr(helper, "NETWORKD_MGMT_CONFIG_PATH", mgmt_network)
    monkeypatch.setattr(helper, "TIMESYNCD_DROPIN_DIR", timesyncd_dir)
    monkeypatch.setattr(helper, "TIMESYNCD_DROPIN_PATH", timesyncd_dir / "labfoundry.conf")
    monkeypatch.setattr(helper, "_run", fake_run)
    monkeypatch.setattr(helper.shutil, "which", lambda command: "/usr/bin/hostnamectl" if command == "hostnamectl" else None)

    assert helper._handle_appliance_settings("apply", [str(config_path)]) == 0

    assert ["/usr/bin/hostnamectl", "set-hostname", "labfoundry.labfoundry.internal"] in commands
    assert ["resolvectl", "dns", "eth0", "1.1.1.1", "9.9.9.9"] in commands
    assert ["resolvectl", "domain", "eth0", ""] in commands
    network_text = mgmt_network.read_text(encoding="utf-8")
    assert "DNS=127.0.0.1" not in network_text
    assert "Domains=~." not in network_text
    assert "DNS=1.1.1.1" in network_text
    assert "DNS=9.9.9.9" in network_text


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

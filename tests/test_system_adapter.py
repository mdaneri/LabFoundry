import subprocess

from labfoundry.app.adapters.system import SystemAdapter


def test_real_dhcp_leases_use_unprivileged_helper_first(monkeypatch):
    import labfoundry.app.adapters.system as system_adapter

    commands: list[list[str]] = []

    def fake_run(command, check, capture_output, text):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            "1893456000 02:15:5d:00:20:40 192.168.50.140 live-client.labfoundry.internal *\n",
            "",
        )

    monkeypatch.setattr(system_adapter.subprocess, "run", fake_run)

    result = SystemAdapter(dry_run=False).read_dhcp_leases()

    assert result.returncode == 0
    assert result.dry_run is False
    assert result.command == [SystemAdapter.HELPER_PATH, "dnsmasq", "leases", "--real"]
    assert commands == [[SystemAdapter.HELPER_PATH, "dnsmasq", "leases", "--real"]]
    assert "live-client.labfoundry.internal" in result.stdout


def test_real_dhcp_leases_fall_back_to_sudo_helper(monkeypatch):
    import labfoundry.app.adapters.system as system_adapter

    commands: list[list[str]] = []

    def fake_run(command, check, capture_output, text):
        commands.append(command)
        if command[0] == SystemAdapter.HELPER_PATH:
            return subprocess.CompletedProcess(command, 1, "", "permission denied\n")
        return subprocess.CompletedProcess(
            command,
            0,
            "1893456000 02:15:5d:00:20:41 192.168.50.141 fallback-client.labfoundry.internal *\n",
            "",
        )

    monkeypatch.setattr(system_adapter.subprocess, "run", fake_run)

    result = SystemAdapter(dry_run=False).read_dhcp_leases()

    assert result.returncode == 0
    assert result.command == ["sudo", "-n", SystemAdapter.HELPER_PATH, "dnsmasq", "leases", "--real"]
    assert commands == [
        [SystemAdapter.HELPER_PATH, "dnsmasq", "leases", "--real"],
        ["sudo", "-n", SystemAdapter.HELPER_PATH, "dnsmasq", "leases", "--real"],
    ]
    assert "fallback-client.labfoundry.internal" in result.stdout


def test_real_dhcp_leases_sudo_password_error_becomes_operator_guidance(monkeypatch):
    import labfoundry.app.adapters.system as system_adapter

    def fake_run(command, check, capture_output, text):
        if command[0] == SystemAdapter.HELPER_PATH:
            return subprocess.CompletedProcess(command, 1, "", "permission denied\n")
        return subprocess.CompletedProcess(command, 1, "", "sudo: a password is required\n")

    monkeypatch.setattr(system_adapter.subprocess, "run", fake_run)

    result = SystemAdapter(dry_run=False).read_dhcp_leases()

    assert result.returncode == 1
    assert result.command == ["sudo", "-n", SystemAdapter.HELPER_PATH, "dnsmasq", "leases", "--real"]
    assert "updated LabFoundry sudoers helper rule" in result.stderr
    assert "sudo: a password is required" not in result.stderr


def test_real_vcf_backup_apply_uses_sudo_helper(monkeypatch):
    import labfoundry.app.adapters.system as system_adapter

    commands: list[list[str]] = []

    def fake_run(command, check, capture_output, text):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"vcf_backups": "apply complete"}\n', "")

    monkeypatch.setattr(system_adapter.subprocess, "run", fake_run)

    result = SystemAdapter(dry_run=False).apply_vcf_backup_config("/var/lib/labfoundry/apply/vcf-backups/labfoundry-vcf-backups-sshd.conf")

    assert result.returncode == 0
    assert result.dry_run is False
    assert result.command == [
        "sudo",
        "-n",
        SystemAdapter.HELPER_PATH,
        "vcf-backups",
        "apply",
        "--real",
        "/var/lib/labfoundry/apply/vcf-backups/labfoundry-vcf-backups-sshd.conf",
    ]
    assert commands == [result.command]


def test_real_local_user_authentication_passes_password_only_on_stdin(monkeypatch):
    import labfoundry.app.adapters.system as system_adapter

    calls: list[tuple[list[str], str | None]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("input")))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(system_adapter.subprocess, "run", fake_run)

    password = "Depot-user1!"
    result = SystemAdapter(dry_run=False).authenticate_local_user("vcf-depot", password)

    assert result.returncode == 0
    assert result.command == ["sudo", "-n", SystemAdapter.HELPER_PATH, "local-users", "authenticate", "--real", "vcf-depot"]
    assert calls == [(result.command, f"{password}\n")]
    assert password not in " ".join(result.command)
    assert password not in result.stdout
    assert password not in result.stderr


def test_dry_run_local_user_authentication_fails_closed():
    result = SystemAdapter(dry_run=True).authenticate_local_user("vcf-depot", "Depot-user1!")

    assert result.returncode == 1
    assert result.dry_run is True

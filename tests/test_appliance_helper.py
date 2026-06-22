import importlib.machinery
import importlib.util
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

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
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

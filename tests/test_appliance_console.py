import importlib.machinery
import importlib.util
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

import labfoundry.app.appliance_console as appliance_console
from labfoundry.app.appliance_console import (
    CursesConsole,
    ConsoleOperationError,
    configure_firewall,
    management_urls,
    schedule_power,
    validate_dns_servers,
    validate_ipv6_management_values,
    validate_management_values,
    validate_ntp_servers,
)


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "appliance" / "labfoundry-helper"


def load_helper_module():
    loader = importlib.machinery.SourceFileLoader("labfoundry_helper_console", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader("labfoundry_helper_console", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_console_management_validation_limits_dhcp_and_static_values():
    assert validate_management_values("dhcp", "", "") == ("dhcp", "", "")
    assert validate_management_values("static", "192.168.49.1/24", "192.168.49.254") == (
        "static",
        "192.168.49.1/24",
        "192.168.49.254",
    )
    with pytest.raises(ConsoleOperationError, match="on-link"):
        validate_management_values("static", "192.168.49.1/24", "192.168.50.1")
    with pytest.raises(ConsoleOperationError, match="cannot include"):
        validate_management_values("dhcp", "192.168.49.1/24", "")


def test_console_dns_and_ntp_validation_accept_compact_lists():
    assert validate_dns_servers("1.1.1.1, 9.9.9.9") == ["1.1.1.1", "9.9.9.9"]
    assert validate_ntp_servers("time.cloudflare.com nts.netnod.se") == ["time.cloudflare.com", "nts.netnod.se"]
    with pytest.raises(ConsoleOperationError, match="DNS server"):
        validate_dns_servers("resolver.example.com")
    with pytest.raises(ConsoleOperationError, match="NTP server"):
        validate_ntp_servers("bad_name")


def test_console_ipv6_management_validation_supports_independent_modes_and_gateways():
    assert validate_ipv6_management_values("disabled", "", "") == ("disabled", "", "")
    assert validate_ipv6_management_values("automatic", "", "") == ("automatic", "", "")
    assert validate_ipv6_management_values("static", "2001:db8:49::10/64", "2001:db8:49::1") == (
        "static",
        "2001:db8:49::10/64",
        "2001:db8:49::1",
    )
    assert validate_ipv6_management_values("static", "2001:db8:49::10/64", "fe80::1") == (
        "static",
        "2001:db8:49::10/64",
        "fe80::1",
    )
    with pytest.raises(ConsoleOperationError, match="cannot include"):
        validate_ipv6_management_values("automatic", "2001:db8:49::10/64", "")
    with pytest.raises(ConsoleOperationError, match="must use IPv6"):
        validate_ipv6_management_values("static", "192.168.49.10/24", "")
    with pytest.raises(ConsoleOperationError, match="must use IPv6"):
        validate_ipv6_management_values("static", "2001:db8:49::10/64", "192.168.49.1")
    with pytest.raises(ConsoleOperationError, match="link-local or on-link"):
        validate_ipv6_management_values("static", "2001:db8:49::10/64", "2001:db8:50::1")
    with pytest.raises(ConsoleOperationError, match="cannot equal"):
        validate_ipv6_management_values("static", "2001:db8:49::10/64", "2001:db8:49::10")


def test_console_management_urls_bracket_ipv6_and_ignore_link_local_addresses():
    assert management_urls("appliance.labfoundry.internal", "192.168.49.10/24", "2001:db8:49::10/64") == (
        "https://appliance.labfoundry.internal/",
        "https://192.168.49.10/",
        "https://[2001:db8:49::10]/",
    )
    assert management_urls("", "", "fe80::10/64") == ()


def test_console_load_summary_uses_one_five_and_fifteen_minute_averages(monkeypatch):
    monkeypatch.setattr(appliance_console.os, "getloadavg", lambda: (0.125, 1.5, 12.345), raising=False)

    assert appliance_console._load_summary() == "1 min 0.12 | 5 min 1.50 | 15 min 12.35"


def test_console_release_summary_drops_embedded_photon_metadata_lines():
    release = "VMware Photon OS 5.0\nPHOTON_BUILD_NUMBER=12345\n"

    assert appliance_console._first_display_line(release, "Linux") == "VMware Photon OS 5.0"


def test_console_uses_bounded_recovery_redraws_after_service_activity():
    assert CursesConsole._recovery_redraws(10.0) == [11.0, 13.0, 18.0]


def test_console_refresh_interval_defaults_to_five_seconds_and_is_bounded(monkeypatch):
    monkeypatch.delenv(appliance_console.CONSOLE_REFRESH_ENV, raising=False)
    assert appliance_console._console_refresh_seconds() == 5
    monkeypatch.setenv(appliance_console.CONSOLE_REFRESH_ENV, "15")
    assert appliance_console._console_refresh_seconds() == 15
    monkeypatch.setenv(appliance_console.CONSOLE_REFRESH_ENV, "0")
    assert appliance_console._console_refresh_seconds() == 1
    monkeypatch.setenv(appliance_console.CONSOLE_REFRESH_ENV, "999")
    assert appliance_console._console_refresh_seconds() == 300
    monkeypatch.setenv(appliance_console.CONSOLE_REFRESH_ENV, "invalid")
    assert appliance_console._console_refresh_seconds() == 5


def test_console_text_editor_supports_cursor_navigation_insertion_and_deletion():
    class FakeCurses:
        KEY_LEFT = 1
        KEY_RIGHT = 2
        KEY_HOME = 3
        KEY_END = 4
        KEY_BACKSPACE = 5
        KEY_DC = 6

    console = CursesConsole.__new__(CursesConsole)
    console.curses = FakeCurses

    value, cursor = console._edit_text("192.168.1.1", 11, FakeCurses.KEY_LEFT)
    value, cursor = console._edit_text(value, cursor, ord("0"))
    assert (value, cursor) == ("192.168.1.01", 11)
    value, cursor = console._edit_text(value, cursor, FakeCurses.KEY_BACKSPACE)
    assert (value, cursor) == ("192.168.1.1", 10)
    value, cursor = console._edit_text(value, cursor, FakeCurses.KEY_DC)
    assert (value, cursor) == ("192.168.1.", 10)
    assert console._edit_text(value, cursor, FakeCurses.KEY_HOME)[1] == 0
    assert console._edit_text(value, 0, FakeCurses.KEY_END)[1] == len(value)


def test_console_management_form_uses_field_navigation_and_cursor_editing():
    keys = [2, 1, ord("9"), 9, 9, 9, 9, 9, 10]

    class FakeWindow:
        def keypad(self, _enabled):
            return None

        def erase(self):
            return None

        def bkgd(self, *_args):
            return None

        def box(self):
            return None

        def addnstr(self, *_args):
            return None

        def move(self, *_args):
            return None

        def refresh(self):
            return None

        def getch(self):
            return keys.pop(0)

    class FakeCurses:
        A_BOLD = 1
        A_REVERSE = 2
        KEY_LEFT = 1
        KEY_DOWN = 2
        KEY_RIGHT = 3
        KEY_HOME = 4
        KEY_END = 5
        KEY_BACKSPACE = 6
        KEY_DC = 7
        KEY_UP = 8
        KEY_BTAB = 353
        KEY_ENTER = 343

        @staticmethod
        def color_pair(value):
            return value

        @staticmethod
        def curs_set(_value):
            return None

        @staticmethod
        def newwin(*_args):
            return FakeWindow()

    console = CursesConsole.__new__(CursesConsole)
    console.curses = FakeCurses
    console.stdscr = SimpleNamespace(getmaxyx=lambda: (24, 80))
    status = SimpleNamespace(
        ipv4_method="static",
        ipv4_cidr="192.168.1.10/24",
        gateway="192.168.1.1",
        ipv6_mode="static",
        ipv6_cidr="2001:db8::10/64",
        ipv6_gateway="fe80::1",
    )

    result = console._management_form(status)

    assert result == (
        "static",
        "192.168.1.10/294",
        "192.168.1.1",
        "static",
        "2001:db8::10/64",
        "fe80::1",
    )


def test_console_top_temporarily_leaves_and_restores_curses(monkeypatch):
    events: list[str] = []

    class FakeCurses:
        class error(Exception):
            pass

        @staticmethod
        def def_prog_mode():
            events.append("save")

        @staticmethod
        def endwin():
            events.append("end")

        @staticmethod
        def reset_prog_mode():
            events.append("restore")

    console = CursesConsole.__new__(CursesConsole)
    console.curses = FakeCurses
    console.message = ""
    console.message_error = False
    console._force_clear = False
    console._initialize_screen = lambda: events.append("initialize")
    commands: list[list[str]] = []
    monkeypatch.setattr(
        appliance_console.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0),
    )

    console.show_top()

    assert commands == [["top"]]
    assert events == ["save", "end", "restore", "initialize"]
    assert console._force_clear is True


def test_console_shell_is_audited_and_returns_to_curses(monkeypatch):
    console = CursesConsole.__new__(CursesConsole)
    events: list[object] = []
    console.message = ""
    console.message_error = False
    console._run_interactive = lambda command, label: events.append((command, label)) or 0
    monkeypatch.setattr(appliance_console, "record_console_shell", lambda action: events.append(action))

    console.show_shell()

    assert events == ["open", (["/bin/bash", "--login"], "Bash console"), "close"]


def test_console_authentication_is_requested_for_each_menu_entry(monkeypatch):
    console = CursesConsole.__new__(CursesConsole)
    prompts = iter(["first-password", "second-password"])
    console._prompt = lambda *args, **kwargs: next(prompts)
    console.message = ""
    console.message_error = False
    passwords: list[str] = []
    monkeypatch.setattr(appliance_console, "authenticate_root", lambda password: passwords.append(password) or True)

    assert console._require_authentication() is True
    assert console._require_authentication() is True
    assert passwords == ["first-password", "second-password"]


def test_console_firewall_toggle_persists_desired_state_and_selects_only_firewall(client, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import FirewallSettings

    selected: list[set[str]] = []
    monkeypatch.setattr(appliance_console, "_submit_console_apply", lambda unit_ids, **kwargs: selected.append(unit_ids) or "job_firewall")

    assert configure_firewall(False) == "job_firewall"
    with SessionLocal() as db:
        firewall = db.scalar(select(FirewallSettings))
        assert firewall is not None
        assert firewall.enabled is False
    assert selected == [{"firewall"}]


def test_console_power_task_is_committed_before_real_helper_invocation(client, monkeypatch):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, JobStatus

    observed: list[tuple[list[str], str, str]] = []

    def fake_run(command, **kwargs):
        with SessionLocal() as db:
            job = db.query(Job).filter(Job.type == "appliance-reboot").one()
            observed.append((command, job.status, job.created_by))
        return subprocess.CompletedProcess(command, 0, "scheduled\n", "")

    monkeypatch.setattr(appliance_console, "_run", fake_run)
    job_id = schedule_power("reboot")

    assert observed == [([str(appliance_console.HELPER_PATH), "appliance-power", "reboot", "--real"], JobStatus.RUNNING.value, "console:root")]
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.SUCCEEDED.value
        assert job.created_by == "console:root"


def test_forced_real_apply_seam_rejects_non_console_jobs(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, JobStatus
    from labfoundry.app.ui import run_appliance_apply_job

    with SessionLocal() as db:
        db.add(Job(id="job_not_console", type="appliance-apply", status=JobStatus.PENDING.value, created_by="admin"))
        db.commit()

    with pytest.raises(ValueError, match="restricted to local console"):
        run_appliance_apply_job("job_not_console", force_real=True)


def test_console_desired_state_edit_is_rejected_before_commit_when_apply_is_active(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import FirewallSettings, Job, JobStatus

    with SessionLocal() as db:
        firewall = db.scalar(select(FirewallSettings))
        assert firewall is not None
        original = firewall.enabled
        db.add(Job(id="job_active_apply", type="appliance-apply", status=JobStatus.RUNNING.value, created_by="admin"))
        db.commit()

    with pytest.raises(ConsoleOperationError, match="already running"):
        configure_firewall(not original)

    with SessionLocal() as db:
        firewall = db.scalar(select(FirewallSettings))
        assert firewall is not None
        assert firewall.enabled is original


def test_console_systemd_unit_replaces_only_tty1():
    unit = Path("image/common/systemd/labfoundry-console.service").read_text(encoding="utf-8")
    provision = Path("image/common/scripts/provision-labfoundry.sh").read_text(encoding="utf-8")
    manager = Path("image/common/systemd/labfoundry-console-manager.conf").read_text(encoding="utf-8")
    assert "TTYPath=/dev/tty1" in unit
    assert "Conflicts=getty@tty1.service" in unit
    assert "getty@tty2" not in unit
    assert "systemctl mask getty@tty1.service" in provision
    assert "systemctl enable labfoundry-console.service" in provision
    assert "getty@tty2" not in provision
    assert "tdnf -y install" in provision and "python3-curses" in provision and "procps-ng" in provision
    assert "ShowStatus=no" in manager
    assert "/etc/systemd/system.conf.d/labfoundry-console.conf" in provision
    deploy = Path("scripts/windows/vmware/deploy-wheel.ps1").read_text(encoding="utf-8")
    assert "systemctl restart labfoundry-console.service" in deploy
    assert "systemctl is-active labfoundry-console.service" in deploy
    assert "/etc/systemd/system.conf.d/labfoundry-console.conf" in deploy
    assert "systemctl daemon-reexec" in deploy


def test_console_service_isolation_preserves_console_network_and_firewall(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    state_dir = tmp_path / "console"
    state_path = state_dir / "services.json"
    monkeypatch.setattr(helper, "CONSOLE_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "CONSOLE_SERVICE_STATE_PATH", state_path)
    monkeypatch.setattr(
        helper,
        "_console_unit_state",
        lambda unit: {"unit": unit, "enabled": True, "active": unit != "chronyd.service"},
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "_run", fake_run)
    assert helper._handle_console("disable-services", []) == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert {row["unit"] for row in saved["units"]} == set(helper.CONSOLE_MANAGED_SERVICE_UNITS)
    assert all(command[:3] == ["systemctl", "disable", "--now"] for command in commands)
    flattened = " ".join(" ".join(command) for command in commands)
    assert "labfoundry-console.service" not in flattened
    assert "systemd-networkd.service" not in flattened
    assert "labfoundry-firewall.service" not in flattened
    output = capsys.readouterr().out
    assert "management networking" in output


def test_console_service_restore_uses_saved_enable_and_active_state(monkeypatch, tmp_path):
    helper = load_helper_module()
    state_dir = tmp_path / "console"
    state_dir.mkdir()
    state_path = state_dir / "services.json"
    state_path.write_text(
        json.dumps(
            {
                "units": [
                    {"unit": "nginx.service", "enabled": True, "active": True},
                    {"unit": "chronyd.service", "enabled": False, "active": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "CONSOLE_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "CONSOLE_SERVICE_STATE_PATH", state_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        helper,
        "_run",
        lambda command: commands.append(command) or subprocess.CompletedProcess(command, 0, "", ""),
    )
    assert helper._handle_console("restore-services", []) == 0
    assert commands == [["systemctl", "enable", "nginx.service"], ["systemctl", "start", "nginx.service"]]
    assert not state_path.exists()


def test_console_service_restore_keeps_snapshot_when_restoration_is_incomplete(monkeypatch, tmp_path):
    helper = load_helper_module()
    state_dir = tmp_path / "console"
    state_dir.mkdir()
    state_path = state_dir / "services.json"
    state_path.write_text(
        json.dumps({"units": [{"unit": "nginx.service", "enabled": True, "active": True}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "CONSOLE_STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "CONSOLE_SERVICE_STATE_PATH", state_path)
    monkeypatch.setattr(
        helper,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 1, "", "restore failed"),
    )

    assert helper._handle_console("restore-services", []) == 1
    assert state_path.exists()

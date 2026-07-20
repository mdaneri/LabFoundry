from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address, ip_interface
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


ENV_PATH = Path("/etc/labfoundry/labfoundry.env")


def _load_environment() -> None:
    if not ENV_PATH.exists():
        return
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_environment()

from sqlalchemy import select

from labfoundry.app.audit import record_audit
from labfoundry.app.database import SessionLocal
from labfoundry.app.models import ApplianceSettings, FirewallSettings, Job, JobStatus, JobStep, PhysicalInterface, utcnow
from labfoundry.app.services.dnsmasq import join_servers, split_servers


HELPER_PATH = Path("/opt/labfoundry/bin/labfoundry-helper")
PHOTON_RELEASE_PATH = Path("/etc/photon-release")
MAINTENANCE_STATE_PATH = Path("/var/lib/labfoundry/console/services.json")
CONSOLE_ACTOR = "console:root"
CONSOLE_REFRESH_ENV = "LABFOUNDRY_CONSOLE_REFRESH_SECONDS"
BASH_PATH = "/usr/bin/bash"


def _console_refresh_seconds() -> int:
    try:
        value = int(os.environ.get(CONSOLE_REFRESH_ENV, "5"))
    except ValueError:
        return 5
    return min(max(value, 1), 300)


CONSOLE_REFRESH_SECONDS = _console_refresh_seconds()


class ConsoleOperationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConsoleStatus:
    hostname: str
    release: str
    version: str
    kernel: str
    cpu: str
    memory: str
    load: str
    interface: str
    ipv4_method: str
    ipv4_cidr: str
    gateway: str
    ipv6_mode: str
    ipv6_cidr: str
    ipv6_gateway: str
    dns_servers: tuple[str, ...]
    app_active: bool
    firewall_enabled: bool
    maintenance_isolation: bool
    urls: tuple[str, ...]


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    timeout: float = 10,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or f"command exited with {result.returncode}").strip()
        raise ConsoleOperationError(detail)
    return result


def _read_text(path: Path, fallback: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback


def _first_display_line(value: str, fallback: str = "") -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), fallback)


def _service_active(unit: str) -> bool:
    try:
        return _run(["systemctl", "is-active", "--quiet", unit], timeout=2).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cpu_summary() -> str:
    model = ""
    count = os.cpu_count() or 0
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        model = platform.processor()
    return f"{count} x {model}" if model else f"{count} CPU"


def _memory_summary() -> str:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        used = max(total - available, 0)
        if total:
            return f"{used / (1024 ** 3):.1f} / {total / (1024 ** 3):.1f} GiB used"
    except (OSError, ValueError):
        pass
    return "Unavailable"


def _load_summary() -> str:
    try:
        one, five, fifteen = os.getloadavg()
    except (AttributeError, OSError):
        return "Unavailable"
    return f"1 min {one:.2f} | 5 min {five:.2f} | 15 min {fifteen:.2f}"


def _package_version() -> str:
    try:
        return version("labfoundry")
    except PackageNotFoundError:
        return "development"


def _management_interface(db: Any) -> PhysicalInterface:
    interface = db.scalar(
        select(PhysicalInterface)
        .where(PhysicalInterface.role == "management")
        .order_by(PhysicalInterface.id)
    )
    if interface is None:
        interface = db.scalar(select(PhysicalInterface).where(PhysicalInterface.name == "eth0"))
    if interface is None:
        raise ConsoleOperationError("No management interface is available. Discover appliance interfaces in LabFoundry first.")
    return interface


def _fallback_gateway(interface_name: str, version: int = 4) -> str:
    try:
        result = _run(["ip", "-6" if version == 6 else "-4", "route", "show", "default", "dev", interface_name], timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    tokens = result.stdout.split()
    return tokens[tokens.index("via") + 1] if "via" in tokens and tokens.index("via") + 1 < len(tokens) else ""


def _fallback_cidr(interface_name: str, version: int = 4) -> str:
    try:
        result = _run(["ip", "-6" if version == 6 else "-4", "-o", "addr", "show", "dev", interface_name, "scope", "global"], timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    tokens = result.stdout.split()
    family_token = "inet6" if version == 6 else "inet"
    return tokens[tokens.index(family_token) + 1] if family_token in tokens and tokens.index(family_token) + 1 < len(tokens) else ""


def _fallback_dns_servers(interface_name: str) -> tuple[str, ...]:
    candidates: list[str] = []
    try:
        result = _run(["resolvectl", "dns", interface_name], timeout=2)
        candidates.extend(re.split(r"[\s,]+", result.stdout))
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not candidates:
        try:
            for line in Path("/etc/resolv.conf").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("nameserver "):
                    candidates.append(line.split(None, 1)[1].strip())
        except OSError:
            pass
    servers: list[str] = []
    for candidate in candidates:
        normalized = candidate.strip("[](),;").split("#", 1)[0].split("%", 1)[0]
        try:
            parsed = ip_address(normalized)
        except ValueError:
            continue
        if parsed.is_loopback:
            continue
        value = str(parsed)
        if value not in servers:
            servers.append(value)
    return tuple(servers)


def management_urls(
    fqdn: str,
    ipv4_cidr: str,
    ipv6_cidr: str,
    *,
    https_enabled: bool = True,
) -> tuple[str, ...]:
    scheme = "https" if https_enabled else "http"
    urls: list[str] = []
    if fqdn:
        urls.append(f"{scheme}://{fqdn}/")
    for candidate, ipv6 in ((ipv4_cidr, False), (ipv6_cidr, True)):
        if not candidate:
            continue
        try:
            parsed_address = ip_interface(candidate).ip
        except ValueError:
            continue
        if parsed_address.is_link_local:
            continue
        urls.append(f"{scheme}://[{parsed_address}]/" if ipv6 else f"{scheme}://{parsed_address}/")
    return tuple(dict.fromkeys(urls))


def load_console_status() -> ConsoleStatus:
    with SessionLocal() as db:
        interface = _management_interface(db)
        settings = db.scalar(select(ApplianceSettings).order_by(ApplianceSettings.id))
        firewall = db.scalar(select(FirewallSettings).order_by(FirewallSettings.id))
        method = (interface.ipv4_method or "static").strip().lower()
        cidr = (interface.host_ip_cidr if method == "dhcp" else interface.ip_cidr) or _fallback_cidr(interface.name, 4)
        gateway = interface.gateway or _fallback_gateway(interface.name, 4)
        ipv6_mode = "disabled" if not interface.ipv6_enabled else ("static" if interface.ipv6_cidr else "automatic")
        ipv6_cidr = (
            interface.ipv6_cidr
            if ipv6_mode == "static"
            else ((interface.host_ipv6_cidr or _fallback_cidr(interface.name, 6)) if ipv6_mode == "automatic" else "")
        )
        ipv6_gateway = interface.ipv6_gateway or (_fallback_gateway(interface.name, 6) if ipv6_mode == "automatic" else "")
        dns_servers = tuple(split_servers(settings.external_dns_servers if settings else "")) or _fallback_dns_servers(interface.name)
        fqdn = settings.fqdn if settings and settings.fqdn else socket.getfqdn()

    urls = management_urls(
        fqdn,
        cidr,
        ipv6_cidr,
        https_enabled=bool(settings and settings.management_https_enabled),
    )
    return ConsoleStatus(
        hostname=fqdn or socket.gethostname(),
        release=_first_display_line(_read_text(PHOTON_RELEASE_PATH), platform.system()),
        version=_package_version(),
        kernel=platform.release(),
        cpu=_cpu_summary(),
        memory=_memory_summary(),
        load=_load_summary(),
        interface=interface.name,
        ipv4_method=method,
        ipv4_cidr=cidr,
        gateway=gateway,
        ipv6_mode=ipv6_mode,
        ipv6_cidr=ipv6_cidr,
        ipv6_gateway=ipv6_gateway,
        dns_servers=dns_servers,
        app_active=_service_active("labfoundry.service"),
        firewall_enabled=bool(firewall and firewall.enabled),
        maintenance_isolation=MAINTENANCE_STATE_PATH.exists(),
        urls=urls,
    )


def authenticate_root(password: str) -> bool:
    if not password:
        return False
    try:
        result = _run(
            [str(HELPER_PATH), "local-users", "authenticate", "--real", "root"],
            input_text=f"{password}\n",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def validate_management_values(ipv4_method: str, ipv4_cidr: str, gateway: str) -> tuple[str, str, str]:
    method = ipv4_method.strip().lower()
    if method not in {"dhcp", "static"}:
        raise ConsoleOperationError("Management IPv4 method must be DHCP or static.")
    if method == "dhcp":
        if ipv4_cidr.strip() or gateway.strip():
            raise ConsoleOperationError("DHCP management networking cannot include a static address or gateway.")
        return method, "", ""
    try:
        parsed_interface = ip_interface(ipv4_cidr.strip())
    except ValueError as exc:
        raise ConsoleOperationError("Management IPv4 must be an IPv4 CIDR such as 192.168.49.1/24.") from exc
    if parsed_interface.version != 4:
        raise ConsoleOperationError("Management IP must use IPv4.")
    gateway_value = gateway.strip()
    if gateway_value:
        try:
            parsed_gateway = ip_address(gateway_value)
        except ValueError as exc:
            raise ConsoleOperationError("Management gateway must be a valid IPv4 address.") from exc
        if parsed_gateway.version != 4 or parsed_gateway not in parsed_interface.network:
            raise ConsoleOperationError("Management gateway must be an on-link IPv4 address.")
        if parsed_gateway == parsed_interface.ip:
            raise ConsoleOperationError("Management gateway cannot equal the management IP.")
    return method, str(parsed_interface), gateway_value


def validate_ipv6_management_values(ipv6_mode: str, ipv6_cidr: str, ipv6_gateway: str) -> tuple[str, str, str]:
    mode = ipv6_mode.strip().lower()
    if mode not in {"disabled", "automatic", "static"}:
        raise ConsoleOperationError("Management IPv6 mode must be disabled, automatic, or static.")
    cidr_value = ipv6_cidr.strip()
    gateway_value = ipv6_gateway.strip()
    if mode != "static":
        if cidr_value or gateway_value:
            raise ConsoleOperationError("Disabled or automatic IPv6 cannot include a static address or gateway.")
        return mode, "", ""
    try:
        parsed_interface = ip_interface(cidr_value)
    except ValueError as exc:
        raise ConsoleOperationError("Management IPv6 must be an IPv6 CIDR such as fd00:49::1/64.") from exc
    if parsed_interface.version != 6:
        raise ConsoleOperationError("Management IPv6 CIDR must use IPv6.")
    if gateway_value:
        try:
            parsed_gateway = ip_address(gateway_value)
        except ValueError as exc:
            raise ConsoleOperationError("Management IPv6 gateway must be a valid IPv6 address.") from exc
        if parsed_gateway.version != 6:
            raise ConsoleOperationError("Management IPv6 gateway must use IPv6.")
        if not parsed_gateway.is_link_local and parsed_gateway not in parsed_interface.network:
            raise ConsoleOperationError("Management IPv6 gateway must be link-local or on-link.")
        if parsed_gateway == parsed_interface.ip:
            raise ConsoleOperationError("Management IPv6 gateway cannot equal the management IPv6 address.")
        gateway_value = str(parsed_gateway)
    return mode, str(parsed_interface), gateway_value


def validate_dns_servers(raw: str) -> list[str]:
    servers = [item for item in re.split(r"[\s,]+", raw.strip()) if item]
    if not servers:
        raise ConsoleOperationError("At least one DNS server is required.")
    for server in servers:
        try:
            ip_address(server)
        except ValueError as exc:
            raise ConsoleOperationError(f"DNS server {server} must be an IPv4 or IPv6 address.") from exc
    return servers


def _captured_apply_payload(units: list[dict[str, Any]], selected_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [unit for unit in units if unit["id"] in selected_ids]
    invalid = [unit["label"] for unit in selected if unit["validation_errors"]]
    if invalid:
        raise ConsoleOperationError(f"Desired state validation failed for: {', '.join(invalid)}.")
    if not selected:
        raise ConsoleOperationError("No appliance apply unit matched this console change.")
    payload = {
        "selected_units": [unit["id"] for unit in selected],
        "skipped_changed_units": [
            {"unit_id": unit["id"], "label": unit["label"], "summary": unit["summary"]}
            for unit in units
            if unit["changed"] and unit["id"] not in selected_ids
        ],
        "captured_units": [
            {
                "unit_id": unit["id"],
                "label": unit["label"],
                "snapshot_hash": unit["snapshot_hash"],
                "summary": unit["summary"],
                "validation_errors": unit["validation_errors"],
                "validation_warnings": unit["validation_warnings"],
                "config_path": unit["config_path"],
                "config_preview": unit["config_preview"],
                "config_diff": unit["config_diff"],
            }
            for unit in selected
        ],
        "units": [],
        "dry_run": False,
        "source": "local_appliance_console",
    }
    return selected, payload


def _console_unit_hashes(unit_ids: set[str]) -> dict[str, str]:
    from labfoundry.app.ui import appliance_apply_units

    with SessionLocal() as db:
        return {
            str(unit["id"]): str(unit["snapshot_hash"])
            for unit in appliance_apply_units(db)
            if unit["id"] in unit_ids
        }


def _ensure_no_active_apply() -> None:
    from labfoundry.app.ui import active_appliance_apply_job

    with SessionLocal() as db:
        active = active_appliance_apply_job(db)
        if active is not None:
            raise ConsoleOperationError(f"Appliance apply task {active.id} is already {active.status}.")


def _submit_console_apply(required_ids: set[str], *, changed_dependents: dict[str, str] | None = None) -> str:
    # Imported lazily so read-only status remains available even if the web stack has a startup issue.
    from labfoundry.app.ui import active_appliance_apply_job, appliance_apply_units, run_appliance_apply_job

    with SessionLocal() as db:
        active = active_appliance_apply_job(db)
        if active is not None:
            raise ConsoleOperationError(f"Appliance apply task {active.id} is already {active.status}.")
        units = appliance_apply_units(db)
        selected_ids = set(required_ids)
        if changed_dependents:
            selected_ids.update(
                str(unit["id"])
                for unit in units
                if (
                    unit["id"] in changed_dependents
                    and str(unit["snapshot_hash"]) != changed_dependents[unit["id"]]
                    and not unit["validation_errors"]
                )
            )
        selected, payload = _captured_apply_payload(units, selected_ids)
        job_id = f"job_{uuid4().hex[:12]}"
        job = Job(
            id=job_id,
            type="appliance-apply",
            status=JobStatus.PENDING.value,
            created_by=CONSOLE_ACTOR,
            progress_percent=0,
            result=json.dumps(payload, indent=2),
        )
        db.add(job)
        for position, unit in enumerate(selected, start=1):
            captured = next(item for item in payload["captured_units"] if item["unit_id"] == unit["id"])
            db.add(
                JobStep(
                    id=f"{job_id}:{unit['id']}",
                    job=job,
                    component_key=unit["id"],
                    label=unit["label"],
                    position=position,
                    status=JobStatus.PENDING.value,
                    progress_percent=0,
                    result=json.dumps(captured, indent=2, sort_keys=True),
                )
            )
        db.commit()
        record_audit(
            db,
            actor=CONSOLE_ACTOR,
            action="create_appliance_apply_task",
            resource_type="job",
            resource_id=job_id,
            detail=f"source=local_console; selected_units={','.join(payload['selected_units'])}",
        )

    run_appliance_apply_job(job_id, force_real=True)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != JobStatus.SUCCEEDED.value:
            detail = job.error if job is not None else "task record disappeared"
            raise ConsoleOperationError(f"Appliance apply task {job_id} failed: {detail or 'unknown failure'}")
    return job_id


def configure_management(
    ipv4_method: str,
    ipv4_cidr: str,
    gateway: str,
    ipv6_mode: str,
    ipv6_cidr: str,
    ipv6_gateway: str,
    raw_dns_servers: str,
) -> str:
    method, cidr, gateway_value = validate_management_values(ipv4_method, ipv4_cidr, gateway)
    mode, ipv6_cidr_value, ipv6_gateway_value = validate_ipv6_management_values(ipv6_mode, ipv6_cidr, ipv6_gateway)
    dns_servers = validate_dns_servers(raw_dns_servers)
    _ensure_no_active_apply()
    dependent_hashes = _console_unit_hashes({"firewall"})
    with SessionLocal() as db:
        interface = _management_interface(db)
        settings = db.scalar(select(ApplianceSettings).order_by(ApplianceSettings.id))
        if settings is None:
            raise ConsoleOperationError("Appliance Settings desired state is unavailable.")
        interface.ipv4_method = method
        interface.ip_cidr = cidr or None
        interface.gateway = gateway_value or None
        interface.ipv6_enabled = mode != "disabled"
        interface.ipv6_cidr = ipv6_cidr_value or None
        interface.ipv6_gateway = ipv6_gateway_value or None
        interface.desired_state_source = "console"
        settings.external_dns_servers = join_servers(dns_servers)
        db.commit()
        record_audit(
            db,
            actor=CONSOLE_ACTOR,
            action="console_update_management_network",
            resource_type="interface",
            resource_id=interface.name,
            detail=f"ipv4_method={method}; ipv6_mode={mode}; dns_servers={len(dns_servers)}",
        )
    return _submit_console_apply({"network", "appliance_settings"}, changed_dependents=dependent_hashes)


def configure_dns(raw_servers: str) -> str:
    servers = validate_dns_servers(raw_servers)
    _ensure_no_active_apply()
    with SessionLocal() as db:
        settings = db.scalar(select(ApplianceSettings).order_by(ApplianceSettings.id))
        if settings is None:
            raise ConsoleOperationError("Appliance Settings desired state is unavailable.")
        settings.external_dns_servers = join_servers(servers)
        db.commit()
        record_audit(db, actor=CONSOLE_ACTOR, action="console_update_dns", resource_type="appliance_settings", resource_id=str(settings.id))
    return _submit_console_apply({"appliance_settings"})


def configure_firewall(enabled: bool) -> str:
    _ensure_no_active_apply()
    with SessionLocal() as db:
        firewall = db.scalar(select(FirewallSettings).order_by(FirewallSettings.id))
        if firewall is None:
            firewall = FirewallSettings(enabled=enabled)
            db.add(firewall)
        else:
            firewall.enabled = enabled
        db.commit()
        db.refresh(firewall)
        record_audit(
            db,
            actor=CONSOLE_ACTOR,
            action="console_enable_firewall" if enabled else "console_disable_firewall",
            resource_type="firewall",
            resource_id=str(firewall.id),
        )
    return _submit_console_apply({"firewall"})


def set_maintenance_isolation(enabled: bool) -> dict[str, Any]:
    action = "disable-services" if enabled else "restore-services"
    result = _run([str(HELPER_PATH), "console", action, "--real"], timeout=60, check=True)
    payload_lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    payload = json.loads(payload_lines[-1]) if payload_lines else {"action": action}
    with SessionLocal() as db:
        record_audit(
            db,
            actor=CONSOLE_ACTOR,
            action=f"console_{action.replace('-', '_')}",
            resource_type="appliance",
            detail="Local console maintenance isolation; management networking, firewall, and console preserved.",
        )
    return payload


def schedule_power(action: str) -> str:
    if action not in {"reboot", "shutdown"}:
        raise ConsoleOperationError("Unsupported appliance power action.")
    job_id = f"job_{uuid4().hex[:12]}"
    with SessionLocal() as db:
        job = Job(
            id=job_id,
            type=f"appliance-{action}",
            status=JobStatus.PENDING.value,
            created_by=CONSOLE_ACTOR,
            progress_percent=0,
        )
        db.add(job)
        db.commit()
        record_audit(
            db,
            actor=CONSOLE_ACTOR,
            action=f"submit_appliance_{action}",
            resource_type="job",
            resource_id=job_id,
            detail=f"Confirmed local console appliance {action} task submitted.",
        )
        job.status = JobStatus.RUNNING.value
        job.started_at = utcnow()
        db.commit()

    result = _run([str(HELPER_PATH), "appliance-power", action, "--real"], timeout=10)
    succeeded = result.returncode == 0
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise ConsoleOperationError("Appliance power task record disappeared before scheduling completed.")
        job.status = JobStatus.SUCCEEDED.value if succeeded else JobStatus.FAILED.value
        job.finished_at = utcnow()
        job.progress_percent = 100
        job.result = json.dumps(
            {
                "action": action,
                "state": "scheduled" if succeeded else "failed",
                "status": job.status,
                "success": succeeded,
                "scheduled": succeeded,
                "delay_seconds": 5,
                "dry_run": False,
            },
            indent=2,
            sort_keys=True,
        )
        job.error = None if succeeded else f"Appliance {action} scheduling failed."
        db.commit()
        record_audit(
            db,
            actor=CONSOLE_ACTOR,
            action=f"schedule_appliance_{action}",
            resource_type="job",
            resource_id=job_id,
            detail=f"labfoundry-helper appliance-power {action}",
            success=succeeded,
        )
    if not succeeded:
        raise ConsoleOperationError(f"Appliance {action} scheduling failed. Review task {job_id} after recovery.")
    return job_id


def record_console_shell(action: str) -> None:
    if action not in {"open", "close"}:
        raise ValueError("Unsupported console shell audit action.")
    with SessionLocal() as db:
        record_audit(
            db,
            actor=CONSOLE_ACTOR,
            action=f"console_root_shell_{action}",
            resource_type="local_console",
            resource_id="tty1",
            detail="Authenticated local root Bash session",
        )
        db.commit()


class CursesConsole:
    def __init__(self, stdscr: Any) -> None:
        import curses

        self.curses = curses
        self.stdscr = stdscr
        self.message = ""
        self.message_error = False
        self._force_clear = True
        self._force_redraw = False
        self._initialize_screen()

    def _initialize_screen(self) -> None:
        curses = self.curses
        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        self.stdscr.keypad(True)
        self.stdscr.timeout(1000)
        curses.start_color()
        curses.use_default_colors()
        # Closest terminal representation of the web palette: slate, pale blue, white,
        # LabFoundry blue, green, amber, and red. Custom RGB is used when supported.
        if curses.can_change_color() and curses.COLORS >= 16:
            palette = {
                8: (59, 72, 89),     # #0f172a-ish slate
                9: (933, 949, 969),  # #eef2f7
                10: (145, 388, 922), # #2563eb
                11: (396, 455, 545), # #64748b
                12: (82, 502, 239),  # #15803d
                13: (706, 325, 35),  # #b45309
                14: (725, 110, 110), # #b91c1c
                15: (859, 918, 996), # #dbeafe
            }
            for color_id, rgb in palette.items():
                try:
                    curses.init_color(color_id, *rgb)
                except curses.error:
                    break
            slate, soft, blue, muted, good, warn, bad, pale_blue = 8, 9, 10, 11, 12, 13, 14, 15
        elif curses.can_change_color() and curses.COLORS >= 8:
            # TERM=linux commonly advertises only eight slots. Reuse cyan for the
            # web UI's pale-blue header instead of falling back to a black banner.
            try:
                curses.init_color(curses.COLOR_CYAN, 859, 918, 996)
            except curses.error:
                pass
            slate, soft, blue, muted, good, warn, bad, pale_blue = (
                curses.COLOR_BLACK,
                curses.COLOR_WHITE,
                curses.COLOR_BLUE,
                curses.COLOR_BLACK,
                curses.COLOR_GREEN,
                curses.COLOR_YELLOW,
                curses.COLOR_RED,
                curses.COLOR_CYAN,
            )
        else:
            slate, soft, blue, muted, good, warn, bad, pale_blue = (
                curses.COLOR_BLACK,
                curses.COLOR_WHITE,
                curses.COLOR_BLUE,
                curses.COLOR_CYAN,
                curses.COLOR_GREEN,
                curses.COLOR_YELLOW,
                curses.COLOR_RED,
                curses.COLOR_CYAN,
            )
        curses.init_pair(1, slate, pale_blue)
        curses.init_pair(2, slate, soft)
        curses.init_pair(3, curses.COLOR_WHITE, blue)
        curses.init_pair(4, muted, soft)
        curses.init_pair(5, good, soft)
        curses.init_pair(6, warn, soft)
        curses.init_pair(7, bad, soft)
        curses.init_pair(8, curses.COLOR_WHITE, slate)
        curses.init_pair(9, slate, pale_blue)

    @staticmethod
    def _recovery_redraws(now: float) -> list[float]:
        """Bounded redraw window for late tty writes while systemd jobs settle."""
        return [now + 1, now + 3, now + 8]

    def _safe_add(self, y: int, x: int, text: str, attr: int = 0, width: int | None = None) -> None:
        height, screen_width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= screen_width:
            return
        available = max((width if width is not None else screen_width - x) - 1, 0)
        try:
            self.stdscr.addnstr(y, x, text, available, attr)
        except self.curses.error:
            pass

    def _fill_line(self, y: int, attr: int) -> None:
        _, width = self.stdscr.getmaxyx()
        self._safe_add(y, 0, " " * width, attr, width + 1)

    def _refresh_screen(self) -> None:
        if self._force_redraw:
            self.stdscr.touchwin()
            self._force_redraw = False
        self.stdscr.refresh()

    def draw_main(self) -> None:
        curses = self.curses
        if self._force_clear:
            self.stdscr.clear()
            self._force_clear = False
        else:
            self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        if height < 22 or width < 72:
            self._safe_add(1, 2, "LabFoundry appliance console requires at least 72 x 22 characters.", curses.color_pair(7) | curses.A_BOLD)
            self._refresh_screen()
            return
        # Keep a complete pale-blue spacer row below Load, followed by a body
        # spacer row, so the management URLs do not visually touch host status.
        header_height = 9
        for y in range(header_height):
            self._fill_line(y, curses.color_pair(1))
        for y in range(header_height, height - 1):
            self._fill_line(y, curses.color_pair(2))
        try:
            status = load_console_status()
        except Exception as exc:  # noqa: BLE001 - recovery console must remain visible.
            self._safe_add(2, 4, "LabFoundry Appliance", curses.color_pair(1) | curses.A_BOLD)
            self._safe_add(5, 4, f"Status unavailable: {exc}", curses.color_pair(7))
            self._draw_footer(height, width)
            self._refresh_screen()
            return

        self._safe_add(1, 4, f"LabFoundry Appliance {status.version}", curses.color_pair(1) | curses.A_BOLD)
        self._safe_add(3, 4, status.release, curses.color_pair(1))
        self._safe_add(4, 4, f"Kernel: {status.kernel}", curses.color_pair(1))
        self._safe_add(5, 4, f"CPU: {status.cpu}", curses.color_pair(1))
        self._safe_add(6, 4, f"Memory: {status.memory}", curses.color_pair(1))
        self._safe_add(7, 4, f"Load: {status.load}", curses.color_pair(1))

        self._safe_add(10, 4, "Manage this appliance at:", curses.color_pair(2) | curses.A_BOLD)
        row = 11
        for url in status.urls[:3]:
            self._safe_add(row, 6, url, curses.color_pair(2))
            row += 1
        self._safe_add(14, 4, "Management network", curses.color_pair(2) | curses.A_BOLD)
        address = "DHCP" if status.ipv4_method == "dhcp" and not status.ipv4_cidr else (status.ipv4_cidr or "Not configured")
        self._safe_add(15, 6, self._network_table_row("Interface", status.interface, width), curses.color_pair(2))
        ipv4 = self._network_table_row(
            "IPv4",
            address,
            width,
            gateway=status.gateway or "none",
            mode=status.ipv4_method,
        )
        self._safe_add(16, 6, ipv4, curses.color_pair(2))
        ipv6_address = status.ipv6_cidr or ("Not configured" if status.ipv6_mode == "disabled" else "Awaiting RA/SLAAC")
        ipv6 = self._network_table_row(
            "IPv6",
            ipv6_address,
            width,
            gateway=status.ipv6_gateway or "none",
            mode=status.ipv6_mode,
        )
        self._safe_add(17, 6, ipv6, curses.color_pair(2))
        self._safe_add(
            18,
            6,
            self._network_table_row("DNS", ", ".join(status.dns_servers) or "Not configured", width),
            curses.color_pair(2),
        )
        auxiliary: tuple[str, str] | None = None
        if status.maintenance_isolation:
            auxiliary = ("Isolation", "enabled")
        elif not status.app_active:
            auxiliary = ("Control plane", "unavailable")
        firewall = self._network_table_row(
            "Firewall",
            "enabled" if status.firewall_enabled else "disabled",
            width,
            auxiliary=auxiliary,
        )
        self._safe_add(19, 6, firewall, curses.color_pair(5 if status.firewall_enabled else 6) | curses.A_BOLD)
        if self.message:
            message_row = height - 2 if height > 22 else 19
            self._safe_add(message_row, 4, self.message, curses.color_pair(7 if self.message_error else 5) | curses.A_BOLD)
        self._draw_footer(height, width)
        self._refresh_screen()

    @staticmethod
    def _network_table_row(
        label: str,
        value: str,
        screen_width: int,
        *,
        gateway: str | None = None,
        mode: str | None = None,
        auxiliary: tuple[str, str] | None = None,
    ) -> str:
        """Format management state with stable columns for the current tty width."""
        content_width = max(screen_width - 8, 1)
        label_width = 10
        if gateway is not None or mode is not None:
            address_width, gateway_width = (
                (36, 22)
                if screen_width >= 100
                else (24, 16)
                if screen_width >= 80
                else (18, 14)
            )
            address = value[:address_width]
            gateway_value = (gateway or "none")[:gateway_width]
            row = (
                f"{label:<{label_width}}{address:<{address_width}}"
                f" GW {gateway_value:<{gateway_width}} Mode {mode or 'none'}"
            )
            return row[:content_width].rstrip()
        if auxiliary:
            auxiliary_text = f"{auxiliary[0]}: {auxiliary[1]}"
            value_width = max(content_width - label_width - len(auxiliary_text) - 1, 1)
            row = f"{label:<{label_width}}{value[:value_width]:<{value_width}} {auxiliary_text}"
            return row[:content_width].rstrip()
        return f"{label:<{label_width}}{value}"[:content_width].rstrip()

    def _draw_footer(self, height: int, width: int) -> None:
        curses = self.curses
        self._fill_line(height - 1, curses.color_pair(3))
        self._safe_add(height - 1, 2, "<F2> Customize", curses.color_pair(3) | curses.A_BOLD)
        self._safe_add(height - 1, 21, "<F3> Top", curses.color_pair(3) | curses.A_BOLD)
        self._safe_add(height - 1, 32, "<F4> Console", curses.color_pair(3) | curses.A_BOLD)
        label = "<F12> Shut down / Restart" if width >= 80 else "<F12> Power"
        self._safe_add(height - 1, max(width - len(label) - 3, 24), label, curses.color_pair(3) | curses.A_BOLD)

    def _dialog(self, title: str, lines: list[str], options: list[str]) -> int:
        curses = self.curses
        height, width = self.stdscr.getmaxyx()
        box_width = min(max(max([len(title), *[len(line) for line in lines], *[len(option) for option in options]]) + 8, 48), width - 4)
        box_height = min(len(lines) + len(options) + 6, height - 4)
        top = max((height - box_height) // 2, 1)
        left = max((width - box_width) // 2, 1)
        window = curses.newwin(box_height, box_width, top, left)
        window.keypad(True)
        selected = 0
        while True:
            window.erase()
            window.bkgd(" ", curses.color_pair(2))
            window.box()
            window.addnstr(1, 3, title, box_width - 6, curses.color_pair(2) | curses.A_BOLD)
            row = 3
            for line in lines:
                window.addnstr(row, 3, line, box_width - 6, curses.color_pair(4))
                row += 1
            for index, option in enumerate(options):
                attr = curses.color_pair(3) | curses.A_BOLD if index == selected else curses.color_pair(2)
                window.addnstr(row + index, 3, f" {option} ", box_width - 6, attr)
            window.refresh()
            key = window.getch()
            if key in {curses.KEY_UP, ord("k")}:
                selected = (selected - 1) % len(options)
            elif key in {curses.KEY_DOWN, ord("j")}:
                selected = (selected + 1) % len(options)
            elif key in {10, 13, curses.KEY_ENTER}:
                return selected
            elif key in {27, curses.KEY_F2, curses.KEY_F12}:
                return len(options) - 1

    def _prompt(self, title: str, label: str, initial: str = "", *, secret: bool = False) -> str | None:
        curses = self.curses
        height, width = self.stdscr.getmaxyx()
        box_width = min(max(len(label) + 8, 64), width - 4)
        window = curses.newwin(8, box_width, max((height - 8) // 2, 1), max((width - box_width) // 2, 1))
        window.bkgd(" ", curses.color_pair(2))
        window.box()
        window.addnstr(1, 3, title, box_width - 6, curses.color_pair(2) | curses.A_BOLD)
        window.addnstr(3, 3, label, box_width - 6, curses.color_pair(4))
        value = initial
        cursor = len(value)
        while True:
            field_width = box_width - 7
            start = max(min(cursor - field_width + 1, max(len(value) - field_width, 0)), 0)
            visible_value = value[start : start + field_width]
            shown = "*" * len(visible_value) if secret else visible_value
            window.addnstr(4, 3, " " * (box_width - 6), box_width - 6, curses.color_pair(8))
            window.addnstr(4, 3, shown, box_width - 6, curses.color_pair(8))
            window.move(4, min(3 + cursor - start, box_width - 4))
            curses.curs_set(1)
            window.refresh()
            key = window.getch()
            if key in {10, 13, curses.KEY_ENTER}:
                curses.curs_set(0)
                return value
            if key == 27:
                curses.curs_set(0)
                return None
            value, cursor = self._edit_text(value, cursor, key)

    def _edit_text(self, value: str, cursor: int, key: int, *, limit: int = 500) -> tuple[str, int]:
        curses = self.curses
        if key == curses.KEY_LEFT:
            return value, max(cursor - 1, 0)
        if key == curses.KEY_RIGHT:
            return value, min(cursor + 1, len(value))
        if key == curses.KEY_HOME:
            return value, 0
        if key == curses.KEY_END:
            return value, len(value)
        if key in {curses.KEY_BACKSPACE, 127, 8} and cursor > 0:
            return value[: cursor - 1] + value[cursor:], cursor - 1
        if key == curses.KEY_DC and cursor < len(value):
            return value[:cursor] + value[cursor + 1 :], cursor
        if 32 <= key <= 126 and len(value) < limit:
            return value[:cursor] + chr(key) + value[cursor:], cursor + 1
        return value, cursor

    def _management_form(self, status: ConsoleStatus) -> tuple[str, str, str, str, str, str, str] | None:
        curses = self.curses
        height, width = self.stdscr.getmaxyx()
        box_height = min(20, height - 2)
        box_width = min(68, width - 4)
        top = max((height - box_height) // 2, 1)
        left = max((width - box_width) // 2, 1)
        window = curses.newwin(box_height, box_width, top, left)
        window.keypad(True)

        values = {
            "ipv4_method": status.ipv4_method,
            "ipv4_cidr": status.ipv4_cidr,
            "gateway": status.gateway,
            "ipv6_mode": status.ipv6_mode,
            "ipv6_cidr": status.ipv6_cidr,
            "ipv6_gateway": status.ipv6_gateway,
            "dns_servers": ", ".join(status.dns_servers),
        }
        cursors = {name: len(value) for name, value in values.items()}
        order = [
            "ipv4_method",
            "ipv4_cidr",
            "gateway",
            "ipv6_mode",
            "ipv6_cidr",
            "ipv6_gateway",
            "dns_servers",
            "apply",
            "cancel",
        ]
        selected = 0
        modes = {"ipv4_method": ("dhcp", "static"), "ipv6_mode": ("disabled", "automatic", "static")}

        def enabled(name: str) -> bool:
            if name in {"ipv4_cidr", "gateway"}:
                return values["ipv4_method"] == "static"
            if name in {"ipv6_cidr", "ipv6_gateway"}:
                return values["ipv6_mode"] == "static"
            return True

        def move(delta: int) -> None:
            nonlocal selected
            for _ in order:
                selected = (selected + delta) % len(order)
                if enabled(order[selected]):
                    return

        field_rows = {
            "ipv4_method": (4, "Mode"),
            "ipv4_cidr": (5, "Address / prefix"),
            "gateway": (6, "Gateway"),
            "ipv6_mode": (10, "Mode"),
            "ipv6_cidr": (11, "Address / prefix"),
            "ipv6_gateway": (12, "Gateway"),
            "dns_servers": (14, "DNS servers"),
        }
        label_x = 5
        field_x = 25
        field_width = box_width - field_x - 4

        while True:
            active_name = order[selected]
            window.erase()
            window.bkgd(" ", curses.color_pair(2))
            window.box()
            title = " Management IP, gateways, and DNS "
            window.addnstr(0, max((box_width - len(title)) // 2, 2), title, len(title), curses.color_pair(2) | curses.A_BOLD)
            window.addnstr(2, 3, "IPv4 configuration", box_width - 6, curses.color_pair(3) | curses.A_BOLD)
            window.addnstr(8, 3, "IPv6 configuration", box_width - 6, curses.color_pair(3) | curses.A_BOLD)

            for name, (row, label) in field_rows.items():
                is_enabled = enabled(name)
                label_attr = curses.color_pair(2 if is_enabled else 4)
                window.addnstr(row, label_x, f"{label}:", field_x - label_x - 2, label_attr | curses.A_BOLD)
                if name in modes:
                    display = f"< {values[name].upper()} >"
                else:
                    inner_width = field_width - 2
                    cursor = cursors[name]
                    start = max(min(cursor - inner_width + 1, max(len(values[name]) - inner_width, 0)), 0)
                    display = values[name][start : start + inner_width]
                field_attr = curses.color_pair(3 if name == active_name else (9 if is_enabled else 4))
                if name == active_name:
                    field_attr |= curses.A_BOLD
                window.addnstr(row, field_x, " " * field_width, field_width, field_attr)
                window.addnstr(row, field_x + 1, display, field_width - 2, field_attr)

            apply_attr = curses.color_pair(3 if active_name == "apply" else 9) | (curses.A_BOLD if active_name == "apply" else 0)
            cancel_attr = curses.color_pair(3 if active_name == "cancel" else 9) | (curses.A_BOLD if active_name == "cancel" else 0)
            window.addnstr(16, box_width // 2 - 12, " < Apply > ", 11, apply_attr)
            window.addnstr(16, box_width // 2 + 2, " < Cancel > ", 12, cancel_attr)
            window.addnstr(18, 3, "Tab/Up/Down: move   Left/Right: edit/select   Esc: cancel", box_width - 6, curses.color_pair(4))

            if active_name in field_rows and active_name not in modes:
                row, _ = field_rows[active_name]
                value = values[active_name]
                cursor = cursors[active_name]
                inner_width = field_width - 2
                start = max(min(cursor - inner_width + 1, max(len(value) - inner_width, 0)), 0)
                window.move(row, min(field_x + 1 + cursor - start, field_x + field_width - 2))
                curses.curs_set(1)
            else:
                curses.curs_set(0)
            window.refresh()
            key = window.getch()
            if key == 27:
                curses.curs_set(0)
                return None
            if key in {curses.KEY_DOWN, 9}:
                move(1)
                continue
            if key in {curses.KEY_UP, curses.KEY_BTAB}:
                move(-1)
                continue
            if active_name in modes:
                choices = modes[active_name]
                if key in {curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, curses.KEY_ENTER, ord(" ")}:
                    offset = -1 if key == curses.KEY_LEFT else 1
                    values[active_name] = choices[(choices.index(values[active_name]) + offset) % len(choices)]
                    if active_name == "ipv4_method" and values[active_name] == "dhcp":
                        values["ipv4_cidr"] = values["gateway"] = ""
                        cursors["ipv4_cidr"] = cursors["gateway"] = 0
                    if active_name == "ipv6_mode" and values[active_name] != "static":
                        values["ipv6_cidr"] = values["ipv6_gateway"] = ""
                        cursors["ipv6_cidr"] = cursors["ipv6_gateway"] = 0
                continue
            if active_name == "apply" and key in {10, 13, curses.KEY_ENTER, ord(" ")}:
                curses.curs_set(0)
                return (
                    values["ipv4_method"],
                    values["ipv4_cidr"] if values["ipv4_method"] == "static" else "",
                    values["gateway"] if values["ipv4_method"] == "static" else "",
                    values["ipv6_mode"],
                    values["ipv6_cidr"] if values["ipv6_mode"] == "static" else "",
                    values["ipv6_gateway"] if values["ipv6_mode"] == "static" else "",
                    values["dns_servers"],
                )
            if active_name == "cancel" and key in {10, 13, curses.KEY_ENTER, ord(" ")}:
                curses.curs_set(0)
                return None
            if active_name in field_rows:
                values[active_name], cursors[active_name] = self._edit_text(
                    values[active_name], cursors[active_name], key, limit=200
                )

    def _require_authentication(self) -> bool:
        password = self._prompt("Administrator authentication", "Root password:", secret=True)
        if password is None:
            return False
        authenticated = authenticate_root(password)
        password = ""
        if not authenticated:
            self.message = "Authentication failed."
            self.message_error = True
        return authenticated

    def _apply_action(self, operation: Callable[[], str | dict[str, Any] | None], success: str) -> None:
        try:
            result = operation()
            suffix = f" ({result})" if isinstance(result, str) else ""
            self.message = f"{success}{suffix}"
            self.message_error = False
        except Exception as exc:  # noqa: BLE001 - show safe operation failure in recovery console.
            self.message = str(exc)
            self.message_error = True
        finally:
            # Service actions can change the active console while systemd settles. Force a
            # physical clear when the menu closes instead of trusting the curses diff buffer.
            self._force_clear = True

    def _run_interactive(self, command: list[str], label: str) -> int | None:
        curses = self.curses
        try:
            curses.def_prog_mode()
            curses.endwin()
            self._clear_terminal()
            # The service sends its own stderr to the journal. Interactive child
            # programs must explicitly bind all three streams to tty1 so Bash's
            # prompt and errors remain visible to the local operator.
            result = subprocess.run(
                command,
                check=False,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stdout,
            )
            if result.returncode not in {0, 130}:
                self.message = f"{label} exited with status {result.returncode}."
                self.message_error = True
            return result.returncode
        except FileNotFoundError:
            self.message = f"{label} is not installed on this appliance."
            self.message_error = True
            return None
        except KeyboardInterrupt:
            return 130
        except OSError as exc:
            self.message = f"Unable to start {label}: {exc}"
            self.message_error = True
            return None
        finally:
            self._clear_terminal()
            try:
                curses.reset_prog_mode()
            except curses.error:
                pass
            self._initialize_screen()
            self._force_clear = True

    @staticmethod
    def _clear_terminal() -> None:
        """Clear the physical tty before and after an interactive handoff."""
        try:
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()
        except OSError:
            pass

    def show_top(self) -> None:
        """Temporarily hand tty1 to top, then restore the curses console."""
        self._run_interactive(["top"], "top")

    def show_shell(self) -> None:
        """Open an authenticated, auditable root login shell on tty1."""
        try:
            record_console_shell("open")
        except Exception as exc:  # noqa: BLE001 - fail closed when the audit cannot be persisted.
            self.message = f"Unable to audit console access: {exc}"
            self.message_error = True
            return
        try:
            self._run_interactive([BASH_PATH, "--login"], "Bash console")
        finally:
            try:
                record_console_shell("close")
            except Exception as exc:  # noqa: BLE001 - preserve the local recovery surface.
                self.message = f"Bash console closed; audit close failed: {exc}"
                self.message_error = True

    def customize(self) -> None:
        if not self._require_authentication():
            return
        while True:
            status = load_console_status()
            isolation_label = "Restore appliance services" if status.maintenance_isolation else "Disable all appliance services"
            choice = self._dialog(
                "Customize LabFoundry",
                ["Only management recovery settings are available from the local console."],
                ["Management IP, gateways, and DNS", "Enable firewall" if not status.firewall_enabled else "Disable firewall", isolation_label, "Back"],
            )
            if choice == 3:
                return
            if choice == 0:
                management = self._management_form(status)
                if management is None:
                    self._restore_main_surface()
                    continue
                ipv4_method, cidr, gateway, ipv6_mode, ipv6_cidr, ipv6_gateway, dns_servers = management
                confirm = self._dialog(
                    "Apply management network and DNS",
                    [
                        f"IPv4: {ipv4_method} {cidr}",
                        f"IPv4 gateway: {gateway or 'none'}",
                        f"IPv6: {ipv6_mode} {ipv6_cidr}",
                        f"IPv6 gateway: {ipv6_gateway or 'none'}",
                        f"DNS: {dns_servers}",
                    ],
                    ["Apply", "Cancel"],
                )
                if confirm == 0:
                    self._apply_action(
                        lambda: configure_management(
                            ipv4_method,
                            cidr,
                            gateway,
                            ipv6_mode,
                            ipv6_cidr,
                            ipv6_gateway,
                            dns_servers,
                        ),
                        "Management networking and DNS applied",
                    )
                self._restore_main_surface()
            elif choice == 1:
                enabling_firewall = not status.firewall_enabled
                firewall_label = "Enable firewall" if enabling_firewall else "Disable firewall"
                warning = (
                    "Rebuilds and applies the current LabFoundry nftables rules."
                    if enabling_firewall
                    else "Clears the active nftables ruleset and persists that state across reboot."
                )
                confirm = self._dialog(firewall_label, [warning], [firewall_label, "Cancel"])
                if confirm == 0:
                    self._apply_action(lambda: configure_firewall(enabling_firewall), f"{firewall_label} completed")
            elif choice == 2:
                enabling = not status.maintenance_isolation
                warning = (
                    "Stops and disables appliance application services."
                    if enabling
                    else "Restores only the services that were enabled before isolation."
                )
                confirm = self._dialog(
                    isolation_label,
                    [warning, "Management networking, firewall, and this console stay available."],
                    [isolation_label, "Cancel"],
                )
                if confirm == 0:
                    self._apply_action(lambda: set_maintenance_isolation(enabling), f"{isolation_label} completed")

    def _restore_main_surface(self) -> None:
        """Physically clear nested form remnants before rebuilding a parent menu."""
        self._force_clear = True
        self.draw_main()

    def power_menu(self) -> None:
        if not self._require_authentication():
            return
        choice = self._dialog(
            "Shut down / Restart",
            ["The selected power action is scheduled after this console records it."],
            ["Restart appliance", "Shut down appliance", "Cancel"],
        )
        if choice == 0:
            if self._dialog("Confirm restart", ["The appliance will restart after a short delay."], ["Restart appliance", "Cancel"]) == 0:
                self._apply_action(lambda: schedule_power("reboot"), "Restart scheduled")
        elif choice == 1:
            if self._dialog("Confirm shutdown", ["The appliance will shut down after a short delay."], ["Shut down appliance", "Cancel"]) == 0:
                self._apply_action(lambda: schedule_power("shutdown"), "Shutdown scheduled")

    def run(self) -> None:
        curses = self.curses
        self.draw_main()
        last_refresh = time.monotonic()
        recovery_redraws = self._recovery_redraws(last_refresh)
        while True:
            key = self.stdscr.getch()
            if key == curses.KEY_F2:
                self.customize()
                self.draw_main()
                last_refresh = time.monotonic()
                recovery_redraws = self._recovery_redraws(last_refresh)
            elif key == curses.KEY_F3:
                self.show_top()
                self.draw_main()
                last_refresh = time.monotonic()
                recovery_redraws = self._recovery_redraws(last_refresh)
            elif key == curses.KEY_F4:
                if self._require_authentication():
                    self.show_shell()
                self.draw_main()
                last_refresh = time.monotonic()
                recovery_redraws = self._recovery_redraws(last_refresh)
            elif key == curses.KEY_F12:
                self.power_menu()
                self.draw_main()
                last_refresh = time.monotonic()
                recovery_redraws = self._recovery_redraws(last_refresh)
            elif recovery_redraws and time.monotonic() >= recovery_redraws[0]:
                recovery_redraws.pop(0)
                # Repaint every cell without clearing to black first. This repairs
                # out-of-band tty writes without exposing a blank intermediate frame.
                self._force_redraw = True
                self.draw_main()
                last_refresh = time.monotonic()
            elif key == curses.KEY_RESIZE or time.monotonic() - last_refresh >= CONSOLE_REFRESH_SECONDS:
                self.draw_main()
                last_refresh = time.monotonic()


def main() -> int:
    if os.name != "posix":
        print("LabFoundry appliance console is available only on the Photon OS appliance.", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("LabFoundry appliance console must run as root on the local virtual console.", file=sys.stderr)
        return 2
    # Database/config modules may already have been imported by the entrypoint, so refresh their cached settings.
    from labfoundry.app.config import get_settings

    get_settings.cache_clear()
    try:
        import curses

        curses.wrapper(lambda stdscr: CursesConsole(stdscr).run())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - systemd will restart the recovery console.
        print(f"LabFoundry appliance console failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

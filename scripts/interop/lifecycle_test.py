#!/usr/bin/env python3
"""LabFoundry Hyper-V lifecycle interop runner.

The Windows Hyper-V script owns VM topology. This runner owns appliance and
guest assertions so failures produce reusable evidence instead of console fog.
"""

from __future__ import annotations

import argparse
import base64
import html
import http.cookiejar
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_interface
from pathlib import Path
from typing import Any

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:  # pragma: no cover - Photon image should include cryptography
    x509 = None  # type: ignore[assignment]


ALL_SCOPES = [
    "read:dashboard",
    "read:interfaces",
    "write:interfaces",
    "read:vlans",
    "write:vlans",
    "read:routes",
    "write:routes",
    "read:wan",
    "write:wan",
    "read:firewall",
    "write:firewall",
    "read:dns",
    "write:dns",
    "read:dhcp",
    "write:dhcp",
    "read:ca",
    "write:ca",
    "read:kms",
    "write:kms",
    "read:repository",
    "write:repository",
    "read:vcf-registry",
    "write:vcf-registry",
    "read:vcf-backups",
    "write:vcf-backups",
    "read:services",
    "write:services",
    "read:logs",
    "read:audit",
    "write:backup",
    "admin:all",
]


@dataclass
class StepResult:
    name: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: utc_now())
    finished_at: str | None = None
    error: str | None = None

    def finish(self, status: str = "passed", error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.finished_at = utc_now()


class LifecycleError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LabFoundry appliance lifecycle interop checks.")
    parser.add_argument("--appliance-url", default="http://192.168.49.1")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--result-dir", default="test-results/hyperv-lifecycle/latest")
    parser.add_argument("--site-interface", default="eth1")
    parser.add_argument("--trunk-interface", default="eth2")
    parser.add_argument("--wan-interface", default="eth3")
    parser.add_argument("--vlan-id", type=int, default=50)
    parser.add_argument("--site-cidr", default="192.168.50.1/24")
    parser.add_argument("--vlan-cidr", default="192.168.60.1/24")
    parser.add_argument("--wan-cidr", default="172.31.50.1/24")
    parser.add_argument("--domain", default="labfoundry.internal")
    parser.add_argument("--client-a-host", default="")
    parser.add_argument("--client-b-host", default="")
    parser.add_argument("--client-ca-request-interface", default="eth3")
    parser.add_argument("--client-ca-request-cidr", default="192.168.49.20/24")
    parser.add_argument("--client-ca-request-url", default="")
    parser.add_argument("--ssh-user", default="", help="Compatibility override for both appliance and client SSH users.")
    parser.add_argument("--appliance-ssh-user", default="admin")
    parser.add_argument("--client-ssh-user", default="alpine")
    parser.add_argument("--ssh-key", default="")
    parser.add_argument("--ssh-password", default="")
    parser.add_argument("--appliance-ssh-host", default="192.168.49.1")
    parser.add_argument("--appliance-ssh-hostkey", default="")
    parser.add_argument("--client-a-hostkey", default="")
    parser.add_argument("--client-b-hostkey", default="")
    parser.add_argument("--vcf-backup-password", default="VMware01!Test")
    parser.add_argument("--allow-dry-run", action="store_true", help="Allow apply units to report dry-run instead of failing.")
    parser.add_argument("--skip-client-checks", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="Write the intended lifecycle plan without changing the appliance.")
    return parser.parse_args(argv)


class HttpClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.bearer_token = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        form: dict[str, Any] | list[tuple[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> tuple[int, str, dict[str, str]]:
        url = f"{self.base_url}{path}"
        body: bytes | None = None
        request_headers = dict(headers or {})
        if self.bearer_token:
            request_headers.setdefault("Authorization", f"Bearer {self.bearer_token}")
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        opener = self.opener if follow_redirects else no_redirect_opener(self.cookie_jar)
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, response.read().decode("utf-8", errors="replace"), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace"), dict(exc.headers.items())

    def json_request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> Any:
        status, body, _headers = self.request(method, path, json_body=json_body)
        if status >= 400:
            raise LifecycleError(f"{method} {path} failed with HTTP {status}: {body[:500]}")
        return json.loads(body) if body else None


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def no_redirect_opener(cookie_jar: http.cookiejar.CookieJar) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar), NoRedirectHandler)


def extract_csrf(body: str) -> str:
    patterns = [
        r'name="csrf"\s+value="([^"]+)"',
        r"data-csrf=\"([^\"]+)\"",
    ]
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            return html.unescape(match.group(1))
    raise LifecycleError("CSRF token not found in HTML response.")


def summarize_html_response(body: str, *, limit: int = 1200) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", body)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    anchors = [
        "Resolve validation errors before submitting appliance changes.",
        "Select at least one appliance change to submit.",
        "Appliance apply task failed",
    ]
    for anchor in anchors:
        index = text.find(anchor)
        if index >= 0:
            return text[index : index + limit]
    return text[:limit]


def ssh_username(args: argparse.Namespace, role: str) -> str:
    if args.ssh_user:
        return args.ssh_user
    if role == "appliance":
        return args.appliance_ssh_user
    return args.client_ssh_user


def ssh_hostkey(host: str, args: argparse.Namespace, role: str) -> str:
    if role == "appliance":
        return args.appliance_ssh_hostkey
    if host == args.client_a_host:
        return args.client_a_hostkey
    if host == args.client_b_host:
        return args.client_b_hostkey
    return ""


def appliance_ssh_command(args: argparse.Namespace, command: str) -> str:
    if ssh_username(args, "appliance") == "root":
        return command
    quoted_command = shell_single_quote(command)
    if args.ssh_password:
        quoted_password = shell_single_quote(args.ssh_password)
        return f"printf '%s\\n' {quoted_password} | sudo -S -p '' sh -lc {quoted_command}"
    return f"sudo -n sh -lc {quoted_command}"


def redact_text(value: str, secrets: list[str] | None = None) -> str:
    redacted = value
    for secret in secrets or []:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def redact_sequence(values: list[str], secrets: list[str] | None = None) -> list[str]:
    return [redact_text(value, secrets) for value in values]


def ssh_command(
    host: str,
    args: argparse.Namespace,
    command: str,
    *,
    role: str,
    redact_values: list[str] | None = None,
) -> dict[str, Any]:
    if not host:
        raise LifecycleError("SSH host was not provided.")
    user = ssh_username(args, role)
    remote_command = appliance_ssh_command(args, command) if role == "appliance" else command
    secrets = [args.ssh_password, *(redact_values or [])]
    if args.ssh_password:
        plink_args = ["plink", "-batch", "-ssh", "-pw", args.ssh_password, f"{user}@{host}", remote_command]
        hostkey = ssh_hostkey(host, args, role)
        if hostkey:
            plink_args[3:3] = ["-hostkey", hostkey]
        redacted = ["plink", "-batch", "-ssh"]
        if hostkey:
            redacted.extend(["-hostkey", hostkey])
        redacted.extend(["-pw", "[redacted]", f"{user}@{host}", redact_text(remote_command, secrets)])
        try:
            completed = subprocess.run(plink_args, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            return {
                "command": redacted,
                "returncode": 124,
                "stdout": redact_text(exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""), secrets),
                "stderr": "SSH command timed out after 120 seconds.",
            }
        return {
            "command": redacted,
            "returncode": completed.returncode,
            "stdout": redact_text(completed.stdout, secrets),
            "stderr": redact_text(completed.stderr, secrets),
        }
    ssh_args = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15"]
    if args.ssh_key:
        ssh_args.extend(["-i", args.ssh_key])
    target = f"{user}@{host}"
    redacted_command = redact_sequence([*ssh_args, target, remote_command], secrets)
    try:
        completed = subprocess.run([*ssh_args, target, remote_command], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": redacted_command,
            "returncode": 124,
            "stdout": redact_text(exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""), secrets),
            "stderr": "SSH command timed out after 120 seconds.",
        }
    return {
        "command": redacted_command,
        "returncode": completed.returncode,
        "stdout": redact_text(completed.stdout, secrets),
        "stderr": redact_text(completed.stderr, secrets),
    }


def require_success(result: dict[str, Any], label: str) -> None:
    if result["returncode"] != 0:
        details = []
        if result.get("stderr"):
            details.append(f"stderr: {result['stderr']}")
        if result.get("stdout"):
            details.append(f"stdout: {result['stdout']}")
        raise LifecycleError(f"{label} failed with exit {result['returncode']}: {' '.join(details) or 'no output'}")


def lifecycle_plan(args: argparse.Namespace) -> dict[str, Any]:
    vlan_name = f"{args.trunk_interface}.{args.vlan_id}"
    return {
        "appliance_url": args.appliance_url,
        "interfaces": {
            "site": {"name": args.site_interface, "ip_cidr": args.site_cidr, "mode": "access"},
            "trunk": {"name": args.trunk_interface, "mode": "trunk"},
            "vlan": {"name": vlan_name, "parent": args.trunk_interface, "vlan_id": args.vlan_id, "ip_cidr": args.vlan_cidr},
            "wan": {"name": args.wan_interface, "ip_cidr": args.wan_cidr, "mode": "access"},
            "client_ca_request": {
                "name": args.client_ca_request_interface,
                "ip_cidr": args.client_ca_request_cidr,
                "url": args.client_ca_request_url or args.appliance_url,
            },
        },
        "apply_units": ["local_users", "network", "firewall", "wan", "dnsmasq", "ca", "kms", "appliance_settings", "vcf_backups"],
        "checks": [
            "appliance health",
            "interface and VLAN desired state",
            "DNS and DHCP desired state",
            "firewall, routing, NAT, and WAN desired state",
            "CA desired state, root certificate download, client CSR request, issued certificate download, and client-side verification",
            "KMS desired state, DNS/firewall apply, PyKMIP service, and TLS client-certificate probe",
            "VCF Backup desired state, local user sync, SFTP listener, and client probe",
            "client DNS/DHCP/routing probes",
        ],
        "client_checks_enabled": not args.skip_client_checks,
    }


def api_login(client: HttpClient, args: argparse.Namespace) -> str:
    path = f"/api/v1/auth/login?{urllib.parse.urlencode({'username': args.username, 'password': args.password})}"
    payload = {"name": "hyperv lifecycle interop", "scopes": ALL_SCOPES}
    token = client.json_request("POST", path, json_body=payload)["raw_token"]
    client.bearer_token = token
    return token


def ui_login(client: HttpClient, args: argparse.Namespace) -> str:
    status, body, _headers = client.request("GET", "/login")
    if status >= 400:
        raise LifecycleError(f"GET /login failed with HTTP {status}")
    csrf = extract_csrf(body)
    status, body, _headers = client.request(
        "POST",
        "/login",
        form={"username": args.username, "password": args.password, "csrf": csrf},
        follow_redirects=False,
    )
    if status not in {303, 302}:
        raise LifecycleError(f"UI login failed with HTTP {status}: {body[:500]}")
    return csrf


def configure_network(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    evidence["refresh"] = client.json_request("POST", "/api/v1/interfaces/refresh")
    if "." in args.site_interface:
        site_parent, site_vlan_raw = args.site_interface.split(".", 1)
        client.json_request(
            "PATCH",
            f"/api/v1/interfaces/physical/{site_parent}",
            json_body={"admin_state": "up", "mode": "trunk", "role": "unused", "ip_cidr": ""},
        )
        ensure_vlan(
            client,
            parent_interface=site_parent,
            vlan_id=int(site_vlan_raw),
            ip_cidr=args.site_cidr,
            role="access",
        )
        evidence["site_vlan"] = args.site_interface
    else:
        client.json_request(
            "PATCH",
            f"/api/v1/interfaces/physical/{args.site_interface}",
            json_body={"admin_state": "up", "mode": "access", "role": "access", "ip_cidr": args.site_cidr},
        )
    client.json_request(
        "PATCH",
        f"/api/v1/interfaces/physical/{args.trunk_interface}",
        json_body={"admin_state": "up", "mode": "trunk", "role": "unused", "ip_cidr": ""},
    )
    client.json_request(
        "PATCH",
        f"/api/v1/interfaces/physical/{args.wan_interface}",
        json_body={"admin_state": "up", "mode": "access", "role": "wan", "ip_cidr": args.wan_cidr},
    )
    evidence["vlan"] = ensure_vlan(
        client,
        parent_interface=args.trunk_interface,
        vlan_id=args.vlan_id,
        ip_cidr=args.vlan_cidr,
        role="access",
    )
    return evidence


def ensure_vlan(client: HttpClient, *, parent_interface: str, vlan_id: int, ip_cidr: str, role: str) -> dict[str, Any]:
    existing_vlans = client.json_request("GET", "/api/v1/vlans")
    vlan_name = f"{parent_interface}.{vlan_id}"
    existing = next((row for row in existing_vlans if row.get("name") == vlan_name), None)
    payload = {
        "parent_interface": parent_interface,
        "vlan_id": vlan_id,
        "ip_cidr": ip_cidr,
        "mtu": 1500,
        "role": role,
        "enabled": True,
    }
    if existing:
        return client.json_request("PATCH", f"/api/v1/vlans/{existing['id']}", json_body=payload)
    return client.json_request("POST", "/api/v1/vlans", json_body=payload)


def configure_dns_dhcp(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    site = ip_interface(args.site_cidr)
    site_ip = str(site.ip)
    site_network = site.network
    hosts = list(site_network.hosts())
    if len(hosts) < 181:
        raise LifecycleError(f"Site network {site_network} is too small for the default lifecycle DHCP pool.")
    range_start = str(hosts[99])
    range_end = str(hosts[179])
    client.json_request(
        "PATCH",
        "/api/v1/dns/settings",
        json_body={
            "enabled": True,
            "listen_interface": args.site_interface,
            "listen_address": site_ip,
            "domain": args.domain,
            "upstream_servers": ["1.1.1.1", "9.9.9.9"],
            "conditional_forwarders": [],
            "cache_size": 1000,
            "expand_hosts": True,
            "authoritative": True,
        },
    )
    records = client.json_request("GET", "/api/v1/dns/records")
    record_payload = {
        "hostname": f"interop-appliance.{args.domain}",
        "record_type": "A",
        "address": site_ip,
        "description": "Hyper-V lifecycle interop record",
        "enabled": True,
    }
    existing_record = next((row for row in records if row.get("hostname") == record_payload["hostname"]), None)
    if existing_record:
        record = client.json_request("PATCH", f"/api/v1/dns/records/{existing_record['id']}", json_body=record_payload)
    else:
        record = client.json_request("POST", "/api/v1/dns/records", json_body=record_payload)

    client.json_request(
        "PATCH",
        "/api/v1/dhcp/settings",
        json_body={
            "enabled": True,
            "interface_name": args.site_interface,
            "site_address": site_ip,
            "prefix_length": site.network.prefixlen,
            "range_start": range_start,
            "range_end": range_end,
            "lease_time": "1h",
            "domain_name": args.domain,
            "dns_server": site_ip,
            "authoritative": True,
        },
    )
    scopes = client.json_request("GET", "/api/v1/dhcp/scopes")
    scope_payload = {
        "name": "Lifecycle SiteA",
        "interface_name": args.site_interface,
        "site_address": site_ip,
        "prefix_length": site.network.prefixlen,
        "range_start": range_start,
        "range_end": range_end,
        "lease_time": "1h",
        "domain_name": args.domain,
        "dns_server": site_ip,
        "ntp_server": "",
        "enabled": True,
        "description": "Hyper-V lifecycle interop scope",
    }
    existing_scope = next((row for row in scopes if row.get("name") == scope_payload["name"]), None)
    if existing_scope:
        scope = client.json_request("PATCH", f"/api/v1/dhcp/scopes/{existing_scope['id']}", json_body=scope_payload)
    else:
        scope = client.json_request("POST", "/api/v1/dhcp/scopes", json_body=scope_payload)
    return {"record": record, "scope": scope}


def configure_firewall_wan(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    site_source = str(ip_interface(args.site_cidr).network)
    wan_network = str(ip_interface(args.wan_cidr).network)
    client.json_request(
        "PATCH",
        "/api/v1/firewall/settings",
        json_body={
            "enabled": True,
            "default_input_policy": "drop",
            "default_forward_policy": "accept",
            "default_output_policy": "accept",
            "allow_established": True,
            "allow_loopback": True,
            "allow_icmp": True,
            "log_dropped": False,
        },
    )
    policies = client.json_request("GET", "/api/v1/wan/policies")
    policy_payload = {
        "name": "Lifecycle WAN",
        "description": "Hyper-V lifecycle interop WAN policy",
        "enabled": True,
        "latency_ms": 25,
        "jitter_ms": 5,
        "packet_loss_percent": 0.0,
        "bandwidth_mbit": 100,
        "corrupt_percent": 0.0,
        "duplicate_percent": 0.0,
        "reorder_percent": 0.0,
    }
    existing_policy = next((row for row in policies if row.get("name") == policy_payload["name"]), None)
    if existing_policy:
        policy = client.json_request("PATCH", f"/api/v1/wan/policies/{existing_policy['id']}", json_body=policy_payload)
    else:
        policy = client.json_request("POST", "/api/v1/wan/policies", json_body=policy_payload)
    route_payload = {
        "destination_cidr": wan_network,
        "gateway": None,
        "interface_name": args.wan_interface,
        "metric": 100,
        "enabled": True,
        "wan_policy_id": policy["id"],
        "wan_mode": "interface",
    }
    routes = client.json_request("GET", "/api/v1/routes")
    existing_route = next(
        (
            row
            for row in routes
            if row.get("destination_cidr") == route_payload["destination_cidr"]
            and row.get("interface_name") == route_payload["interface_name"]
        ),
        None,
    )
    if existing_route:
        route = client.json_request("PATCH", f"/api/v1/routes/{existing_route['id']}", json_body=route_payload)
    else:
        route = client.json_request("POST", "/api/v1/routes", json_body=route_payload)
    nat_payload = {
        "name": "Lifecycle SiteA outbound WAN",
        "enabled": True,
        "source": site_source,
        "outbound_interface": args.wan_interface,
        "masquerade": True,
        "priority": 100,
        "description": "Hyper-V lifecycle interop NAT",
    }
    nat_rules = client.json_request("GET", "/api/v1/nat/rules")
    existing_nat = next((row for row in nat_rules if row.get("name") == nat_payload["name"]), None)
    if existing_nat:
        nat = client.json_request("PATCH", f"/api/v1/nat/rules/{existing_nat['id']}", json_body=nat_payload)
    else:
        nat = client.json_request("POST", "/api/v1/nat/rules", json_body=nat_payload)
    return {"wan_policy": policy, "route": route, "nat": nat}


def certificate_summary(pem: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"pem_bytes": len(pem.encode("utf-8"))}
    if x509 is None:
        return summary
    certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    summary.update(
        {
            "subject": certificate.subject.rfc4514_string(),
            "issuer": certificate.issuer.rfc4514_string(),
            "serial_number": format(certificate.serial_number, "x"),
            "not_before": certificate.not_valid_before_utc.isoformat(),
            "not_after": certificate.not_valid_after_utc.isoformat(),
        }
    )
    return summary


def configure_ca(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    status, body, _headers = client.request("GET", "/certificate-authority")
    if status >= 400:
        raise LifecycleError(f"GET /certificate-authority failed with HTTP {status}")
    csrf = extract_csrf(body)
    form = {
        "enabled": "on",
        "listen_interfaces_present": "1",
        "listen_interfaces": [args.site_interface],
        "root_common_name": "LabFoundry Lifecycle Root CA",
        "organization": "LabFoundry",
        "organizational_unit": "Interop",
        "country": "US",
        "state": "California",
        "locality": "Lifecycle Lab",
        "key_algorithm": "RSA",
        "key_size": "2048",
        "digest_algorithm": "sha256",
        "root_valid_days": "3650",
        "intermediate_valid_days": "1825",
        "csrf": csrf,
    }
    status, response_body, _headers = client.request("POST", "/certificate-authority/settings", form=form, follow_redirects=False)
    if status not in {200, 302, 303}:
        raise LifecycleError(f"CA settings update failed with HTTP {status}: {response_body[:500]}")
    status, root_ca, _headers = client.request("GET", "/certificate-authority/downloads/root-ca.pem")
    if status >= 400 or "BEGIN CERTIFICATE" not in root_ca:
        raise LifecycleError(f"CA root download failed with HTTP {status}")
    return {"root_ca": certificate_summary(root_ca)}


def configure_management_https(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    status, body, _headers = client.request("GET", "/settings")
    if status >= 400:
        raise LifecycleError(f"GET /settings failed with HTTP {status}")
    csrf = extract_csrf(body)
    form = {
        "fqdn": "labfoundry.labfoundry.internal",
        "management_https_enabled": "on",
        "external_dns_servers": "1.1.1.1\n9.9.9.9",
        "ntp_servers": "time1.google.com\ntime2.google.com",
        "csrf": csrf,
    }
    status, response_body, _headers = client.request(
        "POST",
        "/settings",
        form=form,
        headers={"X-LabFoundry-Autosave": "1"},
    )
    if status >= 400:
        raise LifecycleError(f"Management HTTPS settings update failed with HTTP {status}: {response_body[:500]}")
    payload = json.loads(response_body)
    if not payload.get("valid"):
        raise LifecycleError(f"Management HTTPS desired state is invalid: {payload.get('validation_errors')}")
    if not payload.get("management_https_enabled") or not payload.get("management_https_cert_available"):
        raise LifecycleError("Management HTTPS desired state did not report an available CA-managed certificate.")
    return {
        "fqdn": payload.get("fqdn"),
        "management_https_enabled": payload.get("management_https_enabled"),
        "management_https_cert_available": payload.get("management_https_cert_available"),
        "config_path": payload.get("config_path"),
    }


def https_request_unverified(url: str) -> tuple[int, str, dict[str, str]]:
    request = urllib.request.Request(url, method="GET")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            return response.status, response.read().decode("utf-8", errors="replace"), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), dict(exc.headers.items())


def management_https_check(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    http_status, _http_body, http_headers = client.request("GET", "/openapi.json", follow_redirects=False)
    if http_status not in {301, 302, 307, 308}:
        raise LifecycleError(f"HTTP management endpoint should redirect after HTTPS apply, got HTTP {http_status}")
    location = http_headers.get("Location", "")
    if not location.lower().startswith("https://"):
        raise LifecycleError(f"HTTP management redirect did not point at HTTPS: {location}")
    parsed = urllib.parse.urlparse(args.appliance_url)
    host = parsed.hostname or args.appliance_ssh_host
    https_url = f"https://{host}/openapi.json"
    https_status, https_body, _https_headers = https_request_unverified(https_url)
    if https_status >= 400 or '"openapi"' not in https_body:
        raise LifecycleError(f"HTTPS management endpoint failed with HTTP {https_status}")
    return {"http_status": http_status, "redirect_location": location, "https_status": https_status, "https_url": https_url}


def extract_ca_profile_id(body: str, profile_name: str) -> str:
    for match in re.finditer(r'<option value="(\d+)">([^<]+)</option>', body):
        if html.unescape(match.group(2)).strip() == profile_name:
            return match.group(1)
    raise LifecycleError(f"Could not find enabled CA profile {profile_name!r}.")


def extract_ca_certificate_id(body: str, common_name: str) -> str:
    row_pattern = re.compile(
        rf"<tr>\s*<td>{re.escape(html.escape(common_name))}</td>.*?/certificate-authority/certificates/(\d+)/downloads/certificate\.pem",
        flags=re.DOTALL,
    )
    match = row_pattern.search(body)
    if match:
        return match.group(1)
    loose = re.search(r"/certificate-authority/certificates/(\d+)/downloads/certificate\.pem", body)
    if loose and common_name in body:
        return loose.group(1)
    raise LifecycleError(f"Could not find issued CA certificate download link for {common_name}.")


def create_client_csr(common_name: str) -> str:
    if x509 is None:
        raise LifecycleError("cryptography is required to generate the lifecycle client CSR.")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LabFoundry"),
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Lifecycle Client"),
                ]
            )
        )
        .sign(private_key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")


def session_cookie_header(client: HttpClient) -> str:
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in client.cookie_jar)


def ca_client_certificate_request(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_client_checks:
        return {"skipped": "client checks disabled"}
    if not args.client_a_host:
        return {"skipped": "client A host not provided"}
    common_name = f"client-a.{args.domain}"
    csr_text = create_client_csr(common_name)

    status, body, _headers = client.request("GET", "/certificate-authority")
    if status >= 400:
        raise LifecycleError(f"GET /certificate-authority failed with HTTP {status}")
    csrf = extract_csrf(body)
    profile_id = extract_ca_profile_id(body, "VCF KMIP client")
    cookie_header = session_cookie_header(client)
    if not cookie_header:
        raise LifecycleError("No authenticated session cookie is available for the client CA request.")
    ca_request = ip_interface(args.client_ca_request_cidr)
    ca_request_url = (args.client_ca_request_url or args.appliance_url).rstrip("/")
    elevate = elevation_probe()
    command = (
        f"ELEV=\"$({elevate})\"; "
        "test -n \"$ELEV\"; "
        f"$ELEV ip link set {args.client_ca_request_interface} up; "
        f"$ELEV ip addr replace {ca_request.ip}/{ca_request.network.prefixlen} dev {args.client_ca_request_interface}; "
        "http_code=$(curl -sS --connect-timeout 10 --max-time 30 -o /dev/null -w '%{http_code}' "
        f"-X POST -H {shell_single_quote('Cookie: ' + cookie_header)} "
        f"--data-urlencode csrf={shell_single_quote(csrf)} "
        f"--data-urlencode common_name={shell_single_quote(common_name)} "
        f"--data-urlencode profile_id={shell_single_quote(profile_id)} "
        "--data-urlencode subject_alt_names= "
        "--data-urlencode ip_addresses= "
        "--data-urlencode status=csr-staged "
        "--data-urlencode serial_number= "
        "--data-urlencode description='Hyper-V lifecycle client CSR request' "
        f"--data-urlencode csr_text={shell_single_quote(csr_text.strip())} "
        "--data-urlencode enabled=on "
        f"{ca_request_url}/certificate-authority/certificates); "
        "echo \"$http_code\"; test \"$http_code\" = \"302\" -o \"$http_code\" = \"303\""
    )
    request_result = ssh_command(args.client_a_host, args, command, role="client", redact_values=[cookie_header])
    require_success(request_result, "client A CA certificate request")
    return {
        "common_name": common_name,
        "profile": "VCF KMIP client",
        "csr_bytes": len(csr_text.encode("utf-8")),
        "request": request_result,
        "requested_via": f"{ca_request_url}/certificate-authority/certificates",
        "client_probe_ip": str(ca_request.ip),
        "client_probe_interface": args.client_ca_request_interface,
    }


def extract_vcf_backup_user_id(body: str) -> str:
    match = re.search(r'<option value="(\d+)"[^>]*>\s*vcf-backup(?:\s+\(disabled\))?\s*</option>', body)
    if not match:
        raise LifecycleError("Could not find the default vcf-backup local user in /vcf-backups.")
    return match.group(1)


def configure_vcf_backups(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    status, body, _headers = client.request("GET", "/vcf-backups")
    if status >= 400:
        raise LifecycleError(f"GET /vcf-backups failed with HTTP {status}")
    csrf = extract_csrf(body)
    user_id = extract_vcf_backup_user_id(body)
    status, reset_body, _headers = client.request(
        "POST",
        f"/users/{user_id}/password",
        form={"password": args.vcf_backup_password, "confirm_password": args.vcf_backup_password, "csrf": csrf},
        follow_redirects=False,
    )
    if status not in {200, 302, 303}:
        raise LifecycleError(f"VCF backup user password staging failed with HTTP {status}: {reset_body[:500]}")
    status, response_body, _headers = client.request(
        "POST",
        "/vcf-backups/settings",
        form={
            "enabled": "on",
            "listen_interface": args.site_interface,
            "port": "22",
            "sftp_user_id": user_id,
            "chroot_enabled": "on",
            "allow_password_auth": "on",
            "allow_public_key_auth": "on",
            "max_sessions": "4",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    if status >= 400:
        raise LifecycleError(f"VCF Backup settings update failed with HTTP {status}: {response_body[:500]}")
    payload = json.loads(response_body)
    if not payload.get("valid"):
        raise LifecycleError(f"VCF Backup desired state is invalid: {payload.get('validation_errors')}")
    return {
        "listen_interface": payload.get("listen_interface"),
        "listen_address": payload.get("listen_address"),
        "sftp_username": payload.get("sftp_username"),
        "remote_directory": payload.get("remote_directory"),
        "config_path": payload.get("config_path"),
        "valid": payload.get("valid"),
    }


def configure_kms(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    status, body, _headers = client.request("GET", "/kms")
    if status >= 400:
        raise LifecycleError(f"GET /kms failed with HTTP {status}")
    csrf = extract_csrf(body)
    hostname = f"kms.{args.domain}"
    status, response_body, _headers = client.request(
        "POST",
        "/kms/settings",
        form={
            "enabled": "on",
            "backend": "pykmip",
            "listen_interface": args.site_interface,
            "port": "5696",
            "hostname": hostname,
            "server_certificate": hostname,
            "require_client_cert": "on",
            "allow_register": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    if status >= 400:
        raise LifecycleError(f"KMS settings update failed with HTTP {status}: {response_body[:500]}")
    payload = json.loads(response_body)
    if not payload.get("valid"):
        raise LifecycleError(f"KMS desired state is invalid: {payload.get('validation_errors')}")
    status, client_body, _headers = client.request(
        "POST",
        "/kms/clients",
        form={
            "name": "vcf-management",
            "certificate_subject": f"CN=vcf-management.{args.domain},O=LabFoundry",
            "role": "service",
            "allowed_operations": "locate,get,register,create,activate",
            "description": "Hyper-V lifecycle KMIP client",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    if status not in {200, 302, 303, 409}:
        raise LifecycleError(f"KMS client setup failed with HTTP {status}: {client_body[:500]}")
    return {
        "hostname": payload.get("hostname"),
        "listen_interface": payload.get("listen_interface"),
        "listen_address": payload.get("listen_address"),
        "port": payload.get("port"),
        "config_path": payload.get("config_path"),
        "client": "vcf-management",
        "valid": payload.get("valid"),
    }


def apply_units(client: HttpClient, units: list[str], args: argparse.Namespace) -> dict[str, Any]:
    status, body, _headers = client.request("GET", "/appliance-apply")
    if status >= 400:
        raise LifecycleError(f"GET /appliance-apply failed with HTTP {status}")
    csrf = extract_csrf(body)
    form: list[tuple[str, Any]] = [("csrf", csrf)]
    form.extend(("selected_units", unit) for unit in units)
    status, response_body, _headers = client.request("POST", "/appliance-apply", form=form)
    if status >= 400:
        raise LifecycleError(f"Appliance apply failed with HTTP {status}: {summarize_html_response(response_body)}")
    if "Appliance apply task failed" in response_body:
        raise LifecycleError(f"Appliance apply task failed: {summarize_html_response(response_body)}")
    if not args.allow_dry_run and re.search(r"\brecorded\s+as\s+dry-run\b|\bDry-run\s+mode\s+recorded\b", response_body, flags=re.IGNORECASE):
        raise LifecycleError("Appliance apply reported dry-run; rerun with --allow-dry-run or enable real adapters for lifecycle validation.")
    return {"http_status": status, "selected_units": units, "response_contains_job": "appliance-apply" in response_body}


def appliance_health(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    openapi = client.request("GET", "/openapi.json")
    if openapi[0] >= 400:
        raise LifecycleError(f"/openapi.json failed with HTTP {openapi[0]}")
    api_login(client, args)
    dashboard = client.json_request("GET", "/api/v1/dashboard")
    ui_login(client, args)
    ssh = ssh_command(
        args.appliance_ssh_host,
        args,
        "systemctl is-active labfoundry && curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null",
        role="appliance",
    )
    require_success(ssh, "appliance health SSH probe")
    return {"openapi_status": openapi[0], "dashboard_keys": sorted(dashboard.keys()), "ssh": ssh}


def direct_dns_a_query_command(name: str, server: str, expected_ip: str) -> str:
    script = f"""
import random
import socket
import struct
import sys

name = {name!r}
server = {server!r}
expected_ip = {expected_ip!r}
query_id = random.randrange(0, 65536)
qname = b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.rstrip(".").split(".")) + b"\\0"
packet = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(5)
sock.sendto(packet, (server, 53))
data, _ = sock.recvfrom(512)
if len(data) < 12:
    sys.exit("short DNS response")
response_id, flags, _qdcount, ancount, _nscount, _arcount = struct.unpack("!HHHHHH", data[:12])
if response_id != query_id:
    sys.exit("DNS response ID mismatch")
rcode = flags & 0x000F
if rcode != 0:
    sys.exit(f"DNS query failed with rcode {{rcode}}")
offset = 12
while offset < len(data) and data[offset] != 0:
    offset += data[offset] + 1
offset += 5
answers = []
for _ in range(ancount):
    if offset >= len(data):
        break
    if data[offset] & 0xC0 == 0xC0:
        offset += 2
    else:
        while offset < len(data) and data[offset] != 0:
            offset += data[offset] + 1
        offset += 1
    if offset + 10 > len(data):
        break
    rtype, rclass, _ttl, rdlen = struct.unpack("!HHIH", data[offset:offset + 10])
    offset += 10
    rdata = data[offset:offset + rdlen]
    offset += rdlen
    if rtype == 1 and rclass == 1 and rdlen == 4:
        answers.append(socket.inet_ntoa(rdata))
print("\\n".join(answers))
if expected_ip not in answers:
    sys.exit(f"expected {{expected_ip}} from {{server}} for {{name}}, got {{answers}}")
"""
    encoded = base64.b64encode(script.strip().encode("utf-8")).decode("ascii")
    return f"printf %s {encoded} | base64 -d | python3 -"


def host_state_checks(args: argparse.Namespace) -> dict[str, Any]:
    site_ip = str(ip_interface(args.site_cidr).ip)
    checks = {
        "network": "ip -br addr && ip route",
        "dnsmasq": f"test -f /etc/labfoundry/dnsmasq.d/labfoundry.conf && {direct_dns_a_query_command('interop-appliance.labfoundry.internal', site_ip, site_ip)}",
        "firewall": "nft list ruleset | head -n 160",
        "wan": (
            f"sysctl net.ipv4.ip_forward; "
            f"tc qdisc show dev {args.wan_interface}; "
            f"tc qdisc show dev {args.wan_interface} | grep netem | grep delay | grep 25ms"
        ),
        "ca": "test -f /etc/labfoundry/ca/ca-bundle.pem && openssl x509 -in /etc/labfoundry/ca/root-ca.pem -noout -subject",
        "kms_files": (
            "for path in "
            "/etc/labfoundry/kms/pykmip.conf "
            "/etc/pykmip/server.conf "
            "/etc/labfoundry/kms/certs/kms.labfoundry.internal.crt "
            "/etc/labfoundry/kms/certs/kms.labfoundry.internal.key "
            "/etc/labfoundry/kms/clients/certs/vcf-management.crt "
            "/etc/labfoundry/kms/clients/certs/vcf-management.key; "
            "do test -f \"$path\" || { echo \"missing $path\"; exit 1; }; done"
        ),
        "kms_service": "systemctl is-active labfoundry-kms.service || (systemctl status labfoundry-kms.service --no-pager; journalctl -u labfoundry-kms.service -n 80 --no-pager; exit 1)",
        "kms_tls": (
            f"timeout 10 openssl s_client -connect {site_ip}:5696 "
            "-cert /etc/labfoundry/kms/clients/certs/vcf-management.crt "
            "-key /etc/labfoundry/kms/clients/certs/vcf-management.key "
            "-CAfile /etc/labfoundry/ca/root.crt -verify_return_error </dev/null"
        ),
        "vcf_backups": (
            "test -f /etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf && "
            "grep -F Match\\ User\\ vcf-backup /etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf && "
            "grep -F ForceCommand\\ internal-sftp /etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf && "
            "id vcf-backup && "
            "test -d /mnt/labfoundry-vcf-backups/backups && "
            "systemctl is-active sshd"
        ),
    }
    evidence: dict[str, Any] = {}
    for name, command in checks.items():
        result = ssh_command(args.appliance_ssh_host, args, command, role="appliance")
        require_success(result, f"host {name} check")
        evidence[name] = result
    return evidence


def vcf_backup_client_check(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_client_checks:
        return {"skipped": "client checks disabled"}
    if not args.client_a_host:
        return {"skipped": "client A host not provided"}
    site = ip_interface(args.site_cidr)
    site_ip = str(site.ip)
    wan = ip_interface(args.wan_cidr)
    password = args.vcf_backup_password
    command = (
        f"ip route replace {wan.network} via {site_ip} dev eth1 2>/dev/null || true; "
        f"nc -z -w 5 {site_ip} 22; "
        "if command -v sshpass >/dev/null 2>&1; then "
        f"printf 'pwd\\nls\\nquit\\n' | sshpass -p '{password}' "
        f"sftp -oBatchMode=no -oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null -P 22 vcf-backup@{site_ip}; "
        "else "
        "PASS_FILE=$(mktemp); ASKPASS=$(mktemp); "
        f"printf '%s\\n' '{password}' > \"$PASS_FILE\"; "
        "printf '#!/bin/sh\\ncat \"$PASS_FILE\"\\n' > \"$ASKPASS\"; chmod 700 \"$ASKPASS\"; "
        "export PASS_FILE ASKPASS SSH_ASKPASS=\"$ASKPASS\" SSH_ASKPASS_REQUIRE=force DISPLAY=none; "
        f"printf 'pwd\\nls\\nquit\\n' | setsid -w sftp -oBatchMode=no -oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null -P 22 vcf-backup@{site_ip}; "
        "rc=$?; rm -f \"$PASS_FILE\" \"$ASKPASS\"; exit $rc; "
        "fi"
    )
    result = ssh_command(args.client_a_host, args, command, role="client", redact_values=[password])
    require_success(result, "client A VCF Backup SFTP probe")
    return {"client_a_sftp": result, "target": f"vcf-backup@{site_ip}:22"}


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def verify_certificate_signed_by_root(certificate_pem: str, root_ca_pem: str, common_name: str) -> dict[str, Any]:
    if x509 is None:
        raise LifecycleError("cryptography is required to verify the issued lifecycle client certificate.")
    certificate = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    root = x509.load_pem_x509_certificate(root_ca_pem.encode("utf-8"))
    if certificate.issuer != root.subject:
        raise LifecycleError("Issued client certificate issuer does not match the LabFoundry root CA.")
    root.public_key().verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        certificate.signature_hash_algorithm,
    )
    subject_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    subject_cn = subject_names[0].value if subject_names else ""
    if subject_cn != common_name:
        raise LifecycleError(f"Issued client certificate CN {subject_cn!r} does not match {common_name!r}.")
    return {
        "subject": certificate.subject.rfc4514_string(),
        "issuer": certificate.issuer.rfc4514_string(),
        "serial_number": format(certificate.serial_number, "x"),
        "not_after": certificate.not_valid_after_utc.isoformat(),
    }


def ca_client_certificate_check(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_client_checks:
        return {"skipped": "client checks disabled"}
    if not args.client_a_host:
        return {"skipped": "client A host not provided"}
    common_name = f"client-a.{args.domain}"
    status, body, _headers = client.request("GET", "/certificate-authority")
    if status >= 400:
        raise LifecycleError(f"GET /certificate-authority failed with HTTP {status}")
    certificate_id = extract_ca_certificate_id(body, common_name)
    status, certificate_pem, _headers = client.request("GET", f"/certificate-authority/certificates/{certificate_id}/downloads/certificate.pem")
    if status >= 400 or "BEGIN CERTIFICATE" not in certificate_pem:
        raise LifecycleError(f"CA issued certificate download failed with HTTP {status}")
    status, root_ca_pem, _headers = client.request("GET", "/certificate-authority/downloads/root-ca.pem")
    if status >= 400 or "BEGIN CERTIFICATE" not in root_ca_pem:
        raise LifecycleError(f"CA root certificate download failed with HTTP {status}")
    crypto_summary = verify_certificate_signed_by_root(certificate_pem, root_ca_pem, common_name)
    cookie_header = session_cookie_header(client)
    ca_request = ip_interface(args.client_ca_request_cidr)
    ca_request_url = (args.client_ca_request_url or args.appliance_url).rstrip("/")
    elevate = elevation_probe()
    command = (
        f"ELEV=\"$({elevate})\"; "
        "test -n \"$ELEV\"; "
        f"$ELEV ip link set {args.client_ca_request_interface} up; "
        f"$ELEV ip addr replace {ca_request.ip}/{ca_request.network.prefixlen} dev {args.client_ca_request_interface}; "
        "http_code=$(curl -sS --connect-timeout 10 --max-time 30 -o /dev/null -w '%{http_code}' "
        f"-H {shell_single_quote('Cookie: ' + cookie_header)} "
        f"{ca_request_url}/certificate-authority/certificates/{certificate_id}/downloads/certificate.pem); "
        "echo \"$http_code\"; test \"$http_code\" = \"200\""
    )
    download_result = ssh_command(args.client_a_host, args, command, role="client", redact_values=[cookie_header])
    require_success(download_result, "client A CA certificate download")
    return {
        "common_name": common_name,
        "certificate_id": certificate_id,
        "certificate": {**certificate_summary(certificate_pem), "signature_verification": crypto_summary},
        "client_a_download": download_result,
    }


def elevation_probe() -> str:
    return "if command -v sudo >/dev/null 2>&1; then echo 'sudo -n'; elif command -v doas >/dev/null 2>&1; then echo 'doas -n'; else echo ''; fi"


def second_host_address(cidr: str) -> str:
    hosts = list(ip_interface(cidr).network.hosts())
    if len(hosts) < 2:
        raise LifecycleError(f"Network {cidr} is too small for a client probe address.")
    return str(hosts[1])


def client_checks(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_client_checks:
        return {"skipped": "client checks disabled"}
    evidence: dict[str, Any] = {}
    site = ip_interface(args.site_cidr)
    wan = ip_interface(args.wan_cidr)
    site_ip = str(site.ip)
    wan_ip = str(wan.ip)
    wan_peer_ip = second_host_address(args.wan_cidr)
    elevate = elevation_probe()
    if args.client_b_host:
        command = (
            f"ELEV=\"$({elevate})\"; test -n \"$ELEV\"; "
            f"$ELEV ip addr replace {wan_peer_ip}/{wan.network.prefixlen} dev eth1; "
            "$ELEV ip link set eth1 up; "
            f"$ELEV ip route replace {site.network} via {wan_ip} dev eth1; "
            "ip -br addr; ip route; "
            f"ping -c 2 {wan_ip}"
        )
        result = ssh_command(args.client_b_host, args, command, role="client")
        require_success(result, "client B WAN probe")
        evidence["client_b"] = result
    else:
        evidence["client_b"] = {"skipped": "client B host not provided"}
    if args.client_a_host:
        command = (
            f"ELEV=\"$({elevate})\"; "
            "$ELEV /usr/local/sbin/labfoundry-refresh-test-dhcp 2>/dev/null || /usr/local/sbin/labfoundry-refresh-test-dhcp 2>/dev/null || true; "
            f"$ELEV ip route replace {wan.network} via {site_ip} dev eth1; "
            "ip -br addr; "
            "getent hosts interop-appliance.labfoundry.internal; "
            f"nslookup interop-appliance.{args.domain} {site_ip}; "
            f"ping -c 2 {site_ip}; "
            f"ping -c 2 {wan_peer_ip}; "
            f"traceroute -n {wan_peer_ip} 2>/dev/null || true; "
            "resolvectl status 2>/dev/null || true"
        )
        result = ssh_command(args.client_a_host, args, command, role="client")
        require_success(result, "client A DNS/DHCP probe")
        evidence["client_a"] = result
    else:
        evidence["client_a"] = {"skipped": "client A host not provided"}
    return evidence


def run_step(results: list[StepResult], name: str, func, *args) -> Any:  # type: ignore[no-untyped-def]
    step = StepResult(name=name, status="running")
    results.append(step)
    try:
        evidence = func(*args)
    except Exception as exc:  # noqa: BLE001 - preserve failing evidence in result JSON
        step.finish("failed", str(exc))
        raise
    step.evidence = evidence or {}
    step.finish("passed")
    return evidence


def format_step_summary(step: dict[str, Any]) -> str:
    status = str(step.get("status", "unknown")).upper()
    name = str(step.get("name", "unknown"))
    if step.get("error"):
        return f"[{status}] {name}: {step['error']}"
    evidence = step.get("evidence") or {}
    detail = ""
    if name == "configure-ca":
        root_ca = evidence.get("root_ca") or {}
        detail = str(root_ca.get("subject") or f"{root_ca.get('pem_bytes', 0)} bytes")
    elif name == "ca-client-certificate-request":
        detail = f"{evidence.get('common_name', '')} via {evidence.get('profile', '')}"
    elif name == "ca-client-certificate-check":
        detail = f"{evidence.get('common_name', '')} certificate id {evidence.get('certificate_id', '')}"
    elif name == "configure-vcf-backups":
        detail = f"{evidence.get('sftp_username', 'vcf-backup')} on {evidence.get('listen_address', '')}:{22}"
    elif name == "configure-kms":
        detail = f"{evidence.get('hostname', 'kms')} on {evidence.get('listen_address', '')}:{evidence.get('port', 5696)}"
    elif name == "configure-firewall-wan":
        policy = evidence.get("wan_policy") or {}
        route = evidence.get("route") or {}
        detail = f"{policy.get('latency_ms', 0)}ms delay, {policy.get('jitter_ms', 0)}ms jitter on {route.get('interface_name', '')}"
    elif name in {"apply-connectivity-units", "apply-ca-unit", "apply-kms-unit", "apply-lifecycle-units"}:
        detail = ", ".join(evidence.get("selected_units", []))
    elif name == "host-state-checks":
        detail = ", ".join(sorted(evidence.keys()))
    elif name == "vcf-backup-client-check":
        detail = str(evidence.get("target", ""))
    elif name == "client-checks":
        detail = ", ".join(sorted(evidence.keys()))
    return f"[{status}] {name}{': ' + detail if detail else ''}"


def print_human_summary(result: dict[str, Any], result_path: Path) -> None:
    print("")
    print("Lifecycle summary")
    print("=================")
    print(f"Status: {str(result.get('status', 'unknown')).upper()}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    plan = result.get("plan") or {}
    print(f"Appliance: {plan.get('appliance_url', '')}")
    interfaces = plan.get("interfaces") or {}
    if interfaces:
        print(
            "Networks: "
            f"site {interfaces.get('site', {}).get('name', '')} {interfaces.get('site', {}).get('ip_cidr', '')}; "
            f"vlan {interfaces.get('vlan', {}).get('name', '')} {interfaces.get('vlan', {}).get('ip_cidr', '')}; "
            f"wan {interfaces.get('wan', {}).get('name', '')} {interfaces.get('wan', {}).get('ip_cidr', '')}"
        )
    if plan.get("apply_units"):
        print(f"Apply units: {', '.join(plan['apply_units'])}")
    print("Steps:")
    for step in result.get("steps", []):
        print(f"  {format_step_summary(step)}")
    print(f"Result JSON: {result_path}")


def main() -> int:
    args = parse_args()
    result_dir = Path(args.result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    results: list[StepResult] = []
    result: dict[str, Any] = {
        "started_at": utc_now(),
        "plan": lifecycle_plan(args),
        "steps": [],
        "status": "running",
    }
    if args.plan_only:
        result["status"] = "planned"
        result["finished_at"] = utc_now()
        (result_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result["plan"], indent=2))
        return 0

    client = HttpClient(args.appliance_url)
    try:
        run_step(results, "appliance-health", appliance_health, client, args)
        run_step(results, "configure-network", configure_network, client, args)
        run_step(results, "configure-dns-dhcp", configure_dns_dhcp, client, args)
        run_step(results, "configure-firewall-wan", configure_firewall_wan, client, args)
        run_step(results, "configure-ca", configure_ca, client, args)
        run_step(results, "configure-vcf-backups", configure_vcf_backups, client, args)
        run_step(results, "configure-kms", configure_kms, client, args)
        run_step(
            results,
            "apply-connectivity-units",
            apply_units,
            client,
            ["local_users", "network", "firewall", "wan", "dnsmasq", "vcf_backups"],
            args,
        )
        run_step(results, "ca-client-certificate-request", ca_client_certificate_request, client, args)
        run_step(results, "apply-ca-unit", apply_units, client, ["ca"], args)
        run_step(results, "apply-kms-unit", apply_units, client, ["dnsmasq", "firewall", "kms"], args)
        run_step(results, "host-state-checks", host_state_checks, args)
        run_step(results, "client-checks", client_checks, args)
        run_step(results, "ca-client-certificate-check", ca_client_certificate_check, client, args)
        run_step(results, "vcf-backup-client-check", vcf_backup_client_check, args)
        run_step(results, "configure-management-https", configure_management_https, client, args)
        run_step(results, "apply-appliance-settings-unit", apply_units, client, ["appliance_settings"], args)
        run_step(results, "management-https-check", management_https_check, client, args)
        result["status"] = "passed"
        return_code = 0
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error"] = str(exc)
        return_code = 1
    finally:
        result["finished_at"] = utc_now()
        result["steps"] = [step.__dict__ for step in results]
        result_path = result_dir / "result.json"
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print_human_summary(result, result_path)
    return return_code


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import base64
import hashlib
import io
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import IPv6Address, ip_address
from typing import Any, Callable

import httpx
import paramiko
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from labfoundry.app.models import CaSettings


VCF_RESTART_COMMAND = "/opt/vmware/vcf/operationsmanager/scripts/cli/sddcmanager_restart_services.sh"
VCF_SUPPORTED_ROLES = {"VcfInstaller", "SddcManager"}


class VcfTrustError(RuntimeError):
    pass


class VcfTrustPartialError(VcfTrustError):
    pass


@dataclass(frozen=True)
class VcfTrustCredentials:
    api_username: str
    api_password: str
    ssh_password: str = ""
    ssh_private_key: str = ""
    ssh_private_key_passphrase: str = ""
    root_password: str = ""


@dataclass(frozen=True)
class RootCaInfo:
    pem: str
    subject: str
    expires_at: str
    fingerprint: str


def colon_fingerprint(raw: bytes) -> str:
    return ":".join(f"{value:02X}" for value in raw)


def root_ca_info(settings: CaSettings) -> RootCaInfo:
    if not settings.enabled:
        raise VcfTrustError("The LabFoundry certificate authority must be enabled.")
    pem = (settings.root_certificate_pem or "").strip()
    if not pem or pem.count("-----BEGIN CERTIFICATE-----") != 1 or "PRIVATE KEY" in pem:
        raise VcfTrustError("The active LabFoundry root CA must contain exactly one public PEM certificate.")
    try:
        certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    except (ValueError, x509.ExtensionNotFound) as exc:
        raise VcfTrustError("The active LabFoundry root CA is not a valid CA certificate.") from exc
    if not constraints.ca or certificate.issuer != certificate.subject:
        raise VcfTrustError("The active LabFoundry certificate is not a self-signed root CA.")
    canonical_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("utf-8").strip()
    if pem != canonical_pem:
        raise VcfTrustError("The active LabFoundry root CA contains data other than its public PEM certificate.")
    now = datetime.now(timezone.utc)
    if certificate.not_valid_after_utc <= now:
        raise VcfTrustError("The active LabFoundry root CA has expired.")
    return RootCaInfo(
        pem=pem + "\n",
        subject=certificate.subject.rfc4514_string(),
        expires_at=certificate.not_valid_after_utc.isoformat(),
        fingerprint=colon_fingerprint(certificate.fingerprint(hashes.SHA256())),
    )


def pem_fingerprint(pem: str) -> str:
    try:
        certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    except ValueError as exc:
        raise VcfTrustError("VCF returned an invalid trusted certificate.") from exc
    return colon_fingerprint(certificate.fingerprint(hashes.SHA256()))


def discover_ssh_host_key(address: str, port: int, timeout: float = 10.0) -> str:
    sock = socket.create_connection((address, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
        return f"SHA256:{digest}"
    finally:
        transport.close()
        sock.close()


class VcfApiClient:
    def __init__(self, address: str, username: str, password: str, *, timeout: float = 30.0):
        try:
            parsed_address = ip_address(address)
        except ValueError:
            parsed_address = None
        api_host = f"[{address}]" if isinstance(parsed_address, IPv6Address) else address
        self.base_url = f"https://{api_host}"
        self.username = username
        self.password = password
        # VCF appliances commonly begin with a private/self-signed HTTPS certificate.
        # The mutating workflow is protected by the separately pinned SSH host key.
        self.client = httpx.Client(base_url=self.base_url, verify=False, timeout=timeout)
        self.token = ""

    def __enter__(self) -> VcfApiClient:
        response = self.client.post("/v1/tokens", json={"username": self.username, "password": self.password})
        self._raise(response, "VCF API authentication failed")
        self.token = str(response.json().get("accessToken") or "")
        if not self.token:
            raise VcfTrustError("VCF API authentication returned no access token.")
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        return self

    def __exit__(self, *_args: object) -> None:
        self.client.close()

    @staticmethod
    def _raise(response: httpx.Response, message: str) -> None:
        if response.is_success:
            return
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("message") or payload.get("errorCode") or "")
        except (ValueError, AttributeError):
            pass
        suffix = f" ({response.status_code}{': ' + detail if detail else ''})"
        raise VcfTrustError(message + suffix)

    def appliance_info(self) -> dict[str, str]:
        response = self.client.get("/v1/system/appliance-info")
        self._raise(response, "Could not read VCF appliance information")
        payload = response.json()
        role = str(payload.get("role") or "")
        version = str(payload.get("version") or "")
        if role not in VCF_SUPPORTED_ROLES:
            raise VcfTrustError(f"Unsupported VCF appliance role: {role or 'unknown'}.")
        if not version.startswith("9."):
            raise VcfTrustError(f"Unsupported VCF version: {version or 'unknown'}; only VCF 9.x is supported.")
        return {"role": role, "version": version}

    def trusted_certificates(self) -> list[dict[str, Any]]:
        response = self.client.get("/v1/sddc-manager/trusted-certificates")
        self._raise(response, "Could not read the VCF trusted-certificate store")
        payload = response.json()
        return list(payload.get("elements") or [])

    def add_trusted_certificate(self, pem: str) -> None:
        response = self.client.post(
            "/v1/sddc-manager/trusted-certificates",
            json={"certificate": pem, "certificateUsageType": "TRUSTED_FOR_OUTBOUND"},
        )
        self._raise(response, "VCF rejected the LabFoundry root CA")


def find_trusted_certificate(certificates: list[dict[str, Any]], fingerprint: str) -> dict[str, Any] | None:
    for item in certificates:
        pem = str(item.get("certificate") or "")
        if not pem:
            continue
        try:
            if pem_fingerprint(pem) == fingerprint:
                return item
        except VcfTrustError:
            continue
    return None


def load_private_key(pem: str, passphrase: str = "") -> paramiko.PKey:
    last_error: Exception | None = None
    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key(io.StringIO(pem), password=passphrase or None)
        except (paramiko.SSHException, ValueError) as exc:
            last_error = exc
    try:
        password = passphrase.encode("utf-8") if passphrase else None
        private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=password)
        if isinstance(private_key, rsa.RSAPrivateKey):
            normalized = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ).decode("utf-8")
            return paramiko.RSAKey.from_private_key(io.StringIO(normalized))
        if isinstance(private_key, ec.EllipticCurvePrivateKey):
            normalized = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ).decode("utf-8")
            return paramiko.ECDSAKey.from_private_key(io.StringIO(normalized))
        if isinstance(private_key, ed25519.Ed25519PrivateKey):
            normalized = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            ).decode("utf-8")
            return paramiko.Ed25519Key.from_private_key(io.StringIO(normalized))
    except (ValueError, TypeError, paramiko.SSHException) as exc:
        last_error = exc
    raise VcfTrustError("The uploaded SSH private key is invalid or its passphrase is incorrect.") from last_error


def _read_channel(channel: paramiko.Channel, marker: str, timeout: float, *, occurrences: int = 1) -> str:
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    while time.monotonic() < deadline:
        if channel.recv_ready():
            text = channel.recv(4096).decode("utf-8", errors="replace")
            chunks.append(text)
            if "".join(chunks).count(marker) >= occurrences:
                return "".join(chunks)
        elif channel.closed:
            break
        time.sleep(0.1)
    raise VcfTrustError("Timed out waiting for the privileged VCF command.")


def restart_vcf_services(
    address: str,
    port: int,
    expected_host_key: str,
    credentials: VcfTrustCredentials,
    *,
    timeout: float = 30.0,
) -> None:
    client = paramiko.SSHClient()
    discovered: dict[str, str] = {}

    class _PinnedPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, ssh_client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
            digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
            discovered["fingerprint"] = f"SHA256:{digest}"
            if discovered["fingerprint"] != expected_host_key:
                raise paramiko.SSHException("SSH host key does not match the pinned fingerprint.")
            ssh_client.get_host_keys().add(hostname, key.get_name(), key)

    client.set_missing_host_key_policy(_PinnedPolicy())
    connect: dict[str, Any] = {
        "hostname": address,
        "port": port,
        "username": "vcf",
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if credentials.ssh_private_key:
        connect["pkey"] = load_private_key(credentials.ssh_private_key, credentials.ssh_private_key_passphrase)
    else:
        connect["password"] = credentials.ssh_password
    try:
        client.connect(**connect)
        channel = client.invoke_shell(width=160, height=40)
        channel.send("su -\n")
        _read_channel(channel, "Password:", timeout)
        channel.send(credentials.root_password + "\n")
        command = f"{VCF_RESTART_COMMAND}; rc=$?; printf '\\n__LABFOUNDRY_RC__%s\\n' \"$rc\""
        channel.send(command + "\n")
        output = _read_channel(channel, "__LABFOUNDRY_RC__", max(timeout, 120.0), occurrences=2)
        tail = output.rsplit("__LABFOUNDRY_RC__", 1)[1].strip().splitlines()[0]
        if not tail.startswith("0"):
            raise VcfTrustPartialError("The root CA was installed, but the VCF service restart command failed.")
    except VcfTrustPartialError:
        raise
    except (paramiko.SSHException, OSError) as exc:
        raise VcfTrustPartialError("The root CA was installed, but SSH service restart failed.") from exc
    finally:
        client.close()


def execute_vcf_trust(
    *,
    address: str,
    ssh_port: int,
    expected_host_key: str,
    credentials: VcfTrustCredentials,
    ca: RootCaInfo,
    progress: Callable[[int, str], None] | None = None,
    recovery_attempts: int = 12,
    recovery_delay: float = 10.0,
) -> dict[str, Any]:
    update = progress or (lambda _percent, _state: None)
    update(10, "authenticating")
    with VcfApiClient(address, credentials.api_username, credentials.api_password) as api:
        appliance = api.appliance_info()
        update(25, "checking-trust")
        if find_trusted_certificate(api.trusted_certificates(), ca.fingerprint):
            return {**appliance, "outcome": "no-op", "restart": "not-required", "verified": True}
        update(40, "importing")
        api.add_trusted_certificate(ca.pem)
        if not find_trusted_certificate(api.trusted_certificates(), ca.fingerprint):
            raise VcfTrustError("VCF did not return the imported LabFoundry root CA during verification.")

    if appliance["role"] == "VcfInstaller":
        return {**appliance, "outcome": "installed", "restart": "not-applicable", "verified": True}

    update(60, "restarting")
    restart_vcf_services(address, ssh_port, expected_host_key, credentials)
    update(75, "verifying")
    last_error: Exception | None = None
    for _attempt in range(recovery_attempts):
        try:
            with VcfApiClient(address, credentials.api_username, credentials.api_password) as api:
                recovered = api.appliance_info()
                if find_trusted_certificate(api.trusted_certificates(), ca.fingerprint):
                    return {**recovered, "outcome": "installed", "restart": "completed", "verified": True}
                last_error = VcfTrustError("The imported root CA was not present after restart.")
        except (VcfTrustError, httpx.HTTPError) as exc:
            last_error = exc
        time.sleep(recovery_delay)
    raise VcfTrustPartialError("The root CA was installed, but VCF did not recover and verify before the timeout.") from last_error


def sanitized_result(*, address: str, ssh_port: int, ca: RootCaInfo, state: str, **values: Any) -> str:
    return json.dumps(
        {
            "target": address,
            "ssh_port": ssh_port,
            "ca_subject": ca.subject,
            "ca_expires_at": ca.expires_at,
            "ca_fingerprint": ca.fingerprint,
            "snapshot_acknowledged": True,
            "state": state,
            **values,
        },
        indent=2,
    )

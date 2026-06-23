from __future__ import annotations

from ipaddress import ip_address

from labfoundry.app.models import KmsClient, KmsKey, KmsSettings


KMS_BACKENDS = ["pykmip"]
KMS_CLIENT_ROLES = ["admin", "service", "readonly"]
KMS_KEY_ALGORITHMS = ["AES", "RSA", "ECDSA"]
KMS_KEY_STATES = ["pre-active", "active", "deactivated", "compromised", "destroyed"]
KMS_DEFAULT_OPERATIONS = ["locate", "get", "register", "create"]
KMS_DEFAULT_DATABASE_PATH = "/var/lib/labfoundry/kms/pykmip.db"
KMS_DEFAULT_CONFIG_PATH = "/etc/labfoundry/kms/pykmip.conf"


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    items: list[str] = []
    for item in value.replace("\n", ",").split(","):
        normalized = item.strip()
        if normalized and normalized not in items:
            items.append(normalized)
    return items


def join_csv(values: list[str]) -> str:
    return ",".join(split_csv(",".join(values)))


def kms_client_to_dict(client: KmsClient) -> dict:
    return {
        "id": client.id,
        "name": client.name,
        "certificate_subject": client.certificate_subject,
        "role": client.role,
        "allowed_operations": client.allowed_operations,
        "enabled": client.enabled,
        "description": client.description or "",
    }


def kms_key_to_dict(key: KmsKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "algorithm": key.algorithm,
        "length": key.length,
        "usage": key.usage,
        "state": key.state,
        "owner_client_id": key.owner_client_id or "",
        "owner_client_name": key.owner_client.name if key.owner_client else "Unassigned",
        "exportable": key.exportable,
        "enabled": key.enabled,
        "description": key.description or "",
    }


def render_kms_config(
    *,
    settings: KmsSettings,
    clients: list[KmsClient],
    keys: list[KmsKey],
) -> str:
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        "# Backend: PyKMIP lab KMIP server desired state.",
        "[server]",
        f"backend={settings.backend}",
        f"enabled={'yes' if settings.enabled else 'no'}",
        f"hostname={settings.hostname}",
        f"host={settings.listen_address}",
        f"port={settings.port}",
        f"interface={settings.listen_interface}",
        f"database_path={settings.database_path}",
        "",
        "[tls]",
        f"server_certificate={settings.server_certificate}",
        f"ca_certificate={settings.ca_certificate_path}",
        f"require_client_cert={'yes' if settings.require_client_cert else 'no'}",
        "",
        "[policy]",
        f"allow_register={'yes' if settings.allow_register else 'no'}",
        f"allow_destroy={'yes' if settings.allow_destroy else 'no'}",
        "",
        "[clients]",
    ]
    for client in clients:
        if not client.enabled:
            continue
        lines.extend(
            [
                f"client={client.name}",
                f"  subject={client.certificate_subject}",
                f"  role={client.role}",
                f"  operations={join_csv(split_csv(client.allowed_operations))}",
            ]
        )
    lines.extend(["", "[keys]"])
    for key in keys:
        if not key.enabled:
            continue
        owner = key.owner_client.name if key.owner_client else "unassigned"
        lines.extend(
            [
                f"key={key.name}",
                f"  algorithm={key.algorithm}",
                f"  length={key.length}",
                f"  usage={join_csv(split_csv(key.usage))}",
                f"  state={key.state}",
                f"  owner={owner}",
                f"  exportable={'yes' if key.exportable else 'no'}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def validate_kms_state(
    *,
    settings: KmsSettings,
    clients: list[KmsClient],
    keys: list[KmsKey],
) -> list[str]:
    errors: list[str] = []
    if settings.backend not in KMS_BACKENDS:
        errors.append("KMS backend must be pykmip for the MVP scaffold.")
    if settings.enabled:
        if not settings.listen_interface.strip():
            errors.append("KMS listen interface is required.")
        try:
            ip_address(settings.listen_address)
        except ValueError:
            errors.append("KMS listen address must be a valid IPv4 or IPv6 address.")
    if settings.port < 1 or settings.port > 65535:
        errors.append("KMS port must be between 1 and 65535.")
    if not settings.hostname.strip():
        errors.append("KMS hostname is required.")
    if settings.require_client_cert and not settings.ca_certificate_path.strip():
        errors.append("KMS client certificate validation requires a CA certificate path.")
    if not settings.config_path.strip():
        errors.append("KMS config path is required.")
    if not settings.database_path.strip():
        errors.append("KMS database path is required.")

    client_ids = {client.id for client in clients}
    for client in clients:
        if not client.name.strip():
            errors.append("KMS client name is required.")
        if client.role not in KMS_CLIENT_ROLES:
            errors.append(f"KMS client {client.name or client.id} has an unsupported role.")
        if not client.certificate_subject.strip():
            errors.append(f"KMS client {client.name or client.id} requires a certificate subject.")
        if not split_csv(client.allowed_operations):
            errors.append(f"KMS client {client.name or client.id} needs at least one allowed operation.")

    for key in keys:
        label = key.name or str(key.id)
        if not key.name.strip():
            errors.append("KMS key name is required.")
        if key.algorithm not in KMS_KEY_ALGORITHMS:
            errors.append(f"KMS key {label} has an unsupported algorithm.")
        if key.algorithm == "AES" and key.length not in {128, 192, 256}:
            errors.append(f"KMS key {label} AES length must be 128, 192, or 256 bits.")
        if key.algorithm == "RSA" and key.length < 2048:
            errors.append(f"KMS key {label} RSA length must be at least 2048 bits.")
        if key.algorithm == "ECDSA" and key.length not in {256, 384, 521}:
            errors.append(f"KMS key {label} ECDSA length must be 256, 384, or 521.")
        if key.state not in KMS_KEY_STATES:
            errors.append(f"KMS key {label} has an unsupported lifecycle state.")
        if key.owner_client_id is not None and key.owner_client_id not in client_ids:
            errors.append(f"KMS key {label} references a missing client.")
    return errors

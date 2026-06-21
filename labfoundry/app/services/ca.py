from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
import subprocess

from labfoundry.app.models import CaCertificate, CaProfile, CaSettings


def split_multiline(value: str | None) -> list[str]:
    if not value:
        return []
    items: list[str] = []
    for line in value.replace(",", "\n").splitlines():
        item = line.strip().strip(",")
        if item and item not in items:
            items.append(item)
    return items


def join_multiline(values: list[str]) -> str:
    return "\n".join(split_multiline("\n".join(values)))


def ensure_development_root_ca(settings: CaSettings) -> tuple[bytes, bytes]:
    """Create local development CA material for download until the appliance helper owns real files."""
    cache_dir = Path("data") / "ca"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cache_dir / "labfoundry-root-ca.pem"
    key_path = cache_dir / "labfoundry-root-ca-key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path.read_bytes(), key_path.read_bytes()

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return ensure_development_root_ca_with_openssl(settings, cert_path, key_path)

    if settings.key_algorithm == "ECDSA":
        curve = ec.SECP521R1() if settings.key_size >= 521 else ec.SECP384R1() if settings.key_size >= 384 else ec.SECP256R1()
        private_key = ec.generate_private_key(curve)
    else:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=max(settings.key_size, 2048))

    subject_parts = [
        x509.NameAttribute(NameOID.COMMON_NAME, settings.root_common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, settings.organization or "LabFoundry"),
    ]
    if settings.organizational_unit:
        subject_parts.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, settings.organizational_unit))
    if settings.country:
        subject_parts.append(x509.NameAttribute(NameOID.COUNTRY_NAME, settings.country[:2].upper()))
    if settings.state:
        subject_parts.append(x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, settings.state))
    if settings.locality:
        subject_parts.append(x509.NameAttribute(NameOID.LOCALITY_NAME, settings.locality))
    subject = x509.Name(subject_parts)

    from datetime import timedelta

    from labfoundry.app.models import utcnow

    now = utcnow()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=max(settings.root_valid_days, 365)))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    return cert_bytes, key_bytes


def ensure_development_root_ca_with_openssl(settings: CaSettings, cert_path: Path, key_path: Path) -> tuple[bytes, bytes]:
    subject = openssl_subject(settings)
    command = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        f"rsa:{max(settings.key_size, 2048)}",
        "-sha256",
        "-nodes",
        "-days",
        str(max(settings.root_valid_days, 365)),
        "-subj",
        subject,
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("OpenSSL is required for CA downloads when cryptography is unavailable") from exc
    return cert_path.read_bytes(), key_path.read_bytes()


def openssl_subject(settings: CaSettings) -> str:
    parts = [
        ("CN", settings.root_common_name or "LabFoundry Internal Root CA"),
        ("O", settings.organization or "LabFoundry"),
    ]
    if settings.organizational_unit:
        parts.append(("OU", settings.organizational_unit))
    if settings.country:
        parts.append(("C", settings.country[:2].upper()))
    if settings.state:
        parts.append(("ST", settings.state))
    if settings.locality:
        parts.append(("L", settings.locality))
    return "".join(f"/{key}={escape_openssl_subject_value(value)}" for key, value in parts)


def escape_openssl_subject_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("/", "\\/")


def ca_profile_to_dict(profile: CaProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "certificate_type": profile.certificate_type,
        "validity_days": profile.validity_days,
        "key_algorithm": profile.key_algorithm,
        "key_size": profile.key_size,
        "key_usage": profile.key_usage,
        "extended_key_usage": profile.extended_key_usage,
        "san_required": profile.san_required,
        "enabled": profile.enabled,
        "description": profile.description or "",
    }


def ca_certificate_to_dict(certificate: CaCertificate) -> dict:
    return {
        "id": certificate.id,
        "common_name": certificate.common_name,
        "profile_id": certificate.profile_id or "",
        "profile_name": certificate.profile.name if certificate.profile else "Unassigned",
        "subject_alt_names": certificate.subject_alt_names,
        "ip_addresses": certificate.ip_addresses,
        "status": certificate.status,
        "serial_number": certificate.serial_number or "",
        "enabled": certificate.enabled,
        "description": certificate.description or "",
    }


def render_ca_config(
    *,
    settings: CaSettings,
    profiles: list[CaProfile],
    certificates: list[CaCertificate],
) -> str:
    lines = [
        "# Managed by LabFoundry. Local changes may be overwritten.",
        f"ca_home={settings.storage_path}",
        f"root_common_name={settings.root_common_name}",
        f"organization={settings.organization}",
        f"organizational_unit={settings.organizational_unit}",
        f"country={settings.country}",
        f"state={settings.state}",
        f"locality={settings.locality}",
        f"root_key={settings.key_algorithm}:{settings.key_size}",
        f"digest={settings.digest_algorithm}",
        f"root_valid_days={settings.root_valid_days}",
        f"intermediate_valid_days={settings.intermediate_valid_days}",
        f"publish_crl={'yes' if settings.publish_crl else 'no'}",
        f"ocsp={'yes' if settings.ocsp_enabled else 'no'}",
        "",
        "[profiles]",
    ]
    for profile in profiles:
        if not profile.enabled:
            continue
        lines.extend(
            [
                f"profile={profile.name}",
                f"  type={profile.certificate_type}",
                f"  validity_days={profile.validity_days}",
                f"  key={profile.key_algorithm}:{profile.key_size}",
                f"  key_usage={profile.key_usage}",
                f"  extended_key_usage={profile.extended_key_usage}",
                f"  san_required={'yes' if profile.san_required else 'no'}",
            ]
        )
    lines.extend(["", "[certificate_requests]"])
    for certificate in certificates:
        if not certificate.enabled:
            continue
        profile_name = certificate.profile.name if certificate.profile else "unassigned"
        lines.extend(
            [
                f"certificate={certificate.common_name}",
                f"  profile={profile_name}",
                f"  status={certificate.status}",
                f"  dns_names={','.join(split_multiline(certificate.subject_alt_names))}",
                f"  ip_addresses={','.join(split_multiline(certificate.ip_addresses))}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def validate_ca_state(
    *,
    settings: CaSettings,
    profiles: list[CaProfile],
    certificates: list[CaCertificate],
) -> list[str]:
    errors: list[str] = []
    if not settings.root_common_name.strip():
        errors.append("CA root common name is required.")
    if settings.country and len(settings.country.strip()) != 2:
        errors.append("CA country must be a two-letter ISO code.")
    if settings.key_algorithm not in {"RSA", "ECDSA"}:
        errors.append("CA key algorithm must be RSA or ECDSA.")
    if settings.key_algorithm == "RSA" and settings.key_size < 2048:
        errors.append("CA RSA key size must be at least 2048 bits.")
    if settings.root_valid_days < 365:
        errors.append("CA root validity should be at least 365 days.")

    enabled_profiles = {profile.id: profile for profile in profiles if profile.enabled}
    for profile in profiles:
        if not profile.name.strip():
            errors.append("CA profile name is required.")
        if profile.certificate_type not in {"server", "client", "user", "intermediate"}:
            errors.append(f"CA profile {profile.name or profile.id} has an unsupported type.")
        if profile.validity_days < 1:
            errors.append(f"CA profile {profile.name or profile.id} validity must be at least one day.")
        if profile.key_algorithm == "RSA" and profile.key_size < 2048:
            errors.append(f"CA profile {profile.name or profile.id} RSA key size must be at least 2048 bits.")

    for certificate in certificates:
        if not certificate.enabled:
            continue
        if not certificate.common_name.strip():
            errors.append("Certificate common name is required.")
        if certificate.profile_id and certificate.profile_id not in enabled_profiles:
            errors.append(f"Certificate {certificate.common_name or certificate.id} uses a disabled or missing CA profile.")
        profile = enabled_profiles.get(certificate.profile_id)
        dns_names = split_multiline(certificate.subject_alt_names)
        ip_addresses = split_multiline(certificate.ip_addresses)
        if profile and profile.san_required and not dns_names and not ip_addresses:
            errors.append(f"Certificate {certificate.common_name} requires at least one DNS name or IP SAN.")
        for item in ip_addresses:
            try:
                ip_address(item)
            except ValueError:
                errors.append(f"Certificate {certificate.common_name} has invalid IP SAN {item}.")
    return errors

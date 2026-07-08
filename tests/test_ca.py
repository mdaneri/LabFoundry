import pytest

from labfoundry.app.config import Settings
from labfoundry.app.models import CaCertificate, CaSettings, utcnow
from labfoundry.app.secrets import decrypt_secret, encrypt_secret
from labfoundry.app.services.ca import ensure_root_ca_material, render_ca_apply_payload


def test_encrypted_secret_round_trip_and_wrong_key_failure():
    first = Settings(secret_key="test-secret-key-with-enough-length", secrets_key="first-ca-secrets-key")
    second = Settings(secret_key="test-secret-key-with-enough-length", secrets_key="second-ca-secrets-key")

    encrypted = encrypt_secret("-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n", first)

    assert encrypted.startswith("fernet:v1:")
    assert "BEGIN PRIVATE KEY" not in encrypted
    assert decrypt_secret(encrypted, first).startswith("-----BEGIN PRIVATE KEY-----")
    with pytest.raises(ValueError):
        decrypt_secret(encrypted, second)


def test_ca_apply_payload_includes_crl_for_revoked_certificates():
    import json

    settings = CaSettings(
        enabled=True,
        publish_crl=True,
        root_common_name="LabFoundry Test Root CA",
        organization="LabFoundry",
        key_algorithm="RSA",
        key_size=2048,
        digest_algorithm="sha256",
        root_valid_days=3650,
        storage_path="/etc/labfoundry/ca",
    )
    assert ensure_root_ca_material(settings) is True
    certificate = CaCertificate(
        common_name="revoked.labfoundry.internal",
        status="revoked",
        serial_number="2a",
        revoked_at=utcnow(),
        revoked_by="admin",
        revocation_reason="rotation",
        enabled=True,
    )

    payload = json.loads(render_ca_apply_payload(settings, [certificate], include_private_keys=True))

    assert payload["root"]["crl_path"].endswith("/labfoundry-ca.crl")
    assert "BEGIN X509 CRL" in payload["root"]["crl_pem"]
    assert payload["certificates"] == []


def test_existing_root_ca_material_is_not_rotated_by_identity_edits():
    settings = CaSettings(
        enabled=True,
        root_common_name="Original LabFoundry Root",
        organization="LabFoundry",
        key_algorithm="RSA",
        key_size=2048,
        digest_algorithm="sha256",
        root_valid_days=3650,
        storage_path="/etc/labfoundry/ca",
    )
    assert ensure_root_ca_material(settings) is True
    original_certificate = settings.root_certificate_pem
    original_private_key = settings.root_private_key_encrypted
    original_fingerprint = settings.root_fingerprint

    settings.root_common_name = "Renamed LabFoundry Root"
    settings.organization = "Updated LabFoundry"

    assert ensure_root_ca_material(settings) is False
    assert settings.root_certificate_pem == original_certificate
    assert settings.root_private_key_encrypted == original_private_key
    assert settings.root_fingerprint == original_fingerprint


def test_managed_ca_specs_include_portal_https_certificate(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.ui import get_ca_settings_row, managed_ca_certificate_specs

    with SessionLocal() as db:
        settings = get_ca_settings_row(db)
        settings.enabled = True
        settings.portal_hostname = "ca.labfoundry.internal"
        settings.listen_interface = "eth2"
        settings.listen_address = "192.168.87.32"
        db.commit()

        specs = {spec.owner: spec for spec in managed_ca_certificate_specs(db)}

    ca_portal = specs["ca_portal:https"]
    assert ca_portal.common_name == "ca.labfoundry.internal"
    assert ca_portal.dns_names == ["ca.labfoundry.internal"]
    assert ca_portal.ip_addresses == ["192.168.87.32"]
    assert ca_portal.cert_path == "/etc/labfoundry/ca-portal/certs/ca.labfoundry.internal.crt"
    assert ca_portal.key_path == "/etc/labfoundry/ca-portal/certs/ca.labfoundry.internal.key"
    assert ca_portal.chain_path == "/etc/labfoundry/ca-portal/certs/ca.labfoundry.internal-chain.pem"

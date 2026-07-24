from datetime import timedelta
import json

from sqlalchemy import select, text


def _admin_headers(client) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login?username=admin&password=labfoundry-admin",
        json={"name": "oidc administration", "scopes": ["admin:all"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['raw_token']}"}


def test_oidc_public_documents_stay_hidden_while_protocol_gate_is_off(client):
    assert client.get("/identity/.well-known/openid-configuration").status_code == 404
    assert client.get("/identity/jwks").status_code == 404

    headers = _admin_headers(client)
    provider = client.get("/api/v1/oidc/provider", headers=headers)
    assert provider.status_code == 200
    payload = provider.json()
    payload["enabled"] = True
    enable = client.put("/api/v1/oidc/provider", headers=headers, json=payload)
    assert enable.status_code == 409
    assert "Authorization Code flow" in enable.text


def test_oidc_confidential_client_secret_is_argon2_and_shown_only_once(client):
    headers = _admin_headers(client)
    created = client.post(
        "/api/v1/oidc/clients",
        headers=headers,
        json={
            "name": "VCF 9.1",
            "redirect_uris": ["https://vcf.example.test/identity/callback?case=A%2Fb"],
            "post_logout_redirect_uris": [],
            "allowed_scopes": ["openid", "profile", "email", "groups"],
            "allow_loopback_redirects": False,
            "access_token_lifetime_seconds": 300,
            "id_token_lifetime_seconds": 300,
            "authorization_code_lifetime_seconds": 60,
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    plaintext = created.json()["client_secret"]
    assert plaintext
    listed = client.get("/api/v1/oidc/clients", headers=headers)
    assert listed.status_code == 200
    assert plaintext not in listed.text
    assert listed.json()[0]["redirect_uris"] == [
        "https://vcf.example.test/identity/callback?case=A%2Fb"
    ]

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import OidcClient
    from labfoundry.app.services.oidc import verify_client_secret

    with SessionLocal() as db:
        row = db.execute(select(OidcClient)).scalar_one()
        assert row.client_secret_hash.startswith("$argon2")
        assert verify_client_secret(row.client_secret_hash, plaintext)
        old_hash = row.client_secret_hash

    rotated = client.post(
        f"/api/v1/oidc/clients/{listed.json()[0]['id']}/secret/rotate",
        headers=headers,
    )
    assert rotated.status_code == 200
    replacement = rotated.json()["client_secret"]
    assert replacement != plaintext
    with SessionLocal() as db:
        row = db.execute(select(OidcClient)).scalar_one()
        assert row.client_secret_hash != old_hash
        assert not verify_client_secret(row.client_secret_hash, plaintext)
        assert verify_client_secret(row.client_secret_hash, replacement)


def test_oidc_redirect_validation_rejects_wildcards_fragments_and_nonliteral_loopback():
    from labfoundry.app.services.oidc import OidcConfigurationError, validate_redirect_uri

    invalid = [
        ("https://vcf.example.test/*", False),
        ("https://vcf.example.test/callback#fragment", False),
        ("http://localhost:8080/callback", True),
        ("http://127.0.0.1/callback", True),
    ]
    for uri, allow_loopback in invalid:
        try:
            validate_redirect_uri(uri, allow_loopback=allow_loopback)
        except OidcConfigurationError:
            pass
        else:
            raise AssertionError(f"{uri} should be rejected")
    assert (
        validate_redirect_uri("http://127.0.0.1:49152/callback", allow_loopback=True)
        == "http://127.0.0.1:49152/callback"
    )


def test_oidc_rsa_key_is_encrypted_and_rotation_keeps_public_overlap(client, monkeypatch):
    from labfoundry.app import services
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ApplianceSettings, OidcProviderSettings, OidcSigningKey
    from labfoundry.app.secrets import decrypt_secret
    from labfoundry.app.services import oidc

    with SessionLocal() as db:
        appliance = db.execute(select(ApplianceSettings)).scalar_one()
        appliance.fqdn = "labfoundry.example.test"
        appliance.management_https_enabled = True
        provider = oidc.ensure_provider_settings(db)
        provider.issuer_url = "https://labfoundry.example.test/identity"
        provider.clock_skew_seconds = 120
        provider.signing_key_overlap_seconds = 300
        first, _ = oidc.generate_signing_key(db, rotate=False)
        private_pem = decrypt_secret(first.private_key_encrypted)
        assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
        assert private_pem not in first.private_key_encrypted
        second, previous = oidc.generate_signing_key(db, rotate=True)
        assert second.kid != first.kid
        assert previous is first
        assert previous.publish_until >= previous.retired_at + timedelta(seconds=420)
        provider.enabled = True
        db.commit()

    monkeypatch.setattr(oidc, "OIDC_AUTHORIZATION_FLOW_AVAILABLE", True)
    with SessionLocal() as db:
        document = oidc.discovery_document(db)
        jwks = oidc.jwks_document(db)
        assert document["issuer"] == "https://labfoundry.example.test/identity"
        assert {key["kid"] for key in jwks["keys"]} == {
            row.kid for row in db.execute(select(OidcSigningKey)).scalars()
        }
        assert all("d" not in key for key in jwks["keys"])


def test_oidc_subject_is_stable_across_metadata_changes_and_new_after_recreation(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import OidcSubject, User
    from labfoundry.app.services.identity_credentials import VerifiedIdentity, ensure_oidc_subject

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
        identity = VerifiedIdentity("local", user.id, user.username, "Admin", "", None, "Local")
        first = ensure_oidc_subject(db, identity)
        first_uuid = first.subject_uuid
        user.external_display_name = "Renamed Administrator"
        user.external_email = "renamed@example.test"
        db.flush()
        assert ensure_oidc_subject(db, identity).subject_uuid == first_uuid
        db.delete(user)
        db.commit()
        assert db.execute(select(OidcSubject)).scalar_one_or_none() is None
        db.expunge_all()
        recreated = User(username="admin", auth_provider="local", enabled=True)
        db.add(recreated)
        db.flush()
        replacement = ensure_oidc_subject(
            db,
            VerifiedIdentity("local", recreated.id, recreated.username, "Admin", "", None, "Local"),
        )
        assert replacement.subject_uuid != first_uuid


def test_sqlite_foreign_keys_are_enabled(client):
    from labfoundry.app.database import SessionLocal

    with SessionLocal() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_managed_ldap_credential_service_checks_persisted_scope_before_helper(client, monkeypatch):
    from labfoundry.app.adapters.system import AdapterResult
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import LdapOrganization, LdapUser
    from labfoundry.app.services import identity_credentials

    calls: list[tuple[str, str]] = []

    class AuthenticationAdapter:
        def authenticate_ldap_user(self, user_dn: str, password: str) -> AdapterResult:
            calls.append((user_dn, password))
            return AdapterResult(
                command=["labfoundry-helper", "ldap", "authenticate", user_dn],
                dry_run=False,
                returncode=0 if password == "Directory-Password!" else 1,
            )

    monkeypatch.setattr(identity_credentials, "SystemAdapter", AuthenticationAdapter)
    with SessionLocal() as db:
        organization = LdapOrganization(
            name="Research",
            slug="research",
            suffix_dn="dc=research,dc=example,dc=test",
            enabled=True,
        )
        db.add(organization)
        db.flush()
        user = LdapUser(
            organization_id=organization.id,
            uid="duplicate",
            display_name="Directory User",
            enabled=True,
        )
        db.add(user)
        db.commit()
        verified = identity_credentials.verify_credentials(
            db,
            source="managed_ldap",
            organization_id=organization.id,
            username="duplicate",
            password="Directory-Password!",
        )
        assert verified is not None
        assert verified.organization_id == organization.id
        assert calls == [
            (
                "uid=duplicate,ou=users,dc=research,dc=example,dc=test",
                "Directory-Password!",
            )
        ]
        user.enabled = False
        db.commit()
        assert (
            identity_credentials.verify_credentials(
                db,
                source="managed_ldap",
                organization_id=organization.id,
                username="duplicate",
                password="Directory-Password!",
            )
            is None
        )
        assert len(calls) == 1


def test_oidc_backup_restore_preserves_subject_client_and_encrypted_key(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import OidcClient, OidcSigningKey, OidcSubject, User
    from labfoundry.app.services.identity_credentials import VerifiedIdentity, ensure_oidc_subject
    from labfoundry.app.services.oidc import create_client, generate_signing_key
    from labfoundry.app.services.settings_archive import (
        export_settings_archive,
        factory_reset_desired_state,
        restore_settings_archive,
    )

    with SessionLocal() as db:
        admin = db.execute(select(User).where(User.username == "admin")).scalar_one()
        subject = ensure_oidc_subject(
            db,
            VerifiedIdentity("local", admin.id, admin.username, "Admin", "", None, "Local"),
        )
        subject_uuid = subject.subject_uuid
        client_row, _secret = create_client(
            db,
            name="Backup client",
            organization_id=None,
            redirect_uris=["https://backup.example.test/callback"],
            post_logout_redirect_uris=[],
            allowed_scopes=["openid", "profile"],
            allow_loopback_redirects=False,
            access_token_lifetime_seconds=300,
            id_token_lifetime_seconds=300,
            authorization_code_lifetime_seconds=60,
            enabled=True,
        )
        key, _ = generate_signing_key(db, rotate=False)
        encrypted_private_key = key.private_key_encrypted
        db.commit()
        archive = export_settings_archive(db, actor="admin")
        serialized = json.dumps(archive)
        assert encrypted_private_key in serialized
        assert "BEGIN PRIVATE KEY" not in serialized
        assert _secret not in serialized
        factory_reset_desired_state(db)
        assert db.execute(select(OidcClient)).scalar_one_or_none() is None
        restore_settings_archive(db, archive)
        assert db.execute(select(OidcSubject)).scalar_one().subject_uuid == subject_uuid
        assert db.execute(select(OidcClient)).scalar_one().client_id == client_row.client_id
        assert (
            db.execute(select(OidcSigningKey)).scalar_one().private_key_encrypted
            == encrypted_private_key
        )


def test_authentication_page_exposes_preparatory_oidc_ui(client):
    login = client.get("/login")
    csrf = login.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    signed_in = client.post(
        "/login",
        data={"username": "admin", "password": "labfoundry-admin", "csrf": csrf},
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    page = client.get("/authentication")
    assert page.status_code == 200
    assert "OpenID Connect Provider" in page.text
    assert "enablement blocked" in page.text
    assert "VCF 9.1 Identity Broker" in page.text
    assert "Paste the exact VCF Identity Broker redirect URI" in page.text
    assert 'data-autosave-status-id="oidc-provider-autosave-status"' in page.text
    assert page.text.count('class="help-icon"') >= 10
    assert "Rotate OIDC signing key?" in page.text or "Generate first signing key" in page.text

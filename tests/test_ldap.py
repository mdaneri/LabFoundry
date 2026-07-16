import io
import json
import tarfile

from labfoundry.app.models import LdapGroup, LdapGroupMembership, LdapOrganization, LdapSettings, LdapUser
from labfoundry.app.services.ldap import (
    decrypt_recovery_payload,
    encrypt_recovery_payload,
    ldap_apply_payload,
    validate_group_cycles,
    validate_ldap_password,
    validate_ldap_recovery_payload,
    vcf_ldap_settings,
)


def api_token(client, scopes: list[str]) -> str:
    response = client.post(
        "/api/v1/auth/login?username=admin&password=labfoundry-admin",
        json={"name": "LDAP tests", "scopes": scopes},
    )
    assert response.status_code == 200, response.text
    return response.json()["raw_token"]


def test_ldap_api_manages_isolated_organizations_users_groups_and_vcf_mapping(client):
    token = api_token(client, ["read:ldap", "write:ldap"])
    headers = {"Authorization": f"Bearer {token}"}

    settings = client.get("/api/v1/ldap/settings", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["port"] == 636

    org_a = client.post("/api/v1/ldap/organizations", headers=headers, json={"name": "Org A"})
    org_b = client.post("/api/v1/ldap/organizations", headers=headers, json={"name": "Org B"})
    assert org_a.status_code == 201, org_a.text
    assert org_b.status_code == 201, org_b.text
    assert org_a.json()["suffix_dn"] != org_b.json()["suffix_dn"]
    assert org_a.json()["raw_bind_password"]
    assert org_a.json()["raw_bind_password"] not in json.dumps(
        client.get("/api/v1/ldap/organizations", headers=headers).json()
    )
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal

    with SessionLocal() as db:
        stored = db.execute(select(LdapOrganization).where(LdapOrganization.id == org_a.json()["id"])).scalar_one()
        assert stored.bind_password_encrypted
        assert org_a.json()["raw_bind_password"] not in stored.bind_password_encrypted

    user_payload = {
        "uid": "operator",
        "given_name": "VCF",
        "surname": "Operator",
        "display_name": "VCF Operator",
        "email": "operator@example.invalid",
        "enabled": True,
        "password": "VeryStrong1!Directory",
    }
    user_a = client.post(f"/api/v1/ldap/organizations/{org_a.json()['id']}/users", headers=headers, json=user_payload)
    user_b = client.post(f"/api/v1/ldap/organizations/{org_b.json()['id']}/users", headers=headers, json=user_payload)
    assert user_a.status_code == 201, user_a.text
    assert user_b.status_code == 201, user_b.text
    assert user_a.json()["dn"] != user_b.json()["dn"]
    assert user_a.json()["password_status"] == "pending_apply"

    group = client.post(
        f"/api/v1/ldap/organizations/{org_a.json()['id']}/groups",
        headers=headers,
        json={
            "name": "Organization Administrators",
            "description": "VCF organization role import candidate",
            "enabled": True,
            "members": [{"type": "user", "id": user_a.json()["id"]}],
        },
    )
    assert group.status_code == 201, group.text
    assert group.json()["members"][0]["dn"] == user_a.json()["dn"]

    bundle = client.get(f"/api/v1/ldap/organizations/{org_a.json()['id']}/vcf-bundle", headers=headers)
    assert bundle.status_code == 200
    user_attributes = bundle.json()["vcfAutomation91"]["definedSettings"]["userAttributes"]
    assert user_attributes["serviceAccount"] == "employeeType"
    assert "password" not in bundle.json()["vcfAutomation91"]["definedSettings"]

    health = client.get("/api/v1/ldap/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["ldaps_only"] is True
    assert health.json()["organization_count"] == 2


def test_ldap_api_rejects_cross_organization_membership_and_nested_cycle(client):
    token = api_token(client, ["read:ldap", "write:ldap"])
    headers = {"Authorization": f"Bearer {token}"}
    org_a = client.post("/api/v1/ldap/organizations", headers=headers, json={"name": "Cycle A"}).json()
    org_b = client.post("/api/v1/ldap/organizations", headers=headers, json={"name": "Cycle B"}).json()
    user_b = client.post(
        f"/api/v1/ldap/organizations/{org_b['id']}/users",
        headers=headers,
        json={"uid": "foreign", "enabled": False},
    ).json()

    cross_org = client.post(
        f"/api/v1/ldap/organizations/{org_a['id']}/groups",
        headers=headers,
        json={"name": "Cross Org", "enabled": True, "members": [{"type": "user", "id": user_b["id"]}]},
    )
    assert cross_org.status_code == 400

    leaf = client.post(
        f"/api/v1/ldap/organizations/{org_a['id']}/groups",
        headers=headers,
        json={"name": "Leaf", "enabled": False, "members": []},
    )
    parent = client.post(
        f"/api/v1/ldap/organizations/{org_a['id']}/groups",
        headers=headers,
        json={"name": "Parent", "enabled": True, "members": [{"type": "group", "id": leaf.json()["id"]}]},
    )
    assert parent.status_code == 201
    cycle = client.put(
        f"/api/v1/ldap/groups/{leaf.json()['id']}",
        headers=headers,
        json={"name": "Leaf", "enabled": True, "members": [{"type": "group", "id": parent.json()["id"]}]},
    )
    assert cycle.status_code == 400
    assert "cycle" in cycle.json()["detail"].lower()


def test_ldap_password_policy_and_renderer_never_expose_unstaged_hashes():
    settings = LdapSettings()
    assert validate_ldap_password("short", "operator", settings)
    assert validate_ldap_password("VeryStrong1!Directory", "operator", settings) == []

    organization = LdapOrganization(
        id=1,
        name="Org A",
        slug="org-a",
        suffix_dn="dc=org-a,dc=ldap,dc=labfoundry,dc=internal",
        bind_dn="uid=vcf-bind,ou=service-accounts,dc=org-a,dc=ldap,dc=labfoundry,dc=internal",
        bind_password_encrypted="encrypted-value",
    )
    user = LdapUser(
        id=1,
        organization=organization,
        organization_id=1,
        uid="operator",
        surname="Operator",
        display_name="Operator",
        enabled=False,
    )
    organization.users = [user]
    organization.groups = []
    payload = ldap_apply_payload(settings, [organization], include_secrets=False)
    rendered = json.dumps(payload)
    assert "encrypted-value" not in rendered
    assert "userPassword" not in rendered
    assert vcf_ldap_settings(settings, organization, include_password=False)["definedSettings"]["userAttributes"]["serviceAccount"] == "employeeType"


def test_ldap_nested_group_cycle_detection():
    organization = LdapOrganization(id=1, name="Org", slug="org", suffix_dn="dc=org,dc=example")
    first = LdapGroup(id=1, organization=organization, organization_id=1, name="First")
    second = LdapGroup(id=2, organization=organization, organization_id=1, name="Second")
    first.members = [LdapGroupMembership(group=first, member_group=second, member_group_id=2)]
    second.members = [LdapGroupMembership(group=second, member_group=first, member_group_id=1)]
    assert "cycle" in validate_group_cycles([first, second])[0].lower()


def test_ldap_recovery_envelope_and_manifest_validation():
    payload_buffer = io.BytesIO()
    with tarfile.open(fileobj=payload_buffer, mode="w:gz") as archive:
        manifest = json.dumps(
            {
                "format": "labfoundry-ldap-slapcat-v1",
                "databases": [{"index": 1, "suffix": "dc=org-a,dc=example", "filename": "database-1.ldif"}],
            }
        ).encode()
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest)
        archive.addfile(manifest_info, io.BytesIO(manifest))
        ldif = b"dn: dc=org-a,dc=example\n"
        ldif_info = tarfile.TarInfo("database-1.ldif")
        ldif_info.size = len(ldif)
        archive.addfile(ldif_info, io.BytesIO(ldif))
    payload = payload_buffer.getvalue()
    assert validate_ldap_recovery_payload(payload)["format"] == "labfoundry-ldap-slapcat-v1"

    encrypted = encrypt_recovery_payload(payload, "A sufficiently long recovery passphrase")
    assert payload not in encrypted
    assert decrypt_recovery_payload(encrypted, "A sufficiently long recovery passphrase") == payload


def test_ldap_api_settings_accept_observed_management_dhcp_address(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import PhysicalInterface

    with SessionLocal() as db:
        interface = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == "eth0")).scalar_one()
        interface.role = "management"
        interface.mode = "access"
        interface.admin_state = "up"
        interface.oper_state = "up"
        interface.ipv4_method = "dhcp"
        interface.ip_cidr = None
        interface.host_ip_cidr = "192.168.167.219/24"
        db.commit()

    token = api_token(client, ["read:ldap", "write:ldap"])
    response = client.patch(
        "/api/v1/ldap/settings",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "enabled": False,
            "hostname": "ldap.labfoundry.internal",
            "listen_interfaces": ["eth0"],
            "listen_addresses": [],
            "port": 636,
            "password_policy": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["listen_interfaces"] == ["eth0"]
    assert payload["listen_addresses"] == ["192.168.167.219"]


def test_ldap_dns_reconciliation_does_not_change_ldap_snapshot_timestamp(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import LdapSettings, PhysicalInterface
    from labfoundry.app.ui import ldap_context

    with SessionLocal() as db:
        interface = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == "eth0")).scalar_one()
        interface.role = "management"
        interface.mode = "access"
        interface.admin_state = "up"
        interface.oper_state = "up"
        interface.ipv4_method = "dhcp"
        interface.ip_cidr = None
        interface.host_ip_cidr = "192.168.167.219/24"
        settings = db.execute(select(LdapSettings)).scalar_one()
        settings.enabled = True
        settings.listen_interface = "eth0"
        settings.listen_address = "192.168.167.219"
        db.commit()

        ldap_context(db, reconcile=True)
        first_updated_at = settings.updated_at
        first_preview = ldap_context(db, reconcile=True)["ldap_apply_config"]
        db.refresh(settings)
        second_updated_at = settings.updated_at
        second_preview = ldap_context(db, reconcile=True)["ldap_apply_config"]

    assert second_updated_at == first_updated_at
    assert second_preview == first_preview

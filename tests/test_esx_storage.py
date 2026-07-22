import importlib.machinery
import importlib.util
from pathlib import Path

from labfoundry.app.models import EsxNfsShare, EsxStorageSettings, EsxStorageVolume
from labfoundry.app.services.esx_storage import (
    StorageInterface,
    desired_dns_records,
    firewall_rule_specs,
    format_authorization,
    normalize_disk_inventory_entry,
    render_manifest,
    share_paths_overlap,
)


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "appliance" / "labfoundry-helper"


def load_helper_module():
    loader = importlib.machinery.SourceFileLoader("labfoundry_esx_storage_helper", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader("labfoundry_esx_storage_helper", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def state(*, families: str = "ipv4\nipv6", ipv4_clients: str = "192.168.87.11/32", ipv6_clients: str = "2001:db8:87::11/128"):
    settings = EsxStorageSettings(enabled=True, hostname="nfs.labfoundry.internal")
    settings.id = 1
    volume = EsxStorageVolume(
        name="esx-data",
        source_type="blank_disk",
        stable_device_id="/dev/disk/by-id/wwn-0x1234",
        capacity_bytes=10 * 1024**3,
        mount_path="/mnt/labfoundry-esx-storage/esx-data",
    )
    volume.id = 1
    share = EsxNfsShare(
        datastore_name="esx-datastore",
        volume_id=1,
        relative_path="datastores/esx",
        preferred_nfs_version="4.1",
        interface_name="storage.87",
        address_families=families,
        ipv4_clients=ipv4_clients,
        ipv6_clients=ipv6_clients,
        enabled=True,
    )
    share.id = 1
    interfaces = {
        "storage.87": StorageInterface(
            "storage.87",
            ("192.168.87.254/24",),
            ("2001:db8:87::fe/64",),
        )
    }
    return settings, [volume], [share], interfaces


def render(**kwargs):
    settings, volumes, shares, interfaces = state(**kwargs)
    return render_manifest(settings, volumes, shares, interfaces, dns_enabled=True, dns_naming_mode="ip")


def test_dual_stack_share_renders_equal_family_endpoints_and_commands():
    manifest = render()
    share = manifest["shares"][0]

    assert manifest["validation"]["errors"] == []
    assert share["listeners"] == {"ipv4": ["192.168.87.254"], "ipv6": ["2001:db8:87::fe"]}
    assert share["target_hostnames"]["ipv4"] == ["nfs-192-168-87-254.labfoundry.internal"]
    assert share["target_hostnames"]["ipv6"] == ["nfs-2001-db8-87-0-0-0-0-fe.labfoundry.internal"]
    assert "--hosts=nfs-192-168-87-254.labfoundry.internal" in share["connection_commands"]["ipv4"][0]
    assert "--hosts=nfs-2001-db8-87-0-0-0-0-fe.labfoundry.internal" in share["connection_commands"]["ipv6"][0]


def test_ipv4_only_and_ipv6_only_do_not_create_implicit_fallback():
    ipv4 = render(families="ipv4")
    ipv6 = render(families="ipv6")

    assert ipv4["shares"][0]["listeners"]["ipv6"] == []
    assert ipv4["shares"][0]["connection_commands"]["ipv6"] == []
    assert ipv6["shares"][0]["listeners"]["ipv4"] == []
    assert ipv6["shares"][0]["connection_commands"]["ipv4"] == []


def test_mixed_family_client_allowlist_is_rejected():
    manifest = render(ipv4_clients="2001:db8::10/128")
    assert any("does not match the enabled IPV4 family" in message for message in manifest["validation"]["errors"])


def test_dns_records_include_canonical_alias_and_both_address_families():
    records = desired_dns_records(render())
    assert records[:2] == [{
        "hostname": "nfs.labfoundry.internal",
        "record_type": "A",
        "address": "192.168.87.254",
    }, {
        "hostname": "nfs.labfoundry.internal",
        "record_type": "AAAA",
        "address": "2001:db8:87::fe",
    }]
    assert {record["record_type"] for record in records} == {"A", "AAAA"}


def test_firewall_rules_are_family_specific_and_match_preferred_protocol():
    manifest = render()
    rules = firewall_rule_specs(manifest)
    assert {rule["source_expression"] for rule in rules} == {
        "ip saddr 192.168.87.11/32",
        "ip6 saddr 2001:db8:87::11/128",
    }
    assert {rule["ports"] for rule in rules} == {"2049"}

    manifest["shares"][0]["preferred_nfs_version"] = "3"
    assert {rule["ports"] for rule in firewall_rule_specs(manifest)} == {"111,20048,2049"}


def test_blank_disk_inventory_rejects_every_destructive_risk_and_claim():
    eligible = normalize_disk_inventory_entry(
        {
            "stable_device_id": "/dev/disk/by-id/wwn-0x1234",
            "device_path": "/dev/sdb",
            "type": "disk",
            "size_bytes": 1024,
        }
    )
    rejected = normalize_disk_inventory_entry(
        {
            "stable_device_id": "/dev/disk/by-id/wwn-0x5678",
            "device_path": "/dev/sda",
            "type": "disk",
            "partitions": ["/dev/sda1"],
            "filesystem_type": "ext4",
            "mount_path": "/",
            "holders": ["dm-0"],
            "os_related": True,
        },
        claimed_ids={"/dev/disk/by-id/wwn-0x5678"},
    )

    assert eligible["eligible"] is True
    assert rejected["eligible"] is False
    assert "operating-system disk" in rejected["eligibility_reason"]
    assert "already claimed" in rejected["eligibility_reason"]


def test_format_authorization_is_job_manifest_and_device_bound():
    manifest = render()
    authorization = format_authorization(
        job_id="job-123",
        manifest=manifest,
        volume=manifest["volumes"][0],
        confirmation="FORMAT esx-data",
    )
    assert authorization["job_id"] == "job-123"
    assert authorization["stable_device_id"] == "/dev/disk/by-id/wwn-0x1234"
    assert len(authorization["manifest_sha256"]) == 64


def test_export_paths_reject_root_children_and_siblings_remain_valid():
    assert share_paths_overlap("datastores", "datastores/esx") is True
    assert share_paths_overlap("datastores/esx-a", "datastores/esx-b") is False


def test_helper_requires_job_scoped_format_authorization_for_apply():
    helper = load_helper_module()
    manifest = render()
    assert helper._esx_storage_manifest_errors(manifest, require_authorization=False) == []
    assert helper._esx_storage_manifest_errors(manifest, require_authorization=True) == [
        "volume esx-data is missing job-scoped format authorization"
    ]

    manifest["format_authorizations"] = [
        format_authorization(
            job_id="job-123",
            manifest=manifest,
            volume=manifest["volumes"][0],
            confirmation="FORMAT esx-data",
        )
    ]
    assert helper._esx_storage_manifest_errors(manifest, require_authorization=True) == []


def test_helper_blank_disk_revalidation_rejects_partition_mount_lvm_raid_and_os_relationship():
    helper = load_helper_module()
    errors = helper._esx_storage_blank_disk_errors(
        {
            "type": "disk",
            "stable_device_id": "/dev/disk/by-id/wwn-test",
            "partitions": ["/dev/sdb1"],
            "filesystem_type": "ext4",
            "mount_path": "/mnt/data",
            "swap": True,
            "lvm": True,
            "raid": True,
            "holders": ["dm-0"],
            "os_related": True,
            "read_only": False,
        }
    )
    assert errors == [
        "has partitions",
        "has a filesystem",
        "is mounted",
        "is swap",
        "belongs to LVM",
        "belongs to RAID",
        "has holders",
        "is related to the operating-system disk",
    ]


def api_token(client, scopes: list[str]) -> str:
    response = client.post(
        "/api/v1/auth/login?username=admin&password=labfoundry-admin",
        json={"name": "esx storage test", "scopes": scopes},
    )
    assert response.status_code == 200, response.text
    return response.json()["raw_token"]


def test_esx_storage_page_and_dual_stack_api_contract(client):
    page = client.get("/login")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    assert client.post(
        "/login",
        data={"username": "admin", "password": "labfoundry-admin", "csrf": csrf},
        follow_redirects=False,
    ).status_code == 303
    page = client.get("/esx-storage")
    assert page.status_code == 200
    assert "IPv4 and IPv6 are equivalent connection paths" in page.text
    assert 'id="esx-storage-volumes-table"' in page.text
    assert 'id="esx-storage-shares-table"' in page.text

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, DnsSettings, PhysicalInterface

    with SessionLocal() as db:
        db.add(
            PhysicalInterface(
                name="storage87",
                mac_address="00:15:5d:00:87:01",
                role="access",
                mode="access",
                ip_cidr="192.168.87.254/24",
                ipv6_enabled=True,
                ipv6_cidr="2001:db8:87::fe/64",
            )
        )
        dns = db.query(DnsSettings).first()
        if dns is None:
            dns = DnsSettings()
            db.add(dns)
        dns.enabled = True
        dns.domain = "labfoundry.internal"
        db.commit()

    token = api_token(client, ["read:esx-storage", "write:esx-storage", "read:interfaces"])
    headers = {"Authorization": f"Bearer {token}"}
    interfaces = client.get("/api/v1/interfaces/physical", headers=headers).json()
    interface = next(row for row in interfaces if row["name"] == "storage87")
    volume_response = client.post(
        "/api/v1/esx-storage/volumes",
        headers=headers,
        json={"name": "existing-ext4", "source_type": "mounted_ext4", "mount_path": "/mnt/existing-ext4"},
    )
    assert volume_response.status_code == 201, volume_response.text
    share_response = client.post(
        "/api/v1/esx-storage/shares",
        headers=headers,
        json={
            "datastore_name": "dual-stack-ds",
            "volume_id": volume_response.json()["id"],
            "relative_path": "datastores/dual-stack",
            "preferred_nfs_version": "4.1",
            "interface_name": interface["name"],
            "address_families": ["ipv4", "ipv6"],
            "ipv4_clients": ["192.0.2.10/32"],
            "ipv6_clients": ["2001:db8:87::10/128"],
            "enabled": True,
        },
    )
    assert share_response.status_code == 201, share_response.text
    assert share_response.json()["address_families"] == ["ipv4", "ipv6"]
    assert share_response.json()["connection_commands"]["ipv4"]
    assert share_response.json()["connection_commands"]["ipv6"]
    status = client.patch(
        "/api/v1/esx-storage/status",
        headers=headers,
        json={"enabled": True, "hostname": "nfs.labfoundry.internal"},
    )
    assert status.status_code == 200, status.text
    assert status.json()["valid"] is True

    with SessionLocal() as db:
        owned = db.query(DnsRecord).filter(DnsRecord.description == "Created from ESX Storage endpoint.").all()
        assert {(row.hostname, row.record_type, row.address) for row in owned} == {
            ("nfs.labfoundry.internal", "A", "192.168.87.254"),
            ("nfs.labfoundry.internal", "AAAA", "2001:db8:87::fe"),
            ("nfs-192-168-87-254.labfoundry.internal", "A", "192.168.87.254"),
            ("nfs-2001-db8-87-0-0-0-0-fe.labfoundry.internal", "AAAA", "2001:db8:87::fe"),
        }
        storage = db.query(PhysicalInterface).filter(PhysicalInterface.name == "storage87").one()
        storage.ip_cidr = "203.0.113.254/24"
        storage.ipv6_cidr = "2001:db8:88::fe/64"
        db.add(
            DnsRecord(
                hostname="nfs-203-0-113-254.labfoundry.internal",
                record_type="A",
                address="203.0.113.254",
                description="Operator owned",
                enabled=True,
            )
        )
        db.commit()

        from labfoundry.app.ui import ensure_dns_for_esx_storage, esx_storage_context

        ensure_dns_for_esx_storage(db, "admin")
        db.commit()
        remaining_owned = db.query(DnsRecord).filter(DnsRecord.description == "Created from ESX Storage endpoint.").all()
        assert not any(row.address in {"192.168.87.254", "2001:db8:87::fe"} for row in remaining_owned)
        assert any("operator-owned" in error for error in esx_storage_context(db)["esx_storage_validation_errors"])


def test_esx_storage_write_scope_is_enforced(client):
    token = api_token(client, ["read:esx-storage"])
    response = client.post(
        "/api/v1/esx-storage/volumes",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "forbidden", "source_type": "mounted_ext4", "mount_path": "/mnt/forbidden"},
    )
    assert response.status_code == 403

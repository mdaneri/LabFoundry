def login(client):
    page = client.get("/login")
    assert page.status_code == 200
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/login",
        data={"username": "admin", "password": "labfoundry-admin", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def create_api_token(client, scopes):
    response = client.post(
        "/api/v1/auth/login?username=admin&password=labfoundry-admin",
        json={"name": "test token", "scopes": scopes},
    )
    assert response.status_code == 200, response.text
    return response.json()["raw_token"]


def test_login_and_dashboard_render(client):
    login(client)
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 303
    assert root.headers["location"] == "/dashboard"
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "LabFoundry" in response.text
    assert "Routes &amp; WAN Simulation" in response.text
    assert "VCF Offline Depot" in response.text
    assert "HTTPS Repository" not in response.text
    assert "Users" in response.text
    assert "LDAP / Users" not in response.text
    assert 'href="/logs"' in response.text
    assert 'href="/audit-log"' not in response.text
    assert "cdn.tailwindcss.com" not in response.text
    assert "unpkg.com/htmx" not in response.text
    assert 'body class="bg-slate-100 text-slate-900"' not in response.text
    assert "/static/brand/labfoundry-mark.svg" in response.text
    assert '<link rel="icon" href="/favicon.ico" type="image/svg+xml">' in response.text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in response.text
    assert '<meta name="theme-color" content="#1f4f7a">' in response.text
    assert "/static/pwa.js?v=pwa-20260627-1" in response.text
    assert "LF</span>" not in response.text
    assert "/static/vendor/prism/prism-core.min.js" in response.text
    assert "/static/vendor/prism/prism-diff.min.js" in response.text
    assert client.get("/static/brand/labfoundry-mark.svg").status_code == 200
    assert client.get("/static/brand/labfoundry-appliance-graphic.svg").status_code == 200
    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")


def test_pwa_manifest_service_worker_and_offline_shell(client):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.headers["cache-control"] == "no-cache"
    manifest_json = manifest.json()
    assert manifest_json["name"] == "LabFoundry"
    assert manifest_json["short_name"] == "LabFoundry"
    assert manifest_json["start_url"] == "/dashboard"
    assert manifest_json["scope"] == "/"
    assert manifest_json["display"] == "standalone"
    assert manifest_json["theme_color"] == "#1f4f7a"
    assert manifest_json["icons"][0]["src"] == "/static/brand/labfoundry-mark.svg"
    assert manifest_json["icons"][0]["purpose"] == "any maskable"

    service_worker = client.get("/service-worker.js")
    assert service_worker.status_code == 200
    assert service_worker.headers["content-type"].startswith("application/javascript")
    assert service_worker.headers["cache-control"] == "no-cache"
    assert service_worker.headers["service-worker-allowed"] == "/"
    assert "LABFOUNDRY_CACHE" in service_worker.text
    assert "labfoundry-pwa-v14" in service_worker.text
    assert 'request.mode === "navigate"' in service_worker.text
    assert 'caches.match("/static/offline.html")' in service_worker.text
    assert 'request.method !== "GET"' in service_worker.text
    assert "/static/vendor/codemirror/labfoundry-codemirror.min.js" in service_worker.text

    registration = client.get("/static/pwa.js")
    assert registration.status_code == 200
    assert 'navigator.serviceWorker.register("/service-worker.js")' in registration.text

    offline = client.get("/static/offline.html")
    assert offline.status_code == 200
    assert "Appliance connection unavailable" in offline.text
    assert "/static/app.css?v=vcf-depot-tool-reset-20260703-1" in offline.text


def test_login_page_includes_pwa_metadata(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert '<link rel="manifest" href="/manifest.webmanifest">' in response.text
    assert '<meta name="theme-color" content="#1f4f7a">' in response.text
    assert "/static/pwa.js?v=pwa-20260627-1" in response.text


def test_unauthenticated_ui_request_redirects_to_login(client):
    response = client.get("/certificate-authority", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_ui_session_is_rejected_after_appliance_instance_changes(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Setting
    from labfoundry.app.security import SESSION_APPLIANCE_INSTANCE_SETTING_KEY

    login(client)
    assert client.get("/dashboard").status_code == 200

    with SessionLocal() as db:
        setting = db.query(Setting).filter(Setting.key == SESSION_APPLIANCE_INSTANCE_SETTING_KEY).one()
        setting.value = "redeployed-appliance-instance"
        db.commit()

    response = client.get("/vlan-interfaces", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_sidebar_appliance_apply_uses_bottom_pending_cta(client):
    login(client)
    response = client.get("/certificate-authority")

    assert response.status_code == 200
    assert 'class="sidebar-apply-link pending' in response.text
    assert 'href="/appliance-apply"' in response.text
    assert "data-appliance-apply-sidebar" in response.text
    assert "data-appliance-apply-sidebar-title" in response.text
    assert "data-appliance-apply-sidebar-detail" in response.text
    assert "data-appliance-apply-sidebar-badge" in response.text
    assert "Review appliance changes" in response.text
    assert "pending unit" in response.text
    assert 'class="nav-link " href="/appliance-apply"' not in response.text


def test_dns_settings_derives_listen_addresses_from_selected_interface(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import PhysicalInterface

    login(client)
    with SessionLocal() as db:
        db.add(
            PhysicalInterface(
                name="eth9",
                mac_address="00:50:56:00:00:09",
                role="access",
                mode="access",
                ip_cidr="192.168.90.1/24",
                ipv6_cidr="2001:db8:90::1/64",
                admin_state="up",
                oper_state="up",
            )
        )
        db.commit()

    page = client.get("/dns")
    assert page.status_code == 200
    assert "Listen addresses" in page.text
    assert "Add listen address" not in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        "/dns/settings",
        data={
            "csrf": csrf,
            "enabled": "on",
            "listen_interfaces_present": "1",
            "listen_interfaces": "eth9",
            "upstream_servers": "1.1.1.1",
            "conditional_forwarders": "",
            "cache_size": "1000",
            "expand_hosts": "on",
            "authoritative": "on",
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["listen_interfaces"] == ["eth9"]
    assert response.json()["listen_addresses"] == ["192.168.90.1", "2001:db8:90::1"]


def test_forget_missing_physical_interface_deletes_only_stale_rows(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import PhysicalInterface, VlanInterface

    login(client)
    with SessionLocal() as db:
        missing = PhysicalInterface(
            name="missing_eth7",
            mac_address="00:50:56:00:00:07",
            role="unused",
            mode="unused",
            admin_state="down",
            oper_state="missing",
        )
        db.add(missing)
        db.add(VlanInterface(name="missing_eth7.20", parent_interface="missing_eth7", vlan_id=20, enabled=False))
        active = PhysicalInterface(
            name="eth8",
            mac_address="00:50:56:00:00:08",
            role="access",
            mode="access",
            admin_state="up",
            oper_state="up",
        )
        db.add(active)
        db.commit()
        missing_id = missing.id
        active_id = active.id

    page = client.get("/physical-interfaces")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    active_response = client.post(f"/physical-interfaces/{active_id}/forget", data={"csrf": csrf})
    assert active_response.status_code == 409
    response = client.post(f"/physical-interfaces/{missing_id}/forget", data={"csrf": csrf}, follow_redirects=False)

    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(PhysicalInterface, missing_id) is None
        assert db.query(VlanInterface).filter(VlanInterface.parent_interface == "missing_eth7").count() == 0
        assert db.get(PhysicalInterface, active_id) is not None


def test_stage_appliance_apply_config_repairs_staging_permission(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from labfoundry.app import ui

    attempts = {"count": 0}
    repairs: list[str] = []

    def fake_write(path, config_preview):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError("blocked")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config_preview, encoding="utf-8")

    class FakeAdapter:
        def prepare_apply_staging_path(self, path):
            repairs.append(path)
            return SimpleNamespace(returncode=0, stdout="prepared", stderr="")

    monkeypatch.setattr(ui, "_write_staged_config_file", fake_write)
    monkeypatch.setattr(ui, "SystemAdapter", FakeAdapter)

    config_path = tmp_path / "apply" / "wan" / "labfoundry-wan.conf"
    result = ui.stage_appliance_apply_config(str(config_path), "config")

    assert result == str(config_path)
    assert repairs == [str(config_path)]
    assert attempts["count"] == 2
    assert config_path.read_text(encoding="utf-8") == "config"


def test_appliance_apply_status_api_tracks_autosaved_desired_state(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.ui import appliance_apply_units, update_appliance_apply_baselines

    login(client)
    with SessionLocal() as db:
        units = appliance_apply_units(db)
        update_appliance_apply_baselines(db, units, {unit["id"] for unit in units})
        db.commit()

    current = client.get("/appliance-apply/status")
    assert current.status_code == 200
    assert current.json() == {
        "pending_count": 0,
        "label": "Appliance Apply",
        "detail": "Desired state current",
        "badge": "current",
    }

    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/settings",
        data={
            "enabled": "on",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth2"],
            "listen_addresses": ["192.168.50.1"],
            "upstream_servers": "8.8.8.8",
            "cache_size": "500",
            "expand_hosts": "on",
            "authoritative": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    pending = client.get("/appliance-apply/status")
    assert pending.status_code == 200
    assert pending.json()["pending_count"] > 0
    assert pending.json()["label"] == "Review appliance changes"
    assert "pending unit" in pending.json()["detail"]
    assert pending.json()["badge"] == "pending"


def test_legacy_appliance_settings_ntp_baseline_does_not_create_pending_change(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ChronySettings, Setting
    from labfoundry.app.ui import APPLIANCE_APPLY_BASELINES_KEY

    login(client)
    legacy_preview = json.dumps(
        {
            "fqdn": "labfoundry.labfoundry.internal",
            "resolver_mode": "local_dns",
            "resolver_servers": ["127.0.0.1"],
            "local_dns_enabled": True,
            "management_interface": "eth0",
            "management_ip": "192.168.49.1",
            "management_ip_cidr": "192.168.49.1/24",
            "management_https_enabled": False,
            "root_ssh_enabled": False,
            "management_http_port": 8000,
            "management_public_http_port": 80,
            "management_public_https_port": 443,
            "management_upstream_host": "127.0.0.1",
            "management_upstream_port": 8000,
            "management_https_cert_path": "",
            "management_https_key_path": "",
            "ntp_servers": ["time1.google.com", "time2.google.com"],
        },
        indent=2,
        sort_keys=True,
    )
    with SessionLocal() as db:
        chrony_settings = db.execute(select(ChronySettings)).scalar_one()
        chrony_settings.enabled = True
        db.add(chrony_settings)
        db.merge(
            Setting(
                key=APPLIANCE_APPLY_BASELINES_KEY,
                value=json.dumps(
                    {
                        "appliance_settings": {
                            "snapshot_hash": "legacy-hash",
                            "summary": [
                                "FQDN labfoundry.labfoundry.internal",
                                "resolver local DNS",
                                "root SSH disabled",
                                "2 NTP servers",
                            ],
                            "config_path": "/var/lib/labfoundry/apply/appliance-settings/labfoundry-settings.json",
                            "config_preview": legacy_preview,
                            "applied_at": "2026-07-01T00:00:00+00:00",
                        }
                    }
                ),
            )
        )
        db.commit()

    page = client.get("/appliance-apply")

    assert page.status_code == 200
    assert "2 NTP servers" not in page.text
    assert "ntp_servers" not in page.text


def test_settings_page_renders_autosave_validation_and_preview(client, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsSettings

    monkeypatch.setattr("labfoundry.app.ui.socket.gethostname", lambda: "runtime.labfoundry.internal")

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = True
        db.commit()

    response = client.get("/settings")

    assert response.status_code == 200
    assert 'action="/settings"' in response.text
    assert 'data-autosave-status-id="appliance-settings-autosave-status"' in response.text
    assert response.text.count('class="help-icon"') >= 2
    assert 'textarea name="external_dns_servers"' not in response.text
    assert 'input type="hidden" name="external_dns_servers"' in response.text
    assert "Appliance Settings has pending appliance changes" in response.text
    assert "Validation" in response.text
    assert "runtime.labfoundry.internal" in response.text
    assert "labfoundry.labfoundry.internal" in response.text
    assert "Management UI HTTPS" in response.text
    assert "Root SSH login" in response.text
    assert "Operational Logging" in response.text
    assert "External NTP servers" in response.text
    assert 'textarea name="ntp_servers"' in response.text
    assert 'action="/settings/logging"' in response.text
    assert 'select name="level"' in response.text
    assert 'input class="switch-input" type="checkbox" name="syslog_enabled"' in response.text
    assert "Syslog host" in response.text
    assert "data-appliance-settings-root-ssh" in response.text
    assert "/var/lib/labfoundry/apply/appliance-settings/labfoundry-settings.json" in response.text
    assert "resolver_mode" in response.text
    assert "root_ssh_enabled" in response.text
    assert 'class="language-json" data-appliance-settings-preview' in response.text


def test_logging_settings_autosave_updates_preferences(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import AuditEvent, Setting
    from labfoundry.app.operational_logging import (
        LOGGING_LEVEL_KEY,
        LOGGING_SYSLOG_ENABLED_KEY,
        LOGGING_SYSLOG_FACILITY_KEY,
        LOGGING_SYSLOG_HOST_KEY,
        LOGGING_SYSLOG_LEVEL_KEY,
        LOGGING_SYSLOG_PORT_KEY,
        LOGGING_SYSLOG_PROTOCOL_KEY,
    )

    login(client)
    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        "/settings/logging",
        data={
            "level": "DEBUG",
            "syslog_enabled": "on",
            "syslog_host": "127.0.0.1",
            "syslog_port": "5514",
            "syslog_protocol": "udp",
            "syslog_facility": "local4",
            "syslog_level": "WARNING",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["logging_preferences"]["level"] == "DEBUG"
    assert payload["logging_preferences"]["syslog_enabled"] is True
    assert payload["logging_preferences"]["syslog_host"] == "127.0.0.1"
    assert payload["logging_preferences"]["syslog_port"] == 5514
    assert payload["logging_preferences"]["syslog_protocol"] == "udp"
    assert payload["logging_preferences"]["syslog_facility"] == "local4"
    assert payload["logging_preferences"]["syslog_level"] == "WARNING"

    with SessionLocal() as db:
        values = {row.key: row.value for row in db.execute(select(Setting)).scalars().all()}
        assert values[LOGGING_LEVEL_KEY] == "DEBUG"
        assert values[LOGGING_SYSLOG_ENABLED_KEY] == "true"
        assert values[LOGGING_SYSLOG_HOST_KEY] == "127.0.0.1"
        assert values[LOGGING_SYSLOG_PORT_KEY] == "5514"
        assert values[LOGGING_SYSLOG_PROTOCOL_KEY] == "udp"
        assert values[LOGGING_SYSLOG_FACILITY_KEY] == "local4"
        assert values[LOGGING_SYSLOG_LEVEL_KEY] == "WARNING"
        event = db.execute(select(AuditEvent).where(AuditEvent.action == "update_operational_logging_settings")).scalar_one()
        assert event.resource_type == "logging"


def test_logging_settings_requires_syslog_host_when_enabled(client):
    login(client)
    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        "/settings/logging",
        data={
            "level": "INFO",
            "syslog_enabled": "on",
            "syslog_host": "",
            "syslog_port": "514",
            "syslog_protocol": "udp",
            "syslog_facility": "local0",
            "syslog_level": "INFO",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 422
    assert response.json()["message"] == "External syslog host is required when syslog forwarding is enabled."


def test_settings_page_shows_external_dns_editor_when_local_dns_is_disabled(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsSettings

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = False
        db.commit()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "External DNS servers" in response.text
    assert 'textarea name="external_dns_servers"' in response.text
    assert "Local DNS is disabled. External DNS servers are required" in response.text


def test_settings_page_hides_ntp_editor_when_chrony_is_enabled(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ChronySettings

    login(client)
    with SessionLocal() as db:
        chrony_settings = db.execute(select(ChronySettings)).scalar_one()
        chrony_settings.enabled = True
        db.add(chrony_settings)
        db.commit()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "External NTP servers" not in response.text
    assert 'textarea name="ntp_servers"' not in response.text
    assert 'input type="hidden" name="ntp_servers"' in response.text
    assert '  "ntp_servers": [' not in response.text


def test_settings_autosave_updates_appliance_identity_dns_without_ntp(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ApplianceSettings, ChronySettings, DnsRecord, DnsSettings

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = True
        chrony_settings = db.execute(select(ChronySettings)).scalar_one()
        chrony_settings.enabled = True
        db.commit()

    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/settings",
        data={
            "fqdn": "console.labfoundry.internal",
            "root_ssh_enabled": "on",
            "external_dns_servers": "8.8.8.8\n1.1.1.1",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["fqdn"] == "console.labfoundry.internal"
    assert payload["root_ssh_enabled"] is True
    assert payload["external_dns_servers"] == ["8.8.8.8", "1.1.1.1"]
    assert "ntp_servers" not in payload
    assert payload["dns_record_action"] in {"created", "updated", "unchanged", "created+removed-old", "updated+removed-old"}
    assert payload["valid"] is True
    assert '"resolver_mode": "local_dns"' in payload["config_preview"]
    assert '"resolver_servers": [' in payload["config_preview"]
    assert '"127.0.0.1"' in payload["config_preview"]
    assert '"root_ssh_enabled": true' in payload["config_preview"]
    assert "ntp_servers" not in payload["config_preview"]

    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        assert settings.fqdn == "console.labfoundry.internal"
        assert settings.root_ssh_enabled is True
        record = db.execute(
            select(DnsRecord).where(DnsRecord.hostname == "console.labfoundry.internal", DnsRecord.record_type == "A")
        ).scalar_one()
        assert record.address == "192.168.49.1"
    assert "app-owned appliance FQDN" in (record.description or "")


def test_settings_autosave_updates_ntp_servers_when_chrony_is_disabled(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ApplianceSettings, ChronySettings, DnsSettings

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = True
        chrony_settings = db.execute(select(ChronySettings)).scalar_one()
        chrony_settings.enabled = False
        db.add_all([dns_settings, chrony_settings])
        db.commit()

    page = client.get("/settings")
    assert 'textarea name="ntp_servers"' in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/settings",
        data={
            "fqdn": "labfoundry.labfoundry.internal",
            "external_dns_servers": "1.1.1.1\n9.9.9.9",
            "ntp_servers": "time.cloudflare.com\n192.0.2.10",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chrony_enabled"] is False
    assert payload["ntp_servers"] == ["time.cloudflare.com", "192.0.2.10"]
    assert '"time_sync_mode": "systemd-timesyncd"' in payload["config_preview"]
    assert '"ntp_servers": [' in payload["config_preview"]
    assert payload["valid"] is True

    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        assert settings.ntp_servers == "time.cloudflare.com\n192.0.2.10"


def test_chrony_page_autosave_updates_desired_state_and_preview(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ChronySettings

    login(client)
    page = client.get("/chrony")
    assert page.status_code == 200
    assert "Chrony Settings" in page.text
    assert "/var/lib/labfoundry/apply/chronyd/labfoundry-chrony.conf" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/chrony/settings",
        data={
            "enabled": "on",
            "hostname": "ntp.labfoundry.internal",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth2"],
            "upstream_servers": "time.cloudflare.com\ntime.google.com",
            "allow_clients": "192.168.50.0/24",
            "port": "123",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["enabled"] is True
    assert payload["listen_interfaces"] == ["eth2"]
    assert payload["listen_addresses"] == ["192.168.50.1"]
    assert payload["upstream_servers"] == ["time.cloudflare.com", "time.google.com"]
    assert payload["allow_clients"] == "192.168.50.0/24"
    assert payload["valid"] is True
    assert "server time.cloudflare.com iburst" in payload["config_preview"]
    assert "bindaddress 192.168.50.1" in payload["config_preview"]
    assert "allow 192.168.50.0/24" in payload["config_preview"]
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "initializeChronySettings" in js.text
    assert "updateNtpValidation" in js.text

    assert "External NTP servers" not in client.get("/settings").text

    with SessionLocal() as db:
        settings = db.execute(select(ChronySettings)).scalar_one()
        assert settings.enabled is True
        assert settings.listen_interface == "eth2"
        assert settings.listen_address == "192.168.50.1"


def test_chrony_validation_rejects_enabled_service_without_bind_or_upstreams(client):
    login(client)
    page = client.get("/chrony")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/chrony/settings",
        data={
            "enabled": "on",
            "hostname": "ntp.labfoundry.internal",
            "listen_interfaces_present": "1",
            "upstream_servers": "",
            "allow_clients": "any",
            "port": "123",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert "Chrony listen interface is required when the service is enabled." in payload["validation_errors"]
    assert "At least one Chrony upstream server is required." in payload["validation_errors"]


def test_chrony_validation_allows_disabled_service_without_upstreams(client):
    login(client)
    page = client.get("/chrony")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/chrony/settings",
        data={
            "hostname": "ntp.labfoundry.internal",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": [],
            "upstream_servers": "",
            "allow_clients": "any",
            "port": "123",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["valid"] is True
    assert payload["upstream_servers"] == []
    assert "At least one Chrony upstream server is required." not in payload["validation_errors"]
    assert "server " not in payload["config_preview"]


def test_dns_defaults_follow_appliance_fqdn_and_management_ip(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, DnsSettings

    login(client)
    page = client.get("/dns")
    assert page.status_code == 200
    assert 'data-domain="labfoundry.internal"' in page.text
    assert "labfoundry" in page.text
    assert "192.168.49.1" in page.text

    with SessionLocal() as db:
        settings = db.execute(select(DnsSettings)).scalar_one()
        assert settings.domain == "labfoundry.internal"
        record = db.execute(
            select(DnsRecord).where(DnsRecord.hostname == "labfoundry.labfoundry.internal", DnsRecord.record_type == "A")
        ).scalar_one()
        assert record.address == "192.168.49.1"
        assert "app-owned appliance FQDN" in (record.description or "")


def test_settings_fqdn_rename_removes_only_old_app_owned_record(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, DnsSettings

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = True
        db.add(
            DnsRecord(
                hostname="manual.labfoundry.internal",
                record_type="A",
                address="192.168.49.20",
                description="User-owned record",
            )
        )
        db.commit()

    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    first = client.post(
        "/settings",
        data={
            "fqdn": "old-appliance.labfoundry.internal",
            "external_dns_servers": "1.1.1.1\n9.9.9.9",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert first.status_code == 200
    second = client.post(
        "/settings",
        data={
            "fqdn": "new-appliance.labfoundry.internal",
            "external_dns_servers": "1.1.1.1\n9.9.9.9",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert second.status_code == 200
    assert "removed-old" in (second.json()["dns_record_action"] or "")

    with SessionLocal() as db:
        old = db.execute(select(DnsRecord).where(DnsRecord.hostname == "old-appliance.labfoundry.internal")).scalars().all()
        new = db.execute(select(DnsRecord).where(DnsRecord.hostname == "new-appliance.labfoundry.internal")).scalars().all()
        manual = db.execute(select(DnsRecord).where(DnsRecord.hostname == "manual.labfoundry.internal")).scalar_one()
        assert old == []
        assert len(new) == 1
        assert manual.address == "192.168.49.20"


def test_settings_local_dns_disabled_requires_external_dns_without_dns_registration(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, DnsSettings

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = False
        db.commit()

    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/settings",
        data={
            "fqdn": "external-only.labfoundry.internal",
            "external_dns_servers": "",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert "External DNS servers are required when local DNS is disabled." in payload["validation_errors"]
    assert payload["dns_record_action"] is None
    assert '"resolver_mode": "external"' in payload["config_preview"]
    with SessionLocal() as db:
        record = db.execute(select(DnsRecord).where(DnsRecord.hostname == "external-only.labfoundry.internal")).scalar_one_or_none()
        assert record is None


def test_settings_management_https_requires_ca_managed_certificate(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ApplianceSettings, CaCertificate, CaSettings, DnsSettings

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = True
        ca_settings = db.execute(select(CaSettings)).scalar_one()
        ca_settings.enabled = False
        db.commit()

    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    invalid = client.post(
        "/settings",
        data={
            "fqdn": "secure.labfoundry.internal",
            "management_https_enabled": "on",
            "external_dns_servers": "1.1.1.1",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert "Management UI HTTPS requires the local LabFoundry CA to be enabled." in invalid.json()["validation_errors"]

    with SessionLocal() as db:
        ca_settings = db.execute(select(CaSettings)).scalar_one()
        ca_settings.enabled = True
        db.add(
            CaCertificate(
                common_name="secure.labfoundry.internal",
                subject_alt_names="secure.labfoundry.internal",
                ip_addresses="192.168.49.1",
                status="issued",
                certificate_pem="-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n",
                private_key_encrypted="fernet:v1:test",
                managed_owner="appliance:https",
                cert_path="/etc/labfoundry/https/certs/secure.labfoundry.internal.crt",
                key_path="/etc/labfoundry/https/certs/secure.labfoundry.internal.key",
                chain_path="/etc/labfoundry/https/certs/secure.labfoundry.internal-chain.pem",
            )
        )
        db.commit()

    valid = client.post(
        "/settings",
        data={
            "fqdn": "secure.labfoundry.internal",
            "management_https_enabled": "on",
            "external_dns_servers": "1.1.1.1",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert valid.status_code == 200
    payload = valid.json()
    assert payload["valid"] is True
    assert payload["management_https_enabled"] is True
    assert payload["management_https_cert_available"] is True
    assert '"management_https_enabled": true' in payload["config_preview"]
    assert "/etc/labfoundry/https/certs/secure.labfoundry.internal.crt" in payload["config_preview"]

    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        assert settings.management_https_enabled is True


def test_appliance_settings_apply_task_records_dry_run_helper_commands(client, caplog):
    import logging

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    saved = client.post(
        "/settings",
        data={
            "fqdn": "apply.labfoundry.internal",
            "external_dns_servers": "1.1.1.1\n9.9.9.9",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert saved.status_code == 200

    with caplog.at_level(logging.INFO, logger="labfoundry.appliance_apply"):
        apply_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "appliance_settings"})
    assert apply_response.status_code == 200
    assert "Appliance apply task succeeded" in apply_response.text
    assert "completed status=succeeded selected_units=appliance_settings" in caplog.text
    assert "unit=appliance_settings status=succeeded" in caplog.text
    assert "labfoundry-helper appliance-settings validate" in caplog.text
    assert "Task Steps" in apply_response.text
    assert "Appliance Settings" in apply_response.text
    assert "Done" in apply_response.text
    assert "data-apply-progress-modal" not in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert "appliance_settings" in (job.result or "")
        assert "labfoundry-helper appliance-settings validate" in (job.result or "")
        assert "labfoundry-helper appliance-settings apply" in (job.result or "")
        assert "apply.labfoundry.internal" in (job.result or "")


def test_appliance_apply_failure_renders_command_details(client, monkeypatch):
    from labfoundry.app.adapters.system import AdapterResult
    import labfoundry.app.ui as ui_module

    class FailingApplianceSettingsAdapter:
        dry_run = False

        def read_dhcp_leases(self) -> AdapterResult:
            return AdapterResult(command=["labfoundry-helper", "dnsmasq", "leases"], dry_run=True, stdout="")

        def validate_appliance_settings_config(self, config_path: str) -> AdapterResult:
            return AdapterResult(
                command=["labfoundry-helper", "appliance-settings", "validate", config_path],
                dry_run=False,
                stdout="validation ok",
            )

        def apply_appliance_settings_config(self, config_path: str) -> AdapterResult:
            return AdapterResult(
                command=["labfoundry-helper", "appliance-settings", "apply", config_path],
                dry_run=False,
                stdout="password=super-secret\nattempted write",
                stderr="OSError: [Errno 30] Read-only file system: '/etc/labfoundry/nginx/sites.d/management.conf'",
                returncode=30,
            )

    monkeypatch.setattr(ui_module, "SystemAdapter", FailingApplianceSettingsAdapter)

    login(client)
    page = client.get("/appliance-apply")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "appliance_settings"})

    assert response.status_code == 200
    assert "Appliance apply task failed" in response.text
    assert "Appliance Settings failed" in response.text
    assert "Task Steps" in response.text
    assert "Failed" in response.text
    assert "labfoundry-helper appliance-settings apply" in response.text
    assert "exited 30" in response.text
    assert "Read-only file system" in response.text
    assert "password= [redacted]" in response.text
    assert "super-secret" not in response.text


def test_appliance_apply_stops_unit_after_validation_failure(client, monkeypatch):
    import json

    from sqlalchemy import select

    from labfoundry.app.adapters.system import AdapterResult
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job
    import labfoundry.app.ui as ui_module

    class ValidationFailingApplianceSettingsAdapter:
        dry_run = False

        def read_dhcp_leases(self) -> AdapterResult:
            return AdapterResult(command=["labfoundry-helper", "dnsmasq", "leases"], dry_run=True, stdout="")

        def validate_appliance_settings_config(self, config_path: str) -> AdapterResult:
            return AdapterResult(
                command=["labfoundry-helper", "appliance-settings", "validate", config_path],
                dry_run=False,
                stderr="hostname validation failed",
                returncode=2,
            )

        def apply_appliance_settings_config(self, config_path: str) -> AdapterResult:
            raise AssertionError("apply should not run after validation failure")

    monkeypatch.setattr(ui_module, "SystemAdapter", ValidationFailingApplianceSettingsAdapter)

    login(client)
    page = client.get("/settings")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    saved = client.post(
        "/settings",
        data={
            "fqdn": "validate-fail.labfoundry.internal",
            "external_dns_servers": "1.1.1.1",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert saved.status_code == 200

    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "appliance_settings"})

    assert response.status_code == 200
    assert "Appliance apply task failed" in response.text
    assert "labfoundry-helper appliance-settings validate" in response.text
    assert "labfoundry-helper appliance-settings apply" not in response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        payload = json.loads(job.result or "{}")
        commands = payload["units"][0]["commands"]
        assert [command["command"][2] for command in commands] == ["validate"]
        assert "labfoundry-helper appliance-settings apply" not in (job.result or "")


def test_backup_restore_page_exports_settings_archive(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import AuditEvent

    login(client)
    page = client.get("/backup-restore")
    assert page.status_code == 200
    assert "Download settings backup" in page.text
    assert "Restore settings backup" in page.text
    assert "Factory reset settings" in page.text
    assert "Audit events, jobs, API tokens, password hashes, uploaded secret bodies; CA private material stays encrypted" in page.text
    assert "data-confirm-modal" in page.text

    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    exported = client.post("/backup-restore/export", data={"csrf": csrf})

    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/json")
    assert "labfoundry-settings-" in exported.headers["content-disposition"]
    payload = json.loads(exported.content)
    assert payload["kind"] == "labfoundry-settings-archive"
    assert payload["schema_version"] == 1
    assert "appliance_settings" in payload["data"]
    assert "dns_records" in payload["data"]
    assert "users" not in payload["data"]
    assert "api_tokens" not in payload["data"]
    assert "audit_events" not in payload["data"]
    assert "jobs" not in payload["data"]

    with SessionLocal() as db:
        event = db.execute(select(AuditEvent).where(AuditEvent.action == "export_settings_backup")).scalar_one()
        assert event.resource_type == "settings_backup"


def test_esxi_kickstart_api_hides_raw_content_from_read_only_tokens(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import EsxiKickstart

    write_token = create_api_token(client, ["read:esxi-pxe", "write:esxi-pxe"])
    created = client.post(
        "/api/v1/esxi-pxe/kickstarts",
        headers={"Authorization": f"Bearer {write_token}"},
        json={
            "name": "Secure ESXi",
            "description": "secret-bearing ks",
            "content": "install --firstdisk\nnetwork --bootproto=dhcp\nrootpw MySecretPassword\nreboot\n%firstboot\n%end\n",
            "enabled": True,
        },
    )

    assert created.status_code == 201, created.text
    kickstart_id = created.json()["id"]
    assert created.json()["content"] and "MySecretPassword" in created.json()["content"]
    with SessionLocal() as db:
        row = db.execute(select(EsxiKickstart).where(EsxiKickstart.id == kickstart_id)).scalar_one()
        assert "MySecretPassword" in row.content
        assert row.content_hash

    read_token = create_api_token(client, ["read:esxi-pxe"])
    fetched = client.get(f"/api/v1/esxi-pxe/kickstarts/{kickstart_id}", headers={"Authorization": f"Bearer {read_token}"})
    preview = client.get(f"/api/v1/esxi-pxe/kickstarts/{kickstart_id}/preview", headers={"Authorization": f"Bearer {read_token}"})
    download = client.get(f"/api/v1/esxi-pxe/kickstarts/{kickstart_id}/download", headers={"Authorization": f"Bearer {read_token}"})

    assert fetched.status_code == 200
    assert fetched.json()["content"] is None
    assert "MySecretPassword" not in fetched.text
    assert "rootpw ********" in fetched.json()["redacted_preview"]
    assert preview.status_code == 200
    assert "MySecretPassword" not in preview.text
    assert download.status_code == 403


def test_esxi_pxe_ui_create_apply_and_job_redaction(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import AuditEvent, EsxiKickstart, Job

    login(client)
    page = client.get("/esxi-pxe")
    assert page.status_code == 200
    assert "ESXi Kickstarts" in page.text
    assert 'data-codemirror-language="labfoundry-kickstart"' in page.text
    assert "# Sample scripted installation file" in page.text
    assert "vmaccepteula" in page.text
    assert "rootpw vmware01!" in page.text
    assert "install --firstdisk --overwritevmfs" in page.text
    assert "# install --firstdisk --overwritevmfs --dpupcislots=&lt;PCIeSlotID&gt;" in page.text
    assert "network --bootproto=dhcp --device=vmnic0" in page.text
    assert "%post --interpreter=python --ignorefailure=true" in page.text
    assert "stampFile.write(time.asctime())" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    created = client.post(
        "/esxi-pxe/kickstarts",
        data={
            "csrf": csrf,
            "name": "Lab ESXi",
            "description": "install",
            "content": "install --firstdisk\nnetwork --bootproto=dhcp\nrootpw SuperSecret!\nreboot\n%firstboot\n%end\n",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    kickstart_id = int(created.headers["location"].rsplit("=", 1)[1])
    with SessionLocal() as db:
        kickstart = db.execute(select(EsxiKickstart).where(EsxiKickstart.id == kickstart_id)).scalar_one()
        assert "SuperSecret!" in kickstart.content
        assert kickstart.http_path == f"/pxe/esxi/ks/{kickstart.content_hash[:12]}.cfg"

    apply_page = client.get("/appliance-apply")
    assert 'value="esxi_pxe"' in apply_page.text
    apply_csrf = apply_page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    applied = client.post("/appliance-apply", data={"csrf": apply_csrf, "selected_units": "esxi_pxe"})

    assert applied.status_code == 200
    assert "ESXi PXE" in applied.text
    assert "SuperSecret!" not in applied.text
    assert "[redacted]" in applied.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply").order_by(Job.created_at.desc())).scalars().first()
        assert job is not None
        payload = json.loads(job.result or "{}")
        assert payload["selected_units"] == ["esxi_pxe"]
        assert "SuperSecret!" not in (job.result or "")
        assert "labfoundry-helper esxi-pxe apply" in (job.result or "")
        event = db.execute(select(AuditEvent).where(AuditEvent.action == "create_esxi_kickstart")).scalar_one()
        assert "SuperSecret!" not in (event.detail or "")


def test_esxi_pxe_iso_upload_and_host_selection(client, monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    from sqlalchemy import select

    import labfoundry.app.services.esxi_pxe as esxi_pxe
    import labfoundry.app.ui as ui_module
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import EsxiPxeHost, Job

    iso_root = tmp_path / "vcf-depot" / "PROD" / "COMP" / "ESX_HOST"
    monkeypatch.setattr(esxi_pxe, "ESXI_INSTALLER_ISO_ROOT", iso_root)

    login(client)
    page = client.get("/esxi-pxe")
    assert page.status_code == 200
    assert str(iso_root) in page.text
    assert iso_root.is_dir()
    assert 'data-esxi-iso-upload' in page.text
    assert 'data-esxi-iso-upload-progress' in page.text
    assert "Choose an ISO to upload." in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    uploaded = client.post(
        "/esxi-pxe/isos/upload",
        data={"csrf": csrf},
        files={"iso_file": ("VMware-VMvisor-Installer-8.0U3.iso", b"iso bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    assert uploaded.headers["location"] == "/esxi-pxe#esxi-pxe-isos-panel"
    iso_path = iso_root / "VMware-VMvisor-Installer-8.0U3.iso"
    assert iso_path.read_bytes() == b"iso bytes"

    ajax_upload = client.post(
        "/esxi-pxe/isos/upload",
        data={"csrf": csrf},
        files={"iso_file": ("Nested-ESXi.iso", b"ajax iso bytes", "application/octet-stream")},
        headers={"X-LabFoundry-Upload": "1"},
    )
    assert ajax_upload.status_code == 200
    assert ajax_upload.json()["status"] == "uploaded"
    assert ajax_upload.json()["relative_path"] == "Nested-ESXi.iso"

    original_get_settings = ui_module.get_settings
    monkeypatch.setattr(ui_module, "get_settings", lambda: SimpleNamespace(esxi_installer_iso_max_bytes=3))
    too_large = client.post(
        "/esxi-pxe/isos/upload",
        data={"csrf": csrf},
        files={"iso_file": ("Too-Large.iso", b"too large", "application/octet-stream")},
        headers={"X-LabFoundry-Upload": "1"},
    )
    assert too_large.status_code == 413
    assert too_large.json()["status"] == "error"
    assert "too large" in too_large.json()["detail"].lower()
    monkeypatch.setattr(ui_module, "get_settings", original_get_settings)

    vcfdt_iso_path = iso_root / "VCFDT-Downloaded.iso"
    vcfdt_iso_path.write_bytes(b"vcfdt iso bytes")
    refreshed = client.get("/esxi-pxe")
    assert "VMware-VMvisor-Installer-8.0U3.iso" in refreshed.text
    assert "VCFDT-Downloaded.iso" in refreshed.text
    assert "Installer ISOs" in refreshed.text
    assert "Uploaded by user" in refreshed.text
    assert "Downloaded by VCFDT" in refreshed.text
    assert 'id="esxi-pxe-hosts-table"' in refreshed.text
    assert "Default / undefined MACs" in refreshed.text
    assert "host-create-form" not in refreshed.text
    csrf = refreshed.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    vcfdt_delete = client.post(
        "/esxi-pxe/isos/delete",
        data={"csrf": csrf, "installer_iso_path": str(vcfdt_iso_path)},
        follow_redirects=False,
    )
    assert vcfdt_delete.status_code == 303
    assert vcfdt_delete.headers["location"] == "/esxi-pxe#esxi-pxe-isos-panel"
    assert not vcfdt_iso_path.exists()
    host_response = client.post(
        "/esxi-pxe/hosts",
        data={
            "csrf": csrf,
            "hostname": "esxi-iso",
            "mac_address": "00:50:56:11:22:33",
            "installer_iso_path": str(iso_path),
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert host_response.status_code == 303
    host_page = client.get("/esxi-pxe")
    assert host_page.status_code == 200
    assert 'data-hosts=' in host_page.text
    assert "esxi-iso" in host_page.text
    with SessionLocal() as db:
        host = db.execute(select(EsxiPxeHost).where(EsxiPxeHost.hostname == "esxi-iso")).scalar_one()
        assert host.installer_iso_path == str(iso_path)
        host_id = host.id
    delete_response = client.post(
        "/esxi-pxe/isos/delete",
        data={"csrf": csrf, "installer_iso_path": str(iso_path)},
        follow_redirects=False,
    )
    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/esxi-pxe#esxi-pxe-isos-panel"
    assert not iso_path.exists()
    with SessionLocal() as db:
        host = db.get(EsxiPxeHost, host_id)
        assert host.installer_iso_path == ""
    iso_path.write_bytes(b"iso bytes restored")
    host_response = client.post(
        "/esxi-pxe/hosts/" + str(host_id),
        data={
            "csrf": csrf,
            "hostname": "esxi-iso",
            "mac_address": "00:50:56:11:22:33",
            "installer_iso_path": str(iso_path),
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert host_response.status_code == 303

    api_token = create_api_token(client, ["read:esxi-pxe"])
    api_isos = client.get("/api/v1/esxi-pxe/isos", headers={"Authorization": f"Bearer {api_token}"})
    assert api_isos.status_code == 200
    assert {row["relative_path"] for row in api_isos.json()} >= {"VMware-VMvisor-Installer-8.0U3.iso", "Nested-ESXi.iso"}

    apply_page = client.get("/appliance-apply")
    apply_csrf = apply_page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    applied = client.post("/appliance-apply", data={"csrf": apply_csrf, "selected_units": "esxi_pxe"})
    assert applied.status_code == 200
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply").order_by(Job.created_at.desc())).scalars().first()
        payload = json.loads(job.result or "{}")
        manifest = payload["units"][0]["config_preview"]
        manifest_payload = json.loads(manifest)
        assert "VMware-VMvisor-Installer-8.0U3.iso" in manifest
        assert manifest_payload["hosts"][0]["installer_iso_path"] == str(iso_path)


def test_esxi_pxe_default_host_settings_update_existing_rows(client, monkeypatch, tmp_path):
    import json

    from sqlalchemy import select

    import labfoundry.app.services.esxi_pxe as esxi_pxe
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import EsxiKickstart, Setting

    iso_root = tmp_path / "vcf-depot" / "PROD" / "COMP" / "ESX_HOST"
    iso_root.mkdir(parents=True)
    first_iso = iso_root / "First-ESXi.iso"
    second_iso = iso_root / "Second-ESXi.iso"
    first_iso.write_bytes(b"first")
    second_iso.write_bytes(b"second")
    monkeypatch.setattr(esxi_pxe, "ESXI_INSTALLER_ISO_ROOT", iso_root)

    with SessionLocal() as db:
        first_kickstart = EsxiKickstart(name="First", content="install", content_hash=esxi_pxe.content_hash("install"))
        second_kickstart = EsxiKickstart(name="Second", content="install", content_hash=esxi_pxe.content_hash("install"))
        db.add_all([first_kickstart, second_kickstart])
        db.flush()
        first_kickstart_id = first_kickstart.id
        second_kickstart_id = second_kickstart.id
        second_kickstart_hash = second_kickstart.content_hash

        first = esxi_pxe.save_esxi_pxe_default_host_settings(
            db,
            enabled=True,
            kickstart_id=first_kickstart_id,
            installer_iso_path=str(first_iso),
        )
        db.flush()
        second = esxi_pxe.save_esxi_pxe_default_host_settings(
            db,
            enabled=False,
            kickstart_id=second_kickstart_id,
            installer_iso_path=str(second_iso),
        )
        db.flush()

        rows = db.execute(select(Setting).where(Setting.key.like("esxi_pxe.default_host.%"))).scalars().all()
        manifest = json.loads(esxi_pxe.render_esxi_pxe_manifest([], [], default_host=second))

    assert first["enabled"] is True
    assert first["kickstart_id"] == first_kickstart_id
    assert second["enabled"] is False
    assert second["kickstart_id"] == second_kickstart_id
    assert second["installer_iso_path"] == str(second_iso)
    assert manifest["default_host"] == {
        "enabled": False,
        "kickstart_id": second_kickstart_id,
        "kickstart_name": "Second",
        "kickstart_http_path": f"/pxe/esxi/ks/{second_kickstart_hash[:12]}.cfg",
        "installer_iso_path": str(second_iso),
        "installer_iso_name": "Second-ESXi.iso",
    }
    assert len(rows) == 3
    assert {row.key for row in rows} == {
        esxi_pxe.ESXI_PXE_DEFAULT_HOST_ENABLED_KEY,
        esxi_pxe.ESXI_PXE_DEFAULT_HOST_KICKSTART_ID_KEY,
        esxi_pxe.ESXI_PXE_DEFAULT_HOST_INSTALLER_ISO_KEY,
    }


def test_esxi_pxe_default_host_edit_marks_appliance_apply_pending(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import EsxiKickstart
    from labfoundry.app.services import esxi_pxe
    from labfoundry.app.ui import appliance_apply_status, appliance_apply_units, update_appliance_apply_baselines

    login(client)
    page = client.get("/esxi-pxe")
    assert page.status_code == 200
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    with SessionLocal() as db:
        kickstart = EsxiKickstart(name="Baseline ESXi", content="install", content_hash=esxi_pxe.content_hash("install"))
        db.add(kickstart)
        db.flush()
        kickstart_id = kickstart.id
        units = appliance_apply_units(db)
        update_appliance_apply_baselines(db, units, {unit["id"] for unit in units})
        db.commit()
    with SessionLocal() as db:
        assert appliance_apply_status(db, "esxi_pxe")["changed"] is False

    current = client.get("/appliance-apply/status")
    assert current.status_code == 200
    current_pending_count = current.json()["pending_count"]

    response = client.post(
        "/esxi-pxe/default-host",
        data={"csrf": csrf, "enabled": "on", "kickstart_id": str(kickstart_id), "installer_iso_path": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303

    pending = client.get("/appliance-apply/status")
    assert pending.status_code == 200
    assert pending.json()["pending_count"] > current_pending_count
    assert pending.json()["label"] == "Review appliance changes"
    with SessionLocal() as db:
        assert appliance_apply_status(db, "esxi_pxe")["changed"] is True


def test_esxi_kickstart_validation_rejects_duplicate_install_directives(client):
    from labfoundry.app.services.esxi_pxe import kickstart_validation

    content = "\n".join(
        [
            "vmaccepteula",
            "rootpw vmware01!",
            "install --firstdisk --overwritevmfs",
            "install --firstdisk --overwritevmfs --dpupcislots=<PCIeSlotID>",
            "network --bootproto=dhcp --device=vmnic0",
            "reboot",
            "",
        ]
    )

    errors, warnings = kickstart_validation(content, strict=False, max_bytes=8192)

    assert "multiple install/upgrade directives on lines 3, 4; ESXi allows only one." in errors
    assert "missing install or upgrade directive" not in warnings


def test_esxi_kickstart_host_variables_render_from_mac_endpoint(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpScope, EsxiKickstart, EsxiPxeHost
    from labfoundry.app.services.esxi_pxe import (
        assign_kickstart_content,
        canonical_http_path,
        content_hash,
        host_variables_json,
        save_esxi_pxe_boot_settings,
    )

    with SessionLocal() as db:
        scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        scope.ntp_server = "192.168.50.1"
        kickstart = EsxiKickstart(name="Templated ESXi", content="", content_hash="", enabled=True)
        db.add(kickstart)
        db.flush()
        assign_kickstart_content(
            kickstart,
            "install --firstdisk={{custom.disk}}\nnetwork --bootproto=static --ip={{host.ip_address}} --gateway={{dhcp.gateway}} --netmask={{dhcp.netmask}} --hostname={{host.hostname}} --nameserver={{dhcp.dns_servers}}\nntpserver {{dhcp.ntp_servers}}\nrootpw VMware01!\nreboot\n%firstboot\n%end\n",
            max_bytes=262_144,
        )
        kickstart.http_path = canonical_http_path(kickstart.id, kickstart.content_hash)
        host = EsxiPxeHost(
            hostname="esx-vars",
            mac_address="00:50:56:aa:bb:cc",
            ip_address="192.168.50.150",
            kickstart_id=kickstart.id,
            variables_json=host_variables_json({"custom.disk": "mpx.vmhba0:C0:T0:L0"}),
            enabled=True,
        )
        db.add(host)
        save_esxi_pxe_boot_settings(
            db,
            enabled=True,
            hostname="esxi-pxe.labfoundry.internal",
            dhcp_scope_ids=[scope.id],
            listen_interface="eth2",
            listen_address="192.168.50.1",
            tftp_root="/var/lib/labfoundry/pxe/tftp",
            http_port="8080",
            bios_bootfile="undionly.kpxe",
            uefi_bootfile="snponly.efi",
            native_uefi_http_enabled=True,
        )
        db.commit()
        kickstart_file = f"{content_hash(kickstart.content)[:12]}.cfg"

    rendered = client.get(f"/pxe/esxi/ks/{kickstart_file}?mac=01-00-50-56-aa-bb-cc")
    assert rendered.status_code == 200, rendered.text
    assert "install --firstdisk=mpx.vmhba0:C0:T0:L0" in rendered.text
    assert "--ip=192.168.50.150" in rendered.text
    assert "--gateway=192.168.50.1" in rendered.text
    assert "--netmask=255.255.255.0" in rendered.text
    assert "--nameserver=192.168.50.1" in rendered.text
    assert "ntpserver 192.168.50.1" in rendered.text

    assert client.get(f"/pxe/esxi/ks/{kickstart_file}").status_code == 400
    assert client.get(f"/pxe/esxi/ks/{kickstart_file}?mac=not-a-mac").status_code == 400
    assert client.get(f"/pxe/esxi/ks/{kickstart_file}?mac=01-00-50-56-aa-bb-dd").status_code == 404

    with SessionLocal() as db:
        host = db.execute(select(EsxiPxeHost).where(EsxiPxeHost.mac_address == "00:50:56:aa:bb:cc")).scalar_one()
        host.variables_json = json.dumps({})
        db.add(host)
        db.commit()
    unresolved = client.get(f"/pxe/esxi/ks/{kickstart_file}?mac=01-00-50-56-aa-bb-cc")
    assert unresolved.status_code == 400
    assert "custom.disk" in unresolved.text


def test_esxi_pxe_host_variables_api_and_manifest(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import EsxiKickstart, EsxiPxeHost
    from labfoundry.app.services.esxi_pxe import content_hash, render_esxi_pxe_manifest

    token = create_api_token(client, ["read:esxi-pxe", "write:esxi-pxe"])
    created = client.post(
        "/api/v1/esxi-pxe/hosts",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hostname": "api-esx",
            "mac_address": "01-00-50-56-aa-bb-ee",
            "variables": {"rack": "r12", "custom.install_disk": "firstdisk"},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["mac_address"] == "00:50:56:aa:bb:ee"
    assert created.json()["variables"] == {"install_disk": "firstdisk", "rack": "r12"}
    invalid = client.post(
        "/api/v1/esxi-pxe/hosts",
        headers={"Authorization": f"Bearer {token}"},
        json={"hostname": "bad-esx", "mac_address": "00:50:56:aa:bb:ef", "variables": {"host.hostname": "override"}},
    )
    assert invalid.status_code == 400

    with SessionLocal() as db:
        host = db.execute(select(EsxiPxeHost).where(EsxiPxeHost.hostname == "api-esx")).scalar_one()
        assert host.mac_address == "00:50:56:aa:bb:ee"
        assert json.loads(host.variables_json) == {"install_disk": "firstdisk", "rack": "r12"}
        kickstart = EsxiKickstart(name="Vars", content="{{custom.install_disk}}\n", content_hash=content_hash("{{custom.install_disk}}\n"), enabled=True)
        db.add(kickstart)
        db.flush()
        host.kickstart_id = kickstart.id
        db.add(host)
        manifest = json.loads(render_esxi_pxe_manifest([kickstart], [host]))
    assert manifest["hosts"][0]["variables"] == {"install_disk": "firstdisk", "rack": "r12"}


def test_esxi_pxe_boot_settings_update_dnsmasq_and_apply_manifest(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpScope, DhcpSettings, DnsRecord
    from labfoundry.app.services.esxi_pxe import esxi_pxe_boot_settings
    from labfoundry.app.ui import dnsmasq_context, esxi_pxe_context

    login(client)
    page = client.get("/esxi-pxe")
    assert page.status_code == 200
    assert "Boot Service" in page.text
    assert "Hostname" in page.text
    assert "DHCP IP Zone" in page.text
    assert "Listen interfaces" not in page.text
    assert "Listen addresses" not in page.text
    assert 'type="hidden" name="tftp_root"' in page.text
    assert 'type="hidden" name="bios_bootfile"' in page.text
    assert 'type="hidden" name="uefi_bootfile"' in page.text
    assert 'field-label"><span>TFTP root' not in page.text
    assert 'field-label"><span>BIOS bootfile' not in page.text
    assert 'field-label"><span>UEFI bootfile' not in page.text
    assert "<span>BIOS bootfile</span><strong>undionly.kpxe</strong>" in page.text
    assert "<span>UEFI bootfile</span><strong>snponly.efi</strong>" in page.text
    assert "PXE HTTP port" in page.text
    assert "HTTP endpoint" in page.text
    assert "Kickstart variables" in page.text
    assert "{{host.hostname}}" in page.text
    assert "{{dhcp.ntp_servers}}" in page.text
    assert "{{custom.install_disk}}" in page.text
    assert 'class="left-stack"' in page.text
    assert page.text.index("<h2>Boot Service</h2>") < page.text.index("<h2>ESXi Kickstarts</h2>")
    css = client.get("/static/app.css").text
    assert ".esxi-pxe-workspace .esxi-boot-service-panel" in css
    assert ".esxi-pxe-workspace > .side-stack" in css
    assert "grid-column: 2;" in css
    assert ".generated-options-panel" in css
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    with SessionLocal() as db:
        pxe_scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        pxe_scope_id = str(pxe_scope.id)

    response = client.post(
        "/esxi-pxe/boot-settings",
        data={
            "csrf": csrf,
            "enabled": "on",
            "hostname": "esxi-pxe.labfoundry.internal",
            "dhcp_scope_id": pxe_scope_id,
            "listen_addresses_present": "1",
            "listen_interfaces_present": "1",
            "tftp_root": "/var/lib/labfoundry/pxe/tftp",
            "http_port": "8080",
            "bios_bootfile": "undionly.kpxe",
            "uefi_bootfile": "snponly.efi",
            "native_uefi_http_enabled": "on",
            "native_uefi_http_url": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        boot = esxi_pxe_boot_settings(db)
        assert boot["enabled"] is True
        assert boot["hostname"] == "esxi-pxe.labfoundry.internal"
        assert boot["dhcp_scope_id"] == int(pxe_scope_id)
        assert boot["dhcp_scope_name"] == "SiteA"
        assert boot["listen_interface"] == "eth2"
        assert boot["listen_address"] == "192.168.50.1"
        assert boot["http_port"] == 8080
        assert boot["effective_native_uefi_http_url"] == "http://192.168.50.1:8080/pxe/esxi/mboot.efi"
        assert boot["native_uefi_http_enabled"] is True
        record = db.execute(select(DnsRecord).where(DnsRecord.hostname == "esxi-pxe.labfoundry.internal")).scalar_one()
        assert record.address == "192.168.50.1"
        dhcp = db.execute(select(DhcpSettings)).scalar_one()
        dhcp.enabled = True
        db.add(dhcp)
        db.commit()
        dns_preview = dnsmasq_context(db)["config_preview"]
        assert "enable-tftp" in dns_preview
        assert "dhcp-option=tag:sitea,66,esxi-pxe.labfoundry.internal" in dns_preview
        assert "dhcp-boot=tag:sitea,tag:ipxe,tag:efi-x86_64,mboot.efi,esxi-pxe.labfoundry.internal,192.168.50.1" in dns_preview
        assert "dhcp-boot=tag:sitea,tag:ipxe,tag:!efi-x86_64,pxelinux.0,esxi-pxe.labfoundry.internal,192.168.50.1" in dns_preview
        assert "dhcp-boot=tag:sitea,tag:!ipxe,tag:efi-x86_64,snponly.efi,esxi-pxe.labfoundry.internal,192.168.50.1" in dns_preview
        assert "dhcp-boot=tag:sitea,tag:!ipxe,tag:!efi-x86_64,undionly.kpxe,esxi-pxe.labfoundry.internal,192.168.50.1" in dns_preview
        assert "dhcp-boot=tag:sitea,tag:uefi-http,tag:uefi-http-x64,http://192.168.50.1:8080/pxe/esxi/mboot.efi" in dns_preview
        manifest = json.loads(esxi_pxe_context(db)["esxi_pxe_manifest"])
        assert manifest["schema_version"] == 2
        assert manifest["boot"]["enabled"] is True
        assert manifest["boot"]["hostname"] == "esxi-pxe.labfoundry.internal"
        assert manifest["boot"]["dhcp_scope_id"] == int(pxe_scope_id)
        assert manifest["boot"]["http_port"] == 8080
        assert manifest["boot"]["bios_second_stage_bootfile"] == "pxelinux.0"
    dhcp_page = client.get("/dhcp")
    assert dhcp_page.status_code == 200
    assert dhcp_page.text.index("Desired State") < dhcp_page.text.index("Generated PXE") < dhcp_page.text.index("Actual Leases")
    assert 'id="dhcp-generated-pxe" class="tab-panel" role="tabpanel" hidden' in dhcp_page.text
    assert "Generated PXE Boot Options" in dhcp_page.text
    assert "SiteA" in dhcp_page.text
    assert "dhcp-userclass=set:ipxe,iPXE" in dhcp_page.text
    assert "dhcp-match=set:ipxe,175" in dhcp_page.text
    assert "dhcp-boot=tag:sitea,tag:!ipxe,tag:!efi-x86_64,undionly.kpxe,esxi-pxe.labfoundry.internal,192.168.50.1" in dhcp_page.text
    assert "dhcp-boot=tag:sitea,tag:ipxe,tag:efi-x86_64,mboot.efi,esxi-pxe.labfoundry.internal,192.168.50.1" in dhcp_page.text
    assert "dhcp-boot=tag:sitea,tag:!ipxe,tag:efi-x86_64,snponly.efi,esxi-pxe.labfoundry.internal,192.168.50.1" in dhcp_page.text
    assert "dhcp-boot=tag:sitea,tag:uefi-http,tag:uefi-http-x64,http://192.168.50.1:8080/pxe/esxi/mboot.efi" in dhcp_page.text
def test_esxi_pxe_multi_zone_host_reservations_and_grid_menu(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpReservation, DhcpScope, DhcpSettings, DnsRecord
    from labfoundry.app.services.esxi_pxe import esxi_pxe_boot_settings
    from labfoundry.app.ui import dnsmasq_context, esxi_pxe_context

    login(client)
    page = client.get("/esxi-pxe")
    assert page.status_code == 200
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    with SessionLocal() as db:
        sitea = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        siteb = DhcpScope(
            name="SiteB",
            interface_name="eth3",
            site_address="10.1.1.1",
            prefix_length=24,
            range_start="10.1.1.100",
            range_end="10.1.1.200",
            lease_time="12h",
            domain_name="labfoundry.internal",
            dns_server="10.1.1.1",
            ntp_server="10.1.1.1",
            enabled=True,
        )
        db.add(siteb)
        db.commit()
        sitea_id = sitea.id
        siteb_id = siteb.id

    response = client.post(
        "/esxi-pxe/boot-settings",
        data={
            "csrf": csrf,
            "enabled": "on",
            "hostname": "esxi-pxe.labfoundry.internal",
            "dhcp_scope_ids": [str(sitea_id), str(siteb_id)],
            "listen_addresses_present": "1",
            "listen_interfaces_present": "1",
            "tftp_root": "/var/lib/labfoundry/pxe/tftp",
            "http_port": "8080",
            "bios_bootfile": "undionly.kpxe",
            "uefi_bootfile": "snponly.efi",
            "native_uefi_http_enabled": "on",
            "native_uefi_http_url": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        boot = esxi_pxe_boot_settings(db)
        assert boot["dhcp_scope_id"] == sitea_id
        assert boot["dhcp_scope_ids"] == [sitea_id, siteb_id]
        assert boot["dhcp_scope_names"] == ["SiteA", "SiteB"]
        assert boot["listen_interface"] == "eth2\neth3"
        assert boot["listen_address"] == "192.168.50.1\n10.1.1.1"
        assert boot["http_base_url"] == "http://192.168.50.1:8080/pxe/esxi"
        manifest = json.loads(esxi_pxe_context(db)["esxi_pxe_manifest"])
        assert manifest["boot"]["dhcp_scope_id"] == sitea_id
        assert manifest["boot"]["dhcp_scope_ids"] == [sitea_id, siteb_id]
        dhcp = db.execute(select(DhcpSettings)).scalar_one()
        dhcp.enabled = True
        db.add(dhcp)
        db.commit()
        dns_preview = dnsmasq_context(db)["config_preview"]
        assert "dhcp-option=tag:sitea,66,esxi-pxe.labfoundry.internal" in dns_preview
        assert "dhcp-option=tag:siteb,66,esxi-pxe.labfoundry.internal" in dns_preview
        assert "dhcp-boot=tag:sitea,tag:uefi-http,tag:uefi-http-x64,http://192.168.50.1:8080/pxe/esxi/mboot.efi" in dns_preview
        assert "dhcp-boot=tag:siteb,tag:uefi-http,tag:uefi-http-x64,http://10.1.1.1:8080/pxe/esxi/mboot.efi" in dns_preview

    create_host = client.post(
        "/esxi-pxe/hosts",
        data={
            "csrf": csrf,
            "hostname": "esx02",
            "mac_address": "01-00-50-56-aa-bb-cd",
            "ip_address": "10.1.1.150",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert create_host.status_code == 303, create_host.text

    with SessionLocal() as db:
        reservation = db.execute(select(DhcpReservation).where(DhcpReservation.mac_address == "00:50:56:aa:bb:cd")).scalar_one()
        assert reservation.hostname == "esx02.labfoundry.internal"
        assert reservation.ip_address == "10.1.1.150"
        assert reservation.description == "Managed by ESXi PXE host 1."
        record = db.execute(select(DnsRecord).where(DnsRecord.hostname == "esx02.labfoundry.internal")).scalar_one()
        assert record.record_type == "A"
        assert record.address == "10.1.1.150"
        assert record.description == "Managed by ESXi PXE host 1."

    out_of_zone = client.post(
        "/esxi-pxe/hosts/1",
        data={
            "csrf": csrf,
            "hostname": "esx02",
            "mac_address": "01-00-50-56-aa-bb-cd",
            "ip_address": "172.16.1.50",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert out_of_zone.status_code == 400
    assert "inside a selected ESXi PXE DHCP zone" in out_of_zone.text

    remove_reservation = client.post(
        "/esxi-pxe/hosts/1",
        data={
            "csrf": csrf,
            "hostname": "esx02",
            "mac_address": "00:50:56:aa:bb:cd",
            "ip_address": "",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert remove_reservation.status_code == 303
    with SessionLocal() as db:
        assert db.execute(select(DhcpReservation).where(DhcpReservation.mac_address == "00:50:56:aa:bb:cd")).scalar_one_or_none() is None
        assert db.execute(select(DnsRecord).where(DnsRecord.hostname == "esx02.labfoundry.internal")).scalar_one_or_none() is None

    refreshed = client.get("/esxi-pxe")
    assert 'data-tag-name="dhcp_scope_ids"' in refreshed.text
    assert "SiteB - eth3 / 10.1.1.1/24" in refreshed.text
    app_js = client.get("/static/app.js").text
    host_grid_js = app_js.split("function initializeEsxiPxeHostsTable()", 1)[1].split("function initializeVcfBackupSettings()", 1)[0]
    assert "rowContextMenu" in host_grid_js
    assert "Delete host reference" in host_grid_js
    assert 'field: "ip_address"' in host_grid_js
    assert 'field: "variables_json"' in host_grid_js
    assert "<button" not in host_grid_js


def test_esxi_pxe_boot_settings_migrate_legacy_first_stage_defaults(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Setting
    from labfoundry.app.services.esxi_pxe import esxi_pxe_boot_settings

    login(client)
    with SessionLocal() as db:
        db.add(Setting(key="esxi_pxe.boot.bios_bootfile", value="pxelinux.0"))
        db.add(Setting(key="esxi_pxe.boot.uefi_bootfile", value="bootx64.efi"))
        db.commit()

    with SessionLocal() as db:
        boot = esxi_pxe_boot_settings(db)
        assert boot["bios_bootfile"] == "undionly.kpxe"
        assert boot["uefi_bootfile"] == "snponly.efi"
        saved_bios = db.execute(select(Setting).where(Setting.key == "esxi_pxe.boot.bios_bootfile")).scalar_one()
        assert saved_bios.value == "pxelinux.0"


def test_esxi_kickstarts_round_trip_in_settings_archive(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import EsxiKickstart, EsxiPxeHost

    login(client)
    with SessionLocal() as db:
        kickstart = EsxiKickstart(
            name="Archive ESXi",
            content="install\nnetwork --bootproto=dhcp\nrootpw ArchiveSecret\nreboot\n%firstboot\n%end\n",
            content_hash="",
            rendered_content="install\nnetwork --bootproto=dhcp\nrootpw ArchiveSecret\nreboot\n%firstboot\n%end\n",
            enabled=True,
        )
        db.add(kickstart)
        db.flush()
        from labfoundry.app.services.esxi_pxe import assign_kickstart_content, canonical_http_path

        assign_kickstart_content(kickstart, kickstart.content, max_bytes=262_144)
        kickstart.http_path = canonical_http_path(kickstart.id, kickstart.content_hash)
        db.add(
            EsxiPxeHost(
                hostname="esxi-archive",
                mac_address="00:50:56:aa:bb:cc",
                ip_address="192.168.50.150",
                kickstart_id=kickstart.id,
                installer_iso_path="/mnt/labfoundry-vcf-offline-depot/PROD/COMP/ESX_HOST/archive.iso",
                variables_json='{"rack":"r42"}',
                enabled=True,
            )
        )
        db.commit()

    page = client.get("/backup-restore")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    exported = client.post("/backup-restore/export", data={"csrf": csrf})
    payload = json.loads(exported.content)

    assert payload["data"]["esxi_kickstarts"][0]["name"] == "Archive ESXi"
    assert payload["data"]["esxi_pxe_hosts"][0]["kickstart_name"] == "Archive ESXi"
    assert payload["data"]["esxi_pxe_hosts"][0]["ip_address"] == "192.168.50.150"
    assert payload["data"]["esxi_pxe_hosts"][0]["installer_iso_path"].endswith("/archive.iso")
    assert payload["data"]["esxi_pxe_hosts"][0]["variables"] == {"rack": "r42"}

    with SessionLocal() as db:
        db.query(EsxiPxeHost).delete()
        db.query(EsxiKickstart).delete()
        db.commit()

    restored = client.post(
        "/backup-restore/restore",
        data={"csrf": csrf},
        files={"archive_file": ("labfoundry-settings.json", exported.content, "application/json")},
    )

    assert restored.status_code == 200
    with SessionLocal() as db:
        restored_kickstart = db.execute(select(EsxiKickstart).where(EsxiKickstart.name == "Archive ESXi")).scalar_one()
        restored_host = db.execute(select(EsxiPxeHost).where(EsxiPxeHost.hostname == "esxi-archive")).scalar_one()
        assert restored_host.kickstart_id == restored_kickstart.id
        assert restored_host.ip_address == "192.168.50.150"
        assert restored_host.installer_iso_path.endswith("/archive.iso")
        assert restored_host.variables_json == '{"rack": "r42"}'


def test_esxi_pxe_drift_detection_uses_generated_filesystem_copy(client, monkeypatch, tmp_path):
    from sqlalchemy import select

    import labfoundry.app.services.esxi_pxe as esxi_pxe
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import EsxiKickstart

    monkeypatch.setattr(esxi_pxe, "ESXI_KICKSTART_HTTP_ROOT", tmp_path)
    login(client)
    content = "install\nnetwork --bootproto=dhcp\nrootpw DriftSecret\nreboot\n%firstboot\n%end\n"
    with SessionLocal() as db:
        kickstart = EsxiKickstart(name="Drift ESXi", content=content, content_hash=esxi_pxe.content_hash(content), rendered_content=content, rendered_hash=esxi_pxe.content_hash(content), enabled=True)
        db.add(kickstart)
        db.flush()
        kickstart.http_path = esxi_pxe.canonical_http_path(kickstart.id, kickstart.content_hash)
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"{kickstart.content_hash[:12]}.cfg").write_text(content.replace("DriftSecret", "ChangedOnDisk"), encoding="utf-8")
        db.commit()
        kickstart_id = kickstart.id

    page = client.get(f"/esxi-pxe?kickstart_id={kickstart_id}")
    assert page.status_code == 200
    assert "filesystem modified" in page.text
    assert "Filesystem copy differs from database source. The next ESXi PXE apply will overwrite the filesystem copy from the database." in page.text


def test_backup_restore_restore_replaces_settings_and_stops_services(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ApplianceSettings, AuditEvent, ServiceState

    login(client)
    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        settings.fqdn = "restore-target.labfoundry.internal"
        service = db.execute(select(ServiceState).where(ServiceState.service == "dns")).scalar_one()
        service.running = True
        service.enabled = True
        service.health = "healthy"
        db.commit()

    page = client.get("/backup-restore")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    exported = client.post("/backup-restore/export", data={"csrf": csrf})
    archive_bytes = exported.content

    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        settings.fqdn = "temporary-change.labfoundry.internal"
        db.commit()

    restored = client.post(
        "/backup-restore/restore",
        data={"csrf": csrf},
        files={"archive_file": ("labfoundry-settings.json", archive_bytes, "application/json")},
    )

    assert restored.status_code == 200
    assert "Settings restored" in restored.text
    assert "Services are stopped and unconfigured" in restored.text
    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        assert settings.fqdn == "restore-target.labfoundry.internal"
        services = db.execute(select(ServiceState)).scalars().all()
        assert services
        assert all(not service.running and not service.enabled and service.health == "unconfigured" for service in services)
        event = db.execute(select(AuditEvent).where(AuditEvent.action == "restore_settings_backup")).scalar_one()
        assert "services forced stopped/unconfigured" in (event.detail or "")
    payload = json.loads(archive_bytes)
    assert payload["data"]["service_states"]


def test_backup_restore_factory_reset_resets_desired_state_and_stops_services(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import (
        ApplianceSettings,
        AuditEvent,
        CaCertificate,
        CaProfile,
        DhcpReservation,
        DhcpSettings,
        DhcpScope,
        DnsRecord,
        DnsSettings,
        FirewallRule,
        KmsClient,
        KmsKey,
        KmsSettings,
        NatRule,
        PhysicalInterface,
        Route,
        ServiceState,
        Setting,
        VcfBackupSettings,
        VcfDepotDownloadProfile,
        VcfOfflineDepotSettings,
        VcfPrivateRegistrySettings,
        VlanInterface,
        WanPolicy,
    )
    from labfoundry.app.seed import SEED_EXAMPLES_SETTING_KEY, seed_initial_data

    login(client)
    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        settings.fqdn = "custom.labfoundry.internal"
        db.add(DnsRecord(hostname="remove-me.labfoundry.internal", record_type="A", address="192.168.50.250"))
        service = db.execute(select(ServiceState).where(ServiceState.service == "vcf-backups")).scalar_one()
        service.running = True
        service.enabled = True
        service.health = "healthy"
        db.commit()

    page = client.get("/backup-restore")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    reset = client.post("/backup-restore/factory-reset", data={"csrf": csrf})

    assert reset.status_code == 200
    assert "Factory reset complete" in reset.text
    assert "without demo resources" in reset.text
    assert "Non-management NICs are desired admin down" in reset.text
    with SessionLocal() as db:
        settings = db.execute(select(ApplianceSettings)).scalar_one()
        assert settings.fqdn == "labfoundry.labfoundry.internal"
        interfaces = db.execute(select(PhysicalInterface).order_by(PhysicalInterface.name)).scalars().all()
        assert [interface.name for interface in interfaces] == ["eth0"]
        assert interfaces[0].role == "management"
        assert interfaces[0].admin_state == "up"
        assert interfaces[0].ip_cidr == "192.168.49.1/24"
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        assert dns_settings.listen_interface == ""
        assert dns_settings.listen_address in ("", None)
        dhcp_settings = db.execute(select(DhcpSettings)).scalar_one()
        assert dhcp_settings.interface_name == ""
        assert dhcp_settings.site_address == ""
        kms_settings = db.execute(select(KmsSettings)).scalar_one()
        assert kms_settings.listen_interface == ""
        assert kms_settings.listen_address == ""
        vcf_backup_settings = db.execute(select(VcfBackupSettings)).scalar_one()
        assert vcf_backup_settings.listen_interface == ""
        assert vcf_backup_settings.listen_address == ""
        vcf_depot_settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        assert vcf_depot_settings.listen_interface == ""
        assert vcf_depot_settings.listen_address == ""
        vcf_registry_settings = db.execute(select(VcfPrivateRegistrySettings)).scalar_one()
        assert vcf_registry_settings.listen_interface == ""
        assert vcf_registry_settings.listen_address == ""
        removed = db.execute(select(DnsRecord).where(DnsRecord.hostname == "remove-me.labfoundry.internal")).scalar_one_or_none()
        assert removed is None
        assert db.execute(select(VlanInterface)).scalars().all() == []
        assert db.execute(select(WanPolicy)).scalars().all() == []
        assert db.execute(select(NatRule)).scalars().all() == []
        assert db.execute(select(Route)).scalars().all() == []
        dns_records = db.execute(select(DnsRecord)).scalars().all()
        assert len(dns_records) == 1
        assert dns_records[0].hostname == "labfoundry.labfoundry.internal"
        assert dns_records[0].record_type == "A"
        assert dns_records[0].address == "192.168.49.1"
        assert "app-owned appliance FQDN" in (dns_records[0].description or "")
        assert db.execute(select(DhcpScope)).scalars().all() == []
        assert db.execute(select(DhcpReservation)).scalars().all() == []
        assert db.execute(select(FirewallRule)).scalars().all() == []
        assert db.execute(select(CaProfile)).scalars().all() == []
        assert db.execute(select(CaCertificate)).scalars().all() == []
        assert db.execute(select(KmsClient)).scalars().all() == []
        assert db.execute(select(KmsKey)).scalars().all() == []
        assert db.execute(select(VcfDepotDownloadProfile)).scalars().all() == []
        marker = db.execute(select(Setting).where(Setting.key == SEED_EXAMPLES_SETTING_KEY)).scalar_one()
        assert marker.value == "false"
        seed_initial_data(db)
        assert db.execute(select(VlanInterface)).scalars().all() == []
        dns_records = db.execute(select(DnsRecord)).scalars().all()
        assert len(dns_records) == 1
        assert dns_records[0].hostname == "labfoundry.labfoundry.internal"
        assert db.execute(select(VcfDepotDownloadProfile)).scalars().all() == []
        services = db.execute(select(ServiceState)).scalars().all()
        assert services
        assert all(not service.running and not service.enabled and service.health == "unconfigured" for service in services)
        event = db.execute(select(AuditEvent).where(AuditEvent.action == "factory_reset_settings")).scalar_one()
        assert "services forced stopped/unconfigured" in (event.detail or "")


def test_routes_wan_policy_form_renders(client):
    login(client)
    response = client.get("/routes-wan")
    assert response.status_code == 200
    assert "Routes &amp; WAN Simulation" in response.text
    assert "Managed Routes" in response.text
    assert "NAT Rules" in response.text
    assert "WAN Policies" in response.text
    assert "Routes &amp; WAN Simulation has pending appliance changes" in response.text
    assert "Validation" in response.text
    assert "routes-wan-routes-table" in response.text
    assert "routes-wan-nat-table" in response.text
    assert "routes-wan-policies-table" in response.text
    assert "data-mode-options" not in response.text
    assert "<th>Mode</th>" not in response.text
    assert "+ Add route here" in client.get("/static/app.js").text
    assert "+ Add NAT rule here" in client.get("/static/app.js").text
    assert "+ Add policy here" in client.get("/static/app.js").text
    assert "Europe WAN" in response.text
    assert "SiteA outbound WAN" in response.text
    assert "eth1.20" in response.text
    assert "tc qdisc replace" in response.text
    assert "table ip labfoundry_nat" in response.text
    assert "Review appliance changes" in response.text


def test_routes_wan_rejects_route_wan_mode(client):
    login(client)
    page = client.get("/routes-wan")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        "/routes-wan/routes",
        data={
            "destination_cidr": "10.21.0.0/24",
            "gateway": "",
            "interface_name": "eth1.20",
            "metric": "120",
            "wan_policy_id": "",
            "wan_mode": "route",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "planned but not supported in v1" in response.text


def test_routes_wan_allows_ipv6_only_route_targets_but_not_nat_targets(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import NatRule, PhysicalInterface, Route

    with SessionLocal() as db:
        db.add(
            PhysicalInterface(
                name="eth6",
                mac_address="00:50:56:aa:bb:66",
                mode="access",
                role="services",
                ip_cidr="",
                ipv6_cidr="fd00:66::1/64",
                admin_state="up",
                oper_state="up",
            )
        )
        db.commit()

    login(client)
    page = client.get("/routes-wan")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    route_response = client.post(
        "/routes-wan/routes",
        data={
            "destination_cidr": "2001:db8:66::/64",
            "gateway": "",
            "interface_name": "eth6",
            "metric": "120",
            "wan_policy_id": "",
            "wan_mode": "interface",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    nat_response = client.post(
        "/routes-wan/nat-rules",
        data={
            "name": "IPv6-only outbound",
            "source": "192.168.50.0/24",
            "outbound_interface": "eth6",
            "masquerade": "on",
            "priority": "110",
            "description": "",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert route_response.status_code == 303
    assert nat_response.status_code == 422
    assert "Choose an access physical interface" in nat_response.text
    with SessionLocal() as db:
        route = db.execute(select(Route).where(Route.interface_name == "eth6")).scalar_one()
        assert route.destination_cidr == "2001:db8:66::/64"
        assert db.execute(select(NatRule).where(NatRule.outbound_interface == "eth6")).scalar_one_or_none() is None


def test_routes_wan_autosave_endpoints_and_apply_task(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, NatRule, WanPolicy

    login(client)
    page = client.get("/routes-wan")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    policy_response = client.post(
        "/routes-wan/policies",
        data={
            "name": "Metro WAN",
            "description": "short metro impairment",
            "latency_ms": "35",
            "jitter_ms": "5",
            "packet_loss_percent": "0.1",
            "bandwidth_mbit": "250",
            "corrupt_percent": "0",
            "duplicate_percent": "0",
            "reorder_percent": "0",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert policy_response.status_code == 303
    with SessionLocal() as db:
        policy = db.execute(select(WanPolicy).where(WanPolicy.name == "Metro WAN")).scalar_one()
        policy_id = str(policy.id)

    route_response = client.post(
        "/routes-wan/routes",
        data={
            "destination_cidr": "10.20.0.0/24",
            "gateway": "",
            "interface_name": "eth1.20",
            "metric": "120",
            "wan_policy_id": policy_id,
            "wan_mode": "interface",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert route_response.status_code == 303
    nat_response = client.post(
        "/routes-wan/nat-rules",
        data={
            "name": "Metro outbound",
            "source": "192.168.50.0/24",
            "outbound_interface": "eth2",
            "masquerade": "on",
            "priority": "110",
            "description": "NAT through test WAN",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert nat_response.status_code == 303
    refreshed = client.get("/routes-wan")
    assert "Metro WAN" in refreshed.text
    assert "Metro outbound" in refreshed.text
    assert "10.20.0.0/24" in refreshed.text
    assert "ip saddr 192.168.50.0/24 oifname &#34;eth2&#34; masquerade" in refreshed.text
    assert "tc qdisc replace dev eth1.20" in refreshed.text
    with SessionLocal() as db:
        rule = db.execute(select(NatRule).where(NatRule.name == "Metro outbound")).scalar_one()
        assert rule.outbound_interface == "eth2"

    apply_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "wan"})
    assert apply_response.status_code == 200
    assert "Appliance apply task succeeded" in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "wan" in (job.result or "")
        assert "NAT rules" in (job.result or "")
        assert "nft -f /etc/labfoundry/nftables.d/labfoundry-nat.nft" in (job.result or "")
        assert "tc qdisc replace dev eth1.20" in (job.result or "")


def test_api_token_create_and_revoke_ui(client):
    login(client)
    page = client.get("/authentication")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/authentication/api-tokens",
        data={"name": "UI token", "description": "test", "scopes": "read:dashboard", "csrf": csrf},
    )
    assert created.status_code == 200
    assert "Copy this bearer token now" in created.text
    assert "UI token" in created.text


def test_local_users_page_separates_ldap_authentication(client):
    login(client)
    authentication = client.get("/authentication")
    assert authentication.status_code == 200
    assert "LDAP provider" in authentication.text
    assert "managed separately" in authentication.text

    legacy = client.get("/ldap-users", follow_redirects=False)
    assert legacy.status_code == 303
    assert legacy.headers["location"] == "/authentication"

    users = client.get("/users")
    assert users.status_code == 200
    assert "Local Users" in users.text
    assert "LDAP is an authentication provider" in users.text
    assert "users-table" in users.text
    assert "user-password-modal" in users.text
    assert "data-password-toggle" in users.text
    assert "Password Reset" not in users.text
    assert "Reset password" in users.text
    assert "Remove" in users.text
    assert "Password Policy" in users.text
    assert "Local Users has pending appliance changes" in users.text
    assert "Photon OS" in users.text
    assert "OS account" in users.text
    assert "Shell" in users.text
    assert "Temp Password" not in users.text
    assert "admin" in users.text
    assert "vcf-backup" in users.text
    csrf = users.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/users",
        data={"username": "operator", "role": "viewer", "shell": "/bin/bash", "csrf": csrf},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "operator" in created.text
    assert "/bin/bash" in created.text
    assert "disabled" in created.text
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "userActionsFormatter" not in app_js.text
    assert "formatter: userActionsFormatter" not in app_js.text
    assert "openUserPasswordModal" in app_js.text
    assert "deleteUserFromMenu" in app_js.text
    assert "Unlock OS account" in app_js.text
    assert "disableUserFromMenu" in app_js.text
    assert "Disable user" in app_js.text
    users_table_js = app_js.text.split("function initializeUsersTable()", 1)[1].split("function initializeUserPasswordForm()", 1)[0]
    enabled_column_js = users_table_js.split('title: "Enabled"', 1)[1].split('title: "OS account"', 1)[0]
    assert "editor:" not in enabled_column_js
    assert "validatePasswordMatch" in app_js.text
    assert "initializeNonTabbableHelperControls" in app_js.text
    assert '".help-icon, .password-toggle"' in app_js.text
    assert 'control.setAttribute("tabindex", "-1")' in app_js.text
    assert 'field: "shell"' in app_js.text
    assert "Temp Password" not in app_js.text


def test_local_user_reset_modal_endpoint_and_remove(client):
    import html
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import User

    login(client)
    users = client.get("/users")
    csrf = users.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/users",
        data={"username": "remove-me", "role": "viewer", "csrf": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303

    users = client.get("/users")
    payload = users.text.split("data-users='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(payload))
    user_id = next(row["id"] for row in rows if row["username"] == "remove-me")
    csrf = users.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    reset = client.post(
        f"/users/{user_id}/password",
        data={"password": "New-temporary1!", "confirm_password": "New-temporary1!", "csrf": csrf},
        follow_redirects=False,
    )
    assert reset.status_code in {200, 303}

    with SessionLocal() as db:
        enabled_user = db.execute(select(User).where(User.username == "remove-me")).scalar_one()
        assert enabled_user.enabled is True

    disabled = client.post(f"/users/{user_id}/disable", data={"csrf": csrf})
    assert disabled.status_code == 200
    with SessionLocal() as db:
        disabled_user = db.execute(select(User).where(User.username == "remove-me")).scalar_one()
        assert disabled_user.enabled is False
        assert disabled_user.os_sync_status == "pending"

    reset = client.post(
        f"/users/{user_id}/password",
        data={"password": "New-temporary1!", "confirm_password": "New-temporary1!", "csrf": csrf},
        follow_redirects=False,
    )
    assert reset.status_code in {200, 303}

    unlock = client.post(f"/users/{user_id}/unlock", data={"csrf": csrf})
    assert unlock.status_code == 200
    with SessionLocal() as db:
        staged_user = db.execute(select(User).where(User.username == "remove-me")).scalar_one()
        assert staged_user.os_unlock_requested_at is not None
        assert staged_user.os_sync_status == "pending"
    apply_page = client.get("/appliance-apply")
    assert "1 unlock requests" in apply_page.text

    deleted = client.post(f"/users/{user_id}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert deleted.status_code == 303
    refreshed = client.get("/users")
    assert "remove-me" not in refreshed.text


def test_local_users_password_policy_staging_and_apply_redaction(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, User

    login(client)
    users = client.get("/users")
    csrf = users.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    policy = client.post(
        "/users/password-policy",
        data={
            "csrf": csrf,
            "min_length": "14",
            "require_uppercase": "on",
            "require_lowercase": "on",
            "require_number": "on",
            "require_special": "on",
            "disallow_username": "on",
        },
    )
    assert policy.status_code == 200
    assert policy.json()["policy"]["min_length"] == 14

    created = client.post(
        "/users",
        data={"username": "sync-me", "role": "viewer", "csrf": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303
    users = client.get("/users")
    import html
    import json

    rows = json.loads(html.unescape(users.text.split("data-users='", 1)[1].split("'", 1)[0]))
    user_id = next(row["id"] for row in rows if row["username"] == "sync-me")

    weak = client.post(
        f"/users/{user_id}/password",
        data={"password": "short", "confirm_password": "short", "csrf": csrf},
    )
    assert weak.status_code == 400
    assert "Password must be at least 14 characters" in weak.text

    plaintext = "BridgeStrong1!"
    reset = client.post(
        f"/users/{user_id}/password",
        data={"password": plaintext, "confirm_password": plaintext, "csrf": csrf},
        follow_redirects=False,
    )
    assert reset.status_code == 303

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "sync-me")).scalar_one()
        assert not hasattr(user, "pending_os_password_encrypted")
        assert not hasattr(user, "password_hash")
        assert user.shell == "/sbin/nologin"
        assert user.enabled is True

    apply_page = client.get("/appliance-apply")
    assert apply_page.status_code == 200
    assert 'value="local_users"' in apply_page.text
    assert "Local Users" in apply_page.text
    assert "pending OS passwords" in apply_page.text
    assert plaintext not in apply_page.text

    csrf = apply_page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    applied = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "local_users"})
    assert applied.status_code == 200
    assert plaintext not in applied.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply").order_by(Job.created_at.desc())).scalars().first()
        assert job is not None
        assert "local-users" in (job.result or "")
        assert plaintext not in (job.result or "")
        user = db.execute(select(User).where(User.username == "sync-me")).scalar_one()
        assert not hasattr(user, "pending_os_password_encrypted")


def test_real_local_users_apply_clears_pending_passwords_and_baselines_post_apply(client, monkeypatch, tmp_path):
    from sqlalchemy import select

    import labfoundry.app.ui as ui_module
    from labfoundry.app.adapters.system import AdapterResult
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Setting, User

    class SuccessfulLocalUsersAdapter:
        dry_run = False

        def read_dhcp_leases(self) -> AdapterResult:
            return AdapterResult(command=["labfoundry-helper", "dnsmasq", "leases"], dry_run=True, stdout="")

        def validate_local_users_config(self, config_path: str) -> AdapterResult:
            return AdapterResult(command=["labfoundry-helper", "local-users", "validate", config_path], dry_run=False, stdout="validation ok")

        def apply_local_users_config(self, config_path: str) -> AdapterResult:
            return AdapterResult(command=["labfoundry-helper", "local-users", "apply", config_path], dry_run=False, stdout="apply complete")

    staged_path = tmp_path / "apply" / "local-users" / "labfoundry-users.json"
    monkeypatch.setattr(ui_module, "LOCAL_USERS_STAGED_CONFIG_PATH", str(staged_path))
    monkeypatch.setattr(ui_module, "SystemAdapter", SuccessfulLocalUsersAdapter)

    login(client)
    users = client.get("/users")
    csrf = users.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/users",
        data={"username": "real-sync", "role": "viewer", "csrf": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303
    users = client.get("/users")
    import html
    import json

    rows = json.loads(html.unescape(users.text.split("data-users='", 1)[1].split("'", 1)[0]))
    user_id = next(row["id"] for row in rows if row["username"] == "real-sync")
    reset = client.post(
        f"/users/{user_id}/password",
        data={"password": "BridgeStrong1!", "confirm_password": "BridgeStrong1!", "csrf": csrf},
        follow_redirects=False,
    )
    assert reset.status_code == 303

    apply_page = client.get("/appliance-apply")
    csrf = apply_page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    applied = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "local_users"})
    assert applied.status_code == 200
    assert staged_path.is_file()
    assert "BridgeStrong1!" not in applied.text

    with SessionLocal() as db:
        users = db.execute(select(User)).scalars().all()
        assert all(user.os_sync_status == "applied" for user in users)
        baseline = db.execute(select(Setting).where(Setting.key == "appliance_apply.baselines.v1")).scalar_one()
        assert "BridgeStrong1!" not in baseline.value
        assert '"password_pending": true' not in baseline.value


def test_audit_log_renders(client):
    login(client)
    response = client.get("/audit-log", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/logs#logs-audit-panel"

    logs = client.get("/logs")
    assert logs.status_code == 200
    assert "Audit Events" in logs.text
    assert "ui_login" in logs.text


def test_logs_page_renders_fixed_source_tabs_and_redacts_vcfdt_log(client, tmp_path, monkeypatch):
    vcfdt_log = tmp_path / "vdt.log"
    app_log = tmp_path / "labfoundry.log"
    kms_log = tmp_path / "kms.log"
    jwt_segment = (
        "eyJ2ZXIiOiIyIiwidHlwIjoiSldUIiwiYWxnIjoiUlMyNTYifQ."
        "eyJzdWIiOiJ1c2VyQGV4YW1wbGUuY29tIiwiaWF0IjoxNzgyNDQ1MzcxfQ."
        "signatureSegmentLongEnoughToLookLikeJwt"
    )
    vcfdt_log.write_text(
        f"download started\ntoken=secret-download-token\nGET https://dl.broadcom.com/{jwt_segment}/PROD/file.json\ndownload complete\n",
        encoding="utf-8",
    )
    app_log.write_text("app ready\n", encoding="utf-8")
    monkeypatch.setattr("labfoundry.app.ui.VCF_DEPOT_VDT_LOG_PATH", vcfdt_log)
    monkeypatch.setattr("labfoundry.app.ui.LABFOUNDRY_APP_LOG_PATH", app_log)
    monkeypatch.setattr("labfoundry.app.ui.KMS_SERVER_LOG_PATH", kms_log)

    login(client)
    response = client.get("/logs")

    assert response.status_code == 200
    assert "Logs" in response.text
    assert 'data-tab-storage-key="labfoundry:logs:active-tab"' in response.text
    assert "VCFDT" in response.text
    assert "LabFoundry App" in response.text
    assert "KMS" in response.text
    assert "Audit Events" in response.text
    assert "logs-audit-panel" in response.text
    assert str(vcfdt_log) in response.text
    assert "download started" in response.text
    assert "token= [redacted]" in response.text
    assert "https://dl.broadcom.com/[redacted-token]/PROD/file.json" in response.text
    assert "secret-download-token" not in response.text
    assert jwt_segment not in response.text
    assert "Log file has not been written yet." in response.text


def test_configure_logging_writes_main_app_log(tmp_path, monkeypatch):
    import logging
    from logging.handlers import RotatingFileHandler

    from labfoundry.app.config import get_settings
    from labfoundry.app.main import configure_logging

    log_path = tmp_path / "labfoundry.log"
    monkeypatch.setenv("LABFOUNDRY_APP_LOG_PATH", str(log_path))
    get_settings.cache_clear()

    configure_logging()
    logging.getLogger("labfoundry.appliance_apply").error("apply failure visible in main log")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "apply failure visible in main log" in log_path.read_text(encoding="utf-8")

    for handler in list(logging.getLogger().handlers):
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(log_path):
            logging.getLogger().removeHandler(handler)
            handler.close()
    get_settings.cache_clear()


def test_record_audit_writes_redacted_operational_log(client, tmp_path, monkeypatch):
    import logging
    from logging.handlers import RotatingFileHandler

    from labfoundry.app.audit import record_audit
    from labfoundry.app.config import get_settings
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.main import configure_logging

    log_path = tmp_path / "labfoundry.log"
    monkeypatch.setenv("LABFOUNDRY_APP_LOG_PATH", str(log_path))
    get_settings.cache_clear()

    with SessionLocal() as db:
        configure_logging(db)
        record_audit(
            db,
            actor="admin",
            action="update_dns_settings",
            resource_type="dns",
            resource_id="1",
            detail="password=super-secret\nlisten_address=192.168.49.1",
            request_id="req_test",
        )

    for handler in logging.getLogger().handlers:
        handler.flush()

    text = log_path.read_text(encoding="utf-8")
    assert "audit actor=admin action=update_dns_settings resource=dns resource_id=1 success=True request_id=req_test" in text
    assert "password= [redacted]" in text
    assert "listen_address=192.168.49.1" in text
    assert "super-secret" not in text

    for handler in list(logging.getLogger().handlers):
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(log_path):
            logging.getLogger().removeHandler(handler)
            handler.close()
    get_settings.cache_clear()


def test_logs_page_handles_default_pure_posix_log_path(client, monkeypatch):
    from pathlib import PurePosixPath

    monkeypatch.setattr("labfoundry.app.ui.VCF_DEPOT_VDT_LOG_PATH", PurePosixPath("/var/lib/labfoundry/vcfDownloadTool/active-tool/log/vdt.log"))
    monkeypatch.setattr("labfoundry.app.ui.LABFOUNDRY_APP_LOG_PATH", PurePosixPath("/var/log/labfoundry/labfoundry.log"))
    monkeypatch.setattr("labfoundry.app.ui.KMS_SERVER_LOG_PATH", PurePosixPath("/var/log/labfoundry/kms/server.log"))

    login(client)
    response = client.get("/logs")

    assert response.status_code == 200
    assert "VCFDT" in response.text
    assert "LabFoundry App" in response.text
    assert "Audit Events" in response.text
    assert "/var/lib/labfoundry/vcfDownloadTool/active-tool/log/vdt.log" in response.text
    assert "Log file has not been written yet." in response.text


def test_dns_and_dhcp_pages_render(client):
    login(client)
    dns = client.get("/dns")
    assert dns.status_code == 200
    assert "DNS Zones" in dns.text
    assert "dns-records-fallback" in dns.text
    assert "dnsmasq" in dns.text
    assert "labfoundry.labfoundry.internal" in dns.text
    assert "<strong>Avoid .local for VCF.</strong>" not in dns.text
    assert "+ Domain" in dns.text
    assert "New Domain" in dns.text
    assert "Import Hosts" in dns.text
    assert "Import Zone File" in dns.text
    assert "Reverse Zones" in dns.text
    assert "Reverse/PTR" in dns.text
    assert "PTR records are generated automatically" in dns.text
    assert "zone-file-editor" in dns.text
    assert "dns-import-form" in dns.text
    assert "dns-import-controls" in dns.text
    assert "data-codemirror-editor" in dns.text
    assert 'data-codemirror-language="labfoundry-hosts"' in dns.text
    assert 'data-codemirror-language="labfoundry-zone"' in dns.text
    assert "Import zone file into labfoundry.internal" in dns.text
    assert "relative hostnames are saved inside this domain" in dns.text
    assert 'data-domain="labfoundry.internal"' in dns.text
    assert "A (IPv4)" in dns.text
    assert "AAAA (IPv6)" in dns.text
    assert "CNAME (alias)" in dns.text
    assert "ptr-record=" not in dns.text
    assert "1.49.168.192.in-addr.arpa" in dns.text
    assert 'name="listen_interfaces"' in dns.text
    assert 'data-derived-listen-addresses' in dns.text
    assert 'name="conditional_forwarders"' in dns.text
    assert "Conditional forwarders" in dns.text
    assert "domain=server1,server2" in dns.text
    assert "sddc.internal=192.168.10.10,192.168.10.11" in dns.text
    assert dns.text.count('data-tag-editor') >= 1
    assert dns.text.count('data-tag-menu-toggle') >= 1
    assert dns.text.count('data-tag-option=') >= 2
    assert 'placeholder="Add interface..."' in dns.text
    assert 'placeholder="Add listen address..."' not in dns.text
    assert "eth1 - access / trunk" not in dns.text
    assert 'action="/dns/zones"' in dns.text
    assert 'action="/dns/zones/delete"' in dns.text
    assert "data-confirm-modal" in dns.text
    assert "Delete labfoundry.internal?" in dns.text
    assert "It will not touch the appliance until global appliance apply runs." in dns.text
    assert 'action="/dns/zones/import"' in dns.text
    assert 'href="/appliance-apply"' in dns.text
    assert "labfoundry.internal or sitea.internal" in dns.text
    assert "Changes save automatically." in dns.text
    assert "Review appliance changes" in dns.text
    assert "Save desired state" not in dns.text
    assert "Save DNS" not in dns.text

    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "cellEdited" in app_js.text
    assert "rowContextMenu" in app_js.text
    assert "newDnsRecordRow" in app_js.text
    assert "rowHeight: 28" in app_js.text
    assert 'field: "host_label"' in app_js.text
    assert "dnsAddRowHintFormatter" in app_js.text
    assert "+ Add record here" in app_js.text
    assert "initializeZoneEditors" in app_js.text
    assert "A (IPv4)" in app_js.text
    assert "AAAA (IPv6)" in app_js.text
    assert "CNAME (alias)" in app_js.text
    assert "reverseStatusFormatter" in app_js.text
    assert 'title: "Reverse/PTR"' in app_js.text
    assert 'newDnsRecordRow(domain, tableElement.dataset.suggestedIpv4 || "")' in app_js.text
    assert "suggested_ipv4: suggestedAddress" in app_js.text
    assert 'data.record_type !== "A" && data.address === data.suggested_ipv4' in app_js.text
    assert "DNS_ACTIVE_ZONE_STORAGE_KEY" in app_js.text
    assert "initializeCodeMirrorEditors" in app_js.text
    assert "installCodeMirrorPlainTextFallback" in app_js.text
    assert 'textarea.dataset.codemirrorLanguage !== "labfoundry-kickstart"' in app_js.text
    assert 'eventTarget.addEventListener("keydown"' in app_js.text
    assert "event.stopPropagation()" in app_js.text
    assert "LabFoundryCodeMirror.setValue" in app_js.text
    assert "rememberDnsActiveZone(data.domain)" in app_js.text
    assert "dnsZoneTabButtonForDomain(storedDomain)" in app_js.text
    assert "initializeTagEditors" in app_js.text
    assert "initializeEsxiIsoUploadForms" in app_js.text
    assert "XMLHttpRequest" in app_js.text
    assert "X-LabFoundry-Upload" in app_js.text
    assert 'rememberActiveTab("labfoundry:esxi-pxe:active-tab", "esxi-pxe-isos-panel")' in app_js.text
    assert 'window.location.hash = "esxi-pxe-isos-panel"' in app_js.text
    assert "initializeEsxiPxeHostsTable" in app_js.text
    assert 'document.getElementById(hashTargetId)?.closest(".tab-panel")' in app_js.text
    assert 'querySelector(".tag-editor[data-service-bind-interface]")' in app_js.text
    assert 'querySelector(".tag-editor[data-service-bind-address]")' in app_js.text
    assert "initializeConfirmationModals" in app_js.text
    assert "requestConfirmation" in app_js.text
    assert "form[data-confirm-modal]" in app_js.text
    assert "confirm-modal" in app_js.text
    assert "initializeAutosaveForms" in app_js.text
    assert "LABFOUNDRY_MUTATING_METHODS" in app_js.text
    assert "scheduleApplianceApplySidebarRefresh" in app_js.text
    assert 'fetch("/appliance-apply/status"' in app_js.text
    assert "initializeApplianceApplyProgress" in app_js.text
    assert "Submitting appliance changes" in app_js.text
    assert "Waiting for result" in app_js.text
    assert "data-apply-submit-tracker" in app_js.text
    assert "data-apply-progress-modal" not in app_js.text
    assert "index === 0 ? \"Applying\"" not in app_js.text
    assert "initializeDhcpScopesTable" in app_js.text
    assert "autoSaveDhcpScope" in app_js.text
    assert "+ Add IP zone here" in app_js.text
    assert "isUniqueNewDhcpScopeName" in app_js.text
    assert "dhcpScopeCellEditable" in app_js.text
    assert "applyDhcpScopeInterfaceDefaults" in app_js.text
    assert 'title: "Family"' in app_js.text
    assert "address_family" in app_js.text
    assert 'title: "NTP"' in app_js.text
    assert "domainOptions" in app_js.text
    assert "domainValues" in app_js.text
    assert "initializeDhcpOptionsTable" in app_js.text
    assert "autoSaveDhcpOption" in app_js.text
    assert "+ Add DHCP option here" in app_js.text
    assert "initializeDhcpReservationsTable" in app_js.text
    assert "autoSaveDhcpReservation" in app_js.text
    assert "+ Add reservation here" in app_js.text
    assert 'title: "DNS name / FQDN"' in app_js.text
    assert "initializeCaSettings" in app_js.text
    assert "data-ca-config-preview" in app_js.text
    assert "data-ca-derived-address" not in app_js.text
    assert "initializeServiceBindEditors" in app_js.text
    assert "data-tag-single" in app_js.text
    assert "X-LabFoundry-Autosave" in app_js.text
    assert "tag-editor:change" in app_js.text
    assert "data-tag-menu-toggle" in app_js.text
    assert 'data-action="save"' not in app_js.text

    app_css = client.get("/static/app.css")
    assert app_css.status_code == 200
    assert "margin: 0;" in app_css.text
    assert "background: var(--bg);" in app_css.text
    assert "color: var(--text);" in app_css.text
    assert ".add-row-hint" in app_css.text
    assert ".new-record-row-locked" in app_css.text
    assert 'tabulator-field="host_label"' in app_css.text
    assert ".alert.warning" in app_css.text
    assert ".tag-editor" in app_css.text
    assert ".tag-add-button" in app_css.text
    assert ".tag-suggestions" in app_css.text
    assert ".autosave-status" in app_css.text
    assert ".appliance-apply-form" in app_css.text
    assert ".apply-change-set-panel" in app_css.text
    assert ".form-grid > label > .field-label" in app_css.text
    assert ".service-bind-editor" in app_css.text
    assert ".apply-submit-panel" in app_css.text
    assert ".config-diff code" in app_css.text
    assert "overflow-wrap: anywhere;" in app_css.text
    assert "white-space: pre-wrap;" in app_css.text
    assert ".page-apply-notice" in app_css.text
    assert ".apply-inline-tracker" in app_css.text
    assert ".apply-progress-modal" not in app_css.text
    assert ".apply-step-row" in app_css.text
    assert ".confirm-modal" in app_css.text
    assert ".confirm-modal::backdrop" in app_css.text
    assert "backdrop-filter" not in app_css.text
    assert ".section-head" in app_css.text
    assert ".dns-import-controls" in app_css.text
    assert "min-height: clamp(360px, 50vh, 640px) !important;" in app_css.text

    dhcp = client.get("/dhcp")
    assert dhcp.status_code == 200
    assert "DHCP IP Zones" in dhcp.text
    assert "Desired State" in dhcp.text
    assert "Generated PXE" in dhcp.text
    assert "Actual Leases" in dhcp.text
    assert 'id="dhcp-generated-pxe"' in dhcp.text
    assert 'id="dhcp-actual-leases"' in dhcp.text
    assert "api-client.labfoundry.internal" in dhcp.text
    assert "labfoundry-helper dnsmasq leases" in dhcp.text
    assert "dhcp-scopes-table" in dhcp.text
    assert "data-scope-defaults" in dhcp.text
    assert "data-domain-options" in dhcp.text
    assert 'data-domain-options=\'["labfoundry.internal"]\'' in dhcp.text
    assert "labfoundry.internal" in dhcp.text
    assert "dhcp-scopes-fallback" in dhcp.text
    assert "DHCP Options" in dhcp.text
    assert "dhcp-options-table" in dhcp.text
    assert "dhcp-options-fallback" in dhcp.text
    assert "dhcp-reservations-table" in dhcp.text
    assert "dhcp-reservations-fallback" in dhcp.text
    assert "DNS name / FQDN" in dhcp.text
    assert 'data-autosave-status-id="dhcp-settings-autosave-status"' in dhcp.text
    assert "Changes save automatically." in dhcp.text
    assert 'href="/appliance-apply"' in dhcp.text
    assert "Review appliance changes" in dhcp.text
    assert "Save DHCP" not in dhcp.text
    assert "192.168.50.100" in dhcp.text
    assert "192.168.50.1" in dhcp.text


def test_dhcp_new_zone_row_defaults_follow_interface_dns_and_chrony(client):
    import html
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ChronySettings, DnsSettings

    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = True
        dns_settings.listen_interface = "eth2"
        dns_settings.listen_address = "192.168.50.1"
        chrony_settings = db.execute(select(ChronySettings)).scalar_one()
        chrony_settings.enabled = True
        chrony_settings.listen_interface = "eth2"
        chrony_settings.listen_address = "192.168.50.1"
        db.add_all([dns_settings, chrony_settings])
        db.commit()

    login(client)
    page = client.get("/dhcp")

    assert page.status_code == 200
    payload = page.text.split("data-scope-defaults='", 1)[1].split("'", 1)[0]
    defaults = json.loads(html.unescape(payload))
    eth2 = next(item for item in defaults["interfaces"] if item["name"] == "eth2")
    eth1_vlan = next(item for item in defaults["interfaces"] if item["name"] == "eth1.20")
    assert eth2["ipv4_address"] == "192.168.50.1"
    assert eth2["ipv4_prefix"] == 24
    assert eth2["dns_default"] == "192.168.50.1"
    assert eth2["ntp_default"] == "192.168.50.1"
    assert eth1_vlan["dns_default"] == ""
    assert eth1_vlan["ntp_default"] == ""
    assert "sitea" in defaults["existing_names"]
    assert defaults["default_domain"] == "labfoundry.internal"
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert 'rowData.dns_server = interfaceDefaults.dns_default || "";' in app_js.text
    assert 'rowData.ntp_server = interfaceDefaults.ntp_default || "";' in app_js.text


def test_dns_new_record_row_suggests_next_available_ipv4(client):
    import html
    import json

    login(client)
    page = client.get("/dns")

    assert page.status_code == 200
    assert 'data-suggested-ipv4="192.168.50.2"' in page.text
    payload = page.text.split("data-records='", 1)[1].split("'", 1)[0]
    records = json.loads(html.unescape(payload))
    assert any(record["address"] == "192.168.49.1" for record in records)


def test_dns_ipv4_suggestion_falls_back_to_existing_a_record_network():
    from labfoundry.app.models import DhcpReservation, DhcpScope, DnsRecord
    from labfoundry.app.ui import dns_record_suggested_ipv4

    records = [
        DnsRecord(hostname="labfoundry.labfoundry.internal", record_type="A", address="192.168.49.1", enabled=True),
        DnsRecord(hostname="used.labfoundry.internal", record_type="A", address="192.168.49.2", enabled=True),
    ]

    assert dns_record_suggested_ipv4(records, "labfoundry.internal", [], []) == "192.168.49.3"

    scopes = [
        DhcpScope(
            name="SiteA",
            site_address="192.168.50.1",
            prefix_length=24,
            range_start="192.168.50.100",
            range_end="192.168.50.200",
            domain_name="labfoundry.internal",
            enabled=True,
        )
    ]
    reservations = [
        DhcpReservation(
            hostname="reserved.labfoundry.internal",
            mac_address="02:15:5d:00:20:10",
            ip_address="192.168.50.2",
        )
    ]

    assert dns_record_suggested_ipv4(records, "labfoundry.internal", scopes, reservations) == "192.168.50.3"


def test_dns_settings_badge_reflects_desired_state_not_runtime_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsSettings, ServiceState

    login(client)
    with SessionLocal() as db:
        settings = db.execute(select(DnsSettings)).scalar_one()
        settings.enabled = True
        service = db.execute(select(ServiceState).where(ServiceState.service == "dns")).scalar_one()
        service.enabled = False
        service.running = False
        service.health = "disabled"
        db.commit()

    page = client.get("/dns")
    settings_panel = page.text.split("<h2>DNS Settings</h2>", 1)[1].split("</form>", 1)[0]

    assert page.status_code == 200
    assert '<span class="status-pill good">enabled</span>' in settings_panel
    assert '<span class="status-pill muted">disabled</span>' not in settings_panel


def test_dhcp_leases_page_reflects_live_adapter_output(client, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.adapters.system import AdapterResult
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpReservation, DnsRecord, EsxiPxeHost

    def fake_read_dhcp_leases(self):
        return AdapterResult(
            command=["sudo", "-n", "/opt/labfoundry/bin/labfoundry-helper", "dnsmasq", "leases", "--real"],
            dry_run=False,
            stdout=(
                "1893456000 02:15:5d:00:20:40 192.168.50.140 live-client.labfoundry.internal *\n"
                "1893456000 02:15:5d:00:20:41 192.168.1.110 stale-client.labfoundry.internal *\n"
            ),
        )

    monkeypatch.setattr("labfoundry.app.ui.SystemAdapter.read_dhcp_leases", fake_read_dhcp_leases)

    login(client)
    page = client.get("/dhcp")

    assert page.status_code == 200
    assert '<span class="status-pill good">live</span>' in page.text
    assert "sudo -n /opt/labfoundry/bin/labfoundry-helper dnsmasq leases --real" in page.text
    assert "live-client.labfoundry.internal" in page.text
    assert "stale-client.labfoundry.internal" not in page.text
    assert "192.168.1.110" not in page.text
    assert "dhcp-leases-table" in page.text
    assert "dhcp-leases-fallback" in page.text
    assert "data-leases=" in page.text
    assert "data-dhcp-lease-reservation" in page.text
    assert "data-dhcp-lease-pxe-host" in page.text
    assert "dhcp-lease-reservation-modal" in page.text
    assert "dhcp-lease-pxe-modal" in page.text
    assert "Create reservation" in page.text
    assert "Create PXE entry" in page.text
    assert "Deny DHCP for MAC" in page.text
    app_js = client.get("/static/app.js").text
    assert "initializeDhcpLeasesTable" in app_js
    assert "rowContextMenu" in app_js
    assert "openDhcpLeasePxeModal" in app_js
    assert "dhcpLeaseActionFormatter" not in app_js
    assert "openDhcpLeaseActionsMenu" not in app_js
    assert "Create PXE entry" in app_js
    assert "Deny DHCP for MAC" in app_js
    assert "initializeDhcpLeaseReservationActions" in app_js
    assert '<span class="status-pill warn">dry-run</span>' not in page.text

    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dhcp/reservations",
        data={
            "hostname": "live-client.labfoundry.internal",
            "mac_address": "02:15:5d:00:20:40",
            "ip_address": "192.168.50.140",
            "description": "Created from live DHCP lease 192.168.50.140.",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        reservation = db.execute(select(DhcpReservation).where(DhcpReservation.mac_address == "02:15:5d:00:20:40")).scalar_one()
        assert reservation.hostname == "live-client.labfoundry.internal"
        assert reservation.ip_address == "192.168.50.140"
        assert reservation.enabled is True
        record = db.execute(select(DnsRecord).where(DnsRecord.hostname == "live-client.labfoundry.internal", DnsRecord.record_type == "A")).scalar_one()
        assert record.address == "192.168.50.140"

    with SessionLocal() as db:
        from labfoundry.app.models import DhcpScope
        from labfoundry.app.services.esxi_pxe import save_esxi_pxe_boot_settings

        scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        save_esxi_pxe_boot_settings(
            db,
            enabled=True,
            hostname="esxi-pxe.labfoundry.internal",
            listen_interface="eth2",
            listen_address="192.168.50.1",
            dhcp_scope_id=str(scope.id),
            dhcp_scope_ids=[str(scope.id)],
            tftp_root="/var/lib/labfoundry/pxe/tftp",
            http_port=8080,
            bios_bootfile="undionly.kpxe",
            uefi_bootfile="snponly.efi",
            native_uefi_http_enabled=True,
            native_uefi_http_url="",
        )
        db.commit()

    pxe_response = client.post(
        "/dhcp/leases/pxe-host",
        data={
            "hostname": "pxe-client.labfoundry.internal",
            "mac_address": "02:15:5d:00:20:42",
            "ip_address": "192.168.50.142",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert pxe_response.status_code == 303
    assert pxe_response.headers["location"] == "/esxi-pxe#esxi-pxe-hosts"
    with SessionLocal() as db:
        host = db.execute(select(EsxiPxeHost).where(EsxiPxeHost.mac_address == "02:15:5d:00:20:42")).scalar_one()
        assert host.hostname == "pxe-client.labfoundry.internal"
        assert host.ip_address == "192.168.50.142"
        assert host.enabled is True

    deny_response = client.post(
        "/dhcp/leases/deny",
        data={
            "hostname": "deny-client.labfoundry.internal",
            "mac_address": "02:15:5d:00:20:43",
            "ip_address": "192.168.50.143",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert deny_response.status_code == 303
    with SessionLocal() as db:
        deny = db.execute(select(DhcpReservation).where(DhcpReservation.mac_address == "02:15:5d:00:20:43")).scalar_one()
        assert deny.enabled is False
        assert deny.description == "Deny DHCP for 02:15:5d:00:20:43."


def test_firewall_preview_derives_dns_dhcp_rule_from_dhcp_scope_vlan(client):
    import html
    import json
    import re

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpScope, DhcpSettings, FirewallRule, VlanInterface

    with SessionLocal() as db:
        dhcp_settings = db.execute(select(DhcpSettings)).scalar_one()
        dhcp_settings.enabled = True
        scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        scope.interface_name = "eth2.50"
        scope.site_address = "192.168.50.1"
        scope.prefix_length = 24
        scope.enabled = True
        legacy_rule = db.execute(select(FirewallRule).where(FirewallRule.name == "sitea-dns-dhcp")).scalar_one()
        legacy_rule.interface_name = "eth1"
        if db.execute(select(VlanInterface).where(VlanInterface.name == "eth2.50")).scalar_one_or_none() is None:
            db.add(
                VlanInterface(
                    name="eth2.50",
                    parent_interface="eth2",
                    vlan_id=50,
                    ip_cidr="192.168.50.1/24",
                    role="services",
                    enabled=True,
                )
            )
        db.commit()

    login(client)
    firewall = client.get("/firewall")

    assert firewall.status_code == 200
    assert "Managed Service Rules" in firewall.text
    assert "Groups" in firewall.text
    assert "data-firewall-validation-refresh" in firewall.text
    assert "Add group" in firewall.text
    assert "No custom groups yet." in firewall.text
    assert 'data-source-group-select' not in firewall.text
    assert firewall.text.index('class="form-stack source-group-create-form"') < firewall.text.index('class="source-group-manager"')
    assert "eth2.50" in firewall.text
    assert "data-interfaces=" in firewall.text
    assert "&#34;eth2.50&#34;" in firewall.text
    assert "data-source-groups=" in firewall.text
    assert "data-groups=" in firewall.text
    editable_payload = re.search(r'id="firewall-rules-table"[^>]+data-rules=\'([^\']*)\'', firewall.text, re.S)
    managed_payload = re.search(r'id="managed-firewall-rules-table"[^>]+data-rules=\'([^\']*)\'', firewall.text, re.S)
    assert editable_payload is not None
    assert managed_payload is not None
    editable_rows = json.loads(html.unescape(editable_payload.group(1)))
    managed_rows = json.loads(html.unescape(managed_payload.group(1)))
    assert not any(row["name"] == "sitea-dns-dhcp" and row["interface_name"] == "eth1" for row in editable_rows)
    assert any(row["name"] == "sitea-dns-dhcp" and row["interface_name"] == "eth1" and row["managed_state"] == "replaced" for row in managed_rows)
    assert any(row["name"] == "sitea-dns-dhcp" and row["interface_name"] == "eth2.50" and row["managed_state"] == "generated" for row in managed_rows)
    assert any(row["name"] == "mgmt-console" and row["managed_state"] == "generated" and row["source_group_id"] == "any" and row["source_group_name"] == "Any" for row in managed_rows)
    generated_index = next(i for i, row in enumerate(managed_rows) if row["name"] == "sitea-dns-dhcp" and row["managed_state"] == "generated")
    replaced_index = next(i for i, row in enumerate(managed_rows) if row["name"] == "sitea-dns-dhcp" and row["managed_state"] == "replaced")
    assert replaced_index == generated_index + 1
    assert 'iifname &#34;eth2.50&#34; udp dport 67 accept comment &#34;sitea-dns-dhcp&#34;' in firewall.text
    assert 'iifname &#34;eth1&#34; ip saddr 192.168.50.0/24 udp dport { 53, 67 } accept comment &#34;sitea-dns-dhcp&#34;' not in firewall.text

    csrf = firewall.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    group_response = client.post(
        "/firewall/source-groups",
        data={
            "csrf": csrf,
            "action": "create",
            "group_name": "Managed clients",
            "group_entries": "any",
        },
    )
    assert group_response.status_code == 200

    group_response = client.post(
        "/firewall/source-groups",
        data={
            "csrf": csrf,
            "action": "update",
            "group_id": "custom:managed-clients",
            "group_name": "Managed clients",
            "group_entries": "10.77.0.0/16",
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert group_response.status_code == 200
    assert group_response.json()["status"] == "saved"
    assert group_response.json()["updated_at"]
    assert "config_preview" in group_response.json()

    rename_response = client.post(
        "/firewall/source-groups",
        data={
            "csrf": csrf,
            "action": "rename",
            "group_id": "custom:managed-clients",
            "group_name": "Managed client sources",
        },
    )
    assert rename_response.status_code == 200

    assignment_response = client.post(
        "/firewall/managed-rules/source-group",
        data={"csrf": csrf, "rule_name": "mgmt-console", "source_group_id": "custom:managed-clients"},
    )
    assert assignment_response.status_code == 200

    rule_response = client.post(
        "/firewall/rules",
        data={
            "csrf": csrf,
            "name": "grouped-custom",
            "direction": "input",
            "action": "accept",
            "protocol": "tcp",
            "source": "group:custom:managed-clients",
            "destination": "group:custom:managed-clients",
            "destination_port": "443",
            "interface_name": "eth2.50",
            "priority": "101",
            "enabled": "on",
        },
    )
    assert rule_response.status_code == 200

    updated_firewall = client.get("/firewall")
    assert "10.77.0.0/16" in updated_firewall.text
    assert "Managed client sources" in updated_firewall.text
    assert "data-source-group-rename" in updated_firewall.text
    source_group_manager = re.search(r'<div class="source-group-manager" data-source-group-manager>(.*?)</div>\s*<dialog id="firewall-rename-group-modal"', updated_firewall.text, re.S)
    assert source_group_manager is not None
    assert 'data-source-group-select' in source_group_manager.group(1)
    assert '<option value="any">' not in source_group_manager.group(1)
    assert 'iifname &#34;eth0&#34; ip saddr 10.77.0.0/16 tcp dport { 22, 80, 443 } accept comment &#34;mgmt-console&#34;' in updated_firewall.text
    assert 'iifname &#34;eth2.50&#34; ip saddr 10.77.0.0/16 ip daddr 10.77.0.0/16 tcp dport 443 accept comment &#34;grouped-custom&#34;' in updated_firewall.text
    assert 'iifname &#34;eth2.50&#34; udp dport 67 accept comment &#34;sitea-dns-dhcp&#34;' in updated_firewall.text

    apply_page = client.get("/appliance-apply")
    assert apply_page.status_code == 200
    assert "DNS/DHCP (dnsmasq)" in apply_page.text
    assert "Firewall" in apply_page.text
    assert "eth2.50" in apply_page.text


def test_dns_listen_options_include_access_and_vlans_not_trunks(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import VlanInterface

    with SessionLocal() as db:
        db.add(
            VlanInterface(
                name="eth1.60",
                parent_interface="eth1",
                vlan_id=60,
                ip_cidr="192.168.60.1/24",
                role="services",
                enabled=True,
            )
        )
        db.commit()

    login(client)
    page = client.get("/dns")

    assert page.status_code == 200
    assert "eth2 - access / access / 192.168.50.1" in page.text
    assert "eth1.60 - VLAN 60 on eth1 / services / 192.168.60.1" in page.text
    assert "eth1 - access / trunk" not in page.text
    assert 'data-tag-option="eth1.60"' in page.text
    assert 'data-tag-option="192.168.60.1"' not in page.text


def test_certificate_authority_page_renders(client):
    login(client)
    ca = client.get("/certificate-authority")
    assert ca.status_code == 200
    assert "Certificate Authority" in ca.text
    assert "Certificate Requests" in ca.text
    assert "Profiles" in ca.text
    assert "CSR Intake" in ca.text
    assert "ca-certificates-table" in ca.text
    assert "ca-profiles-table" in ca.text
    assert "+ Add certificate here" in client.get("/static/app.js").text
    assert "+ Add profile here" in client.get("/static/app.js").text
    assert "LabFoundry Internal Root CA" in ca.text
    assert "VCF service TLS" in ca.text
    assert "labfoundry.labfoundry.internal" in ca.text
    assert 'data-autosave-status-id="ca-settings-autosave-status"' in ca.text
    assert "Listen interfaces" in ca.text
    assert "Listen addresses" in ca.text
    assert 'name="listen_interfaces_present"' in ca.text
    assert 'name="listen_interfaces"' in ca.text
    assert 'data-derived-listen-addresses' in ca.text
    assert 'placeholder="Add interface..."' in ca.text
    assert 'placeholder="Add listen address..."' not in ca.text
    assert 'data-tag-option="eth2"' in ca.text
    assert "eth1 - unused / trunk" not in ca.text
    assert "Read-only addresses resolved" in ca.text
    assert 'data-ca-derived-address' not in ca.text
    assert 'name="listen_interface"' not in ca.text
    assert 'name="listen_address"' not in ca.text
    assert "Changes save automatically." in ca.text
    assert 'href="/appliance-apply"' in ca.text
    assert "Review appliance changes" in ca.text
    assert "labfoundry-ca.json" in ca.text
    assert 'class="language-json"' in ca.text
    assert "data-confirm-modal" in ca.text
    assert '<strong>/etc/labfoundry/ca</strong>' in ca.text
    assert "fixed-value-field" in ca.text
    assert 'name="storage_path"' not in ca.text
    assert '<input name="storage_path"' not in ca.text
    assert "Downloads" in ca.text
    assert "Download root CA" in ca.text
    assert "Download CA bundle" in ca.text
    assert "ca-download-details" in ca.text
    assert 'data-secret-mask="hidden">hidden</span>' in ca.text
    assert 'data-secret-toggle aria-label="Show secrets key source"' in ca.text
    assert "/certificate-authority/downloads/root-ca.pem" in ca.text
    assert "/certificate-authority/downloads/ca-bundle.pem" in ca.text


def test_certificate_authority_downloads_public_pems(client):
    login(client)
    root = client.get("/certificate-authority/downloads/root-ca.pem")
    assert root.status_code == 200
    assert root.headers["content-disposition"] == 'attachment; filename="labfoundry-root-ca.pem"'
    assert "BEGIN CERTIFICATE" in root.text
    assert "BEGIN PRIVATE KEY" not in root.text

    bundle = client.get("/certificate-authority/downloads/ca-bundle.pem")
    assert bundle.status_code == 200
    assert bundle.headers["content-disposition"] == 'attachment; filename="labfoundry-ca-bundle.pem"'
    assert "BEGIN CERTIFICATE" in bundle.text


def test_ca_apply_payload_leaves_csr_private_key_empty():
    import json

    from labfoundry.app.models import CaCertificate, CaSettings
    from labfoundry.app.services.ca import render_ca_apply_payload

    settings = CaSettings(
        enabled=True,
        root_common_name="LabFoundry Test Root CA",
        root_certificate_pem="-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----\n",
        storage_path="/etc/labfoundry/ca",
    )
    certificate = CaCertificate(
        common_name="client-a.labfoundry.internal",
        status="issued",
        certificate_pem="-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n",
        chain_pem="-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n",
        csr_text="-----BEGIN CERTIFICATE REQUEST-----\ncsr\n-----END CERTIFICATE REQUEST-----\n",
        cert_path="/etc/labfoundry/ca/client-a.crt",
        key_path="",
        chain_path="/etc/labfoundry/ca/client-a-chain.pem",
        enabled=True,
    )

    payload = json.loads(render_ca_apply_payload(settings, [certificate], include_private_keys=True))

    assert payload["certificates"][0]["managed_owner"] == ""
    assert payload["certificates"][0]["private_key_pem"] == ""


def test_certificate_authority_issues_encrypted_managed_certs_and_exports(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import CaCertificate, CaSettings

    with SessionLocal() as db:
        settings = db.execute(select(CaSettings)).scalar_one()
        settings.enabled = True
        settings.listen_interface = "eth0"
        settings.listen_address = "192.168.49.1"
        db.commit()

    login(client)
    page = client.get("/certificate-authority")
    assert page.status_code == 200
    assert "Managed certs" in page.text
    assert "appliance:https" in page.text
    assert "Private key" in page.text
    assert "BEGIN PRIVATE KEY" not in page.text

    with SessionLocal() as db:
        settings = db.execute(select(CaSettings)).scalar_one()
        managed = db.execute(select(CaCertificate).where(CaCertificate.managed_owner == "appliance:https")).scalar_one()
        assert settings.root_certificate_pem.startswith("-----BEGIN CERTIFICATE-----")
        assert settings.root_private_key_encrypted.startswith("fernet:v1:")
        assert "BEGIN PRIVATE KEY" not in settings.root_private_key_encrypted
        assert managed.status == "issued"
        assert managed.private_key_encrypted.startswith("fernet:v1:")
        assert managed.certificate_pem.startswith("-----BEGIN CERTIFICATE-----")
        certificate_id = managed.id

    cert = client.get(f"/certificate-authority/certificates/{certificate_id}/downloads/certificate.pem")
    assert cert.status_code == 200
    assert "BEGIN CERTIFICATE" in cert.text
    assert "BEGIN PRIVATE KEY" not in cert.text

    key = client.get(f"/certificate-authority/certificates/{certificate_id}/downloads/private-key.pem")
    assert key.status_code == 200
    assert "BEGIN PRIVATE KEY" in key.text


def test_kms_page_renders(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ServiceState

    login(client)
    with SessionLocal() as db:
        service = db.execute(select(ServiceState).where(ServiceState.service == "kms")).scalar_one()
        service.enabled = True
        service.running = True
        service.health = "healthy"
        db.commit()

    kms = client.get("/kms")
    assert kms.status_code == 200
    assert "KMS / KMIP" in kms.text
    assert "PyKMIP" in kms.text
    assert "lab KMIP server" in kms.text
    assert "kms-keys-table" in kms.text
    assert "kms-clients-table" in kms.text
    assert "vcf-management" in kms.text
    assert "vcf-sddc-manager-aes" in kms.text
    assert "kms.labfoundry.internal" in kms.text
    assert "Listen interfaces" in kms.text
    assert "Listen addresses" in kms.text
    assert "service-bind-editor" in kms.text
    assert "service-bind-editor stacked-service-bind-editor" in kms.text
    assert '<select name="backend"' not in kms.text
    assert 'type="hidden" name="backend" value="pykmip"' in kms.text
    assert kms.text.index('name="hostname"') < kms.text.index('data-tag-name="listen_interfaces"')
    assert kms.text.index('data-tag-name="listen_interfaces"') < kms.text.index('data-derived-listen-addresses')
    assert kms.text.index('data-derived-listen-addresses') < kms.text.index('name="port"')
    assert 'name="listen_interfaces_present"' in kms.text
    assert 'data-tag-name="listen_interfaces"' in kms.text
    assert 'data-tag-name="listen_addresses"' not in kms.text
    assert "data-tag-single" not in kms.text
    assert "192.168.50.1" in kms.text
    assert "eth2 - access / access / 192.168.50.1" in kms.text
    assert 'data-autosave-status-id="kms-settings-autosave-status"' in kms.text
    assert "Changes save automatically." in kms.text
    assert 'href="/appliance-apply"' in kms.text
    assert "Review appliance changes" in kms.text
    assert "pykmip.conf" in kms.text
    assert "/var/lib/labfoundry/kms/pykmip.db" in kms.text
    assert "<span>Database path</span>" not in kms.text
    assert "<span>Config path</span>" not in kms.text
    assert "<span>Client CA path</span>" in kms.text
    assert "fixed-value-field" in kms.text
    assert 'name="server_certificate"' not in kms.text
    assert 'name="ca_certificate_path"' not in kms.text
    assert 'name="database_path"' not in kms.text
    assert 'name="config_path"' not in kms.text
    assert "data-confirm-modal" in kms.text

    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "initializeKmsKeysTable" in app_js.text
    assert "initializeKmsClientsTable" in app_js.text
    assert "initializeKmsSettings" in app_js.text
    assert "+ Add key here" in app_js.text
    assert "+ Add client here" in app_js.text
    assert "deleteKmsKeyFromMenu" in app_js.text
    assert "deleteKmsClientFromMenu" in app_js.text
    assert '<span class="status-pill good">live</span>' in kms.text
    assert "preview-modal" in kms.text
    assert "data-preview-modal-code" in kms.text
    assert "initializeTerminalNoteActions" in app_js.text


def test_kms_settings_autosave_returns_json(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord

    login(client)
    page = client.get("/kms")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/kms/settings",
        data={
            "enabled": "on",
            "backend": "pykmip",
            "listen_interface": "eth2",
            "listen_address": "10.0.0.99",
            "port": "5696",
            "hostname": "kms.labfoundry.internal",
            "server_certificate": "rogue-kms.labfoundry.internal",
            "ca_certificate_path": "/tmp/rogue-client-ca.crt",
            "database_path": "/tmp/rogue-kms.db",
            "config_path": "/tmp/rogue-kms.conf",
            "require_client_cert": "on",
            "allow_register": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["listen_address"] == "192.168.50.1"
    assert payload["listen_addresses"] == ["192.168.50.1"]
    assert payload["server_certificate"] == "kms.labfoundry.internal"
    assert "KMS requires Certificate Authority to be enabled before activation." in payload["validation_errors"]
    refreshed = client.get("/kms")
    assert "enabled" in refreshed.text
    assert "/tmp/rogue-kms.db" not in refreshed.text
    assert "/tmp/rogue-kms.conf" not in refreshed.text
    assert "/tmp/rogue-client-ca.crt" not in refreshed.text
    assert "/etc/labfoundry/ca/root.crt" in refreshed.text
    assert "/var/lib/labfoundry/kms/pykmip.db" in refreshed.text
    assert "/etc/labfoundry/kms/pykmip.conf" in refreshed.text
    assert "10.0.0.99" not in refreshed.text

    with SessionLocal() as db:
        record = db.execute(select(DnsRecord).where(DnsRecord.hostname == "kms.labfoundry.internal", DnsRecord.record_type == "A")).scalar_one()
        assert record.address == "192.168.50.1"
        assert "KMS/KMIP endpoint" in (record.description or "")


def test_kms_settings_accept_multiple_listen_targets(client):
    login(client)
    page = client.get("/kms")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/kms/settings",
        data={
            "enabled": "on",
            "backend": "pykmip",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth2", "eth0"],
            "listen_addresses": ["192.168.50.1", "192.168.49.1"],
            "port": "5696",
            "hostname": "kms.labfoundry.internal",
            "server_certificate": "kms.labfoundry.internal",
            "require_client_cert": "on",
            "allow_register": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["listen_interfaces"] == ["eth2", "eth0"]
    assert payload["listen_addresses"] == ["192.168.50.1", "192.168.49.1"]
    assert "# LabFoundry KMS listen interfaces: eth2, eth0" in payload["config_preview"]
    assert "# LabFoundry KMS listen addresses: 192.168.50.1, 192.168.49.1" in payload["config_preview"]


def test_kms_enable_autocreates_ca_managed_certificate_rows(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import CaCertificate, CaSettings

    login(client)
    with SessionLocal() as db:
        ca_settings = db.execute(select(CaSettings)).scalar_one()
        ca_settings.enabled = True
        db.commit()

    page = client.get("/kms")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/kms/settings",
        data={
            "enabled": "on",
            "backend": "pykmip",
            "listen_interface": "eth2",
            "port": "5696",
            "hostname": "kms.labfoundry.internal",
            "server_certificate": "kms.labfoundry.internal",
            "require_client_cert": "on",
            "allow_register": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["validation_errors"] == []

    with SessionLocal() as db:
        server_cert = db.execute(select(CaCertificate).where(CaCertificate.managed_owner == "kms:server")).scalar_one()
        client_cert = db.execute(select(CaCertificate).where(CaCertificate.managed_owner == "kms:client:vcf-management")).scalar_one()
        assert server_cert.status == "issued"
        assert server_cert.ip_addresses == "192.168.50.1"
        assert server_cert.cert_path == "/etc/labfoundry/kms/certs/kms.labfoundry.internal.crt"
        assert client_cert.status == "issued"


def test_kms_apply_task_captures_current_desired_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, Job

    login(client)
    page = client.get("/kms")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "kms"})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "/var/lib/labfoundry/apply/kms/pykmip.conf" in (job.result or "")
        assert "pykmip" in (job.result or "")
        assert "vcf-sddc-manager-aes" in (job.result or "")


def test_vcf_backups_page_uses_local_user_for_sftp(client):
    login(client)
    page = client.get("/vcf-backups")
    assert page.status_code == 200
    assert "VCF Backup SFTP" in page.text
    assert "Authentication uses one local LabFoundry user from Users" in page.text
    assert "SFTP user" in page.text
    assert "vcf-backup" in page.text
    assert "/mnt/labfoundry-vcf-backups" in page.text
    assert "/backups" in page.text
    assert 'action="/vcf-backups/settings"' in page.text
    assert 'data-autosave-status-id="vcf-backup-settings-status"' in page.text
    assert 'href="/appliance-apply"' in page.text
    assert "Review appliance changes" in page.text
    assert "VCF Backup SFTP desired state is disabled" in page.text
    assert "Listen interfaces" in page.text
    assert "Listen addresses" in page.text
    assert "service-bind-editor stacked-service-bind-editor" in page.text
    assert 'data-tag-name="listen_interfaces"' in page.text
    assert 'data-tag-name="listen_addresses"' not in page.text
    assert page.text.index('data-derived-listen-addresses') < page.text.index('name="port"')
    assert page.text.count("fixed-value-field") >= 2
    assert "<span>Config path</span>" not in page.text
    assert "eth1 - access / trunk" not in page.text
    assert "eth2 - access / access / 192.168.50.1" in page.text
    assert 'data-service-bind-address="192.168.50.1"' in page.text
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "initializeVcfBackupSettings" in app_js.text
    assert "updateVcfBackupDerivedAddress" in app_js.text
    assert "updateVcfBackupValidation" in app_js.text


def test_vcf_backups_settings_badge_reflects_desired_state(client, monkeypatch):
    from labfoundry.app.config import get_settings

    login(client)
    monkeypatch.setenv("LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS", "false")
    get_settings.cache_clear()

    page = client.get("/vcf-backups")

    assert page.status_code == 200
    settings_panel = page.text.split("<h2>SFTP Settings</h2>", 1)[1].split("</form>", 1)[0]
    assert '<span class="status-pill muted">disabled</span>' in settings_panel
    assert '<span class="status-pill warn">dry-run</span>' not in page.text


def test_vcf_private_registry_page_models_harbor_and_bundle_relocation(client):
    login(client)
    page = client.get("/vcf-private-registry")
    assert page.status_code == 200
    assert "VCF Private Registry" in page.text
    assert "Harbor-backed private registry" in page.text
    assert '<aside class="side-stack">' in page.text
    assert "<h2>Harbor Settings</h2>" in page.text
    assert 'data-tab-target="vcf-registry-settings-panel"' not in page.text
    assert "<span>Config path</span>" not in page.text
    assert "registry.labfoundry.internal" in page.text
    assert "vcf-supervisor-services" in page.text
    assert "/mnt/labfoundry-vcf-registry" in page.text
    assert "Upload CA bundle" in page.text
    assert "Choose CA bundle" in page.text
    assert "file-upload-icon" in page.text
    assert "not uploaded" in page.text
    assert 'action="/vcf-private-registry/settings"' in page.text
    assert 'data-autosave-status-id="vcf-registry-settings-status"' in page.text
    assert "Supervisor Service bundles" in page.text
    assert "Review appliance changes" in page.text
    assert "Review appliance changes" in page.text
    assert "harbor_admin_password: &lt;provisioned-by-labfoundry-helper&gt;" in page.text
    assert "eth1 - access / trunk" not in page.text
    assert "eth2 - access / access / 192.168.50.1" in page.text
    assert "Listen addresses" in page.text
    assert "service-bind-editor" in page.text
    assert 'data-service-bind-address="192.168.50.1"' in page.text
    assert 'data-tag-name="listen_addresses"' not in page.text
    assert page.text.count("fixed-value-field") >= 1
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "initializeVcfRegistrySettings" in app_js.text
    assert "initializeVcfRegistryBundlesTable" in app_js.text
    assert "initializeFileUploadControls" in app_js.text
    assert "updateVcfRegistryValidation" in app_js.text


def test_vcf_private_registry_settings_autosave_bundle_status_api_and_apply_task(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, Job

    login(client)
    page = client.get("/vcf-private-registry")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    settings_response = client.post(
        "/vcf-private-registry/settings",
        data={
            "enabled": "on",
            "hostname": "registry.labfoundry.internal",
            "listen_interface": "eth2",
            "port": "443",
            "harbor_project": "vcf-supervisor-services",
            "config_path": "/etc/labfoundry/harbor/harbor.yml",
            "ca_bundle_path": "/etc/labfoundry/ca/ca-bundle.pem",
            "server_certificate": "registry.labfoundry.internal",
            "robot_account": "robot$vcf-supervisor-services",
            "relocation_dry_run": "on",
            "csrf": csrf,
        },
        files={"ca_bundle_file": ("registry-ca.pem", "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n", "application/x-pem-file")},
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["status"] == "saved"
    assert settings_response.json()["listen_address"] == "192.168.50.1"
    assert settings_response.json()["listen_addresses"] == ["192.168.50.1"]
    assert settings_response.json()["endpoint"] == "registry.labfoundry.internal"
    assert settings_response.json()["dns_record_action"] == "created"
    assert settings_response.json()["ca_bundle_source"] == "uploaded"
    assert settings_response.json()["ca_bundle_uploaded_name"] == "registry-ca.pem"
    assert settings_response.json()["ca_bundle_available"] is True
    assert settings_response.json()["validation_warnings"] == []
    assert "hostname: registry.labfoundry.internal" in settings_response.json()["harbor_config_preview"]
    assert "<provisioned-by-labfoundry-helper>" in settings_response.json()["harbor_config_preview"]
    with SessionLocal() as db:
        dns_record = db.execute(
            select(DnsRecord).where(
                DnsRecord.hostname == "registry.labfoundry.internal",
                DnsRecord.record_type == "A",
            )
        ).scalar_one()
        assert dns_record.address == "192.168.50.1"
        assert dns_record.enabled is True

    multi_response = client.post(
        "/vcf-private-registry/settings",
        data={
            "enabled": "on",
            "hostname": "registry.labfoundry.internal",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth2", "eth0"],
            "listen_addresses": ["192.168.50.1", "192.168.49.1"],
            "port": "443",
            "harbor_project": "vcf-supervisor-services",
            "server_certificate": "registry.labfoundry.internal",
            "robot_account": "robot$vcf-supervisor-services",
            "relocation_dry_run": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert multi_response.status_code == 200
    assert multi_response.json()["listen_interfaces"] == ["eth2", "eth0"]
    assert multi_response.json()["listen_addresses"] == ["192.168.50.1", "192.168.49.1"]
    assert "labfoundry_listen_interfaces: ['eth2', 'eth0']" in multi_response.json()["harbor_config_preview"]

    moved_response = client.post(
        "/vcf-private-registry/settings",
        data={
            "enabled": "on",
            "hostname": "registry.labfoundry.internal",
            "listen_interface": "eth0",
            "port": "443",
            "harbor_project": "vcf-supervisor-services",
            "ca_bundle_path": "/etc/labfoundry/ca/ca-bundle.pem",
            "server_certificate": "registry.labfoundry.internal",
            "robot_account": "robot$vcf-supervisor-services",
            "relocation_dry_run": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert moved_response.status_code == 200
    assert moved_response.json()["listen_address"] == "192.168.49.1"
    assert moved_response.json()["dns_record_action"] == "updated"
    assert moved_response.json()["ca_bundle_source"] == "uploaded"
    with SessionLocal() as db:
        dns_record = db.execute(
            select(DnsRecord).where(
                DnsRecord.hostname == "registry.labfoundry.internal",
                DnsRecord.record_type == "A",
            )
        ).scalar_one()
        assert dns_record.address == "192.168.49.1"

    bundle_response = client.post(
        "/vcf-private-registry/bundles",
        data={
            "name": "sample-supervisor-service",
            "source_reference": "projects.registry.vmware.com/sample/supervisor-service:1.0.0",
            "target_reference": "",
            "enabled": "on",
            "status": "planned",
            "notes": "sample relocation",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert bundle_response.status_code == 303
    refreshed = client.get("/vcf-private-registry")
    assert "sample-supervisor-service" in refreshed.text
    assert "imgpkg copy -b projects.registry.vmware.com/sample/supervisor-service:1.0.0" in refreshed.text
    assert "registry.labfoundry.internal/vcf-supervisor-services/supervisor-service" in refreshed.text

    token_page = client.post(
        "/authentication/api-tokens",
        data={
            "name": "vcf-registry-status-test",
            "description": "",
            "scopes": "read:vcf-registry",
            "csrf": csrf,
        },
    )
    raw_token = token_page.text.split('<textarea readonly rows="5">', 1)[1].split("</textarea>", 1)[0]
    status = client.get("/api/v1/vcf-private-registry/status", headers={"Authorization": f"Bearer {raw_token}"})
    assert status.status_code == 200
    assert status.json()["hostname"] == "registry.labfoundry.internal"
    assert status.json()["endpoint"] == "registry.labfoundry.internal"
    assert status.json()["bundle_count"] == 1

    apply_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "vcf_private_registry"})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "vcf-private-registry" in (job.result or "")
        assert "imgpkg copy" in (job.result or "")
        assert "provisioned-by-labfoundry-helper" not in (job.result or "")
        assert "password123" not in (job.result or "").lower()


def make_vcfdt_archive(path, version="9.1.0.0100.25429019"):
    import io
    import tarfile

    with tarfile.open(path, "w:gz") as archive:
        payload = version.encode("utf-8")
        info = tarfile.TarInfo("conf/tool-version.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        properties_payload = b"spring.profiles.active=depot\nlcm.depot.adapter.host=archive.example.test\n"
        properties_info = tarfile.TarInfo("conf/application-prodv2.properties")
        properties_info.size = len(properties_payload)
        archive.addfile(properties_info, io.BytesIO(properties_payload))


def test_vcf_offline_depot_page_redirect_and_uploads_are_sanitized(client, tmp_path, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, Job, Setting
    from labfoundry.app.services.vcf_offline_depot import (
        VCF_DEPOT_APPLICATION_PROPERTIES_CONTENT_KEY,
        VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY,
        VCF_DEPOT_TOKEN_VALUE_KEY,
    )

    monkeypatch.setattr("labfoundry.app.ui.find_local_vcf_download_tool_archive", lambda: None)

    login(client)
    legacy = client.get("/https-repository", follow_redirects=False)
    assert legacy.status_code == 307
    assert legacy.headers["location"] == "/vcf-offline-depot"

    page = client.get("/vcf-offline-depot")
    assert page.status_code == 200
    assert "VCF Offline Depot" in page.text
    assert "HTTPS Repository" not in page.text
    assert "Download Profiles" in page.text
    assert "Tool & Credentials" not in page.text
    assert "Review appliance changes" in page.text
    assert "VCF Download Tool" in page.text
    assert "Add or update the VCF Download Tool package" in page.text
    assert "no package staged" in page.text
    assert ">Add</strong>" in page.text
    assert "Reset VCFDT package" in page.text
    assert "Also reset saved application-prodv2.properties configuration" in page.text
    assert 'data-vcf-depot-tool-reset-action>Reset</button>' in page.text
    assert 'button danger compact-button hidden' in page.text
    assert "Stage download token" in page.text
    assert ">Token</button>" in page.text
    assert 'data-vcf-depot-token-modal-open data-vcf-depot-requires-tool disabled' in page.text
    assert "Choose a token file or paste token text." in page.text
    assert 'action="/vcf-offline-depot/download-token"' in page.text
    assert "vcf-depot-token-modal" in page.text
    assert 'data-vcf-depot-token-modal-open' in page.text
    assert 'name="download_token_file"' in page.text
    assert 'name="download_token_text"' in page.text
    assert "Stage activation code" in page.text
    assert ">Code</button>" in page.text
    assert 'action="/vcf-offline-depot/activation-code"' in page.text
    assert "vcf-depot-activation-modal" in page.text
    assert 'data-vcf-depot-activation-modal-open' in page.text
    assert 'name="activation_code_file"' in page.text
    assert 'name="activation_code_text"' in page.text
    assert "Edit application-prodv2.properties" in page.text
    assert ">Edit</button>" in page.text
    assert 'data-vcf-depot-properties-modal-open data-vcf-depot-requires-tool disabled' in page.text
    assert 'action="/vcf-offline-depot/application-properties"' in page.text
    assert 'name="application_properties"' in page.text
    assert "Save configuration" in page.text
    assert "lcm.depot.adapter.host=dl.broadcom.com" in page.text
    assert "/vcf-offline-depot/profiles/" in page.text
    assert "Start" in page.text
    assert page.text.index("<th>Name</th>") < page.text.index("<th>Start</th>") < page.text.index("<th>Type</th>")
    assert 'href="/logs"' in page.text
    assert "Generate software depot ID" in page.text
    assert 'data-vcf-depot-requires-tool disabled>Generate software depot ID</button>' in page.text
    assert "Software depot ID" in page.text
    assert "VCFDT staging" in page.text
    assert "Staged VCFDT inputs" not in page.text
    depot_settings_index = page.text.index("<h2>Depot Settings</h2>")
    vcfdt_staging_index = page.text.index("VCFDT staging")
    assert depot_settings_index < vcfdt_staging_index < page.text.index("VCF Download Tool", vcfdt_staging_index) < page.text.index("Software depot ID")
    assert '<span class="status-pill warn">dry-run</span>' not in page.text
    assert "Activation code" in page.text
    assert "Choose activation file" in page.text
    assert "Choose VCFDT archive" not in page.text
    assert "DNS record follows the selected listen address." in page.text
    assert "Server certificate" not in page.text
    assert 'name="server_certificate"' not in page.text
    assert "Telemetry choice" not in page.text
    assert "<span>Telemetry</span>" in page.text
    assert 'name="telemetry_enabled"' in page.text
    assert 'name="telemetry_choice"' not in page.text
    assert "stacked-service-bind-editor" in page.text
    assert "depot-port-telemetry-row" not in page.text
    assert 'data-vcf-depot-software-depot-cell' in page.text
    assert 'data-vcf-depot-software-depot-id' in page.text
    assert 'data-autosave-upload-progress' in page.text
    assert "not generated" not in page.text
    assert "<span>Tool file</span>" not in page.text
    assert 'data-vcf-depot-tool-name' not in page.text
    assert 'data-tab-storage-key="labfoundry:vcf-offline-depot:active-tab"' in page.text
    assert "/mnt/labfoundry-vcf-offline-depot" in page.text
    assert "Depot store volume" in page.text
    assert page.text.count("fixed-value-field") >= 1
    assert "depot.labfoundry.internal" in page.text
    assert "eth0 - management / access" not in page.text
    assert "eth1 - access / trunk" not in page.text
    assert "eth2 - access / access / 192.168.50.1" in page.text
    assert "Listen interfaces" in page.text
    assert "Listen addresses" in page.text
    assert 'data-tag-name="listen_addresses"' not in page.text
    assert "Listen addresses" in page.text
    assert "service-bind-editor" in page.text
    assert 'data-service-bind-address="192.168.50.1"' in page.text
    assert '<input class="readonly-inline-value hidden" type="text" value="" readonly data-vcf-depot-software-depot-id aria-label="Software depot ID">' in page.text
    assert 'action="/vcf-offline-depot/settings"' in page.text
    assert 'data-autosave-status-id="vcf-depot-settings-status"' in page.text
    assert 'data-components=' in page.text
    assert 'data-esx-platforms=' in page.text
    assert "VCF_OBSERVABILITY_DATA_PLATFORM" in page.text
    assert "VSAN_FILE_SERVICES" in page.text
    assert "embeddedEsx-6.7-INT" in page.text
    assert "esxio-9.1-INTL" in page.text
    assert 'href="/appliance-apply"' in page.text
    assert "vcf-download-tool binaries list" in page.text

    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "initializeVcfDepotSettings" in app_js.text
    assert "initializeVcfDepotProfilesTable" in app_js.text
    assert "All components" in app_js.text
    assert "componentValues" in app_js.text
    assert "esxPlatformValues" in app_js.text
    assert "vcfDepotDisabledPlatformsEditor" in app_js.text
    assert "vcfDepotRememberActiveTab" in app_js.text
    assert "tabulator-checklist-option" in app_js.text
    assert "tool staged" in app_js.text
    assert "DNS record created for this endpoint." in app_js.text
    assert "Old endpoint DNS record removed." in app_js.text
    assert "updateVcfDepotHttpsPreview" in app_js.text
    assert "updateVcfDepotValidation" in app_js.text
    assert "initializeVcfDepotSoftwareDepotIdGenerator" in app_js.text
    assert "initializeVcfDepotTokenPaste" in app_js.text
    assert "initializeVcfDepotActivationPaste" in app_js.text
    assert "initializeVcfDepotPropertiesEditor" in app_js.text
    assert "initializeCopyValueButtons" in app_js.text
    assert "clearSelectedFileInputs" in app_js.text
    assert "Uploaded ${payload.tool_archive_name" in app_js.text
    assert "autosaveErrorFromText" in app_js.text
    assert "copyTextWithTextareaFallback" in app_js.text
    assert "window.isSecureContext" in app_js.text
    assert "softwareDepotId instanceof HTMLInputElement" in app_js.text
    assert "setVcfDepotToolDependentActions" in app_js.text
    assert "startVcfDepotProfileDownload" in app_js.text
    assert 'label: "Start download"' in app_js.text
    profiles_table_js = app_js.text.split("function initializeVcfDepotProfilesTable", 1)[1]
    assert profiles_table_js.index('title: "Name"') < profiles_table_js.index('title: "Start"') < profiles_table_js.index('title: "Type"')

    app_css = client.get("/static/app.css")
    assert app_css.status_code == 200
    assert ".tabulator-checklist-editor" in app_css.text
    assert ".inline-action-row" in app_css.text
    assert ".setting-inline-actions" in app_css.text
    assert ".readonly-inline-value" in app_css.text
    assert ".icon-button" in app_css.text
    assert ".code-editor-textarea" in app_css.text
    assert ".code-editor-textarea + .cm-editor" in app_css.text
    assert "#vcf-depot-properties-modal .confirm-modal-panel" in app_css.text
    assert ".vcfdt-tool-manager" in app_css.text
    assert ".compact-file-upload" in app_css.text
    assert 'data-codemirror-editor data-codemirror-language="labfoundry-hosts" data-vcf-depot-properties-textarea' in page.text

    archive_path = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    make_vcfdt_archive(archive_path)
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/vcf-offline-depot/settings",
        data={
            "enabled": "on",
            "hostname": "depot.labfoundry.internal",
            "listen_interface": "eth2",
            "port": "443",
            "csrf": csrf,
        },
        files={
            "tool_archive_file": ("vcf-download-tool-9.1.0.test.tar.gz", archive_path.read_bytes(), "application/gzip"),
            "download_token_file": ("download-token.txt", "super-secret-token", "text/plain"),
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["listen_address"] == "192.168.50.1"
    assert payload["listen_addresses"] == ["192.168.50.1"]
    assert payload["endpoint"] == "depot.labfoundry.internal"
    assert payload["server_certificate"] == "depot.labfoundry.internal"
    assert payload["telemetry_choice"] == "DISABLE"
    assert payload["tool_archive_name"] == "vcf-download-tool-9.1.0.test.tar.gz"
    assert payload["tool_version"] == "9.1.0.0100.25429019"
    assert payload["software_depot_id"] == ""
    assert "vcf-download-tool executable" in payload["software_depot_id_error"]
    assert payload["download_token_present"] is True
    assert payload["application_properties_present"] is True
    assert payload["application_properties_source"] == "VCFDT default"
    assert payload["valid"] is True
    assert payload["dns_record_action"] == "created"
    assert "listen 192.168.50.1:443 ssl;" in payload["https_config_preview"]
    assert "alias /mnt/labfoundry-vcf-offline-depot/PROD/;" in payload["https_config_preview"]
    assert "root /mnt/labfoundry-vcf-offline-depot;" not in payload["https_config_preview"]
    assert "--depot-store=/mnt/labfoundry-vcf-offline-depot" in payload["command_preview"]
    assert "super-secret-token" not in response.text
    assert "archive.example.test" not in response.text

    multi_response = client.post(
        "/vcf-offline-depot/settings",
        data={
            "enabled": "on",
            "hostname": "depot.labfoundry.internal",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth0", "eth2"],
            "listen_addresses": ["192.168.49.1", "192.168.50.1"],
            "port": "443",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert multi_response.status_code == 200
    multi_payload = multi_response.json()
    assert multi_payload["listen_interfaces"] == ["eth0", "eth2"]
    assert multi_payload["listen_addresses"] == ["192.168.49.1", "192.168.50.1"]
    assert multi_payload["valid"] is False
    assert any("Listen interface eth0 uses the management role" in error for error in multi_payload["validation_errors"])
    assert "listen 192.168.49.1:443 ssl;" in multi_payload["https_config_preview"]
    assert "listen 192.168.50.1:443 ssl;" in multi_payload["https_config_preview"]

    with SessionLocal() as db:
        token_secret = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_VALUE_KEY)).scalar_one()
        software_id_error = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY)).scalar_one()
        dns_record = db.execute(
            select(DnsRecord).where(
                DnsRecord.hostname == "depot.labfoundry.internal",
                DnsRecord.record_type == "A",
            )
        ).scalar_one()
        assert token_secret.value == "super-secret-token"
        assert "vcf-download-tool executable" in software_id_error.value
        assert dns_record.address == "192.168.49.1"
        assert dns_record.enabled is True

    moved_response = client.post(
        "/vcf-offline-depot/settings",
        data={
            "enabled": "on",
            "hostname": "offline-depot.labfoundry.internal",
            "listen_interface": "eth2",
            "port": "443",
            "telemetry_enabled": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert moved_response.status_code == 200
    moved_payload = moved_response.json()
    assert moved_payload["hostname"] == "offline-depot.labfoundry.internal"
    assert moved_payload["server_certificate"] == "offline-depot.labfoundry.internal"
    assert moved_payload["telemetry_choice"] == "ENABLE"
    assert moved_payload["listen_address"] == "192.168.50.1"
    assert moved_payload["valid"] is True
    assert moved_payload["dns_record_action"] == "created+removed-old"
    with SessionLocal() as db:
        old_dns_record = db.execute(
            select(DnsRecord).where(
                DnsRecord.hostname == "depot.labfoundry.internal",
                DnsRecord.record_type == "A",
            )
        ).scalar_one_or_none()
        new_dns_record = db.execute(
            select(DnsRecord).where(
                DnsRecord.hostname == "offline-depot.labfoundry.internal",
                DnsRecord.record_type == "A",
            )
        ).scalar_one()
        assert old_dns_record is None
        assert new_dns_record.address == "192.168.50.1"

    properties_response = client.post(
        "/vcf-offline-depot/application-properties",
        data={
            "application_properties": "spring.profiles.active=depot\nlcm.depot.adapter.host=stage.example.test\nactivation.code=secret-activation-property\n",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert properties_response.status_code == 200
    properties_payload = properties_response.json()
    assert properties_payload["application_properties_present"] is True
    assert properties_payload["application_properties_source"] == "operator saved"
    assert properties_payload["application_properties_updated_at"]
    assert "secret-activation-property" not in properties_response.text
    with SessionLocal() as db:
        properties_setting = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_APPLICATION_PROPERTIES_CONTENT_KEY)).scalar_one()
        assert "stage.example.test" in properties_setting.value

    raw_token = create_api_token(client, ["read:repository"])
    status = client.get("/api/v1/vcf-offline-depot/status", headers={"Authorization": f"Bearer {raw_token}"})
    assert status.status_code == 200
    assert status.json()["hostname"] == "offline-depot.labfoundry.internal"
    assert status.json()["tool_archive_name"] == "vcf-download-tool-9.1.0.test.tar.gz"
    assert status.json()["software_depot_id"] == ""
    assert "vcf-download-tool executable" in status.json()["software_depot_id_error"]
    assert status.json()["download_token_present"] is True
    assert status.json()["activation_code_present"] is False
    assert status.json()["application_properties_present"] is True
    assert status.json()["application_properties_source"] == "operator saved"
    assert "super-secret" not in status.text
    assert "secret-activation-property" not in status.text
    alias = client.get("/api/v1/repository/status", headers={"Authorization": f"Bearer {raw_token}"})
    assert alias.status_code == 200
    assert alias.json()["endpoint"] == status.json()["endpoint"]

    apply_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "vcf_offline_depot"})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "vcf-offline-depot" in (job.result or "")
        assert "stage-tool" in (job.result or "")
        assert "apply-properties" in (job.result or "")
        assert "vcf-download-tool binaries download" in (job.result or "")
    assert "super-secret-token" not in (job.result or "")
    assert "secret-activation-property" not in (job.result or "")


def test_vcf_offline_depot_rejects_truncated_vcfdt_upload(client, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import VcfOfflineDepotSettings

    monkeypatch.setattr("labfoundry.app.ui.find_local_vcf_download_tool_archive", lambda: None)

    login(client)
    page = client.get("/vcf-offline-depot")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        "/vcf-offline-depot/settings",
        data={
            "hostname": "depot.labfoundry.internal",
            "listen_interface": "eth2",
            "port": "443",
            "csrf": csrf,
        },
        files={
            "tool_archive_file": ("vcf-download-tool-9.1.0.test.tar.gz", b"\x1f\x8b\x08\x00truncated", "application/gzip"),
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 400
    assert "archive appears incomplete or invalid" in response.text
    with SessionLocal() as db:
        settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        assert settings.tool_archive_path == ""


def test_vcf_offline_depot_tool_reset_can_preserve_or_clear_configuration(client, tmp_path, monkeypatch):
    from pathlib import Path

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Setting, VcfOfflineDepotSettings
    from labfoundry.app.services.vcf_offline_depot import (
        VCF_DEPOT_APPLICATION_PROPERTIES_CONTENT_KEY,
        VCF_DEPOT_APPLICATION_PROPERTIES_SOURCE_KEY,
        VCF_DEPOT_APPLICATION_PROPERTIES_UPDATED_AT_KEY,
        VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY,
        VCF_DEPOT_SOFTWARE_DEPOT_ID_GENERATED_AT_KEY,
        VCF_DEPOT_SOFTWARE_DEPOT_ID_KEY,
    )

    monkeypatch.setattr("labfoundry.app.ui.find_local_vcf_download_tool_archive", lambda: None)

    archive_path = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    make_vcfdt_archive(archive_path)
    login(client)
    page = client.get("/vcf-offline-depot")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    upload = client.post(
        "/vcf-offline-depot/settings",
        data={"hostname": "depot.labfoundry.internal", "listen_interface": "eth2", "port": "443", "csrf": csrf},
        files={"tool_archive_file": ("vcf-download-tool-9.1.0.test.tar.gz", archive_path.read_bytes(), "application/gzip")},
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert upload.status_code == 200
    assert upload.json()["tool_archive_name"] == "vcf-download-tool-9.1.0.test.tar.gz"

    refreshed = client.get("/vcf-offline-depot")
    assert ">Update</strong>" in refreshed.text
    assert 'data-vcf-depot-tool-reset-action>Reset</button>' in refreshed.text
    assert 'button danger compact-button hidden' not in refreshed.text

    properties = client.post(
        "/vcf-offline-depot/application-properties",
        data={"csrf": csrf, "application_properties": "spring.profiles.active=depot\ncustom.setting=true\n"},
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert properties.status_code == 200
    with SessionLocal() as db:
        settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        stored_archive = Path(settings.tool_archive_path)
        assert stored_archive.exists()
        assert settings.tool_version == "9.1.0.0100.25429019"

    reset = client.post("/vcf-offline-depot/tool/reset", data={"csrf": csrf}, follow_redirects=False)
    assert reset.status_code == 303
    with SessionLocal() as db:
        settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        assert settings.tool_archive_path == ""
        assert settings.tool_version == ""
        assert not stored_archive.exists()
        for key in [VCF_DEPOT_SOFTWARE_DEPOT_ID_KEY, VCF_DEPOT_SOFTWARE_DEPOT_ID_GENERATED_AT_KEY, VCF_DEPOT_SOFTWARE_DEPOT_ID_ERROR_KEY]:
            assert db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none() is None
        properties_setting = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_APPLICATION_PROPERTIES_CONTENT_KEY)).scalar_one()
        assert "custom.setting=true" in properties_setting.value

    reset_page = client.get("/vcf-offline-depot")
    assert "no package staged" in reset_page.text
    assert "operator saved · saved" in reset_page.text
    assert 'data-vcf-depot-properties-modal-open data-vcf-depot-requires-tool disabled' in reset_page.text

    upload_again = client.post(
        "/vcf-offline-depot/settings",
        data={"hostname": "depot.labfoundry.internal", "listen_interface": "eth2", "port": "443", "csrf": csrf},
        files={"tool_archive_file": ("vcf-download-tool-9.1.0.test.tar.gz", archive_path.read_bytes(), "application/gzip")},
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert upload_again.status_code == 200

    reset_with_configuration = client.post(
        "/vcf-offline-depot/tool/reset",
        data={"csrf": csrf, "reset_application_properties": "on"},
        follow_redirects=False,
    )
    assert reset_with_configuration.status_code == 303
    with SessionLocal() as db:
        for key in [
            VCF_DEPOT_APPLICATION_PROPERTIES_CONTENT_KEY,
            VCF_DEPOT_APPLICATION_PROPERTIES_SOURCE_KEY,
            VCF_DEPOT_APPLICATION_PROPERTIES_UPDATED_AT_KEY,
        ]:
            assert db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none() is None


def test_vcf_offline_depot_accepts_pasted_download_token_and_activation_code(client, tmp_path, monkeypatch):
    from pathlib import PurePosixPath

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Setting
    from labfoundry.app.services.vcf_offline_depot import (
        VCF_DEPOT_ACTIVATION_NAME_KEY,
        VCF_DEPOT_ACTIVATION_VALUE_KEY,
        VCF_DEPOT_TOKEN_NAME_KEY,
        VCF_DEPOT_TOKEN_VALUE_KEY,
    )

    runtime_log = tmp_path / "active-tool" / "log" / "vdt.log"
    runtime_token = tmp_path / "active-tool" / "secrets" / "download-token.txt"
    runtime_activation = tmp_path / "active-tool" / "secrets" / "activation-code.txt"
    monkeypatch.setattr("labfoundry.app.ui.VCF_DEPOT_VDT_LOG_PATH", PurePosixPath(runtime_log.as_posix()))

    login(client)
    page = client.get("/vcf-offline-depot")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        "/vcf-offline-depot/download-token",
        data={"download_token_text": "pasted-secret-token", "csrf": csrf},
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["download_token_present"] is True
    assert payload["download_token_name"] == "pasted token"
    assert payload["download_token_updated_at"]
    assert "--depot-download-token-file=/var/lib/labfoundry/vcfDownloadTool/active-tool/secrets/download-token.txt" in payload["command_preview"]
    assert "pasted-secret-token" not in response.text
    assert runtime_token.read_text(encoding="utf-8") == "pasted-secret-token"

    with SessionLocal() as db:
        token_name = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_NAME_KEY)).scalar_one()
        token_secret = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_VALUE_KEY)).scalar_one()
        assert token_name.value == "pasted token"
        assert token_secret.value == "pasted-secret-token"

    upload_response = client.post(
        "/vcf-offline-depot/download-token",
        data={"download_token_text": "", "csrf": csrf},
        files={"download_token_file": ("download-token.txt", "uploaded-secret-token", "text/plain")},
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    assert upload_payload["download_token_present"] is True
    assert upload_payload["download_token_name"] == "download-token.txt"
    assert "uploaded-secret-token" not in upload_response.text
    assert runtime_token.read_text(encoding="utf-8") == "uploaded-secret-token"

    with SessionLocal() as db:
        token_name = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_NAME_KEY)).scalar_one()
        token_secret = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_TOKEN_VALUE_KEY)).scalar_one()
        assert token_name.value == "download-token.txt"
        assert token_secret.value == "uploaded-secret-token"

    activation_response = client.post(
        "/vcf-offline-depot/activation-code",
        data={"activation_code_text": "pasted-secret-activation-code", "csrf": csrf},
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert activation_response.status_code == 200
    activation_payload = activation_response.json()
    assert activation_payload["activation_code_present"] is True
    assert activation_payload["activation_code_name"] == "pasted activation code"
    assert "--depot-download-activation-code-file=/var/lib/labfoundry/vcfDownloadTool/active-tool/secrets/activation-code.txt" in activation_payload["command_preview"]
    assert "pasted-secret-activation-code" not in activation_response.text
    assert runtime_activation.read_text(encoding="utf-8") == "pasted-secret-activation-code"

    with SessionLocal() as db:
        activation_name = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_ACTIVATION_NAME_KEY)).scalar_one()
        activation_secret = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_ACTIVATION_VALUE_KEY)).scalar_one()
        assert activation_name.value == "pasted activation code"
        assert activation_secret.value == "pasted-secret-activation-code"

    activation_upload_response = client.post(
        "/vcf-offline-depot/activation-code",
        data={"activation_code_text": "", "csrf": csrf},
        files={"activation_code_file": ("activation-code.txt", "uploaded-secret-activation-code", "text/plain")},
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert activation_upload_response.status_code == 200
    activation_upload_payload = activation_upload_response.json()
    assert activation_upload_payload["activation_code_present"] is True
    assert activation_upload_payload["activation_code_name"] == "activation-code.txt"
    assert "uploaded-secret-activation-code" not in activation_upload_response.text
    assert runtime_activation.read_text(encoding="utf-8") == "uploaded-secret-activation-code"

    with SessionLocal() as db:
        activation_name = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_ACTIVATION_NAME_KEY)).scalar_one()
        activation_secret = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_ACTIVATION_VALUE_KEY)).scalar_one()
        assert activation_name.value == "activation-code.txt"
        assert activation_secret.value == "uploaded-secret-activation-code"

    apply_page = client.get("/appliance-apply")
    assert apply_page.status_code == 200
    assert "Download input file: staged" in apply_page.text
    assert "ESX input file: staged" in apply_page.text
    assert "pasted-secret-token" not in apply_page.text
    assert "uploaded-secret-token" not in apply_page.text
    assert "pasted-secret-activation-code" not in apply_page.text
    assert "uploaded-secret-activation-code" not in apply_page.text


def test_vcf_offline_depot_manual_profile_download_starts_job(client, tmp_path, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, Setting, VcfDepotDownloadProfile, VcfOfflineDepotSettings
    from labfoundry.app.services.vcf_offline_depot import (
        VCF_DEPOT_TOKEN_NAME_KEY,
        VCF_DEPOT_TOKEN_VALUE_KEY,
    )

    archive_path = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    make_vcfdt_archive(archive_path)
    queued: list[tuple[str, int]] = []
    monkeypatch.setattr("labfoundry.app.ui.queue_vcf_depot_download_job", lambda job_id, profile_id: queued.append((job_id, profile_id)))
    with SessionLocal() as db:
        settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        settings.tool_archive_path = str(archive_path)
        settings.tool_version = "9.1.0"
        db.add(Setting(key=VCF_DEPOT_TOKEN_NAME_KEY, value="download-token.txt"))
        db.add(Setting(key=VCF_DEPOT_TOKEN_VALUE_KEY, value="manual-secret-token"))
        profile = VcfDepotDownloadProfile(
            name="vcf-install",
            profile_type="binaries",
            sku="VCF",
            vcf_version="9.1.0",
            binary_type="INSTALL",
            enabled=True,
        )
        db.add(profile)
        db.commit()
        profile_id = profile.id

    login(client)
    page = client.get("/vcf-offline-depot")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        f"/vcf-offline-depot/profiles/{profile_id}/download",
        data={"csrf": csrf},
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "started"
    assert payload["profile_name"] == "vcf-install"
    assert payload["profile_status"] == "ready"
    assert payload["dry_run"] is False
    assert payload["log_path"] == "/var/lib/labfoundry/vcfDownloadTool/active-tool/log/vdt.log"
    assert len(payload["commands"]) == 2
    assert payload["commands"][0]["command"][0] == "/var/lib/labfoundry/vcfDownloadTool/active-tool/bin/vcf-download-tool"
    assert "--depot-download-token-file=/var/lib/labfoundry/vcfDownloadTool/active-tool/secrets/download-token.txt" in payload["commands"][0]["command"]
    assert "manual-secret-token" not in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "vcf-depot-download")).scalar_one()
        profile = db.get(VcfDepotDownloadProfile, profile_id)
        assert queued == [(job.id, profile_id)]
        assert job.status == "pending"
        assert '"profile_name": "vcf-install"' in (job.result or "")
        assert '"dry_run": false' in (job.result or "")
        assert '"log_path": "/var/lib/labfoundry/vcfDownloadTool/active-tool/log/vdt.log"' in (job.result or "")
        assert "/var/lib/labfoundry/vcfDownloadTool/active-tool/bin/vcf-download-tool" in (job.result or "")
        assert "--depot-download-token-file=/var/lib/labfoundry/vcfDownloadTool/active-tool/secrets/download-token.txt" in (job.result or "")
        assert "manual-secret-token" not in (job.result or "")
        assert profile and profile.status == "ready"


def test_vcf_offline_depot_generates_software_depot_id(client, tmp_path, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Setting, VcfOfflineDepotSettings
    from labfoundry.app.services.vcf_offline_depot import (
        SoftwareDepotIdResult,
        VCF_DEPOT_SOFTWARE_DEPOT_ID_GENERATED_AT_KEY,
        VCF_DEPOT_SOFTWARE_DEPOT_ID_KEY,
    )

    archive_path = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    archive_path.write_bytes(b"placeholder")
    with SessionLocal() as db:
        settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        settings.tool_archive_path = str(archive_path)
        settings.tool_version = "9.1.0"
        db.commit()

    def fake_generate(archive_path_value):
        assert str(archive_path_value) == str(archive_path)
        return SoftwareDepotIdResult(
            success=True,
            software_depot_id="8c9506c6-7bdf-44d5-b2e9-50d829d66b99",
            command=["vcf-download-tool", "configuration", "generate", "--software-depot-id"],
        )

    monkeypatch.setattr("labfoundry.app.ui.generate_vcf_software_depot_id", fake_generate)
    login(client)
    page = client.get("/vcf-offline-depot")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        "/vcf-offline-depot/software-depot-id/generate",
        data={"csrf": csrf},
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "generated"
    assert payload["software_depot_id"] == "8c9506c6-7bdf-44d5-b2e9-50d829d66b99"
    assert payload["software_depot_id_error"] == ""
    assert payload["software_depot_id_generated_at"]
    with SessionLocal() as db:
        software_id = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_SOFTWARE_DEPOT_ID_KEY)).scalar_one()
        generated_at = db.execute(select(Setting).where(Setting.key == VCF_DEPOT_SOFTWARE_DEPOT_ID_GENERATED_AT_KEY)).scalar_one()
        assert software_id.value == "8c9506c6-7bdf-44d5-b2e9-50d829d66b99"
        assert generated_at.value == payload["software_depot_id_generated_at"]


def test_vcf_offline_depot_migrates_legacy_store_path(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import VcfOfflineDepotSettings

    with SessionLocal() as db:
        settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        settings.depot_store_path = "/srv/repository"
        db.commit()

    login(client)
    page = client.get("/vcf-offline-depot")

    assert page.status_code == 200
    assert "/mnt/labfoundry-vcf-offline-depot" in page.text
    assert "/srv/repository" not in page.text
    with SessionLocal() as db:
        settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        assert settings.depot_store_path == "/mnt/labfoundry-vcf-offline-depot"


def test_vcf_private_registry_uses_local_ca_bundle_when_ca_is_enabled(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import CaSettings

    with SessionLocal() as db:
        ca_settings = db.execute(select(CaSettings)).scalar_one()
        ca_settings.enabled = True
        ca_settings.storage_path = "/etc/labfoundry/ca"
        db.commit()

    login(client)
    page = client.get("/vcf-private-registry")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    assert "CA bundle source" in page.text
    assert "Local CA" in page.text
    assert "Upload CA bundle" not in page.text

    response = client.post(
        "/vcf-private-registry/settings",
        data={
            "enabled": "on",
            "hostname": "registry.labfoundry.internal",
            "listen_interface": "eth2",
            "port": "443",
            "harbor_project": "vcf-supervisor-services",
            "server_certificate": "registry.labfoundry.internal",
            "robot_account": "robot$vcf-supervisor-services",
            "relocation_dry_run": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["ca_bundle_source"] == "local-ca"
    assert response.json()["ca_bundle_source_label"] == "Local CA"
    assert response.json()["ca_bundle_path"] == "/etc/labfoundry/ca/ca-bundle.pem"
    assert response.json()["ca_bundle_available"] is True
    assert response.json()["validation_errors"] == []


def test_vcf_backups_listen_interfaces_include_vlans_not_trunks(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import VlanInterface

    with SessionLocal() as db:
        db.add(
            VlanInterface(
                name="eth1.60",
                parent_interface="eth1",
                vlan_id=60,
                ip_cidr="192.168.60.1/24",
                role="services",
                enabled=True,
            )
        )
        db.commit()

    login(client)
    page = client.get("/vcf-backups")
    assert page.status_code == 200
    assert "eth1 - access / trunk" not in page.text
    assert "eth1.60 - VLAN 60 on eth1 / services / 192.168.60.1" in page.text


def test_vcf_backups_settings_autosave_and_status_api(client):
    import re

    login(client)
    page = client.get("/vcf-backups")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    user_id = re.search(r'<option value="(\d+)" selected>vcf-backup(?: \(disabled\))?</option>', page.text).group(1)
    reset = client.post(
        f"/users/{user_id}/password",
        data={"password": "Backup-user1!", "confirm_password": "Backup-user1!", "csrf": csrf},
    )
    assert reset.status_code in {200, 303}
    response = client.post(
        "/vcf-backups/settings",
        data={
            "enabled": "on",
            "listen_interface": "eth2",
            "port": "22",
            "sftp_user_id": user_id,
            "storage_path": "/srv/vcf-backups",
            "chroot_enabled": "on",
            "allow_password_auth": "on",
            "allow_public_key_auth": "on",
            "max_sessions": "4",
            "config_path": "/etc/labfoundry/ssh/sshd_config.d/labfoundry-vcf-backups.conf",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert response.json()["listen_interface"] == "eth2"
    assert response.json()["listen_address"] == "192.168.50.1"
    assert response.json()["sftp_username"] == "vcf-backup"
    assert response.json()["storage_path"] == "/mnt/labfoundry-vcf-backups"
    assert response.json()["remote_directory"] == "/backups"
    assert response.json()["valid"] is True
    assert "# Service listener targets: 192.168.50.1:22" in response.json()["config_preview"]
    assert "Match User vcf-backup" in response.json()["config_preview"]
    assert "ForceCommand internal-sftp -d /backups" in response.json()["config_preview"]
    assert "enabled" in client.get("/vcf-backups").text

    token_page = client.post(
        "/authentication/api-tokens",
        data={
            "name": "vcf-status-test",
            "description": "",
            "scopes": "read:vcf-backups",
            "csrf": csrf,
        },
    )
    raw_token = token_page.text.split('<textarea readonly rows="5">', 1)[1].split("</textarea>", 1)[0]
    status = client.get("/api/v1/vcf-backups/status", headers={"Authorization": f"Bearer {raw_token}"})
    assert status.status_code == 200
    assert status.json()["listen_interface"] == "eth2"
    assert status.json()["listen_address"] == "192.168.50.1"
    assert status.json()["sftp_username"] == "vcf-backup"
    assert status.json()["storage_path"] == "/mnt/labfoundry-vcf-backups"
    assert status.json()["remote_directory"] == "/backups"


def test_vcf_backups_settings_accept_multiple_listen_targets(client):
    import re

    login(client)
    page = client.get("/vcf-backups")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    user_id = re.search(r'<option value="(\d+)" selected>vcf-backup(?: \(disabled\))?</option>', page.text).group(1)
    response = client.post(
        "/vcf-backups/settings",
        data={
            "enabled": "on",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth0", "eth2"],
            "listen_addresses": ["192.168.49.1", "192.168.50.1"],
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

    assert response.status_code == 200
    payload = response.json()
    assert payload["listen_interfaces"] == ["eth0", "eth2"]
    assert payload["listen_addresses"] == ["192.168.49.1", "192.168.50.1"]
    assert "# Listen interfaces: eth0, eth2" in payload["config_preview"]
    assert "# Service listener targets: 192.168.49.1:22, 192.168.50.1:22" in payload["config_preview"]


def test_vcf_backups_disabled_disables_default_backup_user(client):
    import re

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import User

    login(client)
    page = client.get("/vcf-backups")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    user_id = re.search(r'<option value="(\d+)" selected>vcf-backup(?: \(disabled\))?</option>', page.text).group(1)
    reset = client.post(
        f"/users/{user_id}/password",
        data={"password": "Backup-user1!", "confirm_password": "Backup-user1!", "csrf": csrf},
    )
    assert reset.status_code in {200, 303}

    disabled_service = client.post(
        "/vcf-backups/settings",
        data={
            "listen_interface": "eth2",
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

    assert disabled_service.status_code == 200
    with SessionLocal() as db:
        backup_user = db.execute(select(User).where(User.username == "vcf-backup")).scalar_one()
        assert backup_user.enabled is False
        assert backup_user.os_sync_status == "pending"
    apply_page = client.get("/appliance-apply")
    assert "Local Users" in apply_page.text
    assert "pending OS passwords" in apply_page.text


def test_vcf_backups_apply_task_captures_sftp_config(client):
    import re

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/vcf-backups")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    user_id = re.search(r'<option value="(\d+)" selected>vcf-backup(?: \(disabled\))?</option>', page.text).group(1)
    reset = client.post(
        f"/users/{user_id}/password",
        data={"password": "Backup-user1!", "confirm_password": "Backup-user1!", "csrf": csrf},
    )
    assert reset.status_code in {200, 303}
    settings_response = client.post(
        "/vcf-backups/settings",
        data={
            "enabled": "on",
            "listen_interface": "eth2",
            "port": "22",
            "sftp_user_id": user_id,
            "chroot_enabled": "on",
            "allow_password_auth": "on",
            "allow_public_key_auth": "on",
            "max_sessions": "4",
            "csrf": csrf,
        },
    )
    assert settings_response.status_code == 200
    page = client.get("/vcf-backups")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "vcf_backups"})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "vcf-backups" in (job.result or "")
        assert "internal-sftp" in (job.result or "")


def test_appliance_apply_unit_keeps_raw_config_for_helper_staging():
    from labfoundry.app.ui import make_appliance_apply_unit

    unit = make_appliance_apply_unit(
        unit_id="vcf_backups",
        label="VCF Backups",
        page_url="/vcf-backups",
        context={},
        summary=["service enabled"],
        validation_errors=[],
        config_path="/etc/ssh/sshd_config.d/labfoundry-vcf-backups.conf",
        config_preview="Match User vcf-backup\n  PasswordAuthentication yes\n  ForceCommand internal-sftp -d /backups\n",
        baseline=None,
    )

    assert "PasswordAuthentication yes" in unit["raw_config_preview"]
    assert "[redacted sensitive line]" in unit["config_preview"]
    assert "PasswordAuthentication yes" not in unit["config_preview"]


def test_physical_and_vlan_pages_render(client):
    login(client)
    physical = client.get("/physical-interfaces")
    assert physical.status_code == 200
    assert "Physical Interfaces" in physical.text
    assert "Review observed Photon NICs, then edit desired access, trunk, IPv4, IPv6, and admin state" in physical.text
    assert "physical-interfaces-table" in physical.text
    assert "Refresh host inventory" in physical.text
    assert "Observed IPv4" in physical.text
    assert "Observed IPv6" in physical.text
    assert "IPv4 CIDR" in physical.text
    assert "IPv6 CIDR" in physical.text
    assert "network-state-icon up" in physical.text
    assert "eth0" in physical.text
    assert "192.168.49.1/24" in physical.text
    assert "192.168.50.1/24" in physical.text
    assert "Link Type" in physical.text
    assert "Review appliance changes" in physical.text
    assert "/var/lib/labfoundry/apply/network/labfoundry-network.conf" in physical.text

    vlans = client.get("/vlan-interfaces")
    assert vlans.status_code == 200
    assert "VLAN Interfaces" in vlans.text
    assert "For standard access-mode NICs, assign IPv4/IPv6 CIDR on Physical Interfaces instead." in vlans.text
    assert "vlan-interfaces-table" in vlans.text
    assert "+ Add VLAN here" in client.get("/static/app.js").text
    assert 'data-parent-options=\'[{"label": "eth1 - access - trunk' in vlans.text
    assert "data-parent-options" in vlans.text
    app_js = client.get("/static/app.js").text
    assert "deleteVlanInterfaceFromMenu" in app_js
    assert "refreshNetworkSideStack" in app_js
    assert "networkStateIcon" in app_js
    assert "operStateFormatter" in app_js
    assert "cidrInputEditor" in app_js
    assert "isValidCidr" in app_js
    assert 'editorParams: { family: "ipv4", placeholder: "192.168.50.1/24" }' in app_js
    assert 'editorParams: { family: "ipv6", placeholder: "fd00:50::1/64" }' in app_js
    app_css = client.get("/static/app.css").text
    assert ".network-state-icon.up" in app_css
    assert ".network-state-icon.down" in app_css
    assert ".network-state-icon.missing" in app_css
    assert ".invalid-cidr-input" in app_css
    assert "Review appliance changes" in vlans.text
    assert "/var/lib/labfoundry/apply/network/labfoundry-network.conf" in vlans.text


def test_physical_interface_refresh_imports_host_inventory_without_apply_job(client, monkeypatch):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, PhysicalInterface, Route, VlanInterface
    from labfoundry.app.services.networking import HostPhysicalInterface

    login(client)

    def fake_discover():
        return [
            HostPhysicalInterface(
                name="ens192",
                mac_address="00:15:5d:aa:bb:cc",
                driver="hv_netvsc",
                speed="10000 Mbps",
                host_ip_cidr="192.168.49.22/24",
                host_mtu=1500,
                host_admin_state="up",
                oper_state="up",
            )
        ]

    monkeypatch.setattr("labfoundry.app.services.networking.discover_host_physical_interfaces", fake_discover)
    page = client.get("/physical-interfaces")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/physical-interfaces/refresh", data={"csrf": csrf}, follow_redirects=False)

    assert response.status_code == 303
    refreshed = client.get("/physical-interfaces")
    assert "ens192" in refreshed.text
    assert "192.168.49.22/24" in refreshed.text
    assert "host" in refreshed.text
    assert "02:15:5d:00:10:02" not in refreshed.text
    assert "02:15:5d:00:10:03" not in refreshed.text

    with SessionLocal() as db:
        interface = db.execute(select(PhysicalInterface).where(PhysicalInterface.name == "ens192")).scalar_one()
        assert interface.inventory_source == "host"
        assert interface.desired_state_source == "seed"
        assert interface.ip_cidr is None
        assert interface.admin_state == "down"
        assert db.execute(select(PhysicalInterface).where(PhysicalInterface.name.in_(["eth0", "eth1", "eth2"]))).scalars().all() == []
        assert db.execute(select(VlanInterface).where(VlanInterface.parent_interface == "eth1")).scalars().all() == []
        assert db.execute(select(Route).where(Route.interface_name == "eth1.20")).scalars().all() == []
        assert db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one_or_none() is None


def test_physical_interface_edit_updates_desired_state(client):
    import html
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import (
        CaSettings,
        ChronySettings,
        DhcpScope,
        DnsRecord,
        DnsSettings,
        KmsSettings,
        VcfBackupSettings,
        VcfOfflineDepotSettings,
        VcfPrivateRegistrySettings,
    )
    from labfoundry.app.services.esxi_pxe import (
        ESXI_PXE_DEFAULT_HOSTNAME,
        ESXI_PXE_DNS_RECORD_DESCRIPTION,
        ESXI_PXE_HTTP_PORT,
        ESXI_TFTP_ROOT,
        esxi_pxe_boot_settings,
        save_esxi_pxe_boot_settings,
    )

    login(client)
    with SessionLocal() as db:
        for model in (
            DnsSettings,
            ChronySettings,
            CaSettings,
            KmsSettings,
            VcfBackupSettings,
            VcfOfflineDepotSettings,
            VcfPrivateRegistrySettings,
        ):
            settings = db.execute(select(model)).scalar_one()
            settings.enabled = True
            settings.listen_interface = "eth2"
            settings.listen_address = "192.168.50.1"
            db.add(settings)
        scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        scope.interface_name = "eth2"
        scope.site_address = "192.168.50.1"
        scope.prefix_length = 24
        scope.range_start = "192.168.50.100"
        scope.range_end = "192.168.50.200"
        scope.dns_server = "192.168.50.1"
        scope.ntp_server = "192.168.50.1"
        db.add(scope)
        save_esxi_pxe_boot_settings(
            db,
            enabled=True,
            hostname=ESXI_PXE_DEFAULT_HOSTNAME,
            listen_interface="eth2",
            listen_address="192.168.50.1",
            dhcp_scope_ids=[scope.id],
            tftp_root=ESXI_TFTP_ROOT.as_posix(),
            http_port=ESXI_PXE_HTTP_PORT,
            bios_bootfile="undionly.kpxe",
            uefi_bootfile="snponly.efi",
            native_uefi_http_enabled=True,
            native_uefi_http_url="http://192.168.50.1:8080/pxe/esxi/mboot.efi",
        )
        db.add(
            DnsRecord(
                hostname=ESXI_PXE_DEFAULT_HOSTNAME,
                record_type="A",
                address="192.168.50.1",
                description=ESXI_PXE_DNS_RECORD_DESCRIPTION,
                enabled=True,
            )
        )
        db.commit()

    page = client.get("/physical-interfaces")
    payload = page.text.split("data-interfaces='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(payload))
    interface_id = next(row["id"] for row in rows if row["name"] == "eth2")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        f"/physical-interfaces/{interface_id}/edit",
        data={
            "role": "wan",
            "mode": "access",
            "ip_cidr": "192.168.70.1/24",
            "mtu": "1400",
            "admin_state": "down",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    refreshed = client.get("/physical-interfaces")
    assert '"role": "wan"' in refreshed.text
    assert '"mode": "access"' in refreshed.text
    assert '"ip_cidr": "192.168.70.1/24"' in refreshed.text
    assert '"mtu": 1400' in refreshed.text
    assert '"admin_state": "down"' in refreshed.text
    assert '"desired_state_source": "user"' in refreshed.text

    with SessionLocal() as db:
        for model in (
            DnsSettings,
            ChronySettings,
            CaSettings,
            KmsSettings,
            VcfBackupSettings,
            VcfOfflineDepotSettings,
            VcfPrivateRegistrySettings,
        ):
            settings = db.execute(select(model)).scalar_one()
            assert settings.listen_interface == "eth2"
            assert settings.listen_address == "192.168.70.1"
        scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        assert scope.interface_name == "eth2"
        assert scope.site_address == "192.168.70.1"
        assert scope.prefix_length == 24
        assert scope.range_start == "192.168.70.100"
        assert scope.range_end == "192.168.70.200"
        assert scope.dns_server == "192.168.70.1"
        assert scope.ntp_server == "192.168.70.1"
        kms_record = db.execute(select(DnsRecord).where(DnsRecord.hostname == "kms.labfoundry.internal", DnsRecord.record_type == "A")).scalar_one()
        assert kms_record.address == "192.168.70.1"
        boot = esxi_pxe_boot_settings(db)
        assert boot["listen_interface"] == "eth2"
        assert boot["listen_address"] == "192.168.70.1"
        assert boot["effective_native_uefi_http_url"] == "http://192.168.70.1:8080/pxe/esxi/mboot.efi"
        pxe_record = db.execute(select(DnsRecord).where(DnsRecord.hostname == ESXI_PXE_DEFAULT_HOSTNAME, DnsRecord.record_type == "A")).scalar_one()
        assert pxe_record.address == "192.168.70.1"


def test_physical_interface_edit_repairs_stale_scope_after_host_inventory_refresh(client):
    import html
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ChronySettings, DhcpScope, DnsRecord, DnsSettings, Setting
    from labfoundry.app.services.esxi_pxe import ESXI_PXE_DEFAULT_HOSTNAME, ESXI_PXE_DNS_RECORD_DESCRIPTION, ESXI_PXE_HTTP_PORT, ESXI_PXE_LISTEN_ADDRESS_KEY, ESXI_TFTP_ROOT, save_esxi_pxe_boot_settings

    login(client)
    with SessionLocal() as db:
        dns_settings = db.execute(select(DnsSettings)).scalar_one()
        dns_settings.enabled = True
        dns_settings.listen_interface = "eth2"
        dns_settings.listen_address = "192.168.1.1"
        chrony_settings = db.execute(select(ChronySettings)).scalar_one()
        chrony_settings.enabled = True
        chrony_settings.listen_interface = "eth2"
        chrony_settings.listen_address = "192.168.1.1"
        scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        scope.interface_name = "eth2"
        scope.site_address = "192.168.1.1"
        scope.prefix_length = 24
        scope.range_start = "192.168.1.100"
        scope.range_end = "192.168.1.120"
        scope.dns_server = "192.168.1.1"
        scope.ntp_server = "192.168.1.1"
        save_esxi_pxe_boot_settings(
            db,
            enabled=True,
            hostname=ESXI_PXE_DEFAULT_HOSTNAME,
            listen_interface="eth2",
            listen_address="192.168.1.1",
            dhcp_scope_ids=[scope.id],
            tftp_root=ESXI_TFTP_ROOT.as_posix(),
            http_port=ESXI_PXE_HTTP_PORT,
            bios_bootfile="undionly.kpxe",
            uefi_bootfile="snponly.efi",
            native_uefi_http_enabled=True,
            native_uefi_http_url="",
        )
        db.add(
            DnsRecord(
                hostname=ESXI_PXE_DEFAULT_HOSTNAME,
                record_type="A",
                address="192.168.1.1",
                description=ESXI_PXE_DNS_RECORD_DESCRIPTION,
                enabled=True,
            )
        )
        db.commit()

    page = client.get("/physical-interfaces")
    payload = page.text.split("data-interfaces='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(payload))
    interface_id = next(row["id"] for row in rows if row["name"] == "eth2")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        f"/physical-interfaces/{interface_id}/edit",
        data={
            "role": "access",
            "mode": "access",
            "ip_cidr": "192.168.50.1/24",
            "mtu": "1500",
            "admin_state": "up",
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        scope = db.execute(select(DhcpScope).where(DhcpScope.name == "SiteA")).scalar_one()
        assert scope.site_address == "192.168.50.1"
        assert scope.range_start == "192.168.50.100"
        assert scope.range_end == "192.168.50.120"
        assert scope.dns_server == "192.168.50.1"
        assert scope.ntp_server == "192.168.50.1"
        pxe_record = db.execute(select(DnsRecord).where(DnsRecord.hostname == ESXI_PXE_DEFAULT_HOSTNAME, DnsRecord.record_type == "A")).scalar_one()
        assert pxe_record.address == "192.168.50.1"
        pxe_listen = db.execute(select(Setting).where(Setting.key == ESXI_PXE_LISTEN_ADDRESS_KEY)).scalar_one()
        assert pxe_listen.value == "192.168.50.1"
        pxe_listen.value = "192.168.1.1"
        db.add(pxe_listen)
        db.commit()

    second_response = client.post(
        f"/physical-interfaces/{interface_id}/edit",
        data={
            "role": "access",
            "mode": "access",
            "ip_cidr": "192.168.50.1/24",
            "mtu": "1500",
            "admin_state": "up",
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert second_response.status_code == 303
    with SessionLocal() as db:
        pxe_listen = db.execute(select(Setting).where(Setting.key == ESXI_PXE_LISTEN_ADDRESS_KEY)).scalar_one()
        assert pxe_listen.value == "192.168.50.1"


def test_physical_interface_link_type_locked_when_vlans_exist(client):
    import html
    import json

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import PhysicalInterface, VlanInterface

    login(client)
    with SessionLocal() as db:
        eth1 = db.query(PhysicalInterface).filter_by(name="eth1").one()
        eth1.mode = "trunk"
        db.add(
            VlanInterface(
                name="eth1.50",
                parent_interface="eth1",
                vlan_id=50,
                ip_cidr="192.168.50.1/24",
                mtu=1500,
                role="access",
                enabled=True,
            )
        )
        db.commit()

    page = client.get("/physical-interfaces")
    payload = page.text.split("data-interfaces='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(payload))
    eth1_row = next(row for row in rows if row["name"] == "eth1")
    assert eth1_row["vlan_count"] >= 1
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    response = client.post(
        f"/physical-interfaces/{eth1_row['id']}/edit",
        data={
            "role": "access",
            "mode": "access",
            "ip_cidr": "",
            "mtu": "1500",
            "admin_state": "up",
            "csrf": csrf,
        },
    )
    assert response.status_code == 409
    assert "Move or delete those VLANs before changing the link type" in response.text


def test_vlan_interface_create_edit_delete_and_apply(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/vlan-interfaces")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/vlan-interfaces",
        data={
            "parent_interface": "eth1",
            "vlan_id": "50",
            "ip_cidr": "192.168.50.1/24",
            "mtu": "1500",
            "role": "services",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    page = client.get("/vlan-interfaces")
    assert "eth1.50" in page.text
    assert "192.168.50.1/24" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    apply_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "network"})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert "labfoundry-helper" in (job.result or "")
        assert "eth1.50" in (job.result or "")

    page = client.get("/vlan-interfaces")
    import html
    import json

    payload = page.text.split("data-vlans='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(payload))
    vlan_id = next(row["id"] for row in rows if row["name"] == "eth1.50")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    deleted = client.post(f"/vlan-interfaces/{vlan_id}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert deleted.status_code == 303
    assert "eth1.50" not in client.get("/vlan-interfaces").text


def test_vlan_page_prefers_real_trunk_parent_when_inventory_has_eth2(client):
    import html
    import json

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import PhysicalInterface

    login(client)
    with SessionLocal() as db:
        db.query(PhysicalInterface).delete()
        db.add_all(
            [
                PhysicalInterface(
                    name="eth0",
                    mac_address="00:15:5d:01:1d:1a",
                    ip_cidr="192.168.49.1/24",
                    role="management",
                    mode="access",
                    inventory_source="host",
                    desired_state_source="user",
                ),
                PhysicalInterface(
                    name="eth1",
                    mac_address="00:15:5d:01:1d:1b",
                    ip_cidr="192.168.50.1/24",
                    role="access",
                    mode="access",
                    inventory_source="host",
                    desired_state_source="user",
                ),
                PhysicalInterface(
                    name="eth2",
                    mac_address="00:15:5d:01:1d:1c",
                    role="access",
                    mode="trunk",
                    inventory_source="host",
                    desired_state_source="user",
                ),
                PhysicalInterface(
                    name="eth3",
                    mac_address="00:15:5d:01:1d:1d",
                    role="wan",
                    mode="access",
                    inventory_source="host",
                    desired_state_source="user",
                ),
            ]
        )
        db.commit()

    page = client.get("/vlan-interfaces")
    payload = page.text.split("data-parent-options='", 1)[1].split("'", 1)[0]
    options = json.loads(html.unescape(payload))

    assert options == [{"name": "eth2", "label": "eth2 - access - trunk - host NIC - 00:15:5d:01:1d:1c"}]
    assert "eth2 - access - trunk - host NIC" in page.text
    assert "eth1 - access - trunk" not in page.text


def test_vlan_page_disables_missing_parent_vlan(client):
    import html
    import json

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import PhysicalInterface, VlanInterface

    login(client)
    with SessionLocal() as db:
        db.query(VlanInterface).delete()
        db.query(PhysicalInterface).delete()
        db.add_all(
            [
                PhysicalInterface(
                    name="missing_155d011d1d",
                    mac_address="00:15:5d:01:1d:1d",
                    role="unused",
                    mode="unused",
                    admin_state="down",
                    oper_state="missing",
                    inventory_source="host",
                    desired_state_source="user",
                ),
                PhysicalInterface(
                    name="eth2",
                    mac_address="00:15:5d:01:1d:1c",
                    role="access",
                    mode="trunk",
                    inventory_source="host",
                    desired_state_source="user",
                ),
                VlanInterface(
                    parent_interface="missing_155d011d1d",
                    name="missing_155d011d1d.11",
                    vlan_id=11,
                    ip_cidr="192.168.11.1/24",
                    enabled=True,
                ),
            ]
        )
        db.commit()

    page = client.get("/vlan-interfaces")
    assert page.status_code == 200
    vlan_payload = page.text.split("data-vlans='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(vlan_payload))
    row = next(item for item in rows if item["name"] == "missing_155d011d1d.11")
    assert row["parent_missing"] is True
    assert row["enabled"] is False

    parent_payload = page.text.split("data-parent-options='", 1)[1].split("'", 1)[0]
    options = json.loads(html.unescape(parent_payload))
    assert options == [{"name": "eth2", "label": "eth2 - access - trunk - host NIC - 00:15:5d:01:1d:1c"}]

    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        f"/vlan-interfaces/{row['id']}/edit",
        data={
            "parent_interface": "missing_155d011d1d",
            "vlan_id": "11",
            "ip_cidr": "192.168.11.1/24",
            "mtu": "1500",
            "role": "access",
            "enabled": "on",
            "csrf": csrf,
        },
    )
    assert response.status_code == 409
    assert "missing from host inventory" in response.text


def test_vlan_interface_rejects_non_trunk_parent(client):
    login(client)
    page = client.get("/vlan-interfaces")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/vlan-interfaces",
        data={
            "parent_interface": "eth2",
            "vlan_id": "60",
            "ip_cidr": "192.168.60.1/24",
            "mtu": "1500",
            "role": "access",
            "enabled": "on",
            "csrf": csrf,
        },
    )
    assert response.status_code == 409
    assert "is not a trunk interface" in response.text


def test_vlan_interface_requires_vlan_id_and_ip_cidr(client):
    login(client)
    page = client.get("/vlan-interfaces")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    missing_ip = client.post(
        "/vlan-interfaces",
        data={
            "parent_interface": "eth1",
            "vlan_id": "70",
            "ip_cidr": "",
            "mtu": "1500",
            "role": "access",
            "enabled": "on",
            "csrf": csrf,
        },
    )
    assert missing_ip.status_code == 409
    assert "VLAN IPv4 CIDR, IPv6 CIDR, or both are required." in missing_ip.text

    missing_vlan = client.post(
        "/vlan-interfaces",
        data={
            "parent_interface": "eth1",
            "vlan_id": "",
            "ip_cidr": "192.168.70.1/24",
            "mtu": "1500",
            "role": "access",
            "enabled": "on",
            "csrf": csrf,
        },
    )
    assert missing_vlan.status_code == 409
    assert "VLAN ID is required" in missing_vlan.text


def test_firewall_page_create_rule_and_apply_task(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/firewall")
    assert page.status_code == 200
    assert "Firewall Rules" in page.text
    assert "firewall-rules-table" in page.text
    assert "Review appliance changes" in page.text
    assert "nftables" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    rejected = client.post(
        "/firewall/rules",
        data={
            "name": "raw-source-rejected",
            "direction": "input",
            "action": "accept",
            "protocol": "tcp",
            "source": "192.168.50.0/24",
            "destination": "any",
            "destination_port": "443",
            "interface_name": "eth2",
            "priority": "29",
            "enabled": "on",
            "description": "raw source should not save",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 422
    assert "Source must use Any or a firewall group." in rejected.text

    group_response = client.post(
        "/firewall/source-groups",
        data={
            "csrf": csrf,
            "action": "create",
            "group_name": "VCenter clients",
            "group_entries": "192.168.50.0/24",
        },
    )
    assert group_response.status_code == 200

    created = client.post(
        "/firewall/rules",
        data={
            "name": "allow-vcenter",
            "direction": "input",
            "action": "accept",
            "protocol": "tcp",
            "source": "group:custom:vcenter-clients",
            "destination": "any",
            "destination_port": "443",
            "interface_name": "eth2",
            "priority": "30",
            "enabled": "on",
            "description": "VCF management access",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert "allow-vcenter" in client.get("/firewall").text

    apply_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "firewall"})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert "labfoundry-helper firewall apply" in (job.result or "")
        assert "allow-vcenter" in (job.result or "")


def test_firewall_settings_autosave_updates_desired_state_preview(client):
    login(client)
    page = client.get("/firewall")
    assert page.status_code == 200
    assert "data-firewall-enabled-status" in page.text
    assert "vcf-depot-tool-reset-20260703-1" in page.text
    codemirror = client.get("/static/vendor/codemirror/labfoundry-codemirror.min.js")
    assert codemirror.status_code == 200
    assert "LabFoundryCodeMirror" in codemirror.text
    assert "initializeSwitchFields" in client.get("/static/app.js").text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    disabled = client.post(
        "/firewall/settings",
        data={
            "csrf": csrf,
            "default_input_policy": "drop",
            "default_forward_policy": "drop",
            "default_output_policy": "accept",
            "allow_established": "on",
            "allow_loopback": "on",
            "allow_icmp": "on",
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert disabled.status_code == 200
    disabled_payload = disabled.json()
    assert disabled_payload["enabled"] is False
    assert disabled_payload["valid"] is True
    assert "LabFoundry firewall desired state is disabled" in disabled_payload["config_preview"]
    assert "table inet labfoundry" not in disabled_payload["config_preview"]

    enabled = client.post(
        "/firewall/settings",
        data={
            "csrf": csrf,
            "enabled": "on",
            "default_input_policy": "drop",
            "default_forward_policy": "drop",
            "default_output_policy": "accept",
            "allow_established": "on",
            "allow_loopback": "on",
            "allow_icmp": "on",
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert enabled.status_code == 200
    enabled_payload = enabled.json()
    assert enabled_payload["enabled"] is True
    assert enabled_payload["settings"]["enabled"] is True
    assert "table inet labfoundry" in enabled_payload["config_preview"]
    assert 'comment "mgmt-console"' in enabled_payload["config_preview"]
    assert 'tcp ip saddr' not in enabled_payload["config_preview"]
    assert 'tcp dport { 22, 80, 443 } accept comment "mgmt-console"' in enabled_payload["config_preview"]


def test_global_appliance_apply_tracks_baselines_diffs_and_skips(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, Setting

    login(client)
    page = client.get("/appliance-apply")
    assert page.status_code == 200
    assert "Appliance Change Set" in page.text
    change_set_markup = page.text.split('class="panel apply-change-set-panel"', 1)[1].split('<div class="apply-unit-list">', 1)[0]
    assert "Submit appliance changes" in change_set_markup
    assert "data-apply-submit-tracker" in change_set_markup
    assert "apply-submit-panel" in page.text
    assert page.text.count("data-apply-submit-button") == 2
    assert "data-apply-progress-modal" not in page.text
    assert "No last-applied baseline exists yet" in page.text
    assert 'value="firewall"' in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    empty_response = client.post("/appliance-apply", data={"csrf": csrf})
    assert empty_response.status_code == 422
    assert "Select at least one appliance change to submit." in empty_response.text
    firewall_input = empty_response.text.split('value="firewall"', 1)[1].split(">", 1)[0]
    assert "checked" not in firewall_input

    baseline_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "firewall"})
    assert baseline_response.status_code == 200
    assert "Appliance apply task succeeded" in baseline_response.text
    with SessionLocal() as db:
        baseline = db.execute(select(Setting).where(Setting.key == "appliance_apply.baselines.v1")).scalar_one()
        assert '"firewall"' in baseline.value

    firewall_page = client.get("/firewall")
    csrf = firewall_page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    group_response = client.post(
        "/firewall/source-groups",
        data={
            "csrf": csrf,
            "action": "create",
            "group_name": "Global apply clients",
            "group_entries": "192.168.50.0/24",
        },
    )
    assert group_response.status_code == 200

    created = client.post(
        "/firewall/rules",
        data={
            "name": "allow-global-apply-test",
            "direction": "input",
            "action": "accept",
            "protocol": "tcp",
            "source": "group:custom:global-apply-clients",
            "destination": "any",
            "destination_port": "8443",
            "interface_name": "eth2",
            "priority": "35",
            "enabled": "on",
            "description": "global apply diff",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    changed_page = client.get("/appliance-apply")
    assert "--- last-applied/firewall" in changed_page.text
    assert "+++ current/firewall" in changed_page.text
    assert "allow-global-apply-test" in changed_page.text
    assert 'class="language-diff"' in changed_page.text
    assert "/static/vendor/prism/prism-core.min.js" in changed_page.text
    assert "/static/vendor/prism/prism-diff.min.js" in changed_page.text
    assert "Prism.manual = true" in changed_page.text
    assert "highlightConfigPreviews" in client.get("/static/app.js").text

    skipped_response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "network"})
    assert skipped_response.status_code == 200
    assert "allow-global-apply-test" in skipped_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply").order_by(Job.created_at.desc())).scalars().first()
        assert job is not None
        assert "skipped_changed_units" in (job.result or "")
        assert '"unit_id": "firewall"' in (job.result or "")


def test_appliance_startup_initializes_factory_apply_baseline(monkeypatch, tmp_path):
    from sqlalchemy import select
    from starlette.testclient import TestClient

    import labfoundry.app.database as database
    from labfoundry.app.config import get_settings
    from labfoundry.app.models import AuditEvent, Setting, User

    db_path = tmp_path / "labfoundry-appliance-baseline.db"
    monkeypatch.setenv("LABFOUNDRY_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LABFOUNDRY_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setenv("LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD", "labfoundry-admin")
    monkeypatch.setenv("LABFOUNDRY_ENVIRONMENT", "appliance")
    get_settings.cache_clear()
    database.engine.dispose()
    database.engine = database.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.SessionLocal.configure(bind=database.engine)

    from labfoundry.app.main import create_app

    with TestClient(create_app()) as test_client:
        login(test_client)
        page = test_client.get("/appliance-apply")
        assert page.status_code == 200
        assert "No Pending Appliance Changes" in page.text
        assert "11 changed" not in page.text

    with database.SessionLocal() as db:
        baseline = db.execute(select(Setting).where(Setting.key == "appliance_apply.baselines.v1")).scalar_one()
        assert '"local_users"' in baseline.value
        assert '"vcf_private_registry"' in baseline.value
        admin = db.execute(select(User).where(User.username == "admin")).scalar_one()
        assert admin.os_sync_status == "applied"
        assert admin.os_password_applied_at is not None
        event = db.execute(select(AuditEvent).where(AuditEvent.action == "initialize_factory_appliance_apply_baseline")).scalar_one()
        assert event.actor == "system"

    get_settings.cache_clear()


def test_factory_apply_baseline_skips_after_operator_activity(monkeypatch, tmp_path):
    from sqlalchemy import select

    import labfoundry.app.database as database
    from labfoundry.app.audit import record_audit
    from labfoundry.app.config import get_settings
    from labfoundry.app.models import Setting
    from labfoundry.app.seed import seed_initial_data
    from labfoundry.app.ui import initialize_factory_appliance_apply_baseline

    db_path = tmp_path / "labfoundry-appliance-edited.db"
    monkeypatch.setenv("LABFOUNDRY_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LABFOUNDRY_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setenv("LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD", "labfoundry-admin")
    monkeypatch.setenv("LABFOUNDRY_ENVIRONMENT", "appliance")
    get_settings.cache_clear()
    database.engine.dispose()
    database.engine = database.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.SessionLocal.configure(bind=database.engine)
    database.init_db()

    with database.SessionLocal() as db:
        seed_initial_data(db, include_examples=False)
        record_audit(db, actor="admin", action="update_appliance_settings", resource_type="settings")
        assert initialize_factory_appliance_apply_baseline(db) is False
        assert db.execute(select(Setting).where(Setting.key == "appliance_apply.baselines.v1")).scalar_one_or_none() is None

    get_settings.cache_clear()


def test_appliance_apply_runs_firewall_before_wan(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.ui import appliance_apply_units

    login(client)
    with SessionLocal() as db:
        unit_ids = [unit["id"] for unit in appliance_apply_units(db)]

    assert unit_ids.index("firewall") < unit_ids.index("wan")


def test_network_apply_config_includes_removed_vlan_targets_from_baseline():
    from labfoundry.app.ui import network_config_with_removed_vlans, network_vlan_entries_from_config, removed_network_vlan_entries

    baseline = {
        "config_preview": "\n".join(
            [
                "[physical_interfaces]",
                "interface=eth2",
                "  mode=trunk",
                "",
                "[vlan_interfaces]",
                "vlan=eth2.20",
                "  parent=eth2",
                "  vlan_id=20",
                "  ip_cidr=192.168.20.1/24",
                "  mtu=1500",
                "  role=services",
            ]
        )
    }
    current = "\n".join(
        [
            "[physical_interfaces]",
            "interface=eth2",
            "  mode=trunk",
            "",
            "[vlan_interfaces]",
            "",
        ]
    )

    removed = removed_network_vlan_entries(current, network_vlan_entries_from_config(baseline["config_preview"]))
    staged = network_config_with_removed_vlans(current, removed)

    assert removed == [{"name": "eth2.20", "parent": "eth2", "vlan_id": "20"}]
    assert "[removed_vlan_interfaces]" in staged
    assert "vlan=eth2.20" in staged
    assert "  parent=eth2" in staged
    assert "  vlan_id=20" in staged


def test_network_apply_removal_targets_include_successful_apply_history(client):
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, JobStatus, utcnow
    from labfoundry.app.ui import removed_network_vlan_entries, successful_network_apply_vlan_entries

    applied_preview = "\n".join(
        [
            "[physical_interfaces]",
            "interface=eth2",
            "  mode=trunk",
            "",
            "[vlan_interfaces]",
            "vlan=eth2.21",
            "  parent=eth2",
            "  vlan_id=21",
            "  ip_cidr=192.168.21.1/24",
            "  mtu=1500",
            "  role=services",
        ]
    )
    current_preview = "\n".join(
        [
            "[physical_interfaces]",
            "interface=eth2",
            "  mode=trunk",
            "",
            "[vlan_interfaces]",
            "",
        ]
    )
    with SessionLocal() as db:
        job = Job(
            id="job_network_history_vlan",
            type="appliance-apply",
            status=JobStatus.SUCCEEDED.value,
            created_by="admin",
            started_at=utcnow(),
            finished_at=utcnow(),
            progress_percent=100,
            result=json.dumps(
                {
                    "units": [
                        {
                            "unit_id": "network",
                            "success": True,
                            "dry_run": False,
                            "config_preview": applied_preview,
                        }
                    ]
                }
            ),
        )
        db.add(job)
        db.commit()
        applied = successful_network_apply_vlan_entries(db, {"config_preview": current_preview})
        removed = removed_network_vlan_entries(current_preview, applied)

    assert {"name": "eth2.21", "parent": "eth2", "vlan_id": "21"} in removed


def test_network_apply_history_retires_successfully_removed_vlans(client):
    import json

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, JobStatus, utcnow
    from labfoundry.app.ui import removed_network_vlan_entries, successful_network_apply_vlan_entries

    applied_preview = "\n".join(
        [
            "[physical_interfaces]",
            "interface=eth2",
            "  mode=trunk",
            "",
            "[vlan_interfaces]",
            "vlan=eth2.21",
            "  parent=eth2",
            "  vlan_id=21",
            "  ip_cidr=192.168.21.1/24",
            "  mtu=1500",
            "  role=services",
        ]
    )
    current_preview = "\n".join(
        [
            "[physical_interfaces]",
            "interface=eth2",
            "  mode=trunk",
            "",
            "[vlan_interfaces]",
            "",
        ]
    )
    with SessionLocal() as db:
        db.add(
            Job(
                id="job_network_history_vlan_created",
                type="appliance-apply",
                status=JobStatus.SUCCEEDED.value,
                created_by="admin",
                started_at=utcnow(),
                finished_at=utcnow(),
                progress_percent=100,
                result=json.dumps(
                    {
                        "units": [
                            {
                                "unit_id": "network",
                                "success": True,
                                "dry_run": False,
                                "config_preview": applied_preview,
                            }
                        ]
                    }
                ),
            )
        )
        db.add(
            Job(
                id="job_network_history_vlan_removed",
                type="appliance-apply",
                status=JobStatus.SUCCEEDED.value,
                created_by="admin",
                started_at=utcnow(),
                finished_at=utcnow(),
                progress_percent=100,
                result=json.dumps(
                    {
                        "units": [
                            {
                                "unit_id": "network",
                                "success": True,
                                "dry_run": False,
                                "config_preview": current_preview,
                                "removed_vlan_interfaces": [{"name": "eth2.21", "parent": "eth2", "vlan_id": "21"}],
                            }
                        ]
                    }
                ),
            )
        )
        db.commit()
        applied = successful_network_apply_vlan_entries(db, {"config_preview": current_preview})
        removed = removed_network_vlan_entries(current_preview, applied)

    assert {"name": "eth2.21", "parent": "eth2", "vlan_id": "21"} not in removed


def test_services_ui_records_dry_run_action(client):
    import html
    import json

    login(client)
    page = client.get("/services")
    assert page.status_code == 200
    assert "Services" in page.text
    assert "services-table" in page.text
    assert "services-fallback" in page.text
    assert "data-services=" in page.text
    assert "Service Boundary" in page.text
    assert "<th>Health</th>" not in page.text
    assert '<span class="status-pill warn">dry-run</span>' in page.text
    assert "Command shape" in page.text
    assert "systemctl restart dns" in page.text
    service_rows = json.loads(html.unescape(page.text.split("data-services='", 1)[1].split("'", 1)[0]))
    assert all(row["service"] != "ntpd" for row in service_rows)
    assert "NTPD" not in page.text
    chrony_row = next(row for row in service_rows if row["service"] == "chronyd")
    assert chrony_row["display_name"] == "Chrony"
    assert chrony_row["detail"] == "chronyd.service / UDP 123"
    ca_row = next(row for row in service_rows if row["service"] == "ca")
    assert ca_row["running"] is False
    assert ca_row["enabled"] is False
    vcf_backup_row = next(row for row in service_rows if row["service"] == "vcf-backups")
    assert vcf_backup_row["running"] is False
    assert vcf_backup_row["enabled"] is False
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/services/firewall/restart", data={"csrf": csrf})
    assert response.status_code == 200
    assert "Firewall restart recorded" in response.text
    assert "Firewall restart recorded as dry-run" in response.text
    assert "systemctl restart firewall" in response.text
    disabled = client.post("/services/firewall/disable", data={"csrf": csrf})
    rows = json.loads(html.unescape(disabled.text.split("data-services='", 1)[1].split("'", 1)[0]))
    firewall_row = next(row for row in rows if row["service"] == "firewall")
    assert firewall_row["enabled"] is False
    assert "health" not in firewall_row
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "initializeServicesTable" in js.text
    assert "submitServiceAction" in js.text
    assert 'height: "100%"' in js.text
    assert 'height: "520px"' not in js.text
    assert 'title: "Health"' not in js.text
    assert "serviceHealthFormatter" not in js.text
    assert "openServiceActionMenu" not in js.text
    assert "serviceActionsFormatter" not in js.text
    assert 'title: "Enabled"' in js.text
    assert 'editor: "tickCross"' in js.text
    assert 'service-state muted">disabled' in js.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".service-name-cell" in css.text
    assert ".services-workspace" in css.text
    assert ".services-table" in css.text


def test_services_prunes_and_hides_retired_ntpd_row(client):
    import html
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ServiceState
    from labfoundry.app.seed import seed_initial_data

    with SessionLocal() as db:
        db.add(ServiceState(service="ntpd", display_name="NTPD", running=False, enabled=False, health="disabled", detail="ntpd.service / UDP 123"))
        db.commit()
        seed_initial_data(db, include_examples=False)
        assert db.execute(select(ServiceState).where(ServiceState.service == "ntpd")).scalar_one_or_none() is None

    login(client)
    page = client.get("/services")
    assert page.status_code == 200
    service_rows = json.loads(html.unescape(page.text.split("data-services='", 1)[1].split("'", 1)[0]))
    assert all(row["service"] != "ntpd" for row in service_rows)
    assert "NTPD" not in page.text

    token = create_api_token(client, ["read:services"])
    api_response = client.get("/api/v1/services", headers={"Authorization": f"Bearer {token}"})
    assert api_response.status_code == 200
    assert all(row["service"] != "ntpd" for row in api_response.json())
    assert client.get("/api/v1/services/ntpd", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_services_and_esxi_page_show_enabled_esxi_pxe_boot_state(client):
    import html
    import json

    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpScope
    from labfoundry.app.services.esxi_pxe import (
        ESXI_PXE_BIOS_BOOTFILE,
        ESXI_PXE_UEFI_BOOTFILE,
        ESXI_TFTP_ROOT,
        save_esxi_pxe_boot_settings,
    )

    with SessionLocal() as db:
        scope = db.execute(select(DhcpScope).where(DhcpScope.enabled.is_(True)).order_by(DhcpScope.id)).scalars().first()
        assert scope is not None
        save_esxi_pxe_boot_settings(
            db,
            enabled=True,
            hostname="esxi-pxe.labfoundry.internal",
            dhcp_scope_ids=[scope.id],
            listen_interface=scope.interface_name,
            listen_address=scope.site_address,
            tftp_root=ESXI_TFTP_ROOT.as_posix(),
            bios_bootfile=ESXI_PXE_BIOS_BOOTFILE,
            uefi_bootfile=ESXI_PXE_UEFI_BOOTFILE,
            native_uefi_http_enabled=True,
        )
        db.commit()

    login(client)
    esxi_page = client.get("/esxi-pxe")
    assert esxi_page.status_code == 200
    assert '<span class="status-pill good">live</span>' in esxi_page.text

    services_page = client.get("/services")
    assert services_page.status_code == 200
    service_rows = json.loads(html.unescape(services_page.text.split("data-services='", 1)[1].split("'", 1)[0]))
    esxi_row = next(row for row in service_rows if row["service"] == "esxi-pxe")
    assert esxi_row["running"] is True
    assert esxi_row["enabled"] is True
    assert esxi_row["detail"] == "dnsmasq TFTP/DHCP boot options and PXE HTTP files"

    token = create_api_token(client, ["read:services"])
    api_response = client.get("/api/v1/services/esxi-pxe", headers={"Authorization": f"Bearer {token}"})
    assert api_response.status_code == 200
    assert api_response.json()["running"] is True
    assert api_response.json()["enabled"] is True
    assert api_response.json()["health"] == "healthy"


def test_services_and_service_pages_derive_composite_runtime_status(client, monkeypatch):
    import html
    import json

    from sqlalchemy import select

    from labfoundry.app.adapters.system import AdapterResult
    from labfoundry.app.config import get_settings
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import CaSettings, DhcpScope, KmsSettings, VcfBackupSettings, VcfOfflineDepotSettings

    def fake_service_status(self, unit: str):
        return AdapterResult(
            command=["systemctl", "status", unit],
            dry_run=False,
            stdout=json.dumps({"active": "active", "enabled": "enabled"}),
        )

    monkeypatch.setenv("LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("labfoundry.app.ui.SystemAdapter.service_status", fake_service_status)
    monkeypatch.setattr("labfoundry.app.api.v1.SystemAdapter.service_status", fake_service_status)

    with SessionLocal() as db:
        scope = db.execute(select(DhcpScope).where(DhcpScope.enabled.is_(True)).order_by(DhcpScope.id)).scalars().first()
        assert scope is not None
        ca_settings = db.execute(select(CaSettings)).scalar_one()
        ca_settings.enabled = True
        ca_settings.listen_interface = scope.interface_name
        ca_settings.listen_address = scope.site_address
        ca_settings.root_certificate_pem = "present"
        ca_settings.root_private_key_encrypted = "present"
        db.add(ca_settings)
        kms_settings = db.execute(select(KmsSettings)).scalar_one()
        kms_settings.enabled = True
        db.add(kms_settings)
        backup_settings = db.execute(select(VcfBackupSettings)).scalar_one()
        backup_settings.enabled = False
        db.add(backup_settings)
        depot_settings = db.execute(select(VcfOfflineDepotSettings)).scalar_one()
        depot_settings.enabled = True
        db.add(depot_settings)
        db.commit()

    login(client)
    services_page = client.get("/services")
    assert services_page.status_code == 200
    service_rows = json.loads(html.unescape(services_page.text.split("data-services='", 1)[1].split("'", 1)[0]))
    ca_row = next(row for row in service_rows if row["service"] == "ca")
    kms_row = next(row for row in service_rows if row["service"] == "kms")
    backup_row = next(row for row in service_rows if row["service"] == "vcf-backups")
    depot_row = next(row for row in service_rows if row["service"] == "repository")
    assert ca_row["running"] is True
    assert ca_row["enabled"] is True
    assert kms_row["running"] is True
    assert kms_row["enabled"] is True
    assert backup_row["running"] is True
    assert backup_row["enabled"] is False
    assert depot_row["running"] is True
    assert depot_row["enabled"] is True

    assert '<span class="status-pill good">live</span>' in client.get("/kms").text
    assert '<span class="status-pill good">live</span>' in client.get("/vcf-offline-depot").text
    ca_page = client.get("/certificate-authority").text
    assert '<span class="status-pill muted">disabled</span>' not in ca_page
    assert '<span class="status-pill good">live</span>' in ca_page or '<span class="status-pill warn">needs attention</span>' in ca_page

    token = create_api_token(client, ["read:services"])
    assert client.get("/api/v1/services/ca", headers={"Authorization": f"Bearer {token}"}).json()["running"] is True
    assert client.get("/api/v1/services/repository", headers={"Authorization": f"Bearer {token}"}).json()["running"] is True
    assert client.get("/api/v1/services/vcf-backups", headers={"Authorization": f"Bearer {token}"}).json()["running"] is True


def test_services_live_chrony_status_uses_systemd(client, monkeypatch):
    import html
    import json

    from labfoundry.app.adapters.system import AdapterResult
    from labfoundry.app.config import get_settings

    def fake_service_status(self, unit: str):
        active = "active" if unit == "chronyd.service" else "inactive"
        enabled = "enabled" if unit == "chronyd.service" else "disabled"
        return AdapterResult(
            command=["systemctl", "is-active", unit, "&&", "systemctl", "is-enabled", unit],
            dry_run=False,
            stdout=json.dumps({"active": active, "enabled": enabled}),
        )

    monkeypatch.setenv("LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("labfoundry.app.ui.SystemAdapter.service_status", fake_service_status)
    monkeypatch.setattr("labfoundry.app.api.v1.SystemAdapter.service_status", fake_service_status)

    login(client)
    page = client.get("/services")
    assert page.status_code == 200
    service_rows = json.loads(html.unescape(page.text.split("data-services='", 1)[1].split("'", 1)[0]))
    chrony_row = next(row for row in service_rows if row["service"] == "chronyd")
    assert chrony_row["running"] is True
    assert chrony_row["enabled"] is True
    assert "health" not in chrony_row

    token = create_api_token(client, ["read:services"])
    api_response = client.get("/api/v1/services/chronyd", headers={"Authorization": f"Bearer {token}"})
    assert api_response.status_code == 200
    assert api_response.json()["running"] is True
    assert api_response.json()["enabled"] is True
    assert api_response.json()["health"] == "healthy"


def test_services_ui_hides_dry_run_badge_when_adapters_are_live(client, monkeypatch):
    from labfoundry.app.config import get_settings

    monkeypatch.setenv("LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS", "false")
    get_settings.cache_clear()
    login(client)

    page = client.get("/services")

    assert page.status_code == 200
    assert '<span class="status-pill good">live</span>' in page.text
    assert '<span class="status-pill warn">dry-run</span>' not in page.text
    assert "captured as dry-run command intent" not in page.text
    assert "Open Logs on a service row to capture a log preview." in page.text


def test_ca_settings_autosave_returns_json(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import CaSettings

    login(client)
    page = client.get("/certificate-authority")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/certificate-authority/settings",
        data={
            "enabled": "on",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth1", "eth2"],
            "listen_addresses": ["192.168.50.1", "10.0.0.99"],
            "root_common_name": "LabFoundry Test Root CA",
            "organization": "LabFoundry",
            "organizational_unit": "Lab",
            "country": "US",
            "state": "",
            "locality": "",
            "key_algorithm": "RSA",
            "key_size": "4096",
            "digest_algorithm": "sha256",
            "root_valid_days": "3650",
            "intermediate_valid_days": "1825",
            "publish_crl": "on",
            "storage_path": "/tmp/operator-edited-ca",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["listen_interfaces"] == ["eth2"]
    assert payload["listen_addresses"] == ["192.168.50.1"]
    assert "10.0.0.99" not in payload["config_preview"]
    assert "LabFoundry Test Root CA" in client.get("/certificate-authority").text
    with SessionLocal() as db:
        ca_settings = db.execute(select(CaSettings)).scalar_one()
        assert ca_settings.storage_path == "/etc/labfoundry/ca"
        assert ca_settings.listen_interface == "eth2"
        assert ca_settings.listen_address == "192.168.50.1"


def test_ca_apply_task_captures_current_desired_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/certificate-authority")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "ca"})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "LabFoundry Internal Root CA" in (job.result or "")


def test_ca_live_apply_stages_decrypted_private_keys_without_leaking_job_output(client, monkeypatch, tmp_path):
    from pathlib import Path

    from sqlalchemy import select

    from labfoundry.app.adapters.system import AdapterResult
    from labfoundry.app.config import get_settings
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import CaSettings, Job

    staged_path = tmp_path / "labfoundry-ca.json"
    captured: dict[str, str] = {}

    def fake_validate_ca_config(self, config_path: str):
        captured["validate_payload"] = Path(config_path).read_text(encoding="utf-8")
        return AdapterResult(command=["labfoundry-helper", "ca", "validate", config_path], dry_run=False, stdout="validated")

    def fake_apply_ca_config(self, config_path: str):
        captured["apply_payload"] = Path(config_path).read_text(encoding="utf-8")
        return AdapterResult(command=["labfoundry-helper", "ca", "apply", config_path], dry_run=False, stdout="applied")

    monkeypatch.setenv("LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("labfoundry.app.ui.CA_STAGED_CONFIG_PATH", str(staged_path))
    monkeypatch.setattr("labfoundry.app.ui.SystemAdapter.validate_ca_config", fake_validate_ca_config)
    monkeypatch.setattr("labfoundry.app.ui.SystemAdapter.apply_ca_config", fake_apply_ca_config)

    with SessionLocal() as db:
        settings = db.execute(select(CaSettings)).scalar_one()
        settings.enabled = True
        settings.listen_interface = "eth0"
        settings.listen_address = "192.168.49.1"
        db.commit()

    login(client)
    page = client.get("/certificate-authority")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "ca"})

    assert response.status_code == 200
    assert captured["validate_payload"] == captured["apply_payload"]
    assert "BEGIN PRIVATE KEY" in captured["apply_payload"]
    assert "[redacted]" not in captured["apply_payload"]

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "BEGIN PRIVATE KEY" not in (job.result or "")


def test_appliance_apply_status_redacts_undecryptable_ca_private_key(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import CaSettings

    with SessionLocal() as db:
        settings = db.execute(select(CaSettings)).scalar_one()
        settings.root_private_key_encrypted = "not-a-valid-fernet-token"
        db.commit()

    login(client)
    response = client.get("/dns")

    assert response.status_code == 200
    assert "DNS Settings" in response.text
    assert "not-a-valid-fernet-token" not in response.text


def test_dns_settings_accept_multiple_listen_interfaces(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/settings",
        data={
            "enabled": "on",
            "listen_interfaces": ["eth0", "eth2"],
            "upstream_servers": "1.1.1.1\n9.9.9.9",
            "cache_size": "1000",
            "expand_hosts": "on",
            "authoritative": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    refreshed = client.get("/dns")
    assert "interface=eth0" in refreshed.text
    assert "interface=eth2" in refreshed.text
    assert "listen-address=192.168.49.1" in refreshed.text
    assert "listen-address=192.168.50.1" in refreshed.text
    assert "listen-address=192.168.60.1" not in refreshed.text
    assert "domain=labfoundry.internal" in refreshed.text


def test_dns_settings_autosave_returns_json(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/settings",
        data={
            "enabled": "on",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth2"],
            "listen_addresses": ["192.168.50.1"],
            "upstream_servers": "8.8.8.8",
            "conditional_forwarders": "sddc.internal=192.168.10.10,192.168.10.11",
            "cache_size": "500",
            "expand_hosts": "on",
            "authoritative": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert response.json()["listen_interfaces"] == ["eth2"]
    assert response.json()["valid"] is True
    assert "ESXi PXE boot services require DHCP to be enabled so clients receive boot files." not in response.json()["validation_errors"]
    assert "server=/sddc.internal/192.168.10.10" in response.json()["config_preview"]
    assert "server=/sddc.internal/192.168.10.11" in response.json()["config_preview"]
    refreshed = client.get("/dns")
    assert "server=/sddc.internal/192.168.10.10" in refreshed.text
    assert "server=/sddc.internal/192.168.10.11" in refreshed.text
    assert "sddc.internal=192.168.10.10,192.168.10.11" in refreshed.text


def test_dns_settings_autosave_filters_invalid_listen_interfaces(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/settings",
        data={
            "enabled": "on",
            "listen_interfaces_present": "1",
            "listen_addresses_present": "1",
            "listen_interfaces": ["eth1", "eth2"],
            "listen_addresses": ["192.168.50.1"],
            "upstream_servers": "8.8.8.8",
            "cache_size": "500",
            "expand_hosts": "on",
            "authoritative": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["listen_interfaces"] == ["eth2"]
    assert response.json()["valid"] is True
    assert "ESXi PXE boot services require DHCP to be enabled so clients receive boot files." not in response.json()["validation_errors"]
    assert "interface=eth2" in response.json()["config_preview"]


def test_dns_validation_requires_dhcp_only_when_esxi_pxe_boot_enabled(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Setting
    from labfoundry.app.services.esxi_pxe import ESXI_PXE_BOOT_ENABLED_KEY

    login(client)
    with SessionLocal() as db:
        setting = db.execute(select(Setting).where(Setting.key == ESXI_PXE_BOOT_ENABLED_KEY)).scalar_one_or_none()
        if setting is None:
            setting = Setting(key=ESXI_PXE_BOOT_ENABLED_KEY, value="true")
            db.add(setting)
        else:
            setting.value = "true"
        db.commit()

    response = client.get("/dns")

    assert response.status_code == 200
    assert "ESXi PXE boot services require DHCP to be enabled so clients receive boot files." in response.text


def test_dns_apply_task_captures_current_desired_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpSettings, Job

    login(client)
    with SessionLocal() as db:
        dhcp_settings = db.execute(select(DhcpSettings)).scalar_one()
        dhcp_settings.enabled = True
        db.commit()
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "dnsmasq"})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "dnsmasq" in (job.result or "")
        assert "labfoundry.internal" in (job.result or "")


def test_dhcp_settings_autosave_returns_json(client):
    login(client)
    page = client.get("/dhcp")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dhcp/settings",
        data={
            "enabled": "on",
            "interface_name": "eth2",
            "site_address": "192.168.50.1",
            "prefix_length": "24",
            "range_start": "192.168.50.120",
            "range_end": "192.168.50.220",
            "lease_time": "8h",
            "domain_name": "labfoundry.internal",
            "dns_server": "192.168.50.1",
            "authoritative": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"


def test_dhcp_settings_autosave_allows_service_toggle_only(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpSettings

    login(client)
    page = client.get("/dhcp")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dhcp/settings",
        data={
            "enabled": "on",
            "authoritative": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"

    with SessionLocal() as db:
        settings = db.execute(select(DhcpSettings)).scalar_one()
        assert settings.enabled is True
        assert settings.authoritative is True


def test_dhcp_settings_badge_reflects_desired_state_not_seeded_service_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpSettings, ServiceState

    login(client)
    with SessionLocal() as db:
        settings = db.execute(select(DhcpSettings)).scalar_one()
        settings.enabled = True
        state = db.execute(select(ServiceState).where(ServiceState.service == "dhcp")).scalar_one()
        state.enabled = False
        state.running = False
        state.health = "disabled"
        db.commit()

    page = client.get("/dhcp")
    settings_panel = page.text.split("<h2>DHCP Settings</h2>", 1)[1].split("</form>", 1)[0]

    assert page.status_code == 200
    assert '<span class="status-pill good">enabled</span>' in settings_panel
    assert '<span class="status-pill muted">disabled</span>' not in settings_panel


def test_dhcp_scope_edit_form_updates_ip_zone(client):
    login(client)
    page = client.get("/dhcp")
    import html
    import json

    payload = page.text.split("data-scopes='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(payload))
    scope_id = next(row["id"] for row in rows if row["name"] == "SiteA")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    updated = client.post(
        f"/dhcp/scopes/{scope_id}/edit",
        data={
            "name": "SiteA-Lab",
            "interface_name": "eth2",
            "site_address": "192.168.50.1",
            "prefix_length": "24",
            "range_start": "192.168.50.110",
            "range_end": "192.168.50.210",
            "lease_time": "8h",
            "domain_name": "labfoundry.internal",
            "dns_server": "192.168.50.1",
            "ntp_server": "192.168.50.1",
            "description": "edited IP zone",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303

    refreshed = client.get("/dhcp")
    assert "SiteA-Lab" in refreshed.text
    assert "192.168.50.110" in refreshed.text
    assert "edited IP zone" in refreshed.text
    assert '"ntp_server": "192.168.50.1"' in refreshed.text


def test_dhcp_apply_task_captures_current_desired_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DhcpSettings, Job

    login(client)
    with SessionLocal() as db:
        dhcp_settings = db.execute(select(DhcpSettings)).scalar_one()
        dhcp_settings.enabled = True
        db.commit()
    page = client.get("/dhcp")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/appliance-apply", data={"csrf": csrf, "selected_units": "dnsmasq"})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "dnsmasq" in (job.result or "")
        assert "1 reservations" in (job.result or "")


def test_dhcp_reservation_edit_form_updates_row(client):
    login(client)
    page = client.get("/dhcp")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/dhcp/reservations",
        data={
            "hostname": "reserved-client",
            "mac_address": "02:15:5d:00:22:22",
            "ip_address": "192.168.50.122",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    page = client.get("/dhcp")
    import html
    import json

    payload = page.text.split("data-reservations='", 1)[1].split("'", 1)[0]
    rows = json.loads(html.unescape(payload))
    reservation_id = next(row["id"] for row in rows if row["hostname"] == "reserved-client.labfoundry.internal")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    updated = client.post(
        f"/dhcp/reservations/{reservation_id}/edit",
        data={
            "hostname": "reserved-client-2.labfoundry.internal",
            "mac_address": "02:15:5d:00:22:23",
            "ip_address": "192.168.50.123",
            "description": "edited from grid",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303

    refreshed = client.get("/dhcp")
    assert "reserved-client-2.labfoundry.internal" in refreshed.text
    assert "192.168.50.123" in refreshed.text
    assert "edited from grid" in refreshed.text
    dns_page = client.get("/dns")
    assert "reserved-client-2.labfoundry.internal" in dns_page.text


def test_dns_zone_create_adds_domain_tab(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/zones",
        data={"domain": "sitea.internal", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    refreshed = client.get("/dns")
    assert "sitea.internal" in refreshed.text
    assert 'data-domain="sitea.internal"' in refreshed.text


def test_dns_zone_delete_removes_domain_and_scoped_records(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/dns/zones",
        data={"domain": "delete-me.internal", "csrf": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303

    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    record = client.post(
        "/dns/records",
        data={
            "hostname": "app",
            "domain": "delete-me.internal",
            "record_type": "A",
            "address": "192.168.50.222",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert record.status_code == 303

    page = client.get("/dns")
    assert "delete-me.internal" in page.text
    assert "app.delete-me.internal" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    deleted = client.post(
        "/dns/zones/delete",
        data={"domain": "delete-me.internal", "csrf": csrf},
        follow_redirects=False,
    )
    assert deleted.status_code == 303

    refreshed = client.get("/dns")
    assert "delete-me.internal" not in refreshed.text
    assert "app.delete-me.internal" not in refreshed.text
    assert "domain=labfoundry.internal" in refreshed.text


def test_dns_zone_delete_keeps_at_least_one_domain(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/zones/delete",
        data={"domain": "labfoundry.internal", "csrf": csrf},
    )

    assert response.status_code == 422
    assert "At least one DNS domain must remain managed." in response.text
    assert "labfoundry.internal" in response.text


def test_dns_zone_warns_for_local_domain(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/zones",
        data={"domain": "vcf.local", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    refreshed = client.get("/dns")
    assert "Avoid .local for VCF" in refreshed.text
    assert "vcf.internal" in refreshed.text
    assert "VMware Cloud Foundation does not work reliably" in refreshed.text
    assert "RFC 6762" in refreshed.text
    assert "RFC 6761" in refreshed.text
    assert "IANA Special-Use Domain Names registry" in refreshed.text
    assert "ICANN/IANA private-use TLD selection" in refreshed.text


def test_duplicate_dns_record_form_shows_conflict(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    first = client.post(
        "/dns/records",
        data={
            "hostname": "duplicate.labfoundry.internal",
            "record_type": "A",
            "address": "192.168.50.40",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert first.status_code == 303

    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    duplicate = client.post(
        "/dns/records",
        data={
            "hostname": "duplicate.labfoundry.internal",
            "record_type": "A",
            "address": "192.168.50.41",
            "enabled": "on",
            "csrf": csrf,
        },
    )
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.text


def test_dns_record_form_scopes_relative_host_to_domain(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/records",
        data={
            "hostname": "scoped",
            "domain": "labfoundry.internal",
            "record_type": "A",
            "address": "192.168.50.90",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    refreshed = client.get("/dns")
    assert "scoped.labfoundry.internal" in refreshed.text
    assert "scoped" in refreshed.text


def test_dns_record_form_rejects_wrong_ip_family(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/records",
        data={
            "hostname": "wrong-family",
            "domain": "labfoundry.internal",
            "record_type": "AAAA",
            "address": "192.168.50.91",
            "enabled": "on",
            "csrf": csrf,
        },
    )

    assert response.status_code == 422
    assert "must use an IPv6 address" in response.text


def test_dns_record_edit_form_updates_row(client):
    import html
    import json

    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/dns/records",
        data={
            "hostname": "editable.labfoundry.internal",
            "record_type": "A",
            "address": "192.168.50.60",
            "enabled": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    page = client.get("/dns")
    payload = page.text.split("data-records='", 1)[1].split("'", 1)[0]
    records = json.loads(html.unescape(payload))
    record_id = next(record["id"] for record in records if record["hostname"] == "editable.labfoundry.internal")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    updated = client.post(
        f"/dns/records/{record_id}/edit",
        data={
            "hostname": "editable-renamed.labfoundry.internal",
            "record_type": "A",
            "address": "192.168.50.61",
            "description": "edited from UI",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303

    refreshed = client.get("/dns")
    assert "editable-renamed.labfoundry.internal" in refreshed.text
    assert "192.168.50.61" in refreshed.text
    assert "edited from UI" in refreshed.text


def test_hosts_file_editor_replaces_dns_records(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    imported = client.post(
        "/dns/records/import",
        data={
            "domain": "labfoundry.internal",
            "hosts_text": "192.168.50.80 bulk bulk-alias\n",
            "replace_existing": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert imported.status_code == 303

    refreshed = client.get("/dns")
    assert "Import Hosts" in refreshed.text
    assert "bulk.labfoundry.internal" in refreshed.text
    assert "bulk-alias.labfoundry.internal" in refreshed.text
    assert "labfoundry.labfoundry.internal" in refreshed.text


def test_zone_file_editor_import_replaces_domain_records(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    imported = client.post(
        "/dns/zones/import",
        data={
            "domain": "labfoundry.internal",
            "zone_text": "$ORIGIN labfoundry.internal.\nwww IN CNAME labfoundry.labfoundry.internal.\nipv6 IN AAAA 2001:db8::10\n",
            "replace_existing": "on",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert imported.status_code == 303

    refreshed = client.get("/dns")
    assert "Import Zone File" in refreshed.text
    assert "www.labfoundry.internal" in refreshed.text
    assert "cname=www.labfoundry.internal,labfoundry.labfoundry.internal" in refreshed.text
    assert "ipv6.labfoundry.internal" in refreshed.text


def test_zone_file_import_error_preserves_pasted_zone_text(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    zone_text = "$ORIGIN labfoundry.internal.\nbadrecord IN TXT unsupported\n"

    imported = client.post(
        "/dns/zones/import",
        data={
            "domain": "labfoundry.internal",
            "zone_text": zone_text,
            "replace_existing": "on",
            "csrf": csrf,
        },
    )

    assert imported.status_code == 422
    assert "Import Zone File" in imported.text
    assert "Line 2:" in imported.text
    assert "badrecord IN TXT unsupported" in imported.text

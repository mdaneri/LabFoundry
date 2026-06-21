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


def test_login_and_dashboard_render(client):
    login(client)
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 303
    assert root.headers["location"] == "/dashboard"
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "LabFoundry" in response.text
    assert "Routes &amp; WAN Simulation" in response.text
    assert "HTTPS Repository" in response.text
    assert "Users" in response.text
    assert "LDAP / Users" not in response.text
    assert "/static/brand/labfoundry-mark.svg" in response.text
    assert "LF</span>" not in response.text
    assert client.get("/static/brand/labfoundry-mark.svg").status_code == 200
    assert client.get("/static/brand/labfoundry-appliance-graphic.svg").status_code == 200


def test_routes_wan_policy_form_renders(client):
    login(client)
    response = client.get("/routes-wan")
    assert response.status_code == 200
    assert "Routes &amp; WAN Simulation" in response.text
    assert "Managed Routes" in response.text
    assert "WAN Policies" in response.text
    assert "WAN Apply" in response.text
    assert "Validation" in response.text
    assert "routes-wan-routes-table" in response.text
    assert "routes-wan-policies-table" in response.text
    assert "+ Add route here" in client.get("/static/app.js").text
    assert "+ Add policy here" in client.get("/static/app.js").text
    assert "Europe WAN" in response.text
    assert "eth1.20" in response.text
    assert "tc qdisc replace" in response.text
    assert "Create appliance apply task" in response.text


def test_routes_wan_autosave_endpoints_and_apply_task(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, WanPolicy

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
    refreshed = client.get("/routes-wan")
    assert "Metro WAN" in refreshed.text
    assert "10.20.0.0/24" in refreshed.text
    assert "tc qdisc replace dev eth1.20" in refreshed.text

    apply_response = client.post("/routes-wan/apply-task", data={"csrf": csrf})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "wan-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "wan" in (job.result or "")
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
    assert "Password Reset" not in users.text
    assert "Reset password" in users.text
    assert "Remove" in users.text
    assert "admin" in users.text
    assert "vcf-backup" in users.text
    csrf = users.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/users",
        data={"username": "operator", "role": "viewer", "password": "operator-pass", "enabled": "on", "csrf": csrf},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "operator" in created.text
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "userActionsFormatter" not in app_js.text
    assert "formatter: userActionsFormatter" not in app_js.text
    assert "openUserPasswordModal" in app_js.text
    assert "deleteUserFromMenu" in app_js.text
    assert "use action menu" in app_js.text


def test_local_user_reset_modal_endpoint_and_remove(client):
    import html
    import json

    login(client)
    users = client.get("/users")
    csrf = users.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post(
        "/users",
        data={"username": "remove-me", "role": "viewer", "password": "temporary-pass", "enabled": "on", "csrf": csrf},
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
        data={"password": "new-temporary-pass", "confirm_password": "new-temporary-pass", "csrf": csrf},
        follow_redirects=False,
    )
    assert reset.status_code == 303

    deleted = client.post(f"/users/{user_id}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert deleted.status_code == 303
    refreshed = client.get("/users")
    assert "remove-me" not in refreshed.text


def test_audit_log_renders(client):
    login(client)
    response = client.get("/audit-log")
    assert response.status_code == 200
    assert "Audit Events" in response.text
    assert "ui_login" in response.text


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
    assert "Zone File" in dns.text
    assert "Reverse Zones" in dns.text
    assert "Reverse/PTR" in dns.text
    assert "PTR records are generated automatically" in dns.text
    assert "zone-code-editor" in dns.text
    assert "relative hostnames are saved inside this domain" in dns.text
    assert 'data-domain="labfoundry.internal"' in dns.text
    assert "A (IPv4)" in dns.text
    assert "AAAA (IPv6)" in dns.text
    assert "CNAME (alias)" in dns.text
    assert "ptr-record=" not in dns.text
    assert "1.50.168.192.in-addr.arpa" in dns.text
    assert 'name="listen_interfaces"' in dns.text
    assert 'name="listen_addresses"' in dns.text
    assert 'name="conditional_forwarders"' in dns.text
    assert "Conditional forwarders" in dns.text
    assert "domain=server1,server2" in dns.text
    assert "sddc.internal=192.168.10.10,192.168.10.11" in dns.text
    assert dns.text.count('data-tag-editor') >= 2
    assert dns.text.count('data-tag-menu-toggle') >= 2
    assert dns.text.count('data-tag-option=') >= 4
    assert 'placeholder="Add interface..."' in dns.text
    assert 'placeholder="Add listen address..."' in dns.text
    assert 'action="/dns/zones"' in dns.text
    assert 'action="/dns/zones/delete"' in dns.text
    assert "data-confirm-modal" in dns.text
    assert "Delete labfoundry.internal?" in dns.text
    assert "It will not touch the appliance until an apply task runs." in dns.text
    assert 'action="/dns/zones/import"' in dns.text
    assert 'action="/dns/apply-task"' in dns.text
    assert "labfoundry.internal or sitea.internal" in dns.text
    assert "Changes save automatically." in dns.text
    assert "Create appliance apply task" in dns.text
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
    assert '...JSON.parse(tableElement.dataset.records || "[]"), newDnsRecordRow(domain)' in app_js.text
    assert "DNS_ACTIVE_ZONE_STORAGE_KEY" in app_js.text
    assert "rememberDnsActiveZone(data.domain)" in app_js.text
    assert "dnsZoneTabButtonForDomain(storedDomain)" in app_js.text
    assert "initializeTagEditors" in app_js.text
    assert "initializeConfirmationModals" in app_js.text
    assert "requestConfirmation" in app_js.text
    assert "form[data-confirm-modal]" in app_js.text
    assert "confirm-modal" in app_js.text
    assert "initializeAutosaveForms" in app_js.text
    assert "initializeDhcpScopesTable" in app_js.text
    assert "autoSaveDhcpScope" in app_js.text
    assert "+ Add IP zone here" in app_js.text
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
    assert "X-LabFoundry-Autosave" in app_js.text
    assert "tag-editor:change" in app_js.text
    assert "data-tag-menu-toggle" in app_js.text
    assert 'data-action="save"' not in app_js.text

    app_css = client.get("/static/app.css")
    assert app_css.status_code == 200
    assert ".add-row-hint" in app_css.text
    assert 'tabulator-field="host_label"' in app_css.text
    assert ".alert.warning" in app_css.text
    assert ".tag-editor" in app_css.text
    assert ".tag-add-button" in app_css.text
    assert ".tag-suggestions" in app_css.text
    assert ".autosave-status" in app_css.text
    assert ".apply-task-form" in app_css.text
    assert ".confirm-modal" in app_css.text
    assert ".confirm-modal::backdrop" in app_css.text
    assert ".section-head" in app_css.text

    dhcp = client.get("/dhcp")
    assert dhcp.status_code == 200
    assert "DHCP IP Zones" in dhcp.text
    assert "Desired State" in dhcp.text
    assert "Actual Leases" in dhcp.text
    assert 'id="dhcp-actual-leases"' in dhcp.text
    assert "api-client.labfoundry.internal" in dhcp.text
    assert "labfoundry-helper dnsmasq leases" in dhcp.text
    assert "dhcp-scopes-table" in dhcp.text
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
    assert 'action="/dhcp/apply-task"' in dhcp.text
    assert "Create appliance apply task" in dhcp.text
    assert "Save DHCP" not in dhcp.text
    assert "192.168.50.100" in dhcp.text
    assert "192.168.50.1" in dhcp.text


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
    assert "Changes save automatically." in ca.text
    assert 'action="/certificate-authority/apply-task"' in ca.text
    assert "Create appliance apply task" in ca.text
    assert "labfoundry-ca.conf" in ca.text
    assert "data-confirm-modal" in ca.text
    assert "Downloads" in ca.text
    assert "Download root CA" in ca.text
    assert "Download CA bundle" in ca.text
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


def test_kms_page_renders(client):
    login(client)
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
    assert 'data-autosave-status-id="kms-settings-autosave-status"' in kms.text
    assert "Changes save automatically." in kms.text
    assert 'action="/kms/apply-task"' in kms.text
    assert "Create appliance apply task" in kms.text
    assert "pykmip.conf" in kms.text
    assert "/var/lib/labfoundry/kms/pykmip.db" in kms.text
    assert "<span>Database path</span>" not in kms.text
    assert "<span>Config path</span>" not in kms.text
    assert 'name="database_path"' not in kms.text
    assert 'name="config_path"' not in kms.text
    assert "data-confirm-modal" in kms.text

    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "initializeKmsKeysTable" in app_js.text
    assert "initializeKmsClientsTable" in app_js.text
    assert "+ Add key here" in app_js.text
    assert "+ Add client here" in app_js.text
    assert "deleteKmsKeyFromMenu" in app_js.text
    assert "deleteKmsClientFromMenu" in app_js.text


def test_kms_settings_autosave_returns_json(client):
    login(client)
    page = client.get("/kms")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/kms/settings",
        data={
            "enabled": "on",
            "backend": "pykmip",
            "listen_interface": "eth1",
            "listen_address": "192.168.50.1",
            "port": "5696",
            "hostname": "kms.labfoundry.internal",
            "server_certificate": "kms.labfoundry.internal",
            "ca_certificate_path": "/etc/labfoundry/ca/root.crt",
            "database_path": "/tmp/rogue-kms.db",
            "config_path": "/tmp/rogue-kms.conf",
            "require_client_cert": "on",
            "allow_register": "on",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    refreshed = client.get("/kms")
    assert "enabled" in refreshed.text
    assert "/tmp/rogue-kms.db" not in refreshed.text
    assert "/tmp/rogue-kms.conf" not in refreshed.text
    assert "/var/lib/labfoundry/kms/pykmip.db" in refreshed.text
    assert "/etc/labfoundry/kms/pykmip.conf" in refreshed.text


def test_kms_apply_task_captures_current_desired_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import DnsRecord, Job

    login(client)
    page = client.get("/kms")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/kms/apply-task", data={"csrf": csrf})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "kms-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
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
    assert 'action="/vcf-backups/apply-task"' in page.text
    assert "Create appliance apply task" in page.text
    assert "internal-sftp" in page.text
    assert "Derived address" in page.text
    assert "<span>Config path</span>" not in page.text
    assert "eth1 - access / trunk" not in page.text
    assert "eth2 - access / access / 192.168.50.1" in page.text
    assert 'data-address="192.168.50.1"' in page.text
    assert "data-vcf-derived-address" in page.text
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "initializeVcfBackupSettings" in app_js.text
    assert "updateVcfBackupDerivedAddress" in app_js.text
    assert "updateVcfBackupValidation" in app_js.text


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
    assert "imgpkg" in page.text
    assert "Create appliance apply task" in page.text
    assert "harbor_admin_password: &lt;provisioned-by-labfoundry-helper&gt;" in page.text
    assert "eth1 - access / trunk" not in page.text
    assert "eth2 - access / access / 192.168.50.1" in page.text
    assert 'data-address="192.168.50.1"' in page.text
    assert "data-vcf-registry-derived-address" in page.text
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

    apply_response = client.post("/vcf-private-registry/apply-task", data={"csrf": csrf})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "vcf-private-registry-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "vcf-private-registry" in (job.result or "")
        assert "imgpkg copy" in (job.result or "")
        assert "provisioned-by-labfoundry-helper" in (job.result or "")
        assert "password123" not in (job.result or "").lower()


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
    user_id = re.search(r'<option value="(\d+)" selected>vcf-backup</option>', page.text).group(1)
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
    assert "ListenAddress 192.168.50.1" in response.json()["config_preview"]
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


def test_vcf_backups_apply_task_captures_sftp_config(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/vcf-backups")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/vcf-backups/apply-task", data={"csrf": csrf})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "vcf-backups-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "vcf-backups" in (job.result or "")
        assert "internal-sftp" in (job.result or "")


def test_physical_and_vlan_pages_render(client):
    login(client)
    physical = client.get("/physical-interfaces")
    assert physical.status_code == 200
    assert "Physical Interfaces" in physical.text
    assert "Configure IPs on access NICs and mark trunks only when a NIC carries tagged VLANs" in physical.text
    assert "physical-interfaces-table" in physical.text
    assert "IP CIDR" in physical.text
    assert "eth0" in physical.text
    assert "192.168.49.1/24" in physical.text
    assert "192.168.50.1/24" in physical.text
    assert "Link Type" in physical.text
    assert "Create appliance apply task" in physical.text
    assert "labfoundry-network.conf" in physical.text

    vlans = client.get("/vlan-interfaces")
    assert vlans.status_code == 200
    assert "VLAN Interfaces" in vlans.text
    assert "For standard access-mode NICs, assign IP CIDR on Physical Interfaces instead." in vlans.text
    assert "vlan-interfaces-table" in vlans.text
    assert "+ Add VLAN here" in client.get("/static/app.js").text
    assert "data-parent-options='[\"eth1\"]'" in vlans.text
    assert "data-parent-options" in vlans.text
    app_js = client.get("/static/app.js").text
    assert "deleteVlanInterfaceFromMenu" in app_js
    assert "refreshNetworkSideStack" in app_js
    assert "Create appliance apply task" in vlans.text


def test_physical_interface_edit_updates_desired_state(client):
    import html
    import json

    login(client)
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
    apply_response = client.post("/vlan-interfaces/apply-task", data={"csrf": csrf})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "network-apply")).scalar_one()
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
    assert "VLAN IP CIDR is required" in missing_ip.text

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
    assert "Create appliance apply task" in page.text
    assert "nftables" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    created = client.post(
        "/firewall/rules",
        data={
            "name": "allow-vcenter",
            "direction": "input",
            "action": "accept",
            "protocol": "tcp",
            "source": "192.168.50.0/24",
            "destination": "any",
            "destination_port": "443",
            "interface_name": "eth1",
            "priority": "30",
            "enabled": "on",
            "description": "VCF management access",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert "allow-vcenter" in client.get("/firewall").text

    apply_response = client.post("/firewall/apply-task", data={"csrf": csrf})
    assert apply_response.status_code == 200
    assert "Appliance apply task" in apply_response.text
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "firewall-apply")).scalar_one()
        assert "labfoundry-helper firewall apply" in (job.result or "")
        assert "allow-vcenter" in (job.result or "")


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
    assert "Command shape" in page.text
    assert "systemctl restart dns" in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/services/firewall/restart", data={"csrf": csrf})
    assert response.status_code == 200
    assert "Firewall restart recorded" in response.text
    assert "systemctl restart firewall" in response.text
    disabled = client.post("/services/firewall/disable", data={"csrf": csrf})
    rows = json.loads(html.unescape(disabled.text.split("data-services='", 1)[1].split("'", 1)[0]))
    assert next(row for row in rows if row["service"] == "firewall")["enabled"] is False
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "initializeServicesTable" in js.text
    assert "submitServiceAction" in js.text
    assert "openServiceActionMenu" not in js.text
    assert "serviceActionsFormatter" not in js.text
    assert 'title: "Enabled"' in js.text
    assert 'editor: "tickCross"' in js.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".service-name-cell" in css.text


def test_ca_settings_autosave_returns_json(client):
    login(client)
    page = client.get("/certificate-authority")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/certificate-authority/settings",
        data={
            "enabled": "on",
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
            "storage_path": "/etc/labfoundry/ca",
            "csrf": csrf,
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert "LabFoundry Test Root CA" in client.get("/certificate-authority").text


def test_ca_apply_task_captures_current_desired_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/certificate-authority")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/certificate-authority/apply-task", data={"csrf": csrf})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "ca-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "LabFoundry Internal Root CA" in (job.result or "")


def test_dns_settings_accept_multiple_listen_interfaces(client):
    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/dns/settings",
        data={
            "enabled": "on",
            "listen_interfaces": ["eth0", "eth1"],
            "listen_addresses": ["192.168.50.1", "192.168.60.1"],
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
    assert "interface=eth1" in refreshed.text
    assert "listen-address=192.168.50.1" in refreshed.text
    assert "listen-address=192.168.60.1" in refreshed.text
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
            "listen_interfaces": ["eth1"],
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
    assert response.json()["listen_interfaces"] == ["eth1"]
    assert response.json()["valid"] is True
    assert "server=/sddc.internal/192.168.10.10" in response.json()["config_preview"]
    assert "server=/sddc.internal/192.168.10.11" in response.json()["config_preview"]
    refreshed = client.get("/dns")
    assert "server=/sddc.internal/192.168.10.10" in refreshed.text
    assert "server=/sddc.internal/192.168.10.11" in refreshed.text
    assert "sddc.internal=192.168.10.10,192.168.10.11" in refreshed.text


def test_dns_apply_task_captures_current_desired_state(client):
    from sqlalchemy import select

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/dns")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/dns/apply-task", data={"csrf": csrf})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "dns-apply")).scalar_one()
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
            "interface_name": "eth1",
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
            "interface_name": "eth1",
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
    from labfoundry.app.models import Job

    login(client)
    page = client.get("/dhcp")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post("/dhcp/apply-task", data={"csrf": csrf})

    assert response.status_code == 200
    assert "Appliance apply task" in response.text
    assert "Dry-run mode recorded the commands" in response.text

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "dhcp-apply")).scalar_one()
        assert job.status == "succeeded"
        assert "labfoundry-helper" in (job.result or "")
        assert "dnsmasq" in (job.result or "")
        assert '"reservation_count": 1' in (job.result or "")


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
    assert "labfoundry.labfoundry.internal" not in refreshed.text


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
    assert "www.labfoundry.internal" in refreshed.text
    assert "cname=www.labfoundry.internal,labfoundry.labfoundry.internal" in refreshed.text
    assert "ipv6.labfoundry.internal" in refreshed.text

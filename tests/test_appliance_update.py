import importlib.machinery
import importlib.util
import json
import logging
import subprocess
from pathlib import Path

from sqlalchemy import select

from labfoundry.app.adapters.system import AdapterResult


def login(client):
    page = client.get("/login")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/login",
        data={"username": "admin", "password": "labfoundry-admin", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return csrf


def csrf_from_page(page_text: str) -> str:
    return page_text.split('name="csrf" value="', 1)[1].split('"', 1)[0]


def load_helper_module():
    helper_path = Path(__file__).resolve().parents[1] / "scripts" / "appliance" / "labfoundry-helper"
    loader = importlib.machinery.SourceFileLoader("labfoundry_helper_update", str(helper_path))
    spec = importlib.util.spec_from_loader("labfoundry_helper_update", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_appliance_update_page_and_dry_run_job(client):
    login(client)
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import UpdateSource

    with SessionLocal() as db:
        source = db.execute(select(UpdateSource).where(UpdateSource.kind == "labfoundry")).scalar_one()
        source.url = "http://localhost:18080/update"
        db.add(source)
        db.commit()
    page = client.get("/appliance-update")
    assert page.status_code == 200
    assert "Appliance Update" in page.text
    assert "Photon OS" in page.text
    assert "Python Libraries" in page.text
    assert "PowerShell Modules" in page.text
    assert "LabFoundry Wheel" in page.text
    assert "http://localhost:18080/update" in page.text
    assert "channels/&lt;channel&gt;/manifest.json" in page.text
    assert "labfoundry-helper appliance-update check" not in page.text

    csrf = csrf_from_page(page.text)
    response = client.post(
        "/appliance-update/run",
        data={
            "csrf": csrf,
            "selected_streams": ["photon_os", "python_libraries", "labfoundry_wheel"],
        },
    )
    assert response.status_code == 200
    assert "Appliance update pending" in response.text
    assert "recorded as dry-run" in response.text

    from labfoundry.app.models import Job
    from labfoundry.app.worker import run_worker_once

    assert run_worker_once()

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-update")).scalar_one()
        payload = json.loads(job.result or "{}")
    assert payload["mode"] == "run"
    assert payload["dry_run"] is True
    command_lines = [" ".join(command["command"]) for command in payload["commands"]]
    assert "labfoundry-helper appliance-update check /var/lib/labfoundry/apply/appliance-update/labfoundry-update.json" in command_lines
    assert "labfoundry-helper appliance-update apply /var/lib/labfoundry/apply/appliance-update/labfoundry-update.json" in command_lines
    assert "labfoundry-helper appliance-update restart-service /var/lib/labfoundry/apply/appliance-update/labfoundry-update.json" in command_lines


def test_appliance_update_settings_validate_urls(client):
    login(client)
    page = client.get("/appliance-update")
    csrf = csrf_from_page(page.text)
    response = client.post(
        "/appliance-update/settings",
        data={
            "csrf": csrf,
            "photon_source": "configured Photon repositories",
            "python_index_url": "not-a-url",
            "labfoundry_manifest_url": "http://localhost:18080/update/manifest.json",
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert response.status_code == 422
    assert "Python index URL must be an http or https URL" in response.text


def test_appliance_update_settings_reject_embedded_credentials(client):
    login(client)
    page = client.get("/appliance-update")
    csrf = csrf_from_page(page.text)
    response = client.post(
        "/appliance-update/settings",
        data={
            "csrf": csrf,
            "photon_source": "configured Photon repositories",
            "python_index_url": "https://user:token@example.test/simple",
            "labfoundry_manifest_url": "http://localhost:18080/update/manifest.json",
        },
        headers={"X-LabFoundry-Autosave": "1"},
    )
    assert response.status_code == 422
    assert "must not include embedded credentials" in response.text


def test_appliance_update_real_helper_failure_is_logged(client, monkeypatch, caplog):
    import labfoundry.app.ui as ui

    class FailingUpdateAdapter:
        dry_run = False

        def check_appliance_update_config(self, config_path: str) -> AdapterResult:
            return AdapterResult(
                command=["labfoundry-helper", "appliance-update", "check", config_path],
                dry_run=False,
                stdout="",
                stderr="manifest refused connection",
                returncode=1,
            )

    monkeypatch.setattr(ui, "SystemAdapter", lambda: FailingUpdateAdapter())
    monkeypatch.setattr(ui, "stage_appliance_apply_config", lambda _path, _preview: "/var/lib/labfoundry/apply/appliance-update/labfoundry-update.json")

    login(client)
    page = client.get("/appliance-update")
    csrf = csrf_from_page(page.text)
    with caplog.at_level(logging.INFO, logger="labfoundry.appliance_update"):
        response = client.post(
            "/appliance-update/check",
            data={"csrf": csrf, "selected_streams": ["photon_os"]},
        )
        from labfoundry.app.worker import run_worker_once

        assert run_worker_once()

    assert response.status_code == 200
    assert "Appliance update pending" in response.text
    assert "manifest refused connection" in caplog.text
    assert "completed status=failed mode=check streams=photon_os" in caplog.text


def test_appliance_update_staging_exception_records_failed_job_and_logs(client, monkeypatch, caplog):
    import labfoundry.app.ui as ui

    class RealUpdateAdapter:
        dry_run = False

    monkeypatch.setattr(ui, "SystemAdapter", lambda: RealUpdateAdapter())

    def fail_stage(_path: str, _preview: str) -> str:
        raise PermissionError("staging ownership repair failed")

    monkeypatch.setattr(ui, "stage_appliance_apply_config", fail_stage)

    login(client)
    page = client.get("/appliance-update")
    csrf = csrf_from_page(page.text)
    with caplog.at_level(logging.INFO, logger="labfoundry.appliance_update"):
        response = client.post(
            "/appliance-update/run",
            data={"csrf": csrf, "selected_streams": ["photon_os"]},
        )
        from labfoundry.app.worker import run_worker_once

        assert run_worker_once()

    assert response.status_code == 200
    assert "Appliance update pending" in response.text
    assert "failed before helper completion" in caplog.text
    assert "staging ownership repair failed" in caplog.text

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-update")).scalar_one()
        payload = json.loads(job.result or "{}")
    assert job.status == "failed"
    assert payload["commands"][0]["command_line"] == "stage-appliance-update /var/lib/labfoundry/apply/appliance-update/labfoundry-update.json"
    assert "staging ownership repair failed" in payload["commands"][0]["stderr"]


def test_appliance_update_service_version_helpers():
    from labfoundry.app.services.appliance_update import redact_url_userinfo, version_with_git

    assert version_with_git("0.1.0", "abcdef1234567890") == "0.1.0+gabcdef123456"
    assert version_with_git("0.1.0+gold", "abcdef") == "0.1.0+gabcdef"
    assert redact_url_userinfo("https://user:token@example.test/simple") == "https://[redacted]@example.test/simple"


def test_current_version_info_has_public_branch_wheel_label(monkeypatch):
    import labfoundry
    import labfoundry.app.services.appliance_update as appliance_update

    monkeypatch.setattr(labfoundry, "__build_git_commit__", "dd9fca8d9d2b83d4bd39538cbc3727dfa8a82062")
    monkeypatch.setattr(labfoundry, "__build_time_utc__", "2026-07-08T15:45:54Z")
    monkeypatch.setattr(appliance_update, "__version__", "0.1.0+gdd9fca8d9d2b")
    monkeypatch.setattr(appliance_update, "_git_value", lambda _args: "")

    info = appliance_update.current_version_info()

    assert info["base_version"] == "0.1.0"
    assert info["git_short"] == "dd9fca8d9d2b"
    assert info["public_label"] == "dd9fca8 (branch wheel)"


def test_current_version_info_has_installed_checksum_fallback(monkeypatch):
    import labfoundry
    import labfoundry.app.services.appliance_update as appliance_update

    monkeypatch.setattr(labfoundry, "__build_git_commit__", "")
    monkeypatch.setattr(labfoundry, "__build_time_utc__", "")
    monkeypatch.setattr(appliance_update, "_git_value", lambda _args: "")
    monkeypatch.setattr(appliance_update, "_installed_record_sha256", lambda: "abc123def4567890")

    info = appliance_update.current_version_info()

    assert info["public_label"] == "installed sha abc123def456"
    assert info["installed_sha256"] == "abc123def4567890"


def test_build_update_wheel_version_helper():
    script_path = Path("scripts/build_update_wheel.py")
    spec = importlib.util.spec_from_file_location("build_update_wheel", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.version_with_git("0.1.0", "1234567890abcdef") == "0.1.0+g1234567890ab"


def test_build_update_wheel_writes_repository_channel_layout(monkeypatch, tmp_path):
    script_path = Path("scripts/build_update_wheel.py")
    spec = importlib.util.spec_from_file_location("build_update_repository", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")

    def fake_copy_source(target):
        (target / "labfoundry").mkdir(parents=True)
        (target / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")

    def fake_build_wheel(_source, dist):
        dist.mkdir(parents=True)
        wheel = dist / "labfoundry-0.1.0+gabcdef123456-py3-none-any.whl"
        wheel.write_bytes(b"wheel")
        return wheel

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "git_value", lambda _args: "abcdef1234567890abcdef1234567890abcdef12")
    monkeypatch.setattr(module, "copy_source", fake_copy_source)
    monkeypatch.setattr(module, "build_wheel", fake_build_wheel)
    monkeypatch.setattr(module.sys, "argv", ["build_update_wheel.py", "--channel", "preview"])
    assert module.main() == 0
    manifest = json.loads((tmp_path / "dist/update/channels/preview/manifest.json").read_text(encoding="utf-8"))
    index = json.loads((tmp_path / "dist/update/index.json").read_text(encoding="utf-8"))
    assert manifest["wheel"].startswith("../../packages/labfoundry-")
    assert manifest["git_commit"] == "abcdef1234567890abcdef1234567890abcdef12"
    assert index["channels"] == {"preview": "channels/preview/manifest.json"}
    assert (tmp_path / "dist/update/manifest.json").is_file()


def test_labfoundry_repository_url_derives_channel_manifest(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import UpdateSource
    from labfoundry.app.services.update_sources import effective_update_settings

    client.get("/login")
    with SessionLocal() as db:
        source = db.execute(select(UpdateSource).where(UpdateSource.kind == "labfoundry")).scalar_one()
        source.url = "https://updates.example.test/labfoundry/"
        source.settings_json = '{"channel":"preview"}'
        db.add(source)
        db.commit()
        settings = effective_update_settings(db)
    assert settings["labfoundry_manifest_url"] == "https://updates.example.test/labfoundry/channels/preview/manifest.json"


def test_runtime_package_client_source_details(monkeypatch, tmp_path):
    from labfoundry.app.services import appliance_update

    (tmp_path / "photon.repo").write_text(
        "[photon]\nname=Photon 5 release\nbaseurl=https://packages.example.test/photon/$releasever/release\nenabled=1\n"
        "[disabled]\nname=Disabled\nbaseurl=https://packages.example.test/disabled\nenabled=0\n",
        encoding="utf-8",
    )
    details = appliance_update.photon_repository_details(tmp_path)
    assert details == [
        {
            "id": "photon",
            "name": "Photon 5 release",
            "location": "https://packages.example.test/photon/$releasever/release",
            "location_type": "baseurl",
            "file": "photon.repo",
        }
    ]
    assert "photon | Photon 5 release | baseurl=https://packages.example.test" in appliance_update.photon_repository_summary(tmp_path)

    monkeypatch.setattr(
        appliance_update.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, ":env:.index-url='https://mirror.example.test/simple'\n", ""),
    )
    appliance_update.effective_pip_index.cache_clear()
    assert appliance_update.effective_pip_index() == {
        "url": "https://mirror.example.test/simple",
        "source": "pip environment",
        "error": "",
    }
    appliance_update.effective_pip_index.cache_clear()


def test_source_sync_is_queued_and_records_validation_status(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job, UpdateSource
    from labfoundry.app.worker import run_worker_once

    login(client)
    page = client.get("/appliance-update")
    csrf = csrf_from_page(page.text)
    response = client.post("/appliance-update/source-sync", data={"csrf": csrf})
    assert response.status_code == 200
    assert "Appliance update pending" in response.text
    assert run_worker_once() is not None
    with SessionLocal() as db:
        job = db.execute(select(Job).where(Job.type == "appliance-update")).scalar_one()
        sources = db.execute(select(UpdateSource).where(UpdateSource.enabled.is_(True))).scalars().all()
        assert json.loads(job.result)["mode"] == "source_sync"
        assert all(source.validation_status == "valid" for source in sources)
        assert all("dry-run" in source.validation_message for source in sources)


def test_software_source_and_managed_module_lifecycle(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import ManagedPackage, UpdateSource
    from labfoundry.app.services.update_sources import effective_update_settings

    login(client)
    csrf = csrf_from_page(client.get("/appliance-update").text)
    created = client.post(
        "/appliance-update/sources",
        data={"csrf": csrf, "kind": "powershell", "name": "PrivateGallery", "url": "https://packages.example.test/powershell", "priority": "20", "enabled": "on"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        source = db.execute(select(UpdateSource).where(UpdateSource.name == "PrivateGallery")).scalar_one()
        source_id = source.id

    package_created = client.post(
        "/appliance-update/packages",
        data={"csrf": csrf, "name": "Private.PowerCLI.Tools", "source_id": str(source_id), "policy": "pinned", "target_version": "1.2.3", "enabled": "on"},
        follow_redirects=False,
    )
    assert package_created.status_code == 303
    grouped_page = client.get("/appliance-update")
    assert "data-runtime-photon-repositories" in grouped_page.text
    assert "data-runtime-python-index" in grouped_page.text
    assert "https://pypi.org/simple" in grouped_page.text
    assert grouped_page.text.count("aligned-control-grid") >= 4
    assert 'aria-label="Appliance update workspace"' in grouped_page.text
    assert 'data-tab-target="appliance-update-sources"' in grouped_page.text
    assert 'data-tab-target="appliance-update-streams"' in grouped_page.text
    assert grouped_page.text.index('data-tab-target="appliance-update-streams"') < grouped_page.text.index('data-tab-target="appliance-update-sources"')
    assert "Synchronize repositories" in grouped_page.text
    assert grouped_page.text.count('class="appliance-update-source-actions"') == 1
    assert 'class="button secondary icon-button"' in grouped_page.text
    assert 'aria-label="Synchronize repositories"' in grouped_page.text
    assert 'class="muted appliance-update-source-intro"' in grouped_page.text
    assert 'data-appliance-update-validation-panel' in grouped_page.text
    assert "Staged update manifest" in grouped_page.text
    assert 'data-config-preview-open' in grouped_page.text
    assert '<div class="config-preview">' not in grouped_page.text
    assert grouped_page.text.index("Update Info") < grouped_page.text.index('data-appliance-update-validation-panel')
    source_actions = grouped_page.text.index('class="appliance-update-source-actions"')
    source_list = grouped_page.text.index('class="apply-unit-list"', source_actions)
    assert source_actions < source_list
    assert 'aria-label="Managed PowerShell modules"' in grouped_page.text
    assert 'data-tab-target="powershell-module-new"' in grouped_page.text
    assert "VCF.PowerCLI" in grouped_page.text
    assert "Private.PowerCLI.Tools" in grouped_page.text
    assert "one tab per module" in grouped_page.text
    assert "data-add-powershell-repository" not in grouped_page.text
    assert 'data-update-source-group="powershell"' in grouped_page.text
    assert 'aria-label="powershell repositories"' in grouped_page.text
    assert "data-powershell-source-new-tab" in grouped_page.text
    assert 'data-tab-target="update-source-powershell-new"' in grouped_page.text
    assert "PSGallery" in grouped_page.text
    assert "PrivateGallery" in grouped_page.text
    with SessionLocal() as db:
        package = db.execute(select(ManagedPackage).where(ManagedPackage.name == "Private.PowerCLI.Tools")).scalar_one()
        package_id = package.id
        module = next(item for item in effective_update_settings(db)["powershell_modules"] if item["name"] == package.name)
        assert module["repository_name"] == "PrivateGallery"
        assert module["target_version"] == "1.2.3"

    changed_to_latest = client.post(
        f"/appliance-update/packages/{package_id}",
        data={
            "csrf": csrf,
            "name": "Private.PowerCLI.Tools",
            "source_id": str(source_id),
            "policy": "latest",
            "target_version": "1.2.3",
            "enabled_present": "1",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert changed_to_latest.status_code == 303
    with SessionLocal() as db:
        package = db.get(ManagedPackage, package_id)
        assert package.policy == "latest"
        assert package.target_version == ""

    blocked = client.post(f"/appliance-update/sources/{source_id}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert blocked.status_code == 409
    assert "Private.PowerCLI.Tools" in blocked.text
    assert client.post(f"/appliance-update/packages/{package_id}/delete", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    assert client.post(f"/appliance-update/sources/{source_id}/delete", data={"csrf": csrf}, follow_redirects=False).status_code == 303


def test_effective_update_settings_preserves_all_enabled_repository_sources(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import UpdateSource
    from labfoundry.app.services.update_sources import effective_update_settings

    client.get("/login")
    with SessionLocal() as db:
        db.add_all(
            [
                UpdateSource(kind="python", name="Backup Python", url="https://python-backup.example.test/simple", priority=80, enabled=True),
                UpdateSource(
                    kind="labfoundry",
                    name="Backup releases",
                    url="https://updates-backup.example.test/labfoundry",
                    priority=80,
                    enabled=True,
                    settings_json=json.dumps({"channel": "preview"}),
                ),
            ]
        )
        primary_python = db.execute(select(UpdateSource).where(UpdateSource.kind == "python")).scalars().first()
        primary_labfoundry = db.execute(select(UpdateSource).where(UpdateSource.kind == "labfoundry")).scalars().first()
        primary_python.url = "https://python-primary.example.test/simple"
        primary_labfoundry.url = "https://updates-primary.example.test/labfoundry"
        db.commit()

        settings = effective_update_settings(db)

    assert settings["python_index_urls"] == [
        "https://python-primary.example.test/simple",
        "https://python-backup.example.test/simple",
    ]
    assert settings["labfoundry_manifest_urls"] == [
        "https://updates-primary.example.test/labfoundry/channels/stable/manifest.json",
        "https://updates-backup.example.test/labfoundry/channels/preview/manifest.json",
    ]
    manifest = json.loads(
        __import__("labfoundry.app.services.appliance_update", fromlist=["render_update_manifest"]).render_update_manifest(
            selected_streams=["python_libraries", "labfoundry_wheel"], settings=settings, actor="test"
        )
    )
    assert manifest["sources"]["python_index_urls"] == settings["python_index_urls"]
    assert manifest["sources"]["labfoundry_manifest_urls"] == settings["labfoundry_manifest_urls"]


def test_source_credentials_use_protected_runtime_channel_without_manifest_disclosure(client):
    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import UpdateSource
    from labfoundry.app.secrets import encrypt_secret
    from labfoundry.app.services.appliance_update import render_update_manifest
    from labfoundry.app.services.update_sources import effective_update_settings, update_source_credentials

    client.get("/login")
    with SessionLocal() as db:
        source = db.execute(select(UpdateSource).where(UpdateSource.kind == "python")).scalars().first()
        source.url = "https://private.example.test/simple"
        source.credential_encrypted = encrypt_secret(json.dumps({"username": "repo-user", "secret": "repo-token"}))
        db.commit()
        source_id = source.id
        settings = effective_update_settings(db)
        credentials = update_source_credentials(db)

    preview = render_update_manifest(selected_streams=["python_libraries"], settings=settings, actor="test")
    assert "repo-user" not in preview
    assert "repo-token" not in preview
    assert credentials[str(source_id)] == {"username": "repo-user", "secret": "repo-token"}


def test_helper_uses_all_python_indexes_and_credentials_without_logging_them(monkeypatch):
    helper = load_helper_module()
    captured = {}

    def fake_command(command, *, success_codes=None, env=None):
        captured["command"] = command
        captured["env"] = env or {}
        return {"command": command, "returncode": 0, "success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(helper, "_command_payload", fake_command)
    payload = {
        "selected_streams": ["python_libraries"],
        "sources": {
            "python_index_url": "https://primary.example.test/simple",
            "python_index_urls": ["https://primary.example.test/simple", "https://backup.example.test/simple"],
        },
        "source_definitions": [
            {"id": 10, "kind": "python", "url": "https://primary.example.test/simple", "enabled": True},
            {"id": 11, "kind": "python", "url": "https://backup.example.test/simple", "enabled": True},
        ],
    }
    result = helper._check_appliance_update(
        payload,
        {"10": {"username": "primary", "secret": "token-one"}, "11": {"username": "backup", "secret": "token-two"}},
    )
    assert result["checks"]["python_libraries"] == "pip outdated check completed."
    assert captured["env"]["PIP_INDEX_URL"] == "https://primary:token-one@primary.example.test/simple"
    assert captured["env"]["PIP_EXTRA_INDEX_URL"] == "https://backup:token-two@backup.example.test/simple"
    assert "token-one" not in " ".join(captured["command"])
    assert "token-two" not in " ".join(captured["command"])


def test_helper_redacts_repository_credentials_from_package_client_output(monkeypatch):
    from types import SimpleNamespace

    helper = load_helper_module()
    monkeypatch.setattr(
        helper,
        "_run",
        lambda _command, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="index https://repo-user:repo-token@private.example.test/simple",
            stderr="authentication failed for repo-user using repo-token",
        ),
    )
    result = helper._command_payload(
        ["python", "-m", "pip", "list"],
        env={
            "PIP_INDEX_URL": "https://repo-user:repo-token@private.example.test/simple",
            "LF_REPO_USER": "repo-user",
            "LF_REPO_SECRET": "repo-token",
        },
    )
    rendered = json.dumps(result)
    assert "repo-user" not in rendered
    assert "repo-token" not in rendered
    assert "[redacted]" in rendered


def test_helper_falls_back_to_next_labfoundry_release_source(monkeypatch):
    helper = load_helper_module()
    attempted = []
    expected_manifest = {
        "version": "0.2.0",
        "git_commit": "a" * 40,
        "wheel": "labfoundry-0.2.0.whl",
        "sha256": "b" * 64,
    }

    def fake_manifest(url, credential=None):
        attempted.append((url, credential))
        if "primary" in url:
            raise OSError("primary unavailable")
        return expected_manifest

    monkeypatch.setattr(helper, "_download_labfoundry_update_manifest", fake_manifest)
    manifest, url, credential = helper._download_labfoundry_manifest_from_sources(
        {
            "sources": {
                "labfoundry_manifest_urls": [
                    "https://primary.example.test/manifest.json",
                    "https://backup.example.test/manifest.json",
                ]
            },
            "source_definitions": [
                {"id": 1, "kind": "labfoundry", "url": "https://primary.example.test/manifest.json", "enabled": True},
                {"id": 2, "kind": "labfoundry", "url": "https://backup.example.test/manifest.json", "enabled": True},
            ],
        },
        {"2": {"username": "backup", "secret": "token"}},
    )
    assert manifest == expected_manifest
    assert url == "https://backup.example.test/manifest.json"
    assert credential == {"username": "backup", "secret": "token"}
    assert [item[0] for item in attempted] == [
        "https://primary.example.test/manifest.json",
        "https://backup.example.test/manifest.json",
    ]


def test_helper_syncs_owned_photon_and_python_sources(monkeypatch, tmp_path):
    helper = load_helper_module()
    photon_path = tmp_path / "labfoundry-managed.repo"
    pip_path = tmp_path / "pip.conf"
    state_path = tmp_path / "update-sources.json"
    monkeypatch.setattr(helper, "MANAGED_PHOTON_REPO_PATH", photon_path)
    monkeypatch.setattr(helper, "LABFOUNDRY_PIP_CONFIG_PATH", pip_path)
    monkeypatch.setattr(helper, "UPDATE_SOURCE_STATE_PATH", state_path)
    monkeypatch.setattr(helper, "_command_path", lambda _name: None)
    payload = {
        "source_definitions": [
            {
                "kind": "photon",
                "name": "Internal Photon",
                "url": "https://packages.example.test/photon/5/x86_64",
                "enabled": True,
                "settings": {"managed": True, "gpgcheck": True, "tls_verify": True},
            },
            {
                "kind": "python",
                "name": "Internal Python",
                "url": "https://packages.example.test/simple",
                "enabled": True,
                "settings": {"tls_verify": True},
            },
        ]
    }
    result = helper._sync_appliance_update_sources(payload)
    assert result["status"] == "succeeded"
    assert "[internal-photon]" in photon_path.read_text(encoding="utf-8")
    assert "gpgcheck=1" in photon_path.read_text(encoding="utf-8")
    assert "index-url = https://packages.example.test/simple" in pip_path.read_text(encoding="utf-8")
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"powershell_repositories": []}


def test_helper_uses_each_modules_bound_powershell_repository(monkeypatch):
    import base64

    helper = load_helper_module()
    scripts = []
    monkeypatch.setattr(helper, "_command_path", lambda _name: "/usr/bin/pwsh")

    def fake_command(command, *, success_codes=None):
        scripts.append(base64.b64decode(command[-1]).decode("utf-16-le"))
        return {"command": command, "returncode": 0, "success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(helper, "_command_payload", fake_command)
    result = helper._check_appliance_update(
        {
            "selected_streams": ["powershell_modules"],
            "sources": {"powershell_repository_name": "PSGallery"},
            "powershell_modules": [
                {"name": "VCF.PowerCLI", "repository_name": "PSGallery", "target_version": "9.1.0"},
                {"name": "Private.Tools", "repository_name": "PrivateGallery", "target_version": ""},
            ],
        }
    )
    assert result["checks"]["powershell_modules"][1]["repository"] == "PrivateGallery"
    assert any("-Repository 'PrivateGallery'" in script for script in scripts)


def test_helper_normalizes_system_powershell_module_permissions_after_install(monkeypatch, tmp_path):
    helper = load_helper_module()
    powershell_root = tmp_path / "powershell"
    module_root = powershell_root / "Modules"
    commands = []

    def fake_command(command, *, success_codes=None, env=None):
        commands.append(command)
        return {"command": command, "returncode": 0, "success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(helper, "POWERSHELL_SYSTEM_ROOT", powershell_root)
    monkeypatch.setattr(helper, "POWERSHELL_MODULE_ROOT", module_root)
    monkeypatch.setattr(helper, "_command_path", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(helper, "_command_payload", fake_command)

    result = helper._apply_powershell_modules(
        {
            "sources": {"powershell_repository_name": "PSGallery"},
            "powershell_modules": [
                {
                    "name": "VCF.PowerCLI",
                    "repository_name": "PSGallery",
                    "target_version": "9.1.0.25380678",
                    "policy": "pinned",
                }
            ],
        }
    )

    assert len(result) == 3
    assert commands[-2] == ["/usr/bin/chmod", "0755", str(powershell_root), str(module_root)]
    assert commands[-1] == ["/usr/bin/chmod", "-R", "a+rX,go-w", str(module_root)]


def test_helper_reports_powershell_permission_normalization_failure(monkeypatch, tmp_path):
    helper = load_helper_module()
    powershell_root = tmp_path / "powershell"
    module_root = powershell_root / "Modules"

    def fake_command(command, *, success_codes=None, env=None):
        failed = command[:3] == ["/usr/bin/chmod", "-R", "a+rX,go-w"]
        return {
            "command": command,
            "returncode": 1 if failed else 0,
            "success": not failed,
            "stdout": "",
            "stderr": "permission normalization failed" if failed else "",
        }

    monkeypatch.setattr(helper, "POWERSHELL_SYSTEM_ROOT", powershell_root)
    monkeypatch.setattr(helper, "POWERSHELL_MODULE_ROOT", module_root)
    monkeypatch.setattr(helper, "_command_path", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(helper, "_command_payload", fake_command)

    result = helper._apply_appliance_update(
        {
            "selected_streams": ["powershell_modules"],
            "sources": {"powershell_repository_name": "PSGallery"},
            "powershell_modules": [
                {
                    "name": "VCF.PowerCLI",
                    "repository_name": "PSGallery",
                    "target_version": "9.1.0.25380678",
                    "policy": "pinned",
                }
            ],
        }
    )

    assert result["status"] == "failed"
    assert result["applied"] == {}
    assert result["commands"][-1]["command"] == [
        "/usr/bin/chmod",
        "-R",
        "a+rX,go-w",
        str(module_root),
    ]
    assert result["commands"][-1]["success"] is False


def test_helper_runs_managed_script_in_unprivileged_systemd_sandbox(monkeypatch, tmp_path):
    from types import SimpleNamespace

    helper = load_helper_module()
    script_root = tmp_path / "scripts"
    run_root = tmp_path / "runs"
    script_root.mkdir()
    script_path = script_root / "job_1.sh"
    script_path.write_text("date\n", encoding="utf-8")
    monkeypatch.setattr(helper, "AUTOMATION_SCRIPT_DIR", script_root)
    monkeypatch.setattr(helper, "AUTOMATION_RUN_DIR", run_root)
    monkeypatch.setattr(helper.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=1234, pw_gid=1234))
    monkeypatch.setattr(helper, "_chown_path", lambda *_args: None)
    monkeypatch.setattr(helper, "_command_path", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(helper.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "systemd-run" else None)
    captured = {}

    def fake_run(command):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout="completed\n", stderr="")

    monkeypatch.setattr(helper, "_run", fake_run)
    assert helper._handle_automation("run", [str(script_path), "bash", "60", "--", "--scope", "lab environment"]) == 0
    command = captured["command"]
    assert "--uid=labfoundry-automation" in command
    assert "--property=NoNewPrivileges=yes" in command
    assert "--property=ProtectSystem=strict" in command
    writable_path = Path(next(argument.split("=", 2)[2] for argument in command if argument.startswith("--property=ReadWritePaths=")))
    assert writable_path.parent == run_root
    assert f"--property=WorkingDirectory={writable_path}" in command
    assert f"--setenv=HOME={writable_path}" in command
    assert f"--setenv=XDG_CACHE_HOME={writable_path / '.cache'}" in command
    assert command[-4:] == ["/usr/bin/bash", str(script_path.resolve()), "--scope", "lab environment"]


def test_helper_rejects_appliance_update_config_outside_apply_dir(tmp_path):
    helper = load_helper_module()
    config_path = tmp_path / "labfoundry-update.json"
    config_path.write_text("{}", encoding="utf-8")

    try:
        helper._validate_appliance_update_config_path(str(config_path))
    except ValueError as exc:
        assert "must be staged under" in str(exc)
    else:
        raise AssertionError("expected helper to reject config outside appliance update apply dir")


def test_helper_rejects_labfoundry_wheel_sha_mismatch(monkeypatch, tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-update"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-update.json"
    config_path.write_text(
        json.dumps(
            {
                "selected_streams": ["labfoundry_wheel"],
                "sources": {"labfoundry_manifest_url": "http://updates.local/manifest.json"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "APPLIANCE_UPDATE_APPLY_DIR", apply_dir)

    def fake_fetch(url: str) -> bytes:
        if url.endswith("manifest.json"):
            return json.dumps(
                {
                    "version": "0.1.0+gabc",
                    "git_commit": "abcdef1234567890abcdef1234567890abcdef12",
                    "wheel": "labfoundry-0.1.0.whl",
                    "sha256": "0" * 64,
                }
            ).encode("utf-8")
        return b"wheel-content"

    monkeypatch.setattr(helper, "_fetch_http_bytes", fake_fetch)

    assert helper._handle_appliance_update("apply", [str(config_path)]) == 1
    captured = capsys.readouterr()
    assert "sha256 mismatch" in captured.err


def test_helper_rejects_credentialed_update_urls(tmp_path, capsys):
    helper = load_helper_module()
    apply_dir = tmp_path / "apply" / "appliance-update"
    apply_dir.mkdir(parents=True)
    config_path = apply_dir / "labfoundry-update.json"
    config_path.write_text(
        json.dumps(
            {
                "selected_streams": ["labfoundry_wheel"],
                "sources": {"labfoundry_manifest_url": "https://user:token@example.test/manifest.json"},
            }
        ),
        encoding="utf-8",
    )
    helper.APPLIANCE_UPDATE_APPLY_DIR = apply_dir

    assert helper._handle_appliance_update("check", [str(config_path)]) == 2
    captured = capsys.readouterr()
    assert "must not include embedded credentials" in captured.err


def test_helper_writes_failed_update_info_for_failed_commands(monkeypatch):
    helper = load_helper_module()
    written = {}

    def fake_command_payload(command, *, success_codes=None):
        return {"command": command, "returncode": 1, "success": False, "stdout": "", "stderr": "failed"}

    monkeypatch.setattr(helper, "_command_payload", fake_command_payload)
    monkeypatch.setattr(helper, "_command_path", lambda command: command)
    monkeypatch.setattr(helper, "_write_update_info", lambda payload: written.update(payload))

    result = helper._apply_appliance_update({"selected_streams": ["photon_os"], "sources": {}})
    assert result["status"] == "failed"
    assert result["applied"] == {}
    assert result["attempted"]["photon_os"] == "Photon OS packages updated from configured repositories."
    assert result["reboot_recommended"] is False
    assert "error" in result
    assert written["status"] == "failed"

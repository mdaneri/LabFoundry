import importlib.machinery
import importlib.util
import json
import logging
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
    page = client.get("/appliance-update")
    assert page.status_code == 200
    assert "Appliance Update" in page.text
    assert "Photon OS" in page.text
    assert "Python Libraries" in page.text
    assert "LabFoundry Wheel" in page.text
    assert "http://localhost:18080/update/manifest.json" in page.text
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
    assert "Appliance update succeeded" in response.text
    assert "recorded as dry-run" in response.text

    from labfoundry.app.database import SessionLocal
    from labfoundry.app.models import Job

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
            data={"csrf": csrf, "selected_streams": ["labfoundry_wheel"]},
        )

    assert response.status_code == 200
    assert "Appliance update failed" in response.text
    assert "manifest refused connection" in caplog.text
    assert "completed status=failed mode=check streams=labfoundry_wheel" in caplog.text


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

    assert response.status_code == 200
    assert "Appliance update failed" in response.text
    assert "failed before helper execution mode=run streams=photon_os" in caplog.text
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


def test_build_update_wheel_version_helper():
    script_path = Path("scripts/build_update_wheel.py")
    spec = importlib.util.spec_from_file_location("build_update_wheel", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.version_with_git("0.1.0", "1234567890abcdef") == "0.1.0+g1234567890ab"


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
                    "git_commit": "abcdef",
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

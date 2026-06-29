import importlib.machinery
import importlib.util
import json
from pathlib import Path

from sqlalchemy import select


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


def test_appliance_update_service_version_helpers():
    from labfoundry.app.services.appliance_update import redact_url_userinfo, version_with_git

    assert version_with_git("0.1.0", "abcdef1234567890") == "0.1.0+gabcdef123456"
    assert version_with_git("0.1.0+gold", "abcdef") == "0.1.0+gabcdef"
    assert redact_url_userinfo("https://user:token@example.test/simple") == "https://[redacted]@example.test/simple"


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

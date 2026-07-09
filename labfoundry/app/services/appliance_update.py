from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from labfoundry import __version__
from labfoundry.app.models import Job


APPLIANCE_UPDATE_SETTINGS_KEY = "appliance_update.settings.v1"
APPLIANCE_UPDATE_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/appliance-update/labfoundry-update.json"
APPLIANCE_UPDATE_INFO_PATH = "/etc/labfoundry/update-info"
DEFAULT_LABFOUNDRY_MANIFEST_URL = "http://localhost:18080/update/manifest.json"
UPDATE_STREAMS = ("photon_os", "python_libraries", "labfoundry_wheel")
UPDATE_STREAM_LABELS = {
    "photon_os": "Photon OS",
    "python_libraries": "Python Libraries",
    "labfoundry_wheel": "LabFoundry Wheel",
}
DEFAULT_UPDATE_SETTINGS = {
    "photon_source": "configured Photon repositories",
    "python_index_url": "",
    "labfoundry_manifest_url": DEFAULT_LABFOUNDRY_MANIFEST_URL,
}
DIRECT_PYTHON_REQUIREMENTS = [
    "argon2-cffi>=23.1.0",
    "cryptography>=42.0.0",
    "fastapi>=0.115.0",
    "itsdangerous>=2.2.0",
    "jinja2>=3.1.4",
    "pyjwt>=2.9.0",
    "pydantic-settings>=2.6.0",
    "pykmip==0.10.0",
    "python-multipart>=0.0.12",
    "sqlalchemy>=2.0.36",
    "uvicorn[standard]>=0.32.0",
]


def _git_value(args: list[str]) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=Path(__file__).resolve().parents[3], check=False, capture_output=True, text=True)
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


@lru_cache(maxsize=1)
def _installed_record_sha256() -> str:
    try:
        distribution = importlib_metadata.distribution("labfoundry")
    except importlib_metadata.PackageNotFoundError:
        return ""
    record_text = distribution.read_text("RECORD") or ""
    if not record_text:
        return ""
    return hashlib.sha256(record_text.encode("utf-8")).hexdigest()


def current_version_info() -> dict[str, str]:
    full_commit = getattr(__import__("labfoundry"), "__build_git_commit__", "") or _git_value(["rev-parse", "HEAD"])
    short_commit = full_commit[:12] if full_commit else ""
    built_at = getattr(__import__("labfoundry"), "__build_time_utc__", "")
    source_dirty = _git_value(["status", "--short"]) != "" if not built_at else False
    public_label = f"{short_commit[:7]} (branch wheel)" if built_at and short_commit else short_commit
    installed_sha256 = _installed_record_sha256()
    if not public_label and installed_sha256:
        public_label = f"installed sha {installed_sha256[:12]}"
    return {
        "version": __version__,
        "base_version": __version__.split("+", 1)[0],
        "git_commit": full_commit,
        "git_short": short_commit,
        "public_label": public_label,
        "installed_sha256": installed_sha256,
        "built_at": built_at,
        "source_dirty": "true" if source_dirty else "false",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def version_with_git(base_version: str, git_commit: str) -> str:
    normalized = base_version.strip()
    if not normalized:
        normalized = "0.0.0"
    if "+" in normalized:
        normalized = normalized.split("+", 1)[0]
    short = re.sub(r"[^0-9A-Fa-f]", "", git_commit or "")[:12]
    return f"{normalized}+g{short}" if short else normalized


def update_settings_from_json(raw_value: str) -> dict[str, str]:
    settings = dict(DEFAULT_UPDATE_SETTINGS)
    if raw_value:
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            for key in settings:
                value = payload.get(key)
                if isinstance(value, str):
                    settings[key] = value.strip()
    return settings


def update_settings_to_json(settings: dict[str, str]) -> str:
    normalized = dict(DEFAULT_UPDATE_SETTINGS)
    for key in normalized:
        normalized[key] = str(settings.get(key) or "").strip()
    if not normalized["labfoundry_manifest_url"]:
        normalized["labfoundry_manifest_url"] = DEFAULT_LABFOUNDRY_MANIFEST_URL
    return json.dumps(normalized, indent=2, sort_keys=True)


def validate_update_url(value: str, label: str) -> list[str]:
    if not value.strip():
        return []
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return [f"{label} must be an http or https URL."]
    if parsed.username or parsed.password:
        return [f"{label} must not include embedded credentials."]
    return []


def validate_update_settings(settings: dict[str, str]) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_update_url(settings.get("python_index_url", ""), "Python index URL"))
    errors.extend(validate_update_url(settings.get("labfoundry_manifest_url", ""), "LabFoundry manifest URL"))
    return errors


def selected_update_streams(raw_streams: list[str] | tuple[str, ...]) -> list[str]:
    selected = [stream for stream in UPDATE_STREAMS if stream in set(raw_streams)]
    return selected


def redact_url_userinfo(value: str) -> str:
    parsed = urlparse(value or "")
    if not parsed.scheme or not parsed.netloc or not (parsed.username or parsed.password):
        return value
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=f"[redacted]@{host}").geturl()


def render_update_manifest(*, selected_streams: list[str], settings: dict[str, str], actor: str) -> str:
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": actor,
        "selected_streams": selected_streams,
        "sources": {
            "photon_os": settings.get("photon_source") or DEFAULT_UPDATE_SETTINGS["photon_source"],
            "python_index_url": redact_url_userinfo(settings.get("python_index_url", "")),
            "labfoundry_manifest_url": redact_url_userinfo(settings.get("labfoundry_manifest_url") or DEFAULT_LABFOUNDRY_MANIFEST_URL),
        },
        "python_requirements": DIRECT_PYTHON_REQUIREMENTS,
        "current": current_version_info(),
        "policy": {
            "auto_reboot": False,
            "restart_labfoundry_after_wheel": "delayed",
            "wheel_install_mode": "force-reinstall-no-deps",
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def update_result_excerpt(value: str, *, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n[output truncated]"


def parse_latest_update_result(job: Job | None) -> dict[str, Any] | None:
    if job is None or not job.result:
        return None
    try:
        payload = json.loads(job.result)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def read_appliance_file(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"path": path_value, "available": False, "content": "", "error": str(exc)}
    return {"path": path_value, "available": True, "content": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from labfoundry.app.models import EsxiKickstart, EsxiPxeHost, Setting, utcnow

ESXI_PXE_UNIT_ID = "esxi_pxe"
ESXI_PXE_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/esxi-pxe/labfoundry-esxi-pxe.json"
ESXI_KICKSTART_HTTP_ROOT = Path("/var/lib/labfoundry/pxe/http/esxi/ks")
ESXI_KICKSTART_HTTP_PREFIX = "/pxe/esxi/ks"
ESXI_INSTALLER_ISO_ROOT = Path("/mnt/labfoundry-vcf-offline-depot/PROD/COMP/ESX_HOST")
ESXI_PXE_STRICT_VALIDATION_KEY = "esxi_pxe.strict_kickstart_validation"
SECRET_KEYWORD_PATTERN = re.compile(r"(rootpw|password|passwd|token|secret|key|license|activation|credential)", re.IGNORECASE)
TEMPLATE_PATTERN = re.compile(r"({[{%#].*?[}%]}|\$\{[^}]+\})")
SAFE_ISO_UPLOAD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*\.iso$", re.IGNORECASE)


def normalize_kickstart_name(value: str) -> str:
    name = re.sub(r"\s+", " ", (value or "").strip())
    if not name:
        raise ValueError("Kickstart name is required.")
    if len(name) > 120:
        raise ValueError("Kickstart name must be 120 characters or fewer.")
    return name


def normalize_kickstart_content(value: str, *, max_bytes: int) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.strip():
        raise ValueError("Kickstart content is required.")
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(f"Kickstart content is too large. Limit is {max_bytes} bytes.")
    if not text.endswith("\n"):
        text += "\n"
    return text


def decode_kickstart_upload(raw: bytes, *, max_bytes: int) -> str:
    if len(raw) > max_bytes:
        raise ValueError(f"Kickstart upload is too large. Limit is {max_bytes} bytes.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Kickstart upload must be valid UTF-8 text.") from exc
    return normalize_kickstart_content(text, max_bytes=max_bytes)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def canonical_http_path(kickstart_id: int) -> str:
    return f"{ESXI_KICKSTART_HTTP_PREFIX}/{kickstart_id}.cfg"


def generated_kickstart_path(kickstart_id: int) -> Path:
    return ESXI_KICKSTART_HTTP_ROOT / f"{kickstart_id}.cfg"


def ensure_installer_iso_root() -> Path:
    ESXI_INSTALLER_ISO_ROOT.mkdir(parents=True, exist_ok=True)
    return ESXI_INSTALLER_ISO_ROOT


def installer_iso_root_path() -> str:
    return str(ESXI_INSTALLER_ISO_ROOT)


def safe_installer_iso_name(filename: str) -> str:
    name = Path(filename or "").name.strip()
    if not SAFE_ISO_UPLOAD_PATTERN.fullmatch(name):
        raise ValueError("Upload an ESXi installer ISO with a safe .iso filename.")
    return name


def installer_iso_inventory() -> list[dict[str, Any]]:
    root = ensure_installer_iso_root()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.iso"), key=lambda item: str(item).lower()):
        if not path.is_file():
            continue
        stat = path.stat()
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "relative_path": path.relative_to(root).as_posix(),
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    return rows


def normalize_installer_iso_path(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    root = ensure_installer_iso_root().resolve()
    path = Path(raw)
    if not path.is_absolute():
        path = root / raw
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Installer ISO must be under {root}.")
    if resolved.suffix.lower() != ".iso":
        raise ValueError("Installer ISO must be a .iso file.")
    if not resolved.is_file():
        raise ValueError(f"Installer ISO does not exist: {resolved}")
    return str(resolved)


async def store_installer_iso_upload(upload_file: Any, *, max_bytes: int) -> dict[str, Any]:
    root = ensure_installer_iso_root()
    filename = safe_installer_iso_name(upload_file.filename or "")
    destination = root / filename
    temp_path = root / f".{filename}.uploading"
    total = 0
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Installer ISO upload is too large. Limit is {max_bytes} bytes.")
                handle.write(chunk)
        if total == 0:
            raise ValueError("Installer ISO upload is empty.")
        shutil.move(str(temp_path), destination)
        destination.chmod(0o644)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    stat = destination.stat()
    return {
        "name": destination.name,
        "path": str(destination),
        "relative_path": destination.relative_to(root).as_posix(),
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def assign_kickstart_content(kickstart: EsxiKickstart, content: str, *, max_bytes: int) -> None:
    normalized = normalize_kickstart_content(content, max_bytes=max_bytes)
    kickstart.content = normalized
    kickstart.content_hash = content_hash(normalized)
    kickstart.rendered_content = normalized
    kickstart.http_path = canonical_http_path(kickstart.id) if kickstart.id else kickstart.http_path
    kickstart.updated_at = utcnow()


def redacted_kickstart_preview(content: str) -> str:
    lines: list[str] = []
    for raw_line in (content or "").splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(line)
            continue
        lower = stripped.lower()
        if lower.startswith("rootpw") or SECRET_KEYWORD_PATTERN.search(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            command = stripped.split(None, 1)[0]
            if "=" in stripped and not lower.startswith("rootpw"):
                prefix = line.split("=", 1)[0].rstrip()
                lines.append(f"{prefix}= ********")
            else:
                lines.append(f"{indent}{command} ********")
            continue
        lines.append(line)
    return "\n".join(lines)


def kickstart_validation(content: str, *, strict: bool, max_bytes: int) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        normalized = normalize_kickstart_content(content, max_bytes=max_bytes)
    except ValueError as exc:
        return [str(exc)], []

    lines = [line.strip() for line in normalized.splitlines()]
    directive_text = "\n".join(lines).lower()
    checks = [
        ("rootpw", any(line.startswith("rootpw") for line in lines), "missing rootpw"),
        ("install or upgrade", bool(re.search(r"(?m)^(install|upgrade)(\s|$)", directive_text)), "missing install or upgrade directive"),
        ("network", any(line.startswith("network") for line in lines), "missing network directive"),
        ("reboot", any(line.startswith("reboot") for line in lines), "missing reboot directive"),
        ("%firstboot", any(line.startswith("%firstboot") for line in lines), "missing firstboot section"),
    ]
    missing = [message for _label, present, message in checks if not present]
    if strict:
        errors.extend(missing)
    else:
        warnings.extend(missing)

    for line in lines:
        if SECRET_KEYWORD_PATTERN.search(line) and not line.startswith("#"):
            warnings.append("contains plaintext password or secret-looking value")
            break
    if TEMPLATE_PATTERN.search(normalized):
        warnings.append("contains unsupported template variable")
    return errors, list(dict.fromkeys(warnings))


def strict_validation_enabled(db: Session) -> bool:
    row = db.execute(select(Setting).where(Setting.key == ESXI_PXE_STRICT_VALIDATION_KEY)).scalar_one_or_none()
    return bool(row and row.value.strip().lower() in {"1", "true", "yes", "on"})


def filesystem_hash(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def kickstart_drift_state(kickstart: EsxiKickstart) -> str:
    path = generated_kickstart_path(kickstart.id)
    disk_hash = filesystem_hash(path)
    if not kickstart.rendered_hash and disk_hash is None:
        return "not_rendered"
    if kickstart.rendered_hash and disk_hash is None:
        return "filesystem_missing"
    if kickstart.rendered_hash and kickstart.content_hash != kickstart.rendered_hash:
        return "database_changed_pending_apply"
    if disk_hash != kickstart.content_hash:
        return "filesystem_modified"
    return "in_sync"


def kickstart_to_dict(kickstart: EsxiKickstart, *, include_content: bool = False) -> dict[str, Any]:
    payload = {
        "id": kickstart.id,
        "name": kickstart.name,
        "description": kickstart.description or "",
        "content_hash": kickstart.content_hash,
        "rendered_hash": kickstart.rendered_hash or "",
        "http_path": kickstart.http_path or canonical_http_path(kickstart.id),
        "enabled": kickstart.enabled,
        "created_at": kickstart.created_at,
        "updated_at": kickstart.updated_at,
        "last_rendered_at": kickstart.last_rendered_at,
        "last_applied_at": kickstart.last_applied_at,
        "redacted_preview": redacted_kickstart_preview(kickstart.content),
        "drift_state": kickstart_drift_state(kickstart),
    }
    if include_content:
        payload["content"] = kickstart.content
    return payload


def host_to_dict(host: EsxiPxeHost) -> dict[str, Any]:
    iso_path = host.installer_iso_path or ""
    return {
        "id": host.id,
        "hostname": host.hostname,
        "mac_address": host.mac_address,
        "kickstart_id": host.kickstart_id,
        "kickstart_name": host.kickstart.name if host.kickstart else "",
        "installer_iso_path": iso_path,
        "installer_iso_name": Path(iso_path).name if iso_path else "",
        "enabled": host.enabled,
        "created_at": host.created_at,
        "updated_at": host.updated_at,
    }


def render_esxi_pxe_manifest(kickstarts: list[EsxiKickstart], hosts: list[EsxiPxeHost]) -> str:
    iso_error = ""
    try:
        installer_isos = installer_iso_inventory()
    except OSError as exc:
        installer_isos = []
        iso_error = str(exc)
    payload = {
        "kind": "labfoundry-esxi-pxe",
        "schema_version": 1,
        "http_root": str(ESXI_KICKSTART_HTTP_ROOT),
        "installer_iso_root": str(ESXI_INSTALLER_ISO_ROOT),
        "installer_isos": installer_isos,
        "installer_iso_error": iso_error,
        "kickstarts": [
            {
                "id": row.id,
                "name": row.name,
                "enabled": row.enabled,
                "content": row.rendered_content if row.rendered_content is not None else row.content,
                "content_hash": row.content_hash,
                "http_path": row.http_path or canonical_http_path(row.id),
                "generated_path": str(generated_kickstart_path(row.id)),
            }
            for row in kickstarts
        ],
        "hosts": [
            {
                "id": host.id,
                "hostname": host.hostname,
                "mac_address": host.mac_address,
                "kickstart_id": host.kickstart_id,
                "installer_iso_path": host.installer_iso_path or "",
                "installer_iso_name": Path(host.installer_iso_path).name if host.installer_iso_path else "",
                "enabled": host.enabled,
            }
            for host in hosts
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_esxi_pxe_preview(kickstarts: list[EsxiKickstart], hosts: list[EsxiPxeHost]) -> str:
    payload = json.loads(render_esxi_pxe_manifest(kickstarts, hosts))
    for row in payload["kickstarts"]:
        row["content"] = redacted_kickstart_preview(str(row["content"]))
    return json.dumps(payload, indent=2, sort_keys=True)


def mark_kickstarts_applied(kickstarts: list[EsxiKickstart]) -> None:
    timestamp = utcnow()
    for row in kickstarts:
        rendered = row.rendered_content if row.rendered_content is not None else row.content
        row.rendered_hash = content_hash(rendered)
        row.last_rendered_at = timestamp
        row.last_applied_at = timestamp
        row.http_path = canonical_http_path(row.id)

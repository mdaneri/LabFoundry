from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from hashlib import sha256
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from labfoundry.app.config import Settings, get_settings
from labfoundry.app.models import User, utcnow


LOCAL_USERS_PASSWORD_POLICY_KEY = "local_users.password_policy.v1"
LOCAL_USERS_STAGED_CONFIG_PATH = "/var/lib/labfoundry/apply/local-users/labfoundry-users.json"
LOCAL_USERS_CONFIG_VERSION = 1

USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RESERVED_USERNAMES = {
    "adm",
    "bin",
    "daemon",
    "dbus",
    "games",
    "halt",
    "labfoundry",
    "lp",
    "mail",
    "nfsnobody",
    "nobody",
    "operator",
    "root",
    "shutdown",
    "sshd",
    "sync",
    "systemd-network",
    "systemd-resolve",
}

DEFAULT_PASSWORD_POLICY: dict[str, bool | int] = {
    "min_length": 12,
    "require_uppercase": True,
    "require_lowercase": True,
    "require_number": True,
    "require_special": True,
    "disallow_username": True,
}


def _fernet(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    key = base64.urlsafe_b64encode(sha256(settings.secret_key.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_os_password(password: str, settings: Settings | None = None) -> str:
    return _fernet(settings).encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_os_password(encrypted_password: str, settings: Settings | None = None) -> str:
    try:
        return _fernet(settings).decrypt(encrypted_password.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ValueError("Pending OS password could not be decrypted with the current secret key.") from exc


def password_policy_from_json(raw_value: str | None) -> dict[str, bool | int]:
    policy = dict(DEFAULT_PASSWORD_POLICY)
    if raw_value:
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            if "min_length" in payload:
                try:
                    policy["min_length"] = max(8, min(128, int(payload["min_length"])))
                except (TypeError, ValueError):
                    pass
            for key in ("require_uppercase", "require_lowercase", "require_number", "require_special", "disallow_username"):
                if key in payload:
                    policy[key] = bool(payload[key])
    return policy


def password_policy_to_json(policy: dict[str, bool | int]) -> str:
    normalized = password_policy_from_json(json.dumps(policy))
    return json.dumps(normalized, indent=2, sort_keys=True)


def password_policy_summary(policy: dict[str, bool | int]) -> str:
    parts = [f"minimum {int(policy['min_length'])} characters"]
    if policy.get("require_uppercase"):
        parts.append("uppercase")
    if policy.get("require_lowercase"):
        parts.append("lowercase")
    if policy.get("require_number"):
        parts.append("number")
    if policy.get("require_special"):
        parts.append("special")
    if policy.get("disallow_username"):
        parts.append("no username")
    return ", ".join(parts)


def validate_password(password: str, username: str, policy: dict[str, bool | int]) -> list[str]:
    errors: list[str] = []
    min_length = int(policy.get("min_length") or DEFAULT_PASSWORD_POLICY["min_length"])
    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters.")
    if policy.get("require_uppercase") and not any(character.isupper() for character in password):
        errors.append("Password must include an uppercase letter.")
    if policy.get("require_lowercase") and not any(character.islower() for character in password):
        errors.append("Password must include a lowercase letter.")
    if policy.get("require_number") and not any(character.isdigit() for character in password):
        errors.append("Password must include a number.")
    if policy.get("require_special") and not any(not character.isalnum() for character in password):
        errors.append("Password must include a special character.")
    normalized_username = username.strip().lower()
    if policy.get("disallow_username") and normalized_username and normalized_username in password.lower():
        errors.append("Password must not contain the username.")
    return errors


def validate_local_usernames(users: list[User]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for user in users:
        username = user.username.strip().lower()
        if username in seen:
            errors.append(f"Duplicate local user {username}.")
        seen.add(username)
        if not USERNAME_PATTERN.fullmatch(username):
            errors.append(f"Local user {username or user.id} is not a valid Photon OS username.")
        if username in RESERVED_USERNAMES:
            errors.append(f"Local user {username} is reserved by Photon OS or LabFoundry.")
    return errors


def os_sync_status_label(user: User) -> str:
    if user.pending_os_password_encrypted:
        return "password staged"
    if user.os_password_applied_at:
        return "synced"
    if user.os_sync_status == "failed":
        return "sync failed"
    return "password not staged; reset to sync"


def local_user_sync_rows(users: list[User]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for user in users:
        rows.append(
            {
                "username": user.username,
                "role": user.role,
                "enabled": bool(user.enabled),
                "os_state": "enabled" if user.enabled else "locked",
                "password_pending": bool(user.pending_os_password_encrypted),
                "password_pending_since": user.os_password_pending_at.isoformat() if user.os_password_pending_at else "",
                "password_synced_at": user.os_password_applied_at.isoformat() if user.os_password_applied_at else "",
                "last_applied_at": user.os_sync_applied_at.isoformat() if user.os_sync_applied_at else "",
                "sync_status": os_sync_status_label(user),
            }
        )
    return rows


def render_local_users_preview(users: list[User]) -> str:
    rows = local_user_sync_rows(users)
    payload = {
        "managed_by": "LabFoundry",
        "version": LOCAL_USERS_CONFIG_VERSION,
        "scope": "Photon OS local users",
        "passwords_pending": sum(1 for row in rows if row["password_pending"]),
        "users": rows,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_local_users_apply_config(users: list[User], settings: Settings | None = None) -> str:
    payload: dict[str, Any] = {
        "managed_by": "LabFoundry",
        "version": LOCAL_USERS_CONFIG_VERSION,
        "scope": "Photon OS local users",
        "users": [],
    }
    for user in users:
        row: dict[str, Any] = {
            "username": user.username,
            "role": user.role,
            "enabled": bool(user.enabled),
            "home": f"/var/lib/labfoundry/users/{user.username}",
            "shell": "/sbin/nologin",
            "password_pending": bool(user.pending_os_password_encrypted),
            "password_pending_since": user.os_password_pending_at.isoformat() if user.os_password_pending_at else "",
        }
        if user.pending_os_password_encrypted:
            row["password"] = decrypt_os_password(user.pending_os_password_encrypted, settings)
        payload["users"].append(row)
    return json.dumps(payload, indent=2, sort_keys=True)


def stage_user_os_password(user: User, password: str, settings: Settings | None = None) -> None:
    user.pending_os_password_encrypted = encrypt_os_password(password, settings)
    user.os_password_pending_at = utcnow()
    user.os_sync_status = "pending"
    user.os_sync_error = None


def mark_local_users_applied(users: list[User], *, applied_at: datetime | None = None) -> None:
    timestamp = applied_at or utcnow()
    for user in users:
        password_was_pending = bool(user.pending_os_password_encrypted)
        user.pending_os_password_encrypted = None
        user.os_password_pending_at = None
        if password_was_pending:
            user.os_password_applied_at = timestamp
        user.os_sync_applied_at = timestamp
        user.os_sync_status = "applied"
        user.os_sync_error = None


def mark_local_users_failed(users: list[User], error: str) -> None:
    for user in users:
        user.os_sync_status = "failed"
        user.os_sync_error = error[:1000]

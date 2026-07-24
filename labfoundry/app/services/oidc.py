from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import json
from secrets import token_urlsafe
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from joserfc.jwk import RSAKey
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from labfoundry.app.models import (
    ApplianceSettings,
    LdapOrganization,
    LdapUser,
    OidcClient,
    OidcClientRedirectUri,
    OidcProviderSettings,
    OidcSigningKey,
    OidcSubject,
    User,
    utcnow,
)
from labfoundry.app.secrets import encrypt_secret
from labfoundry.app.services.appliance_settings import normalize_fqdn


OIDC_ISSUER_PATH = "/identity"
OIDC_SCOPES = ("openid", "profile", "email", "groups")
OIDC_SIGNING_ALGORITHM = "RS256"
OIDC_TOKEN_ENDPOINT_AUTH_METHOD = "client_secret_basic"
OIDC_AUTHORIZATION_FLOW_AVAILABLE = False
OIDC_CLIENT_SECRET_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


class OidcConfigurationError(ValueError):
    pass


class OidcConflictError(RuntimeError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _appliance_settings(db: Session) -> ApplianceSettings:
    row = db.execute(select(ApplianceSettings)).scalar_one_or_none()
    if row is None:
        row = ApplianceSettings()
        db.add(row)
        db.flush()
    return row


def expected_issuer_url(appliance: ApplianceSettings) -> str:
    fqdn = normalize_fqdn(appliance.fqdn)
    if not fqdn:
        return ""
    return f"https://{fqdn}{OIDC_ISSUER_PATH}"


def normalize_issuer_url(value: str) -> str:
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise OidcConfigurationError("Issuer URL is not valid.") from exc
    if parsed.scheme.lower() != "https":
        raise OidcConfigurationError("Issuer URL must use HTTPS.")
    if parsed.username or parsed.password:
        raise OidcConfigurationError("Issuer URL must not contain user information.")
    if not parsed.hostname:
        raise OidcConfigurationError("Issuer URL must contain a DNS hostname.")
    try:
        ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise OidcConfigurationError("Issuer URL must use a configured FQDN, not an IP address.")
    hostname = normalize_fqdn(parsed.hostname)
    if not hostname or "." not in hostname:
        raise OidcConfigurationError("Issuer URL must contain a fully qualified DNS name.")
    if port is not None:
        raise OidcConfigurationError("Issuer URL must not contain an explicit port.")
    if parsed.path != OIDC_ISSUER_PATH:
        raise OidcConfigurationError(f"Issuer URL path must be exactly {OIDC_ISSUER_PATH}.")
    if parsed.query or parsed.fragment:
        raise OidcConfigurationError("Issuer URL must not contain a query string or fragment.")
    return f"https://{hostname}{OIDC_ISSUER_PATH}"


def ensure_provider_settings(db: Session) -> OidcProviderSettings:
    row = db.execute(select(OidcProviderSettings)).scalar_one_or_none()
    if row is None:
        row = OidcProviderSettings(issuer_url=expected_issuer_url(_appliance_settings(db)))
        db.add(row)
        db.flush()
    return row


def active_signing_key(db: Session) -> OidcSigningKey | None:
    return db.execute(
        select(OidcSigningKey).where(
            OidcSigningKey.status == "active",
            OidcSigningKey.active_slot == 1,
        )
    ).scalar_one_or_none()


def provider_validation_errors(
    db: Session,
    provider: OidcProviderSettings | None = None,
    *,
    require_active_key: bool = True,
) -> list[str]:
    provider = provider or ensure_provider_settings(db)
    appliance = _appliance_settings(db)
    errors: list[str] = []
    try:
        normalized = normalize_issuer_url(provider.issuer_url)
    except OidcConfigurationError as exc:
        errors.append(str(exc))
        normalized = ""
    expected = expected_issuer_url(appliance)
    if normalized and normalized != expected:
        errors.append(
            "Issuer URL must exactly match the configured Appliance Settings FQDN and /identity path."
        )
    if not appliance.management_https_enabled:
        errors.append("Management HTTPS must be enabled before the OIDC provider can be enabled.")
    if require_active_key and active_signing_key(db) is None:
        errors.append("Generate an active OIDC signing key before enabling the provider.")
    return errors


def validate_enabled_provider_at_startup(db: Session) -> None:
    provider = db.execute(select(OidcProviderSettings)).scalar_one_or_none()
    if provider is None or not provider.enabled:
        return
    errors = provider_validation_errors(db, provider)
    if not OIDC_AUTHORIZATION_FLOW_AVAILABLE:
        errors.append(
            "This build contains only the OIDC protocol skeleton; the authorization flow is not available."
        )
    if errors:
        raise RuntimeError("OIDC provider startup validation failed: " + " ".join(errors))


def issuer_endpoint_urls(issuer_url: str) -> dict[str, str]:
    issuer = normalize_issuer_url(issuer_url)
    return {
        "issuer": issuer,
        "discovery_url": f"{issuer}/.well-known/openid-configuration",
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "userinfo_endpoint": f"{issuer}/userinfo",
        "jwks_uri": f"{issuer}/jwks",
        "end_session_endpoint": f"{issuer}/logout",
    }


def discovery_document(db: Session) -> dict[str, object]:
    if not OIDC_AUTHORIZATION_FLOW_AVAILABLE:
        raise OidcConfigurationError("OIDC provider is disabled.")
    provider = ensure_provider_settings(db)
    if not provider.enabled:
        raise OidcConfigurationError("OIDC provider is disabled.")
    errors = provider_validation_errors(db, provider)
    if errors:
        raise OidcConfigurationError(" ".join(errors))
    urls = issuer_endpoint_urls(provider.issuer_url)
    return {
        "issuer": urls["issuer"],
        "authorization_endpoint": urls["authorization_endpoint"],
        "token_endpoint": urls["token_endpoint"],
        "userinfo_endpoint": urls["userinfo_endpoint"],
        "jwks_uri": urls["jwks_uri"],
        "end_session_endpoint": urls["end_session_endpoint"],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": [OIDC_SIGNING_ALGORITHM],
        "token_endpoint_auth_methods_supported": [OIDC_TOKEN_ENDPOINT_AUTH_METHOD],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": list(OIDC_SCOPES),
        "claims_supported": [
            "iss",
            "sub",
            "aud",
            "exp",
            "iat",
            "auth_time",
            "nonce",
            "preferred_username",
            "name",
            "email",
            "email_verified",
            "organization",
            "groups",
        ],
        "claims_parameter_supported": False,
        "request_parameter_supported": False,
        "request_uri_parameter_supported": False,
    }


def jwks_document(db: Session, *, now: datetime | None = None) -> dict[str, list[dict[str, object]]]:
    if not OIDC_AUTHORIZATION_FLOW_AVAILABLE:
        raise OidcConfigurationError("OIDC provider is disabled.")
    provider = ensure_provider_settings(db)
    if not provider.enabled:
        raise OidcConfigurationError("OIDC provider is disabled.")
    errors = provider_validation_errors(db, provider)
    if errors:
        raise OidcConfigurationError(" ".join(errors))
    current = now or utcnow()
    rows = db.execute(select(OidcSigningKey).order_by(OidcSigningKey.created_at)).scalars().all()
    keys: list[dict[str, object]] = []
    for row in rows:
        publish = row.status == "active"
        if row.status == "retired" and row.publish_until is not None:
            publish = _aware(row.publish_until) > current
        if not publish:
            continue
        public_jwk = json.loads(row.public_jwk_json)
        keys.append(public_jwk)
    return {"keys": keys}


def generate_signing_key(
    db: Session,
    *,
    rotate: bool,
    now: datetime | None = None,
) -> tuple[OidcSigningKey, OidcSigningKey | None]:
    current_time = now or utcnow()
    provider = ensure_provider_settings(db)
    previous = active_signing_key(db)
    if previous is not None and not rotate:
        raise OidcConflictError("An active OIDC signing key already exists; use the rotation action.")
    if previous is not None:
        longest_client_lifetime = max(
            (
                max(client.access_token_lifetime_seconds, client.id_token_lifetime_seconds)
                for client in db.execute(select(OidcClient)).scalars().all()
            ),
            default=0,
        )
        minimum_overlap = max(
            provider.access_token_lifetime_seconds,
            provider.id_token_lifetime_seconds,
            longest_client_lifetime,
        ) + provider.clock_skew_seconds
        previous.status = "retired"
        previous.active_slot = None
        previous.retired_at = current_time
        previous.publish_until = current_time + timedelta(
            seconds=max(provider.signing_key_overlap_seconds, minimum_overlap)
        )
        db.add(previous)
        db.flush()

    generated = RSAKey.generate_key(
        key_size=3072,
        parameters={"alg": OIDC_SIGNING_ALGORITHM, "use": "sig"},
        private=True,
        auto_kid=True,
    )
    if not generated.kid:
        raise RuntimeError("OIDC signing key generation did not produce a key identifier.")
    private_pem = generated.as_pem(private=True).decode("ascii")
    public_jwk = generated.as_dict(private=False)
    row = OidcSigningKey(
        kid=generated.kid,
        algorithm=OIDC_SIGNING_ALGORITHM,
        private_key_encrypted=encrypt_secret(private_pem),
        public_jwk_json=json.dumps(public_jwk, sort_keys=True, separators=(",", ":")),
        status="active",
        active_slot=1,
        created_at=current_time,
        activated_at=current_time,
    )
    db.add(row)
    db.flush()
    return row, previous


def signing_key_to_dict(row: OidcSigningKey) -> dict[str, object]:
    public_jwk = json.loads(row.public_jwk_json)
    return {
        "id": row.id,
        "kid": row.kid,
        "algorithm": row.algorithm,
        "status": row.status,
        "key_type": public_jwk.get("kty"),
        "created_at": row.created_at,
        "activated_at": row.activated_at,
        "retired_at": row.retired_at,
        "publish_until": row.publish_until,
    }


def generate_client_id() -> str:
    return f"lf_oidc_{token_urlsafe(24)}"


def generate_client_secret() -> str:
    return token_urlsafe(48)


def hash_client_secret(raw_secret: str) -> str:
    return OIDC_CLIENT_SECRET_HASHER.hash(raw_secret)


def verify_client_secret(secret_hash: str, raw_secret: str) -> bool:
    try:
        return OIDC_CLIENT_SECRET_HASHER.verify(secret_hash, raw_secret)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def normalize_allowed_scopes(scopes: list[str]) -> list[str]:
    normalized: list[str] = []
    for scope in scopes:
        value = scope.strip()
        if value and value not in normalized:
            normalized.append(value)
    unknown = set(normalized) - set(OIDC_SCOPES)
    if unknown:
        raise OidcConfigurationError(f"Unsupported OIDC scopes: {', '.join(sorted(unknown))}.")
    if "openid" not in normalized:
        raise OidcConfigurationError("OIDC clients must allow the openid scope.")
    return [scope for scope in OIDC_SCOPES if scope in normalized]


def validate_redirect_uri(uri: str, *, allow_loopback: bool) -> str:
    if not uri or uri != uri.strip():
        raise OidcConfigurationError("Redirect URIs must not be blank or contain surrounding whitespace.")
    if "*" in uri:
        raise OidcConfigurationError("Wildcard redirect URIs are not supported.")
    if "\\" in uri or any(ord(character) < 0x20 for character in uri):
        raise OidcConfigurationError("Redirect URI contains an invalid character.")
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise OidcConfigurationError("Redirect URI is not valid.") from exc
    if parsed.username or parsed.password:
        raise OidcConfigurationError("Redirect URIs must not contain user information.")
    if not parsed.hostname or not parsed.netloc:
        raise OidcConfigurationError("Redirect URIs must be absolute.")
    if parsed.fragment:
        raise OidcConfigurationError("Redirect URIs must not contain fragments.")
    if parsed.scheme.lower() == "https":
        return uri
    if parsed.scheme.lower() != "http" or not allow_loopback:
        raise OidcConfigurationError("Redirect URIs must use HTTPS.")
    try:
        loopback = ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
    if not loopback or port is None:
        raise OidcConfigurationError(
            "HTTP redirect URIs are allowed only for explicit loopback development clients with a port."
        )
    return uri


def validate_redirect_uri_list(
    values: list[str],
    *,
    allow_loopback: bool,
    required: bool,
) -> list[str]:
    if required and not values:
        raise OidcConfigurationError("At least one exact redirect URI is required.")
    normalized = [validate_redirect_uri(value, allow_loopback=allow_loopback) for value in values]
    if len(set(normalized)) != len(normalized):
        raise OidcConfigurationError("Duplicate redirect URIs are not allowed.")
    return normalized


def get_client(db: Session, client_id: int) -> OidcClient:
    row = db.execute(
        select(OidcClient)
        .where(OidcClient.id == client_id)
        .options(selectinload(OidcClient.redirect_uris), selectinload(OidcClient.organization))
    ).scalar_one_or_none()
    if row is None:
        raise OidcConfigurationError("OIDC client not found.")
    return row


def create_client(
    db: Session,
    *,
    name: str,
    organization_id: int | None,
    redirect_uris: list[str],
    post_logout_redirect_uris: list[str],
    allowed_scopes: list[str],
    allow_loopback_redirects: bool,
    access_token_lifetime_seconds: int,
    id_token_lifetime_seconds: int,
    authorization_code_lifetime_seconds: int,
    enabled: bool,
) -> tuple[OidcClient, str]:
    normalized_name = name.strip()
    if not normalized_name:
        raise OidcConfigurationError("OIDC client name is required.")
    organization = None
    if organization_id is not None:
        organization = db.get(LdapOrganization, organization_id)
        if organization is None or not organization.enabled:
            raise OidcConfigurationError("Bound OIDC client organization must exist and be enabled.")
    normalized_redirects = validate_redirect_uri_list(
        redirect_uris,
        allow_loopback=allow_loopback_redirects,
        required=True,
    )
    normalized_logout_redirects = validate_redirect_uri_list(
        post_logout_redirect_uris,
        allow_loopback=allow_loopback_redirects,
        required=False,
    )
    scopes = normalize_allowed_scopes(allowed_scopes)
    raw_secret = generate_client_secret()
    row = OidcClient(
        name=normalized_name,
        client_id=generate_client_id(),
        client_secret_hash=hash_client_secret(raw_secret),
        organization_id=organization.id if organization else None,
        allowed_scopes=" ".join(scopes),
        token_endpoint_auth_method=OIDC_TOKEN_ENDPOINT_AUTH_METHOD,
        access_token_lifetime_seconds=access_token_lifetime_seconds,
        id_token_lifetime_seconds=id_token_lifetime_seconds,
        authorization_code_lifetime_seconds=authorization_code_lifetime_seconds,
        allow_loopback_redirects=allow_loopback_redirects,
        enabled=enabled,
        updated_at=utcnow(),
    )
    db.add(row)
    db.flush()
    for uri in normalized_redirects:
        db.add(OidcClientRedirectUri(oidc_client_id=row.id, kind="redirect", uri=uri))
    for uri in normalized_logout_redirects:
        db.add(OidcClientRedirectUri(oidc_client_id=row.id, kind="post_logout", uri=uri))
    db.flush()
    return get_client(db, row.id), raw_secret


def rotate_client_secret(db: Session, row: OidcClient) -> str:
    raw_secret = generate_client_secret()
    row.client_secret_hash = hash_client_secret(raw_secret)
    row.updated_at = utcnow()
    db.add(row)
    db.flush()
    return raw_secret


def oidc_client_to_dict(row: OidcClient) -> dict[str, object]:
    redirects = [item.uri for item in row.redirect_uris if item.kind == "redirect"]
    logout_redirects = [item.uri for item in row.redirect_uris if item.kind == "post_logout"]
    return {
        "id": row.id,
        "name": row.name,
        "client_id": row.client_id,
        "organization_id": row.organization_id,
        "organization_slug": row.organization.slug if row.organization else None,
        "redirect_uris": redirects,
        "post_logout_redirect_uris": logout_redirects,
        "allowed_scopes": row.allowed_scopes.split(),
        "token_endpoint_auth_method": row.token_endpoint_auth_method,
        "access_token_lifetime_seconds": row.access_token_lifetime_seconds,
        "id_token_lifetime_seconds": row.id_token_lifetime_seconds,
        "authorization_code_lifetime_seconds": row.authorization_code_lifetime_seconds,
        "allow_loopback_redirects": row.allow_loopback_redirects,
        "enabled": row.enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_clients(db: Session) -> list[OidcClient]:
    return list(
        db.execute(
            select(OidcClient)
            .options(selectinload(OidcClient.redirect_uris), selectinload(OidcClient.organization))
            .order_by(OidcClient.name, OidcClient.id)
        ).scalars()
    )


def list_subjects(db: Session) -> list[dict[str, object]]:
    local_users = {row.id: row for row in db.execute(select(User)).scalars().all()}
    ldap_users = {
        row.id: row
        for row in db.execute(
            select(LdapUser).options(selectinload(LdapUser.organization))
        ).scalars().all()
    }
    output: list[dict[str, object]] = []
    for row in db.execute(select(OidcSubject).order_by(OidcSubject.created_at, OidcSubject.id)).scalars():
        if row.local_user_id is not None and row.local_user_id in local_users:
            user = local_users[row.local_user_id]
            output.append(
                {
                    "subject": row.subject_uuid,
                    "source": "local",
                    "username": user.username,
                    "organization_id": None,
                    "organization_name": "Local",
                    "created_at": row.created_at,
                }
            )
        elif row.ldap_user_id is not None and row.ldap_user_id in ldap_users:
            user = ldap_users[row.ldap_user_id]
            output.append(
                {
                    "subject": row.subject_uuid,
                    "source": "managed_ldap",
                    "username": user.uid,
                    "organization_id": user.organization_id,
                    "organization_name": user.organization.name,
                    "created_at": row.created_at,
                }
            )
    return output

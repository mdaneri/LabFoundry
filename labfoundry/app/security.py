from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from labfoundry.app.config import Settings, get_settings
from labfoundry.app.database import get_db
from labfoundry.app.models import ApiToken, Role, User, utcnow


ALL_SCOPES = {
    "read:dashboard",
    "read:interfaces",
    "write:interfaces",
    "read:vlans",
    "write:vlans",
    "read:routes",
    "write:routes",
    "read:wan",
    "write:wan",
    "read:firewall",
    "write:firewall",
    "read:dns",
    "write:dns",
    "read:dhcp",
    "write:dhcp",
    "read:ca",
    "write:ca",
    "read:kms",
    "write:kms",
    "read:repository",
    "write:repository",
    "read:esxi-pxe",
    "write:esxi-pxe",
    "read:vcf-registry",
    "write:vcf-registry",
    "read:vcf-backups",
    "write:vcf-backups",
    "read:services",
    "write:services",
    "read:logs",
    "read:audit",
    "write:backup",
    "admin:all",
}

ROLE_SCOPES = {
    Role.ADMIN.value: ALL_SCOPES,
    Role.NETWORK_ADMIN.value: {
        "read:dashboard",
        "read:interfaces",
        "write:interfaces",
        "read:vlans",
        "write:vlans",
        "read:routes",
        "write:routes",
        "read:wan",
        "write:wan",
        "read:firewall",
        "write:firewall",
        "read:logs",
        "read:audit",
    },
    Role.SERVICE_ADMIN.value: {
        "read:dashboard",
        "read:dns",
        "write:dns",
        "read:dhcp",
        "write:dhcp",
        "read:ca",
        "write:ca",
        "read:kms",
        "write:kms",
        "read:repository",
        "write:repository",
        "read:esxi-pxe",
        "write:esxi-pxe",
        "read:vcf-registry",
        "write:vcf-registry",
        "read:vcf-backups",
        "write:vcf-backups",
        "read:services",
        "write:services",
        "read:logs",
        "read:audit",
    },
    Role.VIEWER.value: {
        "read:dashboard",
        "read:interfaces",
        "read:vlans",
        "read:routes",
        "read:wan",
        "read:firewall",
        "read:dns",
        "read:dhcp",
        "read:ca",
        "read:kms",
        "read:repository",
        "read:esxi-pxe",
        "read:vcf-registry",
        "read:vcf-backups",
        "read:services",
        "read:logs",
        "read:audit",
    },
}

bearer_scheme = HTTPBearer(auto_error=False)


class Identity:
    def __init__(
        self,
        username: str,
        role: str,
        scopes: set[str],
        user_id: int | None = None,
        token_id: int | None = None,
        token_jti: str | None = None,
        auth_type: str = "session",
    ) -> None:
        self.username = username
        self.role = role
        self.scopes = scopes
        self.user_id = user_id
        self.token_id = token_id
        self.token_jti = token_jti
        self.auth_type = auth_type

    def can(self, scope: str) -> bool:
        return "admin:all" in self.scopes or scope in self.scopes


def hash_token(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def create_jwt(
    *,
    subject: str,
    role: str,
    scopes: list[str],
    jti: str,
    expires_at: datetime,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    now = utcnow()
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": subject,
        "role": role,
        "scope": " ".join(scopes),
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_raw_api_token() -> str:
    return f"lf_{token_urlsafe(36)}"


def role_allows_scopes(role: str, requested_scopes: set[str]) -> bool:
    allowed = ROLE_SCOPES.get(role, set())
    return "admin:all" in allowed or requested_scopes.issubset(allowed)


def scopes_from_string(scopes: str) -> set[str]:
    return {scope for scope in scopes.split() if scope}


def require_scope(scope: str):
    def dependency(identity: Annotated[Identity, Depends(get_current_api_identity)]) -> Identity:
        if not identity.can(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {scope}",
            )
        return identity

    return dependency


def get_session_identity(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Identity | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or not user.enabled:
        request.session.clear()
        return None
    return Identity(
        username=user.username,
        role=user.role,
        scopes=set(ROLE_SCOPES.get(user.role, set())),
        user_id=user.id,
        auth_type="session",
    )


def require_session_identity(identity: Annotated[Identity | None, Depends(get_session_identity)]) -> Identity:
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return identity


def get_current_api_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Identity:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.secret_key,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from exc

    jti = payload.get("jti")
    username = payload.get("sub")
    role = payload.get("role")
    token = db.execute(select(ApiToken).where(ApiToken.jti == jti)).scalar_one_or_none()
    if not token or not token.enabled or token.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is revoked or unknown")
    if ensure_aware(token.expires_at) <= utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is expired")
    if token.owner_username != username or token.role != role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token identity mismatch")

    token.last_used_at = utcnow()
    db.add(token)
    db.commit()
    return Identity(
        username=username,
        role=role,
        scopes=scopes_from_string(token.scopes),
        user_id=token.owner_user_id,
        token_id=token.id,
        token_jti=token.jti,
        auth_type="bearer",
    )


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    settings = get_settings()
    if user and user.enabled and user.username == settings.bootstrap_admin_username and password == settings.bootstrap_admin_password:
        return user
    return None


def default_expiration(settings: Settings | None = None) -> datetime:
    settings = settings or get_settings()
    return utcnow() + timedelta(days=settings.api_token_ttl_days)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

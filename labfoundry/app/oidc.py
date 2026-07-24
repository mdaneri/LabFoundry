from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from labfoundry.app.audit import record_audit
from labfoundry.app.database import get_db
from labfoundry.app.models import OidcSigningKey, utcnow
from labfoundry.app.schemas import (
    OidcClientCreate,
    OidcClientCreated,
    OidcClientEnabledUpdate,
    OidcClientResponse,
    OidcClientSecretRotated,
    OidcProviderSettingsResponse,
    OidcProviderSettingsUpdate,
    OidcSigningKeyResponse,
    OidcSubjectResponse,
)
from labfoundry.app.security import Identity, require_scope
from labfoundry.app.services.oidc import (
    OIDC_AUTHORIZATION_FLOW_AVAILABLE,
    OidcConfigurationError,
    OidcConflictError,
    create_client,
    discovery_document,
    ensure_provider_settings,
    generate_signing_key,
    get_client,
    issuer_endpoint_urls,
    jwks_document,
    list_clients,
    list_subjects,
    normalize_issuer_url,
    oidc_client_to_dict,
    provider_validation_errors,
    rotate_client_secret,
    signing_key_to_dict,
)


public_router = APIRouter(prefix="/identity", tags=["OpenID Connect"])
admin_router = APIRouter(prefix="/api/v1/oidc", tags=["OIDC Provider"])


def _public_configuration_error(exc: OidcConfigurationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@public_router.get("/.well-known/openid-configuration", response_model=None)
def get_openid_configuration(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        document = discovery_document(db)
    except OidcConfigurationError as exc:
        raise _public_configuration_error(exc) from exc
    return JSONResponse(document, headers={"Cache-Control": "public, max-age=300"})


@public_router.get("/jwks", response_model=None)
def get_oidc_jwks(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        document = jwks_document(db)
    except OidcConfigurationError as exc:
        raise _public_configuration_error(exc) from exc
    return JSONResponse(document, headers={"Cache-Control": "public, max-age=300"})


def _provider_response(db: Session) -> OidcProviderSettingsResponse:
    provider = ensure_provider_settings(db)
    errors = provider_validation_errors(db, provider)
    try:
        urls = issuer_endpoint_urls(provider.issuer_url)
    except OidcConfigurationError:
        urls = {
            "discovery_url": "",
            "authorization_endpoint": "",
            "token_endpoint": "",
            "userinfo_endpoint": "",
            "jwks_uri": "",
            "end_session_endpoint": "",
        }
    return OidcProviderSettingsResponse(
        enabled=provider.enabled,
        issuer_url=provider.issuer_url,
        access_token_lifetime_seconds=provider.access_token_lifetime_seconds,
        id_token_lifetime_seconds=provider.id_token_lifetime_seconds,
        authorization_code_lifetime_seconds=provider.authorization_code_lifetime_seconds,
        clock_skew_seconds=provider.clock_skew_seconds,
        signing_key_overlap_seconds=provider.signing_key_overlap_seconds,
        authorization_flow_available=OIDC_AUTHORIZATION_FLOW_AVAILABLE,
        valid=not errors,
        validation_errors=errors,
        discovery_url=urls["discovery_url"],
        authorization_endpoint=urls["authorization_endpoint"],
        token_endpoint=urls["token_endpoint"],
        userinfo_endpoint=urls["userinfo_endpoint"],
        jwks_uri=urls["jwks_uri"],
        end_session_endpoint=urls["end_session_endpoint"],
    )


@admin_router.get("/provider", response_model=OidcProviderSettingsResponse)
def get_oidc_provider_settings(
    _identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> OidcProviderSettingsResponse:
    return _provider_response(db)


@admin_router.put("/provider", response_model=OidcProviderSettingsResponse)
def update_oidc_provider_settings(
    payload: OidcProviderSettingsUpdate,
    request: Request,
    identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> OidcProviderSettingsResponse:
    if payload.enabled and not OIDC_AUTHORIZATION_FLOW_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This build contains the OIDC protocol skeleton only. "
                "The provider cannot be enabled until the Authorization Code flow is available."
            ),
        )
    provider = ensure_provider_settings(db)
    previous_issuer = provider.issuer_url
    try:
        provider.issuer_url = normalize_issuer_url(payload.issuer_url)
    except OidcConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    provider.enabled = payload.enabled
    provider.access_token_lifetime_seconds = payload.access_token_lifetime_seconds
    provider.id_token_lifetime_seconds = payload.id_token_lifetime_seconds
    provider.authorization_code_lifetime_seconds = payload.authorization_code_lifetime_seconds
    provider.clock_skew_seconds = payload.clock_skew_seconds
    provider.signing_key_overlap_seconds = payload.signing_key_overlap_seconds
    provider.updated_at = utcnow()
    db.add(provider)
    db.flush()
    if previous_issuer != provider.issuer_url:
        record_audit(
            db,
            actor=identity.username,
            action="change_oidc_issuer",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            detail=f"issuer={provider.issuer_url}",
            request_id=getattr(request.state, "request_id", None),
        )
    else:
        record_audit(
            db,
            actor=identity.username,
            action="update_oidc_provider",
            resource_type="oidc_provider",
            resource_id=str(provider.id),
            request_id=getattr(request.state, "request_id", None),
        )
    return _provider_response(db)


@admin_router.get("/signing-keys", response_model=list[OidcSigningKeyResponse])
def get_oidc_signing_keys(
    _identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> list[OidcSigningKeyResponse]:
    rows = db.execute(select(OidcSigningKey).order_by(OidcSigningKey.created_at.desc())).scalars().all()
    return [OidcSigningKeyResponse(**signing_key_to_dict(row)) for row in rows]


@admin_router.post(
    "/signing-keys",
    response_model=OidcSigningKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_oidc_signing_key(
    request: Request,
    identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> OidcSigningKeyResponse:
    try:
        row, _previous = generate_signing_key(db, rotate=False)
    except OidcConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    record_audit(
        db,
        actor=identity.username,
        action="generate_oidc_signing_key",
        resource_type="oidc_signing_key",
        resource_id=str(row.id),
        detail=f"kid={row.kid}; algorithm={row.algorithm}",
        request_id=getattr(request.state, "request_id", None),
    )
    return OidcSigningKeyResponse(**signing_key_to_dict(row))


@admin_router.post("/signing-keys/rotate", response_model=OidcSigningKeyResponse)
def rotate_oidc_signing_key(
    request: Request,
    identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> OidcSigningKeyResponse:
    row, previous = generate_signing_key(db, rotate=True)
    action = "rotate_oidc_signing_key" if previous is not None else "generate_oidc_signing_key"
    detail = f"kid={row.kid}; algorithm={row.algorithm}"
    if previous is not None:
        detail += f"; retired_kid={previous.kid}; publish_until={previous.publish_until.isoformat()}"
    record_audit(
        db,
        actor=identity.username,
        action=action,
        resource_type="oidc_signing_key",
        resource_id=str(row.id),
        detail=detail,
        request_id=getattr(request.state, "request_id", None),
    )
    return OidcSigningKeyResponse(**signing_key_to_dict(row))


@admin_router.get("/clients", response_model=list[OidcClientResponse])
def get_oidc_clients(
    _identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> list[OidcClientResponse]:
    return [OidcClientResponse(**oidc_client_to_dict(row)) for row in list_clients(db)]


@admin_router.get("/subjects", response_model=list[OidcSubjectResponse])
def get_oidc_subjects(
    _identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> list[OidcSubjectResponse]:
    return [OidcSubjectResponse(**row) for row in list_subjects(db)]


@admin_router.post(
    "/clients",
    response_model=OidcClientCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_oidc_client(
    payload: OidcClientCreate,
    request: Request,
    identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> OidcClientCreated:
    try:
        row, raw_secret = create_client(
            db,
            name=payload.name,
            organization_id=payload.organization_id,
            redirect_uris=payload.redirect_uris,
            post_logout_redirect_uris=payload.post_logout_redirect_uris,
            allowed_scopes=payload.allowed_scopes,
            allow_loopback_redirects=payload.allow_loopback_redirects,
            access_token_lifetime_seconds=payload.access_token_lifetime_seconds,
            id_token_lifetime_seconds=payload.id_token_lifetime_seconds,
            authorization_code_lifetime_seconds=payload.authorization_code_lifetime_seconds,
            enabled=payload.enabled,
        )
    except OidcConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    record_audit(
        db,
        actor=identity.username,
        action="create_oidc_client",
        resource_type="oidc_client",
        resource_id=str(row.id),
        detail=f"client_id={row.client_id}; organization_id={row.organization_id or 'unbound'}",
        request_id=getattr(request.state, "request_id", None),
    )
    return OidcClientCreated(
        client=OidcClientResponse(**oidc_client_to_dict(row)),
        client_secret=raw_secret,
    )


@admin_router.post(
    "/clients/{client_record_id}/secret/rotate",
    response_model=OidcClientSecretRotated,
)
def rotate_oidc_client_secret(
    client_record_id: int,
    request: Request,
    identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> OidcClientSecretRotated:
    try:
        row = get_client(db, client_record_id)
    except OidcConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    raw_secret = rotate_client_secret(db, row)
    record_audit(
        db,
        actor=identity.username,
        action="rotate_oidc_client_secret",
        resource_type="oidc_client",
        resource_id=str(row.id),
        detail=f"client_id={row.client_id}",
        request_id=getattr(request.state, "request_id", None),
    )
    return OidcClientSecretRotated(client_id=row.client_id, client_secret=raw_secret)


@admin_router.patch("/clients/{client_record_id}/enabled", response_model=OidcClientResponse)
def set_oidc_client_enabled(
    client_record_id: int,
    payload: OidcClientEnabledUpdate,
    request: Request,
    identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> OidcClientResponse:
    try:
        row = get_client(db, client_record_id)
    except OidcConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    row.enabled = payload.enabled
    row.updated_at = utcnow()
    db.add(row)
    db.flush()
    record_audit(
        db,
        actor=identity.username,
        action="enable_oidc_client" if payload.enabled else "disable_oidc_client",
        resource_type="oidc_client",
        resource_id=str(row.id),
        detail=f"client_id={row.client_id}",
        request_id=getattr(request.state, "request_id", None),
    )
    return OidcClientResponse(**oidc_client_to_dict(row))


@admin_router.delete("/clients/{client_record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_oidc_client(
    client_record_id: int,
    request: Request,
    identity: Identity = Depends(require_scope("admin:all")),
    db: Session = Depends(get_db),
) -> Response:
    try:
        row = get_client(db, client_record_id)
    except OidcConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    public_client_id = row.client_id
    db.delete(row)
    db.flush()
    record_audit(
        db,
        actor=identity.username,
        action="delete_oidc_client",
        resource_type="oidc_client",
        resource_id=str(client_record_id),
        detail=f"client_id={public_client_id}",
        request_id=getattr(request.state, "request_id", None),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

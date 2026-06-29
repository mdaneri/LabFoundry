from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from labfoundry.app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_url() -> str:
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite:///"):
        db_path = Path(url.removeprefix("sqlite:///"))
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
    return url


engine = create_engine(
    _engine_url(),
    connect_args={"check_same_thread": False} if _engine_url().startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from labfoundry.app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_user_sync_columns()
    _ensure_sqlite_appliance_settings_columns()
    _ensure_sqlite_ca_columns()
    _ensure_sqlite_vcf_depot_columns()
    _ensure_sqlite_esxi_pxe_columns()


def _ensure_sqlite_user_sync_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("users")}
    columns = {
        "shell": "VARCHAR(80) DEFAULT '/sbin/nologin'",
        "os_password_applied_at": "DATETIME",
        "os_sync_applied_at": "DATETIME",
        "os_sync_status": "VARCHAR(80) DEFAULT 'password_not_staged'",
        "os_sync_error": "TEXT",
        "os_unlock_requested_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))


def _ensure_sqlite_appliance_settings_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "appliance_settings" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("appliance_settings")}
    columns = {
        "management_https_enabled": "BOOLEAN DEFAULT 0",
        "root_ssh_enabled": "BOOLEAN DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE appliance_settings ADD COLUMN {name} {definition}"))


def _ensure_sqlite_ca_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as connection:
        if "ca_settings" in table_names:
            existing = {column["name"] for column in inspector.get_columns("ca_settings")}
            columns = {
                "root_certificate_pem": "TEXT DEFAULT ''",
                "root_private_key_encrypted": "TEXT DEFAULT ''",
                "root_serial_number": "VARCHAR(120) DEFAULT ''",
                "root_fingerprint": "VARCHAR(128) DEFAULT ''",
                "root_issued_at": "DATETIME",
                "root_expires_at": "DATETIME",
                "listen_interface": "VARCHAR(80) DEFAULT ''",
                "listen_address": "VARCHAR(240) DEFAULT ''",
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE ca_settings ADD COLUMN {name} {definition}"))
        if "ca_certificates" in table_names:
            existing = {column["name"] for column in inspector.get_columns("ca_certificates")}
            columns = {
                "certificate_pem": "TEXT DEFAULT ''",
                "private_key_encrypted": "TEXT DEFAULT ''",
                "chain_pem": "TEXT DEFAULT ''",
                "issuer_common_name": "VARCHAR(180) DEFAULT ''",
                "fingerprint": "VARCHAR(128) DEFAULT ''",
                "managed_owner": "VARCHAR(120) DEFAULT ''",
                "cert_path": "VARCHAR(300) DEFAULT ''",
                "key_path": "VARCHAR(300) DEFAULT ''",
                "chain_path": "VARCHAR(300) DEFAULT ''",
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE ca_certificates ADD COLUMN {name} {definition}"))


def _ensure_sqlite_vcf_depot_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "vcf_depot_download_profiles" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("vcf_depot_download_profiles")}
    columns = {
        "patches_only": "BOOLEAN DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE vcf_depot_download_profiles ADD COLUMN {name} {definition}"))


def _ensure_sqlite_esxi_pxe_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "esxi_pxe_hosts" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("esxi_pxe_hosts")}
    columns = {
        "installer_iso_path": "VARCHAR(500) DEFAULT ''",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE esxi_pxe_hosts ADD COLUMN {name} {definition}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    _ensure_sqlite_network_columns()
    _ensure_sqlite_dhcp_columns()
    _ensure_sqlite_user_sync_columns()
    _ensure_sqlite_appliance_settings_columns()
    _ensure_sqlite_chrony_settings_columns()
    _ensure_sqlite_dns_security_columns()
    _ensure_sqlite_ca_columns()
    _ensure_sqlite_vcf_depot_columns()
    _ensure_sqlite_vcf_trust_columns()
    _ensure_sqlite_esxi_pxe_columns()


def _ensure_sqlite_network_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    tables = {
        "physical_interfaces": {
            "host_ipv6_cidr": "VARCHAR(64)",
            "ipv6_cidr": "VARCHAR(64)",
            "gateway": "VARCHAR(64)",
            "ipv4_method": "VARCHAR(20) DEFAULT 'static'",
            "ipv6_enabled": "BOOLEAN DEFAULT 0",
            "ipv6_gateway": "VARCHAR(64)",
        },
        "vlan_interfaces": {
            "ipv6_cidr": "VARCHAR(64)",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in tables.items():
            if table_name not in table_names:
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"))
            if table_name == "physical_interfaces" and "ipv6_enabled" not in existing:
                connection.execute(text("UPDATE physical_interfaces SET ipv6_enabled = 1 WHERE ipv6_cidr IS NOT NULL AND TRIM(ipv6_cidr) <> ''"))


def _ensure_sqlite_dhcp_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "dhcp_scopes" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("dhcp_scopes")}
    columns = {
        "address_family": "VARCHAR(10) DEFAULT 'ipv4'",
        "range_expression": "VARCHAR(500) DEFAULT ''",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE dhcp_scopes ADD COLUMN {name} {definition}"))


def _ensure_sqlite_user_sync_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("users")}
    columns = {
        "roles_json": "TEXT DEFAULT ''",
        "auth_provider": "VARCHAR(40) DEFAULT 'local'",
        "external_subject": "VARCHAR(240) DEFAULT ''",
        "external_display_name": "VARCHAR(180) DEFAULT ''",
        "external_email": "VARCHAR(240) DEFAULT ''",
        "role_override_json": "TEXT DEFAULT ''",
        "shell": "VARCHAR(80) DEFAULT '/sbin/nologin'",
        "web_terminal_access": "BOOLEAN DEFAULT 0",
        "os_password_applied_at": "DATETIME",
        "os_sync_applied_at": "DATETIME",
        "os_sync_status": "VARCHAR(80) DEFAULT 'password_not_staged'",
        "os_sync_error": "TEXT",
        "os_unlock_requested_at": "DATETIME",
    }
    added_web_terminal_access = "web_terminal_access" not in existing
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
        connection.execute(text("""UPDATE users SET roles_json = '["' || role || '"]' WHERE COALESCE(roles_json, '') = ''"""))
        if added_web_terminal_access:
            connection.execute(
                text(
                    "UPDATE users SET web_terminal_access = 1 "
                    "WHERE role = 'admin' OR roles_json LIKE '%\"admin\"%'"
                )
            )


def _ensure_sqlite_appliance_settings_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "appliance_settings" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("appliance_settings")}
    columns = {
        "management_https_enabled": "BOOLEAN DEFAULT 0",
        "web_terminal_enabled": "BOOLEAN DEFAULT 0",
        "web_terminal_interfaces_json": "TEXT DEFAULT '[]'",
        "root_ssh_enabled": "BOOLEAN DEFAULT 0",
        "service_dns_target_naming": "VARCHAR(20) DEFAULT 'ip'",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE appliance_settings ADD COLUMN {name} {definition}"))


def _ensure_sqlite_chrony_settings_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "chrony_settings" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("chrony_settings")}
    columns = {
        "hostname": "VARCHAR(180) DEFAULT 'ntp.labfoundry.internal'",
        "listen_interface": "VARCHAR(240) DEFAULT ''",
        "listen_address": "VARCHAR(240) DEFAULT ''",
        "port": "INTEGER DEFAULT 123",
        "upstream_servers": "TEXT DEFAULT 'time.cloudflare.com\nnts.netnod.se'",
        "upstream_sources_json": """TEXT DEFAULT '[{"description":"Cloudflare public NTS","enabled":true,"id":"cloudflare-nts","maxdelay":"","source":"time.cloudflare.com","use_nts":true},{"description":"Netnod public NTS","enabled":true,"id":"netnod-nts","maxdelay":"","source":"nts.netnod.se","use_nts":true}]'""",
        "allow_clients": "TEXT DEFAULT 'any'",
        "nts_server_enabled": "BOOLEAN DEFAULT 0",
        "nts_server_cert_path": "VARCHAR(300) DEFAULT ''",
        "nts_server_key_path": "VARCHAR(300) DEFAULT ''",
        "nts_ke_port": "INTEGER DEFAULT 4460",
        "command_port_disabled": "BOOLEAN DEFAULT 0",
        "minsources": "INTEGER",
        "maxchange_seconds": "INTEGER",
        "authselectmode": "VARCHAR(20) DEFAULT ''",
        "config_path": "VARCHAR(240) DEFAULT '/var/lib/labfoundry/apply/chronyd/labfoundry-chrony.conf'",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE chrony_settings ADD COLUMN {name} {definition}"))


def _ensure_sqlite_dns_security_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as connection:
        if "dns_settings" in table_names:
            existing = {column["name"] for column in inspector.get_columns("dns_settings")}
            columns = {
                "dnssec_enabled": "BOOLEAN DEFAULT 0",
                "rebind_protection_enabled": "BOOLEAN DEFAULT 0",
                "rebind_domain_exemptions": "TEXT DEFAULT ''",
                "query_logging_mode": "VARCHAR(20) DEFAULT 'off'",
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE dns_settings ADD COLUMN {name} {definition}"))
        if "dns_records" in table_names:
            existing = {column["name"] for column in inspector.get_columns("dns_records")}
            if "record_data_json" not in existing:
                connection.execute(text("ALTER TABLE dns_records ADD COLUMN record_data_json TEXT DEFAULT ''"))
            indexes = connection.execute(text("PRAGMA index_list('dns_records')")).fetchall()
            has_old_unique = False
            has_new_unique = False
            for index in indexes:
                index_name = index[1]
                is_unique = bool(index[2])
                if not is_unique:
                    continue
                columns = [row[2] for row in connection.execute(text(f"PRAGMA index_info('{index_name}')")).fetchall()]
                if columns == ["hostname", "record_type"]:
                    has_old_unique = True
                if columns == ["hostname", "record_type", "address"]:
                    has_new_unique = True
            if has_old_unique and not has_new_unique:
                connection.execute(text("ALTER TABLE dns_records RENAME TO dns_records_old_unique"))
                connection.execute(
                    text(
                        """
                        CREATE TABLE dns_records (
                            id INTEGER NOT NULL,
                            hostname VARCHAR(120) NOT NULL,
                            record_type VARCHAR(20) NOT NULL,
                            address VARCHAR(120) NOT NULL,
                            record_data_json TEXT NOT NULL DEFAULT '',
                            description TEXT,
                            enabled BOOLEAN NOT NULL,
                            created_at DATETIME NOT NULL,
                            PRIMARY KEY (id),
                            CONSTRAINT uq_dns_record_hostname_type_address UNIQUE (hostname, record_type, address)
                        )
                        """
                    )
                )
                connection.execute(text("CREATE INDEX ix_dns_records_hostname ON dns_records (hostname)"))
                connection.execute(
                    text(
                        """
                        INSERT INTO dns_records (id, hostname, record_type, address, record_data_json, description, enabled, created_at)
                        SELECT id, hostname, record_type, address, COALESCE(record_data_json, ''), description, enabled, created_at
                        FROM dns_records_old_unique
                        """
                    )
                )
                connection.execute(text("DROP TABLE dns_records_old_unique"))


def _ensure_sqlite_ca_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as connection:
        if "ca_settings" in table_names:
            existing = {column["name"] for column in inspector.get_columns("ca_settings")}
            columns = {
                "portal_hostname": "VARCHAR(180) DEFAULT 'ca.labfoundry.internal'",
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
                "revoked_at": "DATETIME",
                "revoked_by": "VARCHAR(100)",
                "revocation_reason": "VARCHAR(120) DEFAULT ''",
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE ca_certificates ADD COLUMN {name} {definition}"))


def _ensure_sqlite_vcf_depot_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as connection:
        if "vcf_depot_download_profiles" in table_names:
            existing = {column["name"] for column in inspector.get_columns("vcf_depot_download_profiles")}
            columns = {
                "patches_only": "BOOLEAN DEFAULT 0",
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE vcf_depot_download_profiles ADD COLUMN {name} {definition}"))
        if "vcf_offline_depot_settings" in table_names:
            depot_existing = {column["name"] for column in inspector.get_columns("vcf_offline_depot_settings")}
            depot_columns = {
                "http_user_id": "INTEGER",
                "allow_unauthenticated_access": "BOOLEAN DEFAULT 0",
            }
            for name, definition in depot_columns.items():
                if name not in depot_existing:
                    connection.execute(text(f"ALTER TABLE vcf_offline_depot_settings ADD COLUMN {name} {definition}"))


def _ensure_sqlite_vcf_trust_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "vcf_trust_targets" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("vcf_trust_targets")}
    columns = {
        "api_port": "INTEGER DEFAULT 443",
        "tls_fingerprint": "VARCHAR(160) DEFAULT ''",
    }
    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE vcf_trust_targets ADD COLUMN {name} {definition}"))
        indexes = connection.execute(text("PRAGMA index_list('vcf_trust_targets')")).fetchall()
        has_legacy_unique = False
        has_api_unique = False
        for index in indexes:
            index_name = index[1]
            is_unique = bool(index[2])
            if not is_unique:
                continue
            index_columns = [row[2] for row in connection.execute(text(f"PRAGMA index_info('{index_name}')")).fetchall()]
            if index_columns == ["address", "ssh_port"]:
                has_legacy_unique = True
            if index_columns == ["address", "api_port"]:
                has_api_unique = True
        if has_legacy_unique and not has_api_unique:
            connection.execute(text("ALTER TABLE vcf_trust_targets RENAME TO vcf_trust_targets_legacy_unique"))
            connection.execute(
                text(
                    """
                    CREATE TABLE vcf_trust_targets (
                        id INTEGER NOT NULL,
                        address VARCHAR(240) NOT NULL,
                        ssh_port INTEGER NOT NULL,
                        api_port INTEGER NOT NULL,
                        appliance_role VARCHAR(40) NOT NULL,
                        appliance_version VARCHAR(80) NOT NULL,
                        ssh_host_key_fingerprint VARCHAR(160) NOT NULL,
                        tls_fingerprint VARCHAR(160) NOT NULL,
                        last_ca_fingerprint VARCHAR(128) NOT NULL,
                        last_result VARCHAR(80) NOT NULL,
                        last_job_id VARCHAR(40) NOT NULL,
                        last_attempted_at DATETIME,
                        last_succeeded_at DATETIME,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        PRIMARY KEY (id),
                        CONSTRAINT uq_vcf_trust_target_address_api_port UNIQUE (address, api_port)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO vcf_trust_targets (
                        id,
                        address,
                        ssh_port,
                        api_port,
                        appliance_role,
                        appliance_version,
                        ssh_host_key_fingerprint,
                        tls_fingerprint,
                        last_ca_fingerprint,
                        last_result,
                        last_job_id,
                        last_attempted_at,
                        last_succeeded_at,
                        created_at,
                        updated_at
                    )
                    SELECT
                        id,
                        address,
                        COALESCE(ssh_port, 22),
                        COALESCE(api_port, 443),
                        COALESCE(appliance_role, ''),
                        COALESCE(appliance_version, ''),
                        COALESCE(ssh_host_key_fingerprint, ''),
                        COALESCE(tls_fingerprint, ''),
                        COALESCE(last_ca_fingerprint, ''),
                        COALESCE(last_result, ''),
                        COALESCE(last_job_id, ''),
                        last_attempted_at,
                        last_succeeded_at,
                        COALESCE(created_at, CURRENT_TIMESTAMP),
                        COALESCE(updated_at, CURRENT_TIMESTAMP)
                    FROM vcf_trust_targets_legacy_unique
                    WHERE id IN (
                        SELECT MIN(id)
                        FROM vcf_trust_targets_legacy_unique
                        GROUP BY address, COALESCE(api_port, 443)
                    )
                    """
                )
            )
            connection.execute(text("DROP TABLE vcf_trust_targets_legacy_unique"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_vcf_trust_targets_address ON vcf_trust_targets (address)"))


def _ensure_sqlite_esxi_pxe_columns() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "esxi_pxe_hosts" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("esxi_pxe_hosts")}
    columns = {
        "installer_iso_path": "VARCHAR(500) DEFAULT ''",
        "ip_address": "VARCHAR(64) DEFAULT ''",
        "variables_json": "TEXT DEFAULT '{}'",
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

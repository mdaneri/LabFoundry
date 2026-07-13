import sqlite3


def test_vcf_trust_target_migration_uses_api_port_uniqueness(tmp_path):
    from labfoundry.app import database

    db_path = tmp_path / "legacy-trust.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE vcf_trust_targets (
            id INTEGER NOT NULL,
            address VARCHAR(240) NOT NULL,
            ssh_port INTEGER NOT NULL,
            appliance_role VARCHAR(40) NOT NULL,
            appliance_version VARCHAR(80) NOT NULL,
            ssh_host_key_fingerprint VARCHAR(160) NOT NULL,
            last_ca_fingerprint VARCHAR(128) NOT NULL,
            last_result VARCHAR(80) NOT NULL,
            last_job_id VARCHAR(40) NOT NULL,
            last_attempted_at DATETIME,
            last_succeeded_at DATETIME,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_vcf_trust_target_address_port UNIQUE (address, ssh_port)
        )
        """
    )
    connection.execute("CREATE INDEX ix_vcf_trust_targets_address ON vcf_trust_targets (address)")
    connection.execute(
        """
        INSERT INTO vcf_trust_targets (
            id,
            address,
            ssh_port,
            appliance_role,
            appliance_version,
            ssh_host_key_fingerprint,
            last_ca_fingerprint,
            last_result,
            last_job_id,
            created_at,
            updated_at
        ) VALUES (1, 'vcf.example.test', 22, '', '', '', '', '', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )
    connection.commit()
    connection.close()

    previous_engine = database.engine
    migrated_engine = database.create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    try:
        database.engine = migrated_engine
        database.SessionLocal.configure(bind=migrated_engine)
        database._ensure_sqlite_vcf_trust_columns()

        connection = sqlite3.connect(db_path)
        unique_columns = []
        for index in connection.execute("PRAGMA index_list('vcf_trust_targets')").fetchall():
            if index[2]:
                unique_columns.append(
                    [column[2] for column in connection.execute(f"PRAGMA index_info('{index[1]}')").fetchall()]
                )
        connection.execute(
            """
            INSERT INTO vcf_trust_targets (
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
                created_at,
                updated_at
            ) VALUES ('vcf.example.test', 22, 8443, '', '', '', '', '', '', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
        connection.commit()
        rows_for_host = connection.execute("SELECT COUNT(*) FROM vcf_trust_targets WHERE address='vcf.example.test'").fetchone()[0]
        connection.close()
    finally:
        migrated_engine.dispose()
        database.engine = previous_engine
        database.SessionLocal.configure(bind=previous_engine)

    assert ["address", "api_port"] in unique_columns
    assert rows_for_host == 2

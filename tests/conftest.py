import os
from collections.abc import Generator

import pytest
from starlette.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    db_path = tmp_path / "labfoundry-test.db"
    monkeypatch.setenv("LABFOUNDRY_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LABFOUNDRY_SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setenv("LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD", "labfoundry-admin")
    monkeypatch.setenv("LABFOUNDRY_MONITOR_ENABLED", "false")

    from labfoundry.app.config import get_settings

    get_settings.cache_clear()

    import labfoundry.app.database as database

    database.engine.dispose()
    database.engine = database.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.SessionLocal.configure(bind=database.engine)

    from labfoundry.app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    os.environ.pop("LABFOUNDRY_DATABASE_URL", None)

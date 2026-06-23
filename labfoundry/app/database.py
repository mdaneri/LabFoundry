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


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

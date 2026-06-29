from contextlib import asynccontextmanager
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from labfoundry import __version__
from labfoundry.app.api.v1 import router as api_v1_router
from labfoundry.app.config import get_settings
from labfoundry.app.database import SessionLocal, init_db
from labfoundry.app.problem import install_problem_handlers
from labfoundry.app.seed import seed_initial_data
from labfoundry.app.services.networking import sync_host_physical_interfaces
from labfoundry.app.ui import initialize_factory_appliance_apply_baseline
from labfoundry.app.ui import router as ui_router

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"


def configure_logging() -> None:
    settings = get_settings()
    log_path = settings.app_log_path
    root_logger = logging.getLogger()
    if any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == log_path for handler in root_logger.handlers):
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    except OSError:
        logging.getLogger("labfoundry").exception("Unable to initialize LabFoundry app log at %s", log_path)
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    logging.getLogger("labfoundry").info("LabFoundry app log initialized at %s", log_path)


def refresh_startup_host_inventory(db: Session, *, environment: str) -> None:
    if environment == "appliance":
        sync_host_physical_interfaces(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging()
    init_db()
    with SessionLocal() as db:
        seed_initial_data(db, include_examples=settings.environment != "appliance")
        refresh_startup_host_inventory(db, environment=settings.environment)
        initialize_factory_appliance_apply_baseline(db)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="LabFoundry API",
        version=__version__,
        summary="REST API for the LabFoundry Linux infrastructure appliance.",
        openapi_version="3.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        same_site="lax",
        https_only=False,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID", f"req_{uuid4().hex[:12]}")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    install_problem_handlers(app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(api_v1_router)
    app.include_router(ui_router)

    return app


app = create_app()

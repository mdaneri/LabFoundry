from contextlib import asynccontextmanager
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
from labfoundry.app.ui import router as ui_router

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"


def refresh_startup_host_inventory(db: Session, *, environment: str) -> None:
    if environment == "appliance":
        sync_host_physical_interfaces(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    with SessionLocal() as db:
        seed_initial_data(db, include_examples=settings.environment != "appliance")
        refresh_startup_host_inventory(db, environment=settings.environment)
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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__, database
from app.api.audit import router as audit_router
from app.api.exports import router as exports_router
from app.api.imports import router as imports_router
from app.api.projects import router as projects_router
from app.config import settings
from app.schemas import HealthResponse
from app.services.demo_seeder import seed_demo_projects


def seed_demo_projects_if_empty() -> None:
    with database.SessionLocal.begin() as db:
        seed_demo_projects(db)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        database.init_db()
        seed_demo_projects_if_empty()
        yield
    finally:
        database.engine.dispose()


app = FastAPI(title=settings.app_title, version=__version__, lifespan=lifespan)
app.include_router(projects_router)
app.include_router(imports_router)
app.include_router(audit_router)
app.include_router(exports_router)
static_dir = Path(__file__).resolve().with_name("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(service=app.title, version=app.version)
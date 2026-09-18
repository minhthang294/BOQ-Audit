import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text

from app.api import admin, auth, jobs
from app.auth.security import hash_password
from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine
from app.models.entities import User, UserRole

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("boq-audit")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    (settings.data_dir / "database").mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seeds = [
            (settings.admin_username.strip().lower(), settings.admin_password, settings.admin_name, UserRole.ADMIN),
            (settings.demo_username.strip().lower(), settings.demo_password, settings.demo_name, UserRole.CUSTOMER),
        ]
        for username, password, name, role in seeds:
            if password and not db.scalar(select(User).where(func.lower(User.email) == username)):
                db.add(User(email=username, password_hash=hash_password(password), name=name, role=role))
                logger.info("seed_user_created username=%s role=%s", username, role.value)
        db.commit()
    yield


app = FastAPI(title="BOQ Audit Portal API", version="1.0.0", docs_url="/api/docs" if settings.app_env != "production" else None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(auth.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.middleware("http")
async def request_logging(request: Request, call_next):
    started = time.monotonic()
    try:
        response = await call_next(request)
        logger.info("request method=%s path=%s status=%s duration_ms=%d", request.method, request.url.path, response.status_code, (time.monotonic() - started) * 1000)
        return response
    except Exception:
        logger.exception("request_failed method=%s path=%s", request.method, request.url.path)
        raise


@app.get("/api/health")
async def health():
    database_status = "ok"
    storage_status = "ok"
    probe = settings.data_dir / f".healthcheck-{uuid4().hex}"
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("healthcheck_database_failed")
        database_status = "error"
    try:
        if not settings.data_dir.is_dir():
            raise OSError("DATA_DIR does not exist")
        probe.write_bytes(b"ok")
        probe.unlink()
    except Exception:
        logger.exception("healthcheck_storage_failed")
        probe.unlink(missing_ok=True)
        storage_status = "error"
    payload = {
        "status": "healthy" if database_status == storage_status == "ok" else "unhealthy",
        "database": database_status,
        "storage": storage_status,
    }
    return JSONResponse(payload, status_code=200 if payload["status"] == "healthy" else 503)

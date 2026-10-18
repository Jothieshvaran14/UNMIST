"""FastAPI application factory and entrypoint."""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api.v1 import auth, documents, knowledge, learning, orgs, profiles
from .config import get_settings
from .errors import register_error_handlers
from .logging import configure_logging, get_logger, set_correlation_id
from .schemas.common import HealthResponse

_logger = get_logger("unmist.main")


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    _logger.info(
        "Unmist API starting",
        extra={"environment": settings.environment, "version": __version__},
    )
    yield
    _logger.info("Unmist API shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Unmist API",
        version=__version__,
        description=(
            "Evidence-based knowledge intelligence platform. "
            "Authorization is enforced server-side for every operation."
        ),
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
        openapi_url="/api/openapi.json",
    )

    origins = _cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Org-ID"],
            max_age=600,
        )

    register_error_handlers(app)

    @app.middleware("http")
    async def correlation_and_timing(request: Request, call_next):
        correlation = request.headers.get("x-request-id") or uuid.uuid4().hex
        set_correlation_id(correlation)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _logger.exception("Request failed", extra={"path": request.url.path})
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = correlation
        # Security headers. CSP is intentionally conservative; adjust for the
        # chosen frontend hosting model before enabling rich UI.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        _logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(orgs.router, prefix="/api/v1")
    app.include_router(knowledge.router, prefix="/api/v1")
    app.include_router(learning.router, prefix="/api/v1")
    app.include_router(profiles.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok", version=__version__, environment=settings.environment
        )

    @app.get("/healthz", tags=["system"])
    def healthz() -> JSONResponse:
        # Liveness only. Readiness checks dependencies and is intentionally separate.
        return JSONResponse({"status": "ok"})

    return app


app = create_app()

"""
FastAPI application factory.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.settings import Settings, ensure_directories
from app.logging.logger import configure_logging, get_logger

log = get_logger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    from app.core.settings import settings as default_settings
    cfg = settings or default_settings

    configure_logging(level=cfg.log_level, log_dir=cfg.logs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_directories()
        log.info("startup", version=cfg.app_version, port=cfg.api_port)
        yield
        log.info("shutdown")

    app = FastAPI(
        title=cfg.app_name,
        version=cfg.app_version,
        description=(
            "Enterprise-grade AWS Redshift → Microsoft Fabric DDL Converter. "
            "Parses Redshift TABLE and VIEW DDL and emits parameterised Fabric T-SQL."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Register routers ─────────────────────────────────────────────────
    from app.api.routes.convert import router as convert_router
    from app.api.routes.health import router as health_router
    from app.api.routes.reports import router as reports_router

    app.include_router(health_router,  prefix=cfg.api_prefix)
    app.include_router(convert_router, prefix=cfg.api_prefix)
    app.include_router(reports_router, prefix=cfg.api_prefix)

    return app

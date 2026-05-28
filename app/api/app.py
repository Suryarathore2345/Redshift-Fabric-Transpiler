"""
FastAPI application factory.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

    # ── Serve frontend ────────────────────────────────────────────────────
    # Resolve frontend directory relative to project root
    _here      = Path(__file__).resolve().parent          # app/api/
    _proj_root = _here.parent.parent                      # project root
    _frontend  = _proj_root / "frontend"

    if _frontend.exists():
        # Mount static assets (css, js, images etc.) at /static
        _assets = _frontend / "assets"
        if _assets.exists():
            app.mount("/static", StaticFiles(directory=str(_assets)), name="static")

        # Serve index.html for the root and any unknown path (SPA fallback)
        @app.get("/", include_in_schema=False)
        @app.get("/ui", include_in_schema=False)
        async def serve_ui():
            return FileResponse(str(_frontend / "index.html"))

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            # Let API routes pass through; only intercept UI-looking paths
            if full_path.startswith("api/") or full_path in ("docs", "redoc", "openapi.json"):
                from fastapi import HTTPException
                raise HTTPException(status_code=404)
            index = _frontend / "index.html"
            if index.exists():
                return FileResponse(str(index))
            from fastapi import HTTPException
            raise HTTPException(status_code=404)

        log.info("frontend_mounted", path=str(_frontend))
    else:
        log.warning("frontend_not_found", expected_path=str(_frontend))

    return app

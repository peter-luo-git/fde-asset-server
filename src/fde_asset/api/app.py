"""FastAPI 应用装配。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from fde_asset import __version__
from fde_asset.api import (
    routes_apps,
    routes_assets,
    routes_harvest,
    routes_internal,
    routes_mandatory,
    routes_mcp,
    routes_notify,
    routes_recommend,
    routes_visibility,
    routes_work_items,
)
from fde_asset.api.deps import build_context
from fde_asset.platform.identity import PlatformUnavailable
from fde_asset.settings import AssetSettings


def create_app(settings: AssetSettings | None = None) -> FastAPI:
    context = build_context(settings)
    app = FastAPI(title="FDE 资产中心服务", version=__version__)
    app.state.context = context

    @app.exception_handler(PlatformUnavailable)
    async def platform_unavailable(request: Request, exc: PlatformUnavailable) -> JSONResponse:
        # Directory failures can occur after authentication. Do not expose an
        # upstream exception, URL or response body in the browser error.
        return JSONResponse(
            status_code=503,
            content={"detail": "暂时无法读取平台目录，请检查服务连接和 Agent 目录版本"},
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    async def ready() -> dict[str, object]:
        try:
            with context.engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            database = True
        except Exception:  # noqa: BLE001 - 探活只关心连不连得上
            database = False
        checks = {
            "data_dir": context.settings.root.exists(),
            "repo_dir": context.settings.repos.exists(),
            "database": database,
        }
        return {"status": "ok" if all(checks.values()) else "degraded", "checks": checks}

    app.include_router(routes_assets.router)
    app.include_router(routes_harvest.router)
    app.include_router(routes_work_items.router)
    app.include_router(routes_mandatory.router)
    app.include_router(routes_visibility.router)
    app.include_router(routes_recommend.router)
    app.include_router(routes_apps.router)
    app.include_router(routes_notify.router)
    app.include_router(routes_mcp.router)
    app.include_router(routes_internal.router)
    return app


app = create_app()

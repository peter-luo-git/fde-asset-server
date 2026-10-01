"""FastAPI 应用装配。"""

from __future__ import annotations

from fastapi import FastAPI

from fde_asset import __version__
from fde_asset.api import routes_assets, routes_recommend, routes_harvest, routes_work_items
from fde_asset.api.deps import build_context
from fde_asset.settings import AssetSettings


def create_app(settings: AssetSettings | None = None) -> FastAPI:
    context = build_context(settings)
    app = FastAPI(title="FDE 资产中心服务", version=__version__)
    app.state.context = context

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    async def ready() -> dict[str, object]:
        checks = {
            "data_dir": context.settings.root.exists(),
            "repo_dir": context.settings.repos.exists(),
            "database": context.settings.db_path.exists(),
        }
        return {"status": "ok" if all(checks.values()) else "degraded", "checks": checks}

    app.include_router(routes_assets.router)
    app.include_router(routes_harvest.router)
    app.include_router(routes_work_items.router)
    app.include_router(routes_recommend.router)
    return app


app = create_app()

"""FastAPI 应用装配。业务路由在各切片中加入。"""

from __future__ import annotations

from fastapi import FastAPI

from fde_asset import __version__
from fde_asset.settings import load_settings


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="FDE 资产中心服务", version=__version__)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    async def ready() -> dict[str, object]:
        """就绪检查：数据目录与仓库目录可访问即可（本地形态不依赖外部服务）。"""
        checks = {
            "data_dir": settings.data_dir.expanduser().exists(),
            "repo_dir": settings.repo_dir.expanduser().exists(),
        }
        return {"status": "ok" if all(checks.values()) else "degraded", "checks": checks}

    return app


app = create_app()

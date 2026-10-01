"""请求依赖：服务上下文与身份解析。

正式环境（identity_mode=oidc）由本服务校验 Casdoor 令牌，再向 fde-server 查成员关系；
本地开发（dev）用 X-FDE-User 头 + 目录文件，便于无依赖跑通全链路。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from fde_asset.core.db import create_engine_for, init_db
from fde_asset.modules.leads.rules import LocalActivitySource
from fde_asset.platform.blobs import BlobStore
from fde_asset.platform.identity import LocalDirectory, Principal, PrincipalNotFound
from fde_asset.platform.repo.local_git import LocalGitRepo
from fde_asset.platform.repo.ports import RepoRef
from fde_asset.settings import AssetSettings, load_settings


@dataclass
class ServiceContext:
    settings: AssetSettings
    engine: Any
    repo_port: LocalGitRepo
    directory: LocalDirectory
    activity: LocalActivitySource
    blob_store: BlobStore

    def repos(self) -> list[RepoRef]:
        """已注册的资产仓库：company / dept-* / 项目仓库（本地按目录约定发现）。"""
        found: list[RepoRef] = []
        for path in sorted(self.settings.repos.glob("*.git")):
            name = path.name[:-4]
            if name == "company-assets":
                found.append(RepoRef(name=name, scope="company"))
            elif name.startswith("dept-") and name.endswith("-assets"):
                found.append(RepoRef(name=name, scope="department", department_code=name[5:-7]))
            else:
                found.append(RepoRef(name=name, scope="engagement", engagement_slug=name))
        return found

    def repo_for(
        self, scope: str, *, department_code: str = "", engagement_slug: str = ""
    ) -> RepoRef:
        if scope == "company":
            return RepoRef(name="company-assets", scope="company")
        if scope == "department":
            return RepoRef(
                name=f"dept-{department_code}-assets",
                scope="department",
                department_code=department_code,
            )
        return RepoRef(name=engagement_slug, scope="engagement", engagement_slug=engagement_slug)


def build_context(settings: AssetSettings | None = None) -> ServiceContext:
    settings = settings or load_settings()
    settings.ensure_dirs()
    engine = create_engine_for(settings.db_path)
    init_db(engine)
    return ServiceContext(
        settings=settings,
        engine=engine,
        repo_port=LocalGitRepo(settings.repos),
        directory=LocalDirectory(settings.root / "directory.json"),
        activity=LocalActivitySource(settings.root / "activity.json"),
        blob_store=BlobStore(settings.blob_dir),
    )


def get_context(request: Request) -> ServiceContext:
    context = getattr(request.app.state, "context", None)
    if context is None:  # pragma: no cover - 启动时必定已装配
        raise HTTPException(status_code=503, detail="服务未初始化")
    return context


def get_principal(
    context: ServiceContext = Depends(get_context),
    x_fde_user: str | None = Header(default=None, alias="X-FDE-User"),
    authorization: str | None = Header(default=None),
) -> Principal:
    if context.settings.identity_mode == "oidc":  # pragma: no cover - v0.2 接 Casdoor
        raise HTTPException(status_code=501, detail="OIDC 身份校验将在接入 Casdoor 后启用")
    if not x_fde_user:
        raise HTTPException(status_code=401, detail="缺少身份：开发模式需要 X-FDE-User 请求头")
    try:
        return context.directory.resolve(x_fde_user)
    except PrincipalNotFound:
        raise HTTPException(status_code=401, detail=f"未知用户：{x_fde_user}") from None

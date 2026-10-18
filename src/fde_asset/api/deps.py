"""请求依赖：服务上下文与身份解析。

正式环境（identity_mode=oidc）由本服务校验 Casdoor 令牌，再向 fde-server 查成员关系；
本地开发（dev）用 X-FDE-User 头 + 目录文件，便于无依赖跑通全链路。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from fde_asset.core.db import create_engine_for, init_db
from fde_asset.core.sqlite_import import import_sqlite_if_empty
from fde_asset.modules.asset.legacy_layout import migrate_legacy_repos
from fde_asset.modules.leads.rules import LocalActivitySource
from fde_asset.platform.blobs import BlobStore
from fde_asset.platform.files import FileStore
from fde_asset.platform.identity import (
    LocalDirectory,
    PlatformDirectory,
    PlatformUnavailable,
    Principal,
    PrincipalNotFound,
)
from fde_asset.platform.platform_inbox import NoPlatformInbox, PlatformInbox
from fde_asset.platform.repo.gitea_git import GiteaGitRepo
from fde_asset.platform.repo.local_git import LocalGitRepo
from fde_asset.platform.repo.ports import RepoRef
from fde_asset.settings import AssetSettings, load_settings


logger = logging.getLogger(__name__)


@dataclass
class ServiceContext:
    settings: AssetSettings
    engine: Any
    repo_port: LocalGitRepo
    #: 已入库资产的附件原件（按内容哈希存，不进仓库）
    file_store: FileStore
    directory: LocalDirectory | PlatformDirectory
    activity: LocalActivitySource
    blob_store: BlobStore
    #: 平台工作台的待办；没有和平台共用账号时是个空实现
    platform_inbox: PlatformInbox | NoPlatformInbox = field(default_factory=NoPlatformInbox)

    def target_owner(self, target_type: str, target_id: str) -> str:
        """项目或 Agent 现在的负责人（正式环境由 fde-server 给出，这里读开发名单）。"""
        source = (
            self.directory.engagements() if target_type == "engagement" else self.directory.agents()
        )
        return str(source.get(target_id, {}).get("owner", ""))

    def display_name(self, user_id: str) -> str:
        try:
            return self.directory.resolve(user_id).display_name or user_id
        except Exception:  # noqa: BLE001 - 名单里没有这个人就显示账号
            return user_id

    def repos(self) -> list[RepoRef]:
        """资产仓库。所有资产都在同一个仓库里，所以只有一个。"""
        return [RepoRef()]

    def repo_for(self, scope: str = "", **_owner: str) -> RepoRef:
        """某份资产该写进哪个仓库。不分层级了，始终是那一个；参数只为兼容原来的调用。"""
        return RepoRef()


def build_repo_port(settings: AssetSettings) -> LocalGitRepo:
    """配了 Gitea 就以 Gitea 上的仓库为准，本地只留一份缓存；没配就用本机裸仓库。"""
    if settings.gitea_url:
        return GiteaGitRepo(
            settings.repos,
            base_url=settings.gitea_url,
            owner=settings.gitea_owner,
            token=settings.gitea_access_token(),
            sync_seconds=settings.gitea_sync_seconds,
        )
    return LocalGitRepo(settings.repos)


def build_context(settings: AssetSettings | None = None) -> ServiceContext:
    settings = settings or load_settings()
    settings.ensure_dirs()
    engine = create_engine_for(settings.database_target())
    init_db(engine)
    # 刚从 SQLite 换到 PostgreSQL：库还是空的，而数据目录里留着原来的 SQLite 文件，就把数据搬过来
    copied = import_sqlite_if_empty(engine, settings.db_path)
    if copied:
        logger.warning("已把 SQLite 里的数据搬进 PostgreSQL：%d 张表、%d 行", *copied)
    repo_port = build_repo_port(settings)
    if settings.gitea_url and settings.gitea_webhook_url and settings.gitea_webhook_secret():
        # 资产仓库有推送就回调资产中心，马上同步、重建索引；轮询只是兜底（B9）
        try:
            if repo_port.ensure_push_hook(
                RepoRef(), settings.gitea_webhook_url, settings.gitea_webhook_secret()
            ):
                logger.warning("已给资产仓库挂上 Gitea 推送回调：%s", settings.gitea_webhook_url)
        except Exception as error:  # noqa: BLE001 - 挂不上就只靠轮询，不挡启动
            logger.warning("资产仓库的 Gitea 回调没挂上，只靠轮询：%s", error)
    file_store = FileStore(settings.file_dir)
    # 以前是一层一个仓库；数据目录里还留着那样的旧仓库就先并进来，之后才能建索引
    reset_candidates: list[str] = []
    moved = migrate_legacy_repos(settings, repo_port, file_store, engine, reset_candidates)
    if moved:
        logger.warning("已把旧的分层仓库并入资产仓库：%s", "、".join(moved))
    local_directory = LocalDirectory(settings.root / "directory.json")
    directory: LocalDirectory | PlatformDirectory = local_directory
    if settings.identity_mode == "platform":
        directory = PlatformDirectory(
            settings.server_internal_url,
            settings.server_service_key,
            ttl_seconds=settings.platform_cache_seconds,
            local=local_directory,
        )
    platform_inbox: PlatformInbox | NoPlatformInbox = NoPlatformInbox()
    if settings.identity_mode == "platform" and settings.server_service_key:
        platform_inbox = PlatformInbox(settings.server_internal_url, settings.server_service_key)
    # 搬迁时退回草稿的那些提交，评审人在平台工作台里的待办也一并收掉
    for candidate_id in reset_candidates:
        try:
            platform_inbox.resolve(candidate_id=candidate_id, event="review")
        except Exception:  # noqa: BLE001 - 收不掉只是工作台多一条过期待办，不该拦住启动
            logger.warning("没能收掉草稿 %s 的评审待办", candidate_id)
    return ServiceContext(
        settings=settings,
        engine=engine,
        repo_port=repo_port,
        file_store=file_store,
        platform_inbox=platform_inbox,
        directory=directory,
        activity=LocalActivitySource(settings.root / "activity.json"),
        blob_store=BlobStore(settings.blob_dir, files=file_store),
    )


def get_context(request: Request) -> ServiceContext:
    context = getattr(request.app.state, "context", None)
    if context is None:  # pragma: no cover - 启动时必定已装配
        raise HTTPException(status_code=503, detail="服务未初始化")
    return context


def get_principal(
    request: Request,
    context: ServiceContext = Depends(get_context),
    x_fde_user: str | None = Header(default=None, alias="X-FDE-User"),
    authorization: str | None = Header(default=None),
) -> Principal:
    if context.settings.identity_mode == "oidc":  # pragma: no cover - v0.2 接 Casdoor
        raise HTTPException(status_code=501, detail="OIDC 身份校验将在接入 Casdoor 后启用")
    if context.settings.identity_mode == "platform":
        # 和平台共用账号：只认平台的登录会话，调用方自己报的 X-FDE-User 一律不看
        try:
            return context.directory.resolve_session(
                request.cookies.get(PlatformDirectory.SESSION_COOKIE)
            )
        except PrincipalNotFound:
            raise HTTPException(status_code=401, detail="未登录或登录已失效") from None
        except PlatformUnavailable:
            raise HTTPException(status_code=503, detail="暂时无法向平台确认身份") from None
    if not x_fde_user:
        raise HTTPException(status_code=401, detail="缺少身份：开发模式需要 X-FDE-User 请求头")
    try:
        return context.directory.resolve(x_fde_user)
    except PrincipalNotFound:
        raise HTTPException(status_code=401, detail=f"未知用户：{x_fde_user}") from None

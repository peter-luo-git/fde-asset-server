"""服务配置：只从环境变量读取，不在代码里写死路径。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class AssetSettings(BaseSettings):
    """本地进程形态的默认值：SQLite + 本机裸仓库，无外部依赖。"""

    model_config = SettingsConfigDict(env_prefix="FDE_ASSET_", extra="ignore")

    data_dir: Path = Path("./data")
    database_path: Path | None = None
    # 填了就用 PostgreSQL（形如 postgresql://账号@主机:5432/库名），不填用数据目录下的 SQLite 文件
    database_url: str = ""
    #: 数据库口令放在文件里时给路径，连接串里就不用写口令
    database_password_file: Path | None = None
    repo_dir: Path | None = None
    work_dir: Path | None = None
    index_poll_seconds: int = 30
    server_internal_url: str = "http://127.0.0.1:8000"
    # dev：按请求头识别身份，成员关系读本地名单文件
    # platform：和平台共用一套账号——拿浏览器带来的平台登录会话去问 fde-server 这是谁，
    #           用户、部门、项目成员关系也都来自 fde-server
    # oidc：自己校验 Casdoor 令牌（预留，未实现）
    identity_mode: Literal["dev", "platform", "oidc"] = "dev"
    # platform 模式下向 fde-server 读名单用的共享密钥，两边配成一样
    server_service_key: str = ""
    # 名单和会话的缓存秒数：平台那边建了项目、改了成员关系，最多过这么久在这里生效。
    # 设得短是因为人会在平台上建完项目马上来这边用；被移出项目的人也该尽快看不到。
    platform_cache_seconds: int = 5
    index_text_limit: int = 200_000
    knowledge_index_limit: int = 30_000
    attachment_size_limit: int = 50 * 1024 * 1024
    # 资产仓库放在哪。填了 Gitea 的地址就以 Gitea 上的 <gitea_owner>/assets 为准，
    # 本机 repo_dir 只当缓存；不填就用本机裸仓库（本地开发和自动化测试）
    gitea_url: str = ""
    gitea_owner: str = "fde-assets"
    gitea_token: str = ""
    #: 令牌放在文件里时给路径，优先于上面那一项
    gitea_token_file: Path | None = None
    #: 多久去 Gitea 看一次有没有新提交
    gitea_sync_seconds: float = 20.0
    #: 附件原件的存放目录；不填就在数据目录下
    file_root: Path | None = None

    @property
    def root(self) -> Path:
        return self.data_dir.expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return (self.database_path or self.root / "asset.db").expanduser()

    def database_target(self) -> str | Path:
        """连哪个数据库：PostgreSQL 的连接串，或者 SQLite 文件的路径。"""
        if not self.database_url:
            return self.db_path
        from sqlalchemy.engine import make_url

        url = make_url(self.database_url)
        if self.database_password_file is not None:
            secret = self.database_password_file.expanduser().read_text(encoding="utf-8").strip()
            url = url.set(password=secret)
        return url.render_as_string(hide_password=False)

    @property
    def repos(self) -> Path:
        return (self.repo_dir or self.root / "repos").expanduser()

    @property
    def work(self) -> Path:
        return (self.work_dir or self.root / "work").expanduser()

    @property
    def snapshot_dir(self) -> Path:
        return self.root / "snapshots"

    #: 演示容器绑哪个地址：默认只听本机，要给别人看时部署参数改成 0.0.0.0
    demo_bind_host: str = "127.0.0.1"
    #: 给页面展示的访问地址主机名（开发阶段是本机或指定机器，正式版是内网服务器）
    demo_public_host: str = "127.0.0.1"
    #: 多人协作的工作空间根目录；容器只能挂 <根>/<用户或部门>/ 下面的内容
    workspace_root: Path | None = None

    @property
    def workspaces(self) -> Path:
        return (self.workspace_root or self.root / "workspaces").expanduser()

    @property
    def blob_dir(self) -> Path:
        """草稿阶段的原件，按草稿编号和文件名存；入库后的在 file_dir。"""
        return self.root / "blobs"

    @property
    def file_dir(self) -> Path:
        """已入库资产的附件原件（按内容哈希存，不进 Git 仓库）。"""
        return (self.file_root or self.root / "files").expanduser()

    @property
    def bundle_dir(self) -> Path:
        """给平台拼好的 Agent 资产包存档：平台重试一次运行时按版本号来取同一份。"""
        return self.root / "bundles"

    def gitea_access_token(self) -> str:
        if self.gitea_token_file is not None:
            return self.gitea_token_file.expanduser().read_text(encoding="utf-8").strip()
        return self.gitea_token

    def ensure_dirs(self) -> None:
        for directory in (
            self.root,
            self.repos,
            self.work,
            self.snapshot_dir,
            self.blob_dir,
            self.file_dir,
            self.workspaces,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def load_local_env() -> None:
    """把仓库根的 `.env.local`（已 gitignore，放本地开发的模型密钥）加载进环境变量。

    设了 `FDE_ASSET_SKIP_ENV_FILE` 就不加载：自动化测试要的是确定的结果，
    不能因为这台机器上碰巧有一份密钥就去调真实的模型。
    """
    import os

    from fde_asset.platform.llm.client import load_env_file

    if os.environ.get("FDE_ASSET_SKIP_ENV_FILE"):
        return
    load_env_file(Path(__file__).resolve().parents[2] / ".env.local")


def load_settings() -> AssetSettings:
    load_local_env()
    return AssetSettings()

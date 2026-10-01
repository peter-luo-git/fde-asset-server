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
    repo_dir: Path | None = None
    work_dir: Path | None = None
    index_poll_seconds: int = 30
    server_internal_url: str = "http://127.0.0.1:8000"
    # dev：按请求头识别身份，成员关系读本地种子文件；oidc：校验 Casdoor 令牌 + 调 fde-server（v0.2）
    identity_mode: Literal["dev", "oidc"] = "dev"
    index_text_limit: int = 200_000
    knowledge_index_limit: int = 30_000
    attachment_size_limit: int = 50 * 1024 * 1024

    @property
    def root(self) -> Path:
        return self.data_dir.expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return (self.database_path or self.root / "asset.db").expanduser()

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

    @property
    def blob_dir(self) -> Path:
        """附件原件目录（本地对象存储；接 Gitea 后换 LFS）。"""
        return self.root / "blobs"

    def ensure_dirs(self) -> None:
        for directory in (self.root, self.repos, self.work, self.snapshot_dir, self.blob_dir):
            directory.mkdir(parents=True, exist_ok=True)


def load_settings() -> AssetSettings:
    # 本地开发的模型密钥放在仓库根的 .env.local（已 gitignore）
    from fde_asset.platform.llm.client import load_env_file

    load_env_file(Path(__file__).resolve().parents[2] / ".env.local")
    return AssetSettings()

"""服务配置：只从环境变量读取，不在代码里写死路径。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AssetSettings(BaseSettings):
    """本地进程形态的默认值：SQLite + 本机裸仓库，无外部依赖。"""

    model_config = SettingsConfigDict(env_prefix="FDE_ASSET_", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./data/asset.db"
    data_dir: Path = Path("./data/assets")
    repo_dir: Path = Path("./data/repos")
    work_dir: Path = Path("./data/work")
    index_poll_seconds: int = 30
    server_internal_url: str = "http://127.0.0.1:8000"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"


def load_settings() -> AssetSettings:
    return AssetSettings()

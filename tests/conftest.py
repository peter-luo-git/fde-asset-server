"""测试夹具：每个用例一套独立的本地仓库 + SQLite，互不干扰。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 测试不读 .env.local：结果不能取决于这台机器上有没有配真实的模型
os.environ.setdefault("FDE_ASSET_SKIP_ENV_FILE", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fde_asset.api.app import create_app  # noqa: E402
from fde_asset.api.deps import build_context  # noqa: E402
from fde_asset.modules.asset.indexer import index_all  # noqa: E402
from fde_asset.settings import AssetSettings  # noqa: E402
from scripts.seed_assets import seed  # noqa: E402


@pytest.fixture(autouse=True)
def _env_does_not_leak():
    """个别用例会把 .env.local 的模型配置读进环境变量；用例结束后还原，免得后面的用例悄悄连上真实模型。"""
    before = {key: value for key, value in os.environ.items() if key.startswith("FDE_ASSET_")}
    yield
    for key in [key for key in os.environ if key.startswith("FDE_ASSET_")]:
        if key not in before:
            del os.environ[key]
    os.environ.update(before)


@pytest.fixture()
def settings(tmp_path: Path) -> AssetSettings:
    value = AssetSettings(data_dir=tmp_path / "data")
    value.ensure_dirs()
    return value


@pytest.fixture()
def seeded(settings: AssetSettings) -> AssetSettings:
    seed(settings)
    return settings


@pytest.fixture()
def context(seeded: AssetSettings):
    return build_context(seeded)


@pytest.fixture()
def indexed(context):
    index_all(context.engine, context.repo_port, context.repos())
    return context


@pytest.fixture()
def client(seeded: AssetSettings):
    app = create_app(seeded)
    with TestClient(app) as test_client:
        test_client.headers.update({"X-FDE-User": "admin"})
        test_client.post("/api/v1/admin/assets/reindex")
        yield test_client


def as_user(client: TestClient, user: str) -> TestClient:
    client.headers.update({"X-FDE-User": user})
    return client

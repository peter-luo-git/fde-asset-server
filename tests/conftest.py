"""测试夹具：每个用例一套独立的本地仓库 + 数据库，互不干扰。

默认用 SQLite。设了 `FDE_ASSET_TEST_DATABASE_URL`（指向一个有建库权限的 PostgreSQL 账号，
例如 postgresql://postgres:test@127.0.0.1:55432/postgres）就改用 PostgreSQL：
每个用例在那台服务器上建一个临时库，跑完删掉。
"""

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


POSTGRES_URL = os.environ.get("FDE_ASSET_TEST_DATABASE_URL", "")


@pytest.fixture()
def settings(tmp_path: Path):
    if not POSTGRES_URL:
        value = AssetSettings(data_dir=tmp_path / "data")
        value.ensure_dirs()
        yield value
        return

    import uuid

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    server = make_url(POSTGRES_URL).set(drivername="postgresql+psycopg")
    name = "asset_test_" + uuid.uuid4().hex[:12]
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    value = AssetSettings(
        data_dir=tmp_path / "data",
        database_url=server.set(database=name).render_as_string(hide_password=False),
    )
    value.ensure_dirs()
    try:
        yield value
    finally:
        with admin.connect() as conn:
            # 用例里建的连接池可能还开着，强制断开再删
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture()
def seeded(settings: AssetSettings) -> AssetSettings:
    seed(settings)
    return settings


@pytest.fixture()
def context(seeded: AssetSettings):
    return build_context(seeded)


@pytest.fixture()
def indexed(context):
    index_all(context.engine, context.repo_port, context.repos(), file_store=context.file_store)
    return context


@pytest.fixture()
def client(seeded: AssetSettings):
    app = create_app(seeded)
    with TestClient(app) as test_client:
        test_client.headers.update({"X-FDE-User": "admin"})
        test_client.post("/api/v1/admin/assets/reindex")
        yield test_client


def asset_dir(context, name: str) -> str:
    """资产在仓库里的目录（`assets/<资产编号>`）。目录按编号放，测试里按标识来找。"""
    from sqlalchemy import select

    from fde_asset.core.db import assets

    with context.engine.connect() as conn:
        return conn.execute(select(assets.c.path).where(assets.c.name == name)).scalar_one()


def as_user(client: TestClient, user: str) -> TestClient:
    client.headers.update({"X-FDE-User": user})
    return client

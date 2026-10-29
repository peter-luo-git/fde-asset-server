"""测试夹具：每个用例一套独立的本地仓库 + 数据库，互不干扰。

数据库只用 PostgreSQL（2026-10-18 起去掉了 SQLite）：每个用例在测试库服务器上建一个临时库，
跑完删掉。服务器默认是本机的测试容器（`make test-db` 起），别的地方用
`FDE_ASSET_TEST_DATABASE_URL` 指定一个有建库权限的账号。
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
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


def pytest_addoption(parser):
    parser.addoption(
        "--live-model", action="store_true", help="Run explicitly authorized model acceptance"
    )
    parser.addoption(
        "--docker-integration", action="store_true", help="Run owned Docker acceptance"
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live_model: explicitly authorized real model acceptance")
    config.addinivalue_line("markers", "docker: owned Docker integration acceptance")
    if not config.getoption("live_model"):
        for key in list(os.environ):
            if (
                key.startswith(("FDE_ASSET_LLM_", "FDE_ASSET_RERANK_"))
                or key == "FDE_ASSET_REASON_MODEL"
            ):
                os.environ.pop(key)


def pytest_runtest_setup(item):
    for marker, option in (("live_model", "live_model"), ("docker", "docker_integration")):
        if item.get_closest_marker(marker) and not item.config.getoption(option):
            pytest.skip(f"requires explicit --{option.replace('_', '-')}")


@pytest.fixture(autouse=True)
def _env_does_not_leak():
    """个别用例会把 .env.local 的模型配置读进环境变量；用例结束后还原，免得后面的用例悄悄连上真实模型。"""
    before = {key: value for key, value in os.environ.items() if key.startswith("FDE_ASSET_")}
    yield
    for key in [key for key in os.environ if key.startswith("FDE_ASSET_")]:
        if key not in before:
            del os.environ[key]
    os.environ.update(before)


#: 测试库服务器：默认是 `make test-db` 起的本机容器
POSTGRES_URL = os.environ.get(
    "FDE_ASSET_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/postgres"
)


@contextmanager
def temporary_database():
    """在测试库服务器上建一个临时库，给出它的地址，用完删掉。"""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from scripts.smoke_database import register_database

    server = make_url(POSTGRES_URL).set(drivername="postgresql+psycopg")
    name, marker = register_database()
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        yield server.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            # 用例里建的连接池可能还开着，强制断开再删
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if marker is not None:
            marker.unlink(missing_ok=True)
        admin.dispose()


@pytest.fixture()
def settings(tmp_path: Path):
    with temporary_database() as database_url:
        value = AssetSettings(data_dir=tmp_path / "data", database_url=database_url)
        value.ensure_dirs()
        yield value


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


@pytest.fixture()
def docker_resources(monkeypatch):
    from fde_asset.platform.runner import docker_runner
    from tests.docker_resources import OwnedDockerResources

    with OwnedDockerResources() as resources:
        monkeypatch.setattr(docker_runner, "NETWORK", resources.network)
        yield resources

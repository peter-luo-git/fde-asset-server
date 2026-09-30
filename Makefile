PYTHON ?= python3

.PHONY: install check check-fast check-db api worker dev migrate demo

install:
	uv sync --dev

# 切片完成的唯一标准：这条命令必须绿
check:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run python -m compileall -q src tests
	uv run pytest -q
	uv run alembic heads
	uv lock --check

# 开发中快速自检：make check-fast T=tests/asset/test_manifest.py
check-fast:
	uv run ruff check src tests
	uv run pytest -q $(T)

# 需要 PostgreSQL 的条件测试，单独跑，不阻塞切片
check-db:
	FDE_ASSET_TEST_DATABASE_URL=$(DB_URL) uv run pytest -q -m db

api:
	uv run python -m fde_asset.main_asset_api

worker:
	uv run python -m fde_asset.main_asset_worker

# 本地进程形态：SQLite + 本机裸仓库，无需 Docker / Gitea / 模型凭据
dev:
	uv run python scripts/dev_bootstrap.py
	uv run python -m fde_asset.main_asset_api

migrate:
	uv run alembic upgrade head

demo:
	uv run python scripts/asset_demo.py --step all

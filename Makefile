PYTHON ?= python3

# 优先用 uv；没装 uv 时直接用 .venv 里的可执行文件，命令行写法保持一致。
HAS_UV := $(shell command -v uv >/dev/null 2>&1 && echo yes || echo no)
ifeq ($(HAS_UV),yes)
RUN := uv run
else
RUN := env PATH="$(CURDIR)/.venv/bin:$$PATH" VIRTUAL_ENV="$(CURDIR)/.venv"
endif

.PHONY: install check check-fast check-db api worker dev migrate demo demo-serve

install:
ifeq ($(HAS_UV),yes)
	uv sync --dev
else
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -q -e ".[dev]"
endif

# 切片完成的唯一标准：这条命令必须绿
check:
	$(RUN) ruff check src tests
	$(RUN) ruff format --check src tests
	$(RUN) python -m compileall -q src tests
	$(RUN) pytest -q
	$(RUN) alembic heads
ifeq ($(HAS_UV),yes)
	uv lock --check
endif

# 开发中快速自检：make check-fast T=tests/asset/test_manifest.py
check-fast:
	$(RUN) ruff check src tests
	$(RUN) pytest -q $(T)

# 需要 PostgreSQL 的条件测试，单独跑，不阻塞切片
check-db:
	FDE_ASSET_TEST_DATABASE_URL=$(DB_URL) $(RUN) pytest -q -m db

api:
	$(RUN) python -m fde_asset.main_asset_api

worker:
	$(RUN) python -m fde_asset.main_asset_worker

# 带种子资产的手工验收服务：建临时数据目录、灌 13 份示例资产、建索引后对外提供服务
# 远程访问：make demo-serve DEMO_HOST=0.0.0.0 DEMO_PORT=8100
DEMO_HOST ?= 127.0.0.1
DEMO_PORT ?= 8100
DEMO_DATA ?= .demo-data
demo-serve:
	$(RUN) python scripts/serve_seeded.py --host $(DEMO_HOST) --port $(DEMO_PORT) --data-dir $(DEMO_DATA)

# 本地进程形态：SQLite + 本机裸仓库，无需 Docker / Gitea / 模型凭据
dev:
	$(RUN) python scripts/dev_bootstrap.py
	$(RUN) python -m fde_asset.main_asset_api

migrate:
	$(RUN) alembic upgrade head

demo:
	$(RUN) python scripts/asset_demo.py --step all

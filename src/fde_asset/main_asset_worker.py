"""asset-worker 进程入口：仓库有新提交就重建索引、定时扫描线索、定时探活。任务定义见 `scheduler.py`。"""

from __future__ import annotations

import logging

from fde_asset.api.deps import build_context
from fde_asset.settings import load_settings
from fde_asset.scheduler import run_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_forever(build_context(load_settings()))


if __name__ == "__main__":
    main()

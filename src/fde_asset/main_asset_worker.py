"""asset-worker 进程入口：索引轮询、快照生成、线索挖掘（按切片逐步接入）。"""

from __future__ import annotations

import asyncio
import logging

from fde_asset.settings import load_settings

logger = logging.getLogger(__name__)


async def run_forever() -> None:
    settings = load_settings()
    logger.info("asset-worker 启动，轮询间隔 %s 秒", settings.index_poll_seconds)
    while True:  # pragma: no cover - 循环体在各切片接入具体任务
        await asyncio.sleep(settings.index_poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()

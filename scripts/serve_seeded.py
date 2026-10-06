"""起一个已灌入种子资产的真实 asset-api 进程，供前端跨栈端到端测试连接。

用法：python scripts/serve_seeded.py --port 8123 --data-dir /tmp/xxx
就绪后在标准输出打印一行 `ASSET_API_READY <base_url>`，调用方据此等待。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import uvicorn

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from fde_asset.api.app import create_app  # noqa: E402
from fde_asset.api.deps import build_context  # noqa: E402
from fde_asset.modules.asset.indexer import index_all  # noqa: E402
from fde_asset.settings import AssetSettings, load_local_env  # noqa: E402
from scripts.seed_assets import refresh_directory, refresh_seed_assets, seed  # noqa: E402


class _ReadySignal(uvicorn.Server):
    """uvicorn 启动完成后打印就绪行，避免调用方靠轮询猜测。"""

    def __init__(self, config: uvicorn.Config, base_url: str) -> None:
        super().__init__(config)
        self._base_url = base_url

    async def startup(self, sockets: list | None = None) -> None:  # type: ignore[override]
        await super().startup(sockets=sockets)
        print(f"ASSET_API_READY {self._base_url}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址；远程手工验收用 0.0.0.0")
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="沿用数据目录里已有的资产，不重新灌种子（保住手工造的数据）",
    )
    parser.add_argument(
        "--with-worker",
        action="store_true",
        help="在本进程里顺带跑定时任务（索引轮询、线索扫描、探活）；自动化测试不开，结果才确定",
    )
    args = parser.parse_args()

    # 演示服务也要能用上 .env.local 里配的模型，否则按问题检索只能退回关键词
    load_local_env()
    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="fde-asset-e2e-"))
    settings = AssetSettings(data_dir=data_dir)
    settings.ensure_dirs()
    if args.no_seed:
        print("reusing existing data dir, skip seeding", flush=True)
        added = refresh_directory(settings)
        if added:
            print(
                f"directory.json 补上了后来才有的 {len(added)} 项：" + "、".join(added), flush=True
            )
        seeded = refresh_seed_assets(settings)
        if seeded:
            print(
                f"资产仓库补上了后来才有的 {len(seeded)} 份种子：" + "、".join(seeded), flush=True
            )
    else:
        summary = seed(settings)
        print(f"seeded: {summary}", flush=True)

    # 直接在进程内建索引，调用方一连上就能看到资产，不必先发管理接口。
    context = build_context(settings)
    reports = index_all(
        context.engine,
        context.repo_port,
        context.repos(),
        text_limit=context.settings.index_text_limit,
        owner_of=context.target_owner,
    )
    print(
        "indexed: " + ", ".join(f"{r.repo}={r.indexed}" for r in reports),
        flush=True,
    )

    if args.with_worker:
        from fde_asset.scheduler import start_in_thread

        start_in_thread(context)
        print("worker thread started", flush=True)

    base_url = f"http://{args.host}:{args.port}"
    config = uvicorn.Config(
        create_app(settings), host=args.host, port=args.port, log_level="warning"
    )
    _ReadySignal(config, base_url).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

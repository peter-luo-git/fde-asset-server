"""把接口契约导出成 `contracts/asset-v1.yaml`。

契约是从代码生成的快照：改了接口就重跑这个脚本，把差异一起提交，评审时能直接看到
对外的变化。`tests/test_contract.py` 会拦住「改了接口却没更新契约」。

用法：python scripts/export_openapi.py [--check]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from fde_asset.api.app import create_app  # noqa: E402
from fde_asset.settings import AssetSettings  # noqa: E402

CONTRACT = _ROOT / "contracts" / "asset-v1.yaml"

HEADER = """\
# FDE 资产中心服务 · 对外接口契约（asset-v1）
#
# 由 scripts/export_openapi.py 从代码生成，不要手改；改接口后重跑脚本并一起提交。
# 说明：多数写接口的请求体目前按自由 JSON 接收，契约里只能体现路径、参数与状态码，
# 字段级的约定以 fde-web/src/features/asset/api/types.ts 和跨栈端到端测试为准。
"""


def build_spec() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="fde-asset-openapi-") as folder:
        settings = AssetSettings(data_dir=Path(folder))
        settings.ensure_dirs()
        return create_app(settings).openapi()


def render(spec: dict[str, Any]) -> str:
    body = yaml.safe_dump(spec, allow_unicode=True, sort_keys=True, width=100)
    return HEADER + body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只比对，不写文件；不一致时退出码为 1")
    args = parser.parse_args()
    text = render(build_spec())
    if args.check:
        current = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""
        if current != text:
            print("契约与代码不一致：请运行 python scripts/export_openapi.py 后提交")
            return 1
        print("契约与代码一致")
        return 0
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(text, encoding="utf-8")
    print(f"已写入 {CONTRACT}（{len(build_spec()['paths'])} 个路径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

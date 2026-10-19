"""asset-api 进程入口。"""

from __future__ import annotations

import uvicorn


def main() -> None:
    # 用工厂函数：导入模块时不连数据库，配置（FDE_ASSET_DATABASE_URL）缺了在启动时才报
    uvicorn.run(
        "fde_asset.api.app:create_app", factory=True, host="127.0.0.1", port=8100, reload=False
    )


if __name__ == "__main__":
    main()

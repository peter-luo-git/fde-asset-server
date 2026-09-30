"""asset-api 进程入口。"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("fde_asset.api.app:app", host="127.0.0.1", port=8100, reload=False)


if __name__ == "__main__":
    main()

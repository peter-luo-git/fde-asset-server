"""本地开发初始化：建裸仓库、数据目录，写入种子资产（按 S-02、S-05 逐步充实）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fde_asset.settings import load_settings


def ensure_bare_repo(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)


def main() -> None:
    settings = load_settings()
    for directory in (
        settings.data_dir,
        settings.repo_dir,
        settings.work_dir,
        settings.snapshot_dir,
    ):
        directory.expanduser().mkdir(parents=True, exist_ok=True)
    ensure_bare_repo(settings.repo_dir.expanduser() / "company-assets.git")
    print("本地资产环境就绪：")
    print(f"  裸仓库   {settings.repo_dir.expanduser() / 'company-assets.git'}")
    print(f"  数据目录 {settings.data_dir.expanduser()}")
    print(f"  快照目录 {settings.snapshot_dir.expanduser()}")


if __name__ == "__main__":
    main()

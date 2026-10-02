"""把一个 compose 应用打包成平台能收的 bundle。

作者在自己的项目目录里跑：

    python fde_app_pack.py                      # 用默认的 docker-compose.yml
    python fde_app_pack.py -f compose.prod.yml  # 指定文件

产出 `app-bundle.tar`，网页上传它就行。脚本做三件事：
1. `docker compose build`（有 build 段才做）
2. 把 compose 里用到的镜像一起 `docker save` 成 images.tar
3. 连同 compose 文件打成一个 tar

不想装脚本也可以手工来，就这两步：
    docker compose build && docker save -o images.tar <镜像1> <镜像2> ...
    tar cf app-bundle.tar docker-compose.yml images.tar
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print((result.stderr or result.stdout).strip(), file=sys.stderr)
        raise SystemExit(f"命令失败：{' '.join(args)}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--file", default="docker-compose.yml")
    parser.add_argument("-o", "--output", default="app-bundle.tar")
    parser.add_argument("--skip-build", action="store_true", help="镜像已经构建好了")
    args = parser.parse_args()

    compose_path = Path(args.file)
    if not compose_path.exists():
        raise SystemExit(f"找不到 {compose_path}")
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    services = document.get("services") or {}
    if not services:
        raise SystemExit("compose 里没有 services")

    needs_build = any("build" in service for service in services.values())
    if needs_build and not args.skip_build:
        print("正在构建镜像…")
        run(["docker", "compose", "-f", str(compose_path), "build"])

    images = sorted(
        {str(service["image"]) for service in services.values() if service.get("image")}
    )
    missing = [name for name, service in services.items() if not service.get("image")]
    if missing:
        raise SystemExit(
            "这些服务没有写 image，平台不在线上构建，请给它们写明镜像名：" + "、".join(missing)
        )

    work = Path(tempfile.mkdtemp())
    images_tar = work / "images.tar"
    print(f"正在导出 {len(images)} 个镜像…")
    run(["docker", "save", "-o", str(images_tar), *images])

    output = Path(args.output)
    with tarfile.open(output, "w") as archive:
        archive.add(compose_path, "docker-compose.yml")
        archive.add(images_tar, "images.tar")
    size_mb = output.stat().st_size / 1024 / 1024
    print(f"打包完成：{output}（{size_mb:.1f} MB），网页上传这个文件即可")
    if size_mb > 5120:
        print("注意：超过平台默认的 5GB 上限，考虑用更小的基础镜像")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

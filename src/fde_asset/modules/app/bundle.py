"""上传包的解析。

作者可以传两种东西，平台自己认，不用他选：

| 传什么 | 怎么认 | 对应 |
|---|---|---|
| `docker save` 出来的镜像 tar | 里面有 manifest.json | 单容器 |
| 打包好的 bundle（compose 文件 + 镜像 tar） | 里面有 docker-compose.yml | 多容器 |

bundle 的结构（`fde-app pack` 产出，手工打也行）：

    docker-compose.yml
    images.tar
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from pathlib import Path

COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
IMAGES_NAMES = ("images.tar", "image.tar")


class BundleError(ValueError):
    pass


@dataclass
class Unpacked:
    kind: str  # single | compose
    images_tar: Path
    compose_text: str = ""
    compose_file: Path | None = None


def _safe_members(archive: tarfile.TarFile, target: Path):
    """解包前挡掉路径穿越：tar 里写 ../../etc 是经典攻击。"""
    root = target.resolve()
    for member in archive.getmembers():
        destination = (root / member.name).resolve()
        if root != destination and root not in destination.parents:
            raise BundleError(f"上传包里有越界路径：{member.name}")
        if member.issym() or member.islnk():
            raise BundleError(f"上传包里有链接文件，不接受：{member.name}")
        yield member


def unpack(tar_path: Path, workdir: Path) -> Unpacked:
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tar_path) as archive:
            names = set(archive.getnames())
            compose_name = next((n for n in names if Path(n).name in COMPOSE_NAMES), "")
            if not compose_name:
                # 没有 compose 文件，当作单个镜像 tar
                return Unpacked(kind="single", images_tar=tar_path)
            archive.extractall(workdir, members=_safe_members(archive, workdir))
    except tarfile.TarError as exc:
        raise BundleError(f"上传包不是合法的 tar：{exc}") from None

    compose_file = workdir / compose_name
    images = None
    for candidate in IMAGES_NAMES:
        found = next(workdir.rglob(candidate), None)
        if found is not None:
            images = found
            break
    if images is None:
        raise BundleError(
            "bundle 里只有 compose 文件，没有 images.tar。"
            "请本地 `docker compose build` 之后把用到的镜像 `docker save` 成 images.tar 一起打包"
        )
    return Unpacked(
        kind="compose",
        images_tar=images,
        compose_text=compose_file.read_text(encoding="utf-8"),
        compose_file=compose_file,
    )

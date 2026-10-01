"""附件原件的本地存储。

草稿阶段的附件（PDF / Word / Excel / PPT）先落到这里，提交评审时才真正写进 Git 仓库。
v0.1 用本地目录；接 Gitea 之后换成 LFS 或对象存储，调用方只认 BlobStore 这个接口。
"""

from __future__ import annotations

import re
from pathlib import Path

#: 文件名白名单：挡掉路径穿越和奇怪字符，保留中文、字母数字和常见符号
_UNSAFE = re.compile(r"[\\/\x00-\x1f]")


class BlobError(ValueError):
    pass


def safe_name(filename: str) -> str:
    """把用户给的文件名收敛成一个安全的扁平名字。"""
    name = _UNSAFE.sub("_", (filename or "").strip()).strip(". ")
    if not name or name in {".", ".."}:
        raise BlobError("文件名不合法")
    return name[:180]


class BlobStore:
    """按 scope（这里是候选 id）分目录存放原件。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()

    def _dir(self, scope: str) -> Path:
        return self.root / safe_name(scope)

    def put(self, scope: str, filename: str, content: bytes) -> int:
        target = self._dir(scope)
        target.mkdir(parents=True, exist_ok=True)
        path = target / safe_name(filename)
        path.write_bytes(content)
        return len(content)

    def get(self, scope: str, filename: str) -> bytes | None:
        path = self._dir(scope) / safe_name(filename)
        return path.read_bytes() if path.exists() else None

    def delete(self, scope: str, filename: str) -> bool:
        path = self._dir(scope) / safe_name(filename)
        if not path.exists():
            return False
        path.unlink()
        return True

    def names(self, scope: str) -> list[str]:
        directory = self._dir(scope)
        if not directory.exists():
            return []
        return sorted(p.name for p in directory.iterdir() if p.is_file())

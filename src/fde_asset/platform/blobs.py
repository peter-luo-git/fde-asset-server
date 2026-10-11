"""草稿阶段的原件（PDF / Word / Excel / PPT、应用镜像包），按草稿编号和文件名取用。

内容放在按哈希存放的文件存储里（`platform/files.py`），和入库后的附件是同一套；
附件从头到尾都不进 Git 仓库。
"""

from __future__ import annotations

import re
from pathlib import Path

from fde_asset.platform.files import FileStore

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
    """草稿阶段的原件，按 scope（草稿编号）和文件名取用。

    给了 `files`（按内容哈希存放的文件存储）时，这里只记「这个名字对应哪份内容」，
    内容本身在文件存储里，和入库后的附件是同一套存储：提交评审时不用再拷一遍，
    以后文件存储换地方（比如对象存储）这里不用动。
    没给 `files` 时内容直接放在本目录下（测试和旧数据是这样的）；旧数据第一次被读到时
    会自动挪进文件存储。
    """

    #: 记「名字 → 内容哈希」的子目录
    REFS = ".refs"

    def __init__(self, root: Path, files: FileStore | None = None) -> None:
        self.root = Path(root).expanduser()
        self.files = files

    def _dir(self, scope: str) -> Path:
        return self.root / safe_name(scope)

    def _ref(self, scope: str, filename: str) -> Path:
        return self._dir(scope) / self.REFS / safe_name(filename)

    def _raw(self, scope: str, filename: str) -> Path:
        return self._dir(scope) / safe_name(filename)

    def sha256(self, scope: str, filename: str) -> str | None:
        """这个原件的内容哈希；没有这个原件，或者没接文件存储，返回 None。"""
        if self.files is None:
            return None
        ref = self._ref(scope, filename)
        if ref.exists():
            return ref.read_text(encoding="utf-8").strip()
        raw = self._raw(scope, filename)
        if raw.is_file():
            # 接文件存储之前存下的原件：挪进去，留下指向它的记录
            sha256 = self.files.put(raw.read_bytes())
            self._write_ref(ref, sha256)
            raw.unlink()
            return sha256
        return None

    @staticmethod
    def _write_ref(ref: Path, sha256: str) -> None:
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(sha256, encoding="utf-8")

    def put(self, scope: str, filename: str, content: bytes) -> int:
        if self.files is not None:
            self._write_ref(self._ref(scope, filename), self.files.put(content))
            self._raw(scope, filename).unlink(missing_ok=True)
            return len(content)
        target = self._dir(scope)
        target.mkdir(parents=True, exist_ok=True)
        (target / safe_name(filename)).write_bytes(content)
        return len(content)

    def get(self, scope: str, filename: str) -> bytes | None:
        sha256 = self.sha256(scope, filename)
        if sha256 is not None:
            return self.files.get(sha256) if self.files is not None else None
        path = self._raw(scope, filename)
        return path.read_bytes() if path.is_file() else None

    def delete(self, scope: str, filename: str) -> bool:
        """去掉这个名字。内容留在文件存储里：别的草稿或已入库的资产可能引用着同一份。"""
        removed = False
        for path in (self._ref(scope, filename), self._raw(scope, filename)):
            if path.is_file():
                path.unlink()
                removed = True
        return removed

    def path(self, scope: str, filename: str) -> Path:
        """原件在磁盘上的位置；交给演示运行器 docker load 用。"""
        sha256 = self.sha256(scope, filename)
        if sha256 is not None and self.files is not None:
            return self.files.path(sha256)
        return self._raw(scope, filename)

    def names(self, scope: str) -> list[str]:
        directory = self._dir(scope)
        if not directory.exists():
            return []
        found = {p.name for p in directory.iterdir() if p.is_file()}
        refs = directory / self.REFS
        if refs.exists():
            found |= {p.name for p in refs.iterdir() if p.is_file()}
        return sorted(found)

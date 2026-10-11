"""已入库资产的附件原件：按内容哈希存放，不进 Git 仓库。

资产的 asset.yaml 里只记附件的文件名和 sha256，原件在这里。同一份内容只存一次；
被任何版本引用着的原件都不删（没人引用的清理还没做）。
现在落在挂载的目录里，以后换对象存储只换这个类的实现。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FileStoreError(ValueError):
    pass


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()

    def path(self, sha256: str) -> Path:
        if not _SHA256.match(sha256 or ""):
            raise FileStoreError("附件哈希不合法")
        return self.root / sha256[:2] / sha256

    def put(self, content: bytes) -> str:
        """存一份原件，返回它的 sha256。已经有同样内容的就不再写。"""
        sha256 = hashlib.sha256(content).hexdigest()
        target = self.path(sha256)
        if target.exists():
            return sha256
        target.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再改名：写到一半进程没了，也不会留下一个内容对不上哈希的文件
        descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return sha256

    def get(self, sha256: str) -> bytes | None:
        try:
            target = self.path(sha256)
        except FileStoreError:
            return None
        return target.read_bytes() if target.exists() else None

    def exists(self, sha256: str) -> bool:
        try:
            return self.path(sha256).exists()
        except FileStoreError:
            return False

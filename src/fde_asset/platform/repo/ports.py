"""资产仓库端口：本地裸仓库和 Gitea 两种实现，业务代码只认这里的动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


#: 所有资产都在这一个仓库里；谁能看到哪份资产写在资产自己的 asset.yaml 里，不靠分仓库
ASSET_REPO_NAME = "assets"


@dataclass(frozen=True)
class RepoRef:
    """一个资产仓库。

    正常只有 `assets` 这一个，`scope` 留空。带 scope 的写法只剩旧数据导入在用：
    以前是一层一个仓库，仓库名决定了里面的资产属于哪一层。
    """

    name: str = ASSET_REPO_NAME
    scope: str = ""  # 旧布局：company | department | customer | engagement
    department_code: str = ""
    customer_code: str = ""
    engagement_slug: str = ""

    @property
    def key(self) -> str:
        return self.name


@dataclass(frozen=True)
class TreeEntry:
    path: str
    is_dir: bool
    size: int = 0


class AssetRepoPort(Protocol):
    """读写资产仓库所需的最小动作集合。"""

    def get_head(self, repo: RepoRef) -> str: ...

    def list_tree(self, repo: RepoRef, prefix: str = "") -> list[TreeEntry]: ...

    def read_file(self, repo: RepoRef, path: str, ref: str = "") -> bytes: ...

    def tree_shas(self, repo: RepoRef, prefix: str) -> dict[str, str]:
        """`prefix` 下每个子目录的内容哈希，键是子目录名。"""
        ...

    def create_branch(self, repo: RepoRef, branch: str, base: str = "") -> None: ...

    def commit_files(
        self,
        repo: RepoRef,
        branch: str,
        files: dict[str, bytes],
        message: str,
        author_name: str,
        author_email: str,
    ) -> str: ...

    def merge_branch(self, repo: RepoRef, branch: str, message: str) -> str: ...

    def delete_branch(self, repo: RepoRef, branch: str) -> None: ...

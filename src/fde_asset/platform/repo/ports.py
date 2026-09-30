"""资产仓库端口：v0.1 用本地裸仓库实现，接 Gitea 后换实现不改业务代码。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RepoRef:
    """一个资产仓库。scope 决定它承载哪一层资产。"""

    name: str  # 例：company-assets / dept-data-intel-assets / policy-import
    scope: str  # company | department | engagement
    department_code: str = ""
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

    def read_file(self, repo: RepoRef, path: str) -> bytes: ...

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

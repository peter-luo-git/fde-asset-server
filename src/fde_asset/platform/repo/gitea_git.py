"""Gitea 上的资产仓库：以 Gitea 为准，本机只留一份裸仓库当缓存。

读：先把 Gitea 上的分支拉进缓存（隔几秒才真去拉一次），再照本地裸仓库的办法读。
写：先拉到最新，在缓存上提交或合并，再推回 Gitea；推不上去就报错，并把缓存恢复成 Gitea 的样子。
所以缓存丢了没关系，重新拉就是；Gitea 上的才是真的。
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .local_git import GitCommandError, LocalGitRepo
from .ports import RepoRef, TreeEntry


class GiteaGitRepo(LocalGitRepo):
    def __init__(
        self,
        cache_dir: Path,
        *,
        base_url: str,
        owner: str,
        token: str,
        sync_seconds: float = 20.0,
        default_branch: str = "main",
    ) -> None:
        super().__init__(cache_dir, default_branch)
        if not base_url or not owner or not token:
            raise ValueError("接 Gitea 要同时给出地址、组织名和访问令牌")
        self.base_url = base_url.rstrip("/")
        self.owner = owner
        self._token = token
        self.sync_seconds = sync_seconds
        self._ensured: set[str] = set()
        self._synced_at: dict[str, float] = {}
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        self._lock_file = None

    # ---------- 基础 ----------

    def _remote_url(self, repo: RepoRef) -> str:
        return f"{self.base_url}/{self.owner}/{repo.name}.git"

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """同一份缓存一次只让一处动：接口进程和后台任务可能是两个进程，所以还要锁文件。"""
        with self._thread_lock:
            if self._lock_depth == 0:
                self._lock_file = open(self.repo_dir / ".gitea.lock", "w")  # noqa: SIM115
                fcntl.flock(self._lock_file, fcntl.LOCK_EX)
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
                if self._lock_depth == 0 and self._lock_file is not None:
                    fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                    self._lock_file.close()
                    self._lock_file = None

    def _remote_git(self, args: list[str], cwd: Path) -> str:
        """要连 Gitea 的 git 命令。令牌走环境变量传给 git，不出现在命令行和仓库配置里。"""
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env={
                "GIT_TERMINAL_PROMPT": "0",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": str(cwd),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: token {self._token}",
            },
        )
        if result.returncode != 0:
            detail = result.stderr.strip().replace(self._token, "***")
            raise GitCommandError(f"git {args[0]} 失败：{detail[-400:]}")
        return result.stdout

    def _api(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base_url + "/api/v1" + path,
            method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Authorization": "token " + self._token, "Content-Type": "application/json"},
        )
        # Gitea 在同一个容器网络里，直连；环境里配了访问外网用的代理也不走它
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            return error.code, {}
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GitCommandError(f"连不上 Gitea：{type(error).__name__}") from None

    def _ensure_remote(self, repo: RepoRef) -> None:
        """Gitea 上没有这个组织或仓库就建出来（都是私有的）。"""
        status, _ = self._api("GET", f"/repos/{self.owner}/{repo.name}")
        if status == 200:
            return
        if status != 404:
            raise GitCommandError(f"查不了 Gitea 上的仓库 {self.owner}/{repo.name}：{status}")
        status, _ = self._api("GET", f"/orgs/{self.owner}")
        if status == 404:
            status, _ = self._api(
                "POST", "/orgs", {"username": self.owner, "visibility": "private"}
            )
            if status not in (201, 422):
                raise GitCommandError(f"没能在 Gitea 里建组织 {self.owner}：{status}")
        status, _ = self._api(
            "POST",
            f"/orgs/{self.owner}/repos",
            {"name": repo.name, "private": True, "default_branch": self.default_branch},
        )
        if status not in (201, 409):
            raise GitCommandError(f"没能在 Gitea 里建仓库 {self.owner}/{repo.name}：{status}")

    def ensure_push_hook(self, repo: RepoRef, url: str, secret: str) -> bool:
        """资产仓库有推送就回调资产中心（B9）。已经有指向同一地址的回调就不动；返回这次是否新挂了。"""
        self.ensure_repo(repo)
        status, hooks = self._api("GET", f"/repos/{self.owner}/{repo.name}/hooks")
        if status != 200:
            raise GitCommandError(f"读不了 {self.owner}/{repo.name} 的回调：{status}")
        if isinstance(hooks, list) and any(
            isinstance(hook, dict) and (hook.get("config") or {}).get("url") == url
            for hook in hooks
        ):
            return False
        status, _ = self._api(
            "POST",
            f"/repos/{self.owner}/{repo.name}/hooks",
            {
                "type": "gitea",
                "active": True,
                "events": ["push"],
                "config": {"url": url, "content_type": "json", "secret": secret},
            },
        )
        if status != 201:
            raise GitCommandError(f"没能给 {self.owner}/{repo.name} 挂回调：{status}")
        return True

    def ensure_repo(self, repo: RepoRef) -> Path:
        path = super().ensure_repo(repo)
        if repo.name not in self._ensured:
            with self._exclusive():
                if repo.name not in self._ensured:
                    self._ensure_remote(repo)
                    self._ensured.add(repo.name)
                    self.sync(repo, force=True)
        return path

    def sync(self, repo: RepoRef, *, force: bool = False) -> None:
        """把 Gitea 上的所有分支拉进缓存；缓存里有而 Gitea 上没有的分支会被清掉。"""
        last = self._synced_at.get(repo.name, 0.0)
        if not force and time.monotonic() - last < self.sync_seconds:
            return
        path = super().ensure_repo(repo)
        with self._exclusive():
            self._remote_git(
                [
                    "fetch",
                    "--quiet",
                    "--prune",
                    "--no-tags",
                    self._remote_url(repo),
                    "+refs/heads/*:refs/heads/*",
                ],
                path,
            )
            self._synced_at[repo.name] = time.monotonic()

    def _push(self, repo: RepoRef, refspec: str) -> None:
        try:
            self._remote_git(
                ["push", "--quiet", self._remote_url(repo), refspec], self.path_of(repo)
            )
        except GitCommandError:
            # 没推上去：缓存里多出来的提交不算数，恢复成 Gitea 上的样子再把错报出去
            try:
                self.sync(repo, force=True)
            except GitCommandError:
                pass
            raise

    # ---------- 读 ----------

    def get_head(self, repo: RepoRef) -> str:
        self.ensure_repo(repo)
        self.sync(repo)
        return super().get_head(repo)

    def list_tree(self, repo: RepoRef, prefix: str = "") -> list[TreeEntry]:
        self.ensure_repo(repo)
        self.sync(repo)
        return super().list_tree(repo, prefix)

    def read_file(self, repo: RepoRef, path: str, ref: str = "") -> bytes:
        self.ensure_repo(repo)
        self.sync(repo)
        return super().read_file(repo, path, ref)

    def tree_shas(self, repo: RepoRef, prefix: str) -> dict[str, str]:
        self.ensure_repo(repo)
        self.sync(repo)
        return super().tree_shas(repo, prefix)

    # ---------- 写 ----------

    def create_branch(self, repo: RepoRef, branch: str, base: str = "") -> None:
        with self._exclusive():
            self.ensure_repo(repo)
            self.sync(repo, force=True)
            super().create_branch(repo, branch, base)
            if super().get_head(repo):
                # 立刻推上去：不然下一次同步会把这个只在缓存里的分支清掉
                self._push(repo, f"+refs/heads/{branch}:refs/heads/{branch}")

    def commit_files(
        self,
        repo: RepoRef,
        branch: str,
        files: dict[str, bytes],
        message: str,
        author_name: str = "fde-asset",
        author_email: str = "asset@fde.local",
    ) -> str:
        with self._exclusive():
            self.ensure_repo(repo)
            self.sync(repo, force=True)
            sha = super().commit_files(repo, branch, files, message, author_name, author_email)
            if sha:
                self._push(repo, f"refs/heads/{branch}:refs/heads/{branch}")
            return sha

    def merge_branch(self, repo: RepoRef, branch: str, message: str = "") -> str:
        with self._exclusive():
            self.ensure_repo(repo)
            self.sync(repo, force=True)
            sha = super().merge_branch(repo, branch, message)
            self._push(repo, f"refs/heads/{self.default_branch}:refs/heads/{self.default_branch}")
            return sha

    def delete_branch(self, repo: RepoRef, branch: str) -> None:
        with self._exclusive():
            self.ensure_repo(repo)
            super().delete_branch(repo, branch)
            try:
                self._remote_git(
                    ["push", "--quiet", self._remote_url(repo), f":refs/heads/{branch}"],
                    self.path_of(repo),
                )
            except GitCommandError:
                pass  # Gitea 上本来就没有这个分支

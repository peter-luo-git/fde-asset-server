"""本地裸仓库实现：不依赖 Gitea，只用 git 命令行。"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .ports import RepoRef, TreeEntry


class GitCommandError(RuntimeError):
    pass


class LocalGitRepo:
    """把 `<repo_dir>/<name>.git` 当作唯一可信源；所有写操作经临时工作区完成。"""

    def __init__(self, repo_dir: Path, default_branch: str = "main") -> None:
        self.repo_dir = Path(repo_dir).expanduser()
        self.default_branch = default_branch
        self.repo_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 基础 ----------

    def path_of(self, repo: RepoRef) -> Path:
        return self.repo_dir / f"{repo.name}.git"

    def _git(self, args: list[str], cwd: Path) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env={
                "GIT_TERMINAL_PROMPT": "0",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": str(cwd),
            },
        )
        if result.returncode != 0:
            raise GitCommandError(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
        return result.stdout

    def ensure_repo(self, repo: RepoRef) -> Path:
        path = self.path_of(repo)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", "--bare", "--initial-branch", self.default_branch, str(path)],
                check=True,
                capture_output=True,
            )
        return path

    # ---------- 读 ----------

    def get_head(self, repo: RepoRef) -> str:
        path = self.ensure_repo(repo)
        try:
            return self._git(["rev-parse", self.default_branch], path).strip()
        except GitCommandError:
            return ""  # 空仓库

    def list_tree(self, repo: RepoRef, prefix: str = "") -> list[TreeEntry]:
        path = self.ensure_repo(repo)
        if not self.get_head(repo):
            return []
        args = ["ls-tree", "-r", "-l", self.default_branch]
        if prefix:
            args.append(prefix)
        entries: list[TreeEntry] = []
        for line in self._git(args, path).splitlines():
            meta, file_path = line.split("\t", 1)
            parts = meta.split()
            size = int(parts[3]) if parts[3].isdigit() else 0
            entries.append(TreeEntry(path=file_path, is_dir=False, size=size))
        return entries

    def read_file(self, repo: RepoRef, path: str, ref: str = "") -> bytes:
        repo_path = self.ensure_repo(repo)
        target = f"{ref or self.default_branch}:{path}"
        result = subprocess.run(
            ["git", "show", target],
            cwd=str(repo_path),
            capture_output=True,
        )
        if result.returncode != 0:
            raise FileNotFoundError(f"{repo.name}:{path} 不存在")
        return result.stdout

    # ---------- 写 ----------

    def create_branch(self, repo: RepoRef, branch: str, base: str = "") -> None:
        repo_path = self.ensure_repo(repo)
        base_ref = base or self.default_branch
        head = self.get_head(repo)
        if not head:
            return  # 空仓库：首次提交时直接建分支
        self._git(["branch", "-f", branch, base_ref], repo_path)

    def commit_files(
        self,
        repo: RepoRef,
        branch: str,
        files: dict[str, bytes],
        message: str,
        author_name: str = "fde-asset",
        author_email: str = "asset@fde.local",
    ) -> str:
        """在临时工作区检出分支、写文件、提交并推回裸仓库，返回 commit sha。"""
        repo_path = self.ensure_repo(repo)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            head = self.get_head(repo)
            if head:
                subprocess.run(
                    ["git", "clone", "-q", str(repo_path), str(work)],
                    check=True,
                    capture_output=True,
                )
                try:
                    self._git(["checkout", "-q", branch], work)
                except GitCommandError:
                    self._git(["checkout", "-q", "-b", branch], work)
            else:
                work.mkdir(parents=True)
                self._git(["init", "-q", "--initial-branch", branch], work)
                self._git(["remote", "add", "origin", str(repo_path)], work)

            for rel_path, content in files.items():
                target = work / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            self._git(["config", "user.name", author_name], work)
            self._git(["config", "user.email", author_email], work)
            self._git(["add", "-A"], work)
            status = self._git(["status", "--porcelain"], work).strip()
            if not status:
                return head
            self._git(["commit", "-q", "-m", message], work)
            sha = self._git(["rev-parse", "HEAD"], work).strip()
            self._git(["push", "-q", "origin", f"{branch}:{branch}"], work)
            return sha

    def merge_branch(self, repo: RepoRef, branch: str, message: str = "") -> str:
        """把分支合并到默认分支（本地模式的“合并 PR”）。"""
        repo_path = self.ensure_repo(repo)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            subprocess.run(
                ["git", "clone", "-q", str(repo_path), str(work)], check=True, capture_output=True
            )
            self._git(["config", "user.name", "fde-asset"], work)
            self._git(["config", "user.email", "asset@fde.local"], work)
            try:
                self._git(["checkout", "-q", self.default_branch], work)
            except GitCommandError:
                self._git(["checkout", "-q", "-b", self.default_branch, f"origin/{branch}"], work)
                self._git(["push", "-q", "origin", self.default_branch], work)
                return self._git(["rev-parse", "HEAD"], work).strip()
            self._git(
                ["merge", "-q", "--no-ff", "-m", message or f"merge {branch}", f"origin/{branch}"],
                work,
            )
            sha = self._git(["rev-parse", "HEAD"], work).strip()
            self._git(["push", "-q", "origin", self.default_branch], work)
            return sha

    def delete_branch(self, repo: RepoRef, branch: str) -> None:
        repo_path = self.ensure_repo(repo)
        try:
            self._git(["branch", "-D", branch], repo_path)
        except GitCommandError:
            pass

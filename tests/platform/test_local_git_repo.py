"""本地裸仓库实现：提交、读取、分支、合并、并发覆盖。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fde_asset.platform.repo.local_git import GitCommandError, LocalGitRepo
from fde_asset.platform.repo.ports import RepoRef

REPO = RepoRef(name="company-assets", scope="company")


@pytest.fixture()
def git(tmp_path: Path) -> LocalGitRepo:
    return LocalGitRepo(tmp_path / "repos")


def test_empty_repo_has_no_head(git: LocalGitRepo) -> None:
    assert git.get_head(REPO) == ""
    assert git.list_tree(REPO) == []


def test_commit_and_read(git: LocalGitRepo) -> None:
    sha = git.commit_files(REPO, "main", {"skills/a/SKILL.md": b"# A\n"}, "feat: a")
    assert sha and git.get_head(REPO) == sha
    assert [e.path for e in git.list_tree(REPO)] == ["skills/a/SKILL.md"]
    assert git.read_file(REPO, "skills/a/SKILL.md") == b"# A\n"


def test_missing_file_raises(git: LocalGitRepo) -> None:
    git.commit_files(REPO, "main", {"a.md": b"x"}, "init")
    with pytest.raises(FileNotFoundError):
        git.read_file(REPO, "nope.md")


def test_branch_and_merge(git: LocalGitRepo) -> None:
    git.commit_files(REPO, "main", {"a.md": b"1"}, "init")
    git.create_branch(REPO, "asset/b")
    git.commit_files(REPO, "asset/b", {"b.md": b"2"}, "feat: b")
    assert "b.md" not in [e.path for e in git.list_tree(REPO)]
    git.merge_branch(REPO, "asset/b", "merge b")
    assert sorted(e.path for e in git.list_tree(REPO)) == ["a.md", "b.md"]


def test_delete_branch_is_idempotent(git: LocalGitRepo) -> None:
    git.commit_files(REPO, "main", {"a.md": b"1"}, "init")
    git.delete_branch(REPO, "not-exist")


def test_author_is_recorded(git: LocalGitRepo) -> None:
    git.commit_files(
        REPO, "main", {"a.md": b"1"}, "init", author_name="小陈", author_email="chen@fde.local"
    )
    log = git._git(["log", "-1", "--format=%an <%ae>"], git.path_of(REPO)).strip()
    assert log == "小陈 <chen@fde.local>"


def test_no_change_returns_head(git: LocalGitRepo) -> None:
    first = git.commit_files(REPO, "main", {"a.md": b"1"}, "init")
    again = git.commit_files(REPO, "main", {"a.md": b"1"}, "same")
    assert first == again


def test_invalid_git_command_raises(git: LocalGitRepo) -> None:
    with pytest.raises(GitCommandError):
        git._git(["not-a-command"], git.ensure_repo(REPO))

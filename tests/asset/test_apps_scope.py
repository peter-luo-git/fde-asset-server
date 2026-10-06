"""应用：只在应用市场里，不进资产目录；「我的应用」只有我负责的；已部署的应用能登记；旧数据会补上种子应用。"""

from __future__ import annotations

import yaml

from fde_asset.platform.repo.local_git import LocalGitRepo
from fde_asset.platform.repo.ports import RepoRef
from scripts.seed_assets import refresh_seed_assets
from tests.conftest import as_user


def _titles(response) -> set[str]:
    return {item["title"] for item in response.json()["items"]}


def test_my_apps_are_the_ones_i_or_my_department_own(client) -> None:
    market = {"保单导入控制台", "标注小工具"}
    # 应用市场：看得见的都列
    assert _titles(as_user(client, "li").get("/api/v1/apps")) == market

    # 小陈：个人负责一份，部门负责一份
    assert _titles(as_user(client, "chen").get("/api/v1/apps", params={"mine": True})) == market
    # 小李：只有部门负责的那一份
    assert _titles(as_user(client, "li").get("/api/v1/apps", params={"mine": True})) == {
        "保单导入控制台"
    }
    # 管理员看得见，但不是负责人
    admin = as_user(client, "admin")
    assert _titles(admin.get("/api/v1/apps")) == market
    assert _titles(admin.get("/api/v1/apps", params={"mine": True})) == set()


def test_catalog_can_leave_applications_out(client) -> None:
    chen = as_user(client, "chen")
    everything = chen.get("/api/v1/assets", params={"limit": 200}).json()
    without = chen.get(
        "/api/v1/assets", params={"limit": 200, "exclude_kind": "Application"}
    ).json()
    assert "Application" in {item["kind"] for item in everything["items"]}
    assert "Application" not in {item["kind"] for item in without["items"]}
    assert without["total"] == everything["total"] - 2, "总数也要跟着减，分页才对得上"

    asked = chen.get(
        "/api/v1/assets/ask", params={"q": "保单导入 对账 面板", "exclude_kind": "Application"}
    ).json()
    assert all(item["kind"] != "Application" for item in asked["items"])
    with_apps = chen.get("/api/v1/assets/ask", params={"q": "保单导入 对账 面板"}).json()
    assert any(item["kind"] == "Application" for item in with_apps["items"])


def test_registering_an_app_that_already_runs_elsewhere(client) -> None:
    """已经部署在别处的应用：填地址和网络类型就算登记了，不用传镜像。"""
    chen = as_user(client, "chen")
    draft = chen.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Application",
            "title": "对账差异看板",
            "scope": "department",
            "department_code": "data-intel",
        },
    ).json()
    patched = chen.patch(
        f"/api/v1/harvest-candidates/{draft['candidate_id']}",
        json={
            "meta": {
                "summary": "每天早上看一眼对账差异",
                "app": {
                    "source_type": "external",
                    "maturity": "pilot",
                    "runtime_type": "url",
                    "demo": {
                        "network": "intranet",
                        "url": "https://recon.example.internal",
                        "reachable_from": "公司内网",
                        "account": "演示账号见团队密码库",
                    },
                },
            }
        },
    )
    assert patched.status_code == 200, patched.text
    spec = yaml.safe_load(patched.json()["files"]["asset.yaml"])["spec"]
    assert spec["sourceType"] == "external" and spec["maturity"] == "pilot"
    assert spec["runtime"]["type"] == "url"
    assert spec["demo"] == {
        "network": "intranet",
        "url": "https://recon.example.internal",
        "account": "演示账号见团队密码库",
        "reachable_from": "公司内网",
        "note": spec["demo"].get("note", ""),
    }

    # 别的类型的草稿不认这些字段，免得往问题资产里写进一段运行配置
    case = chen.post(
        "/api/v1/harvest-candidates",
        json={"kind": "Case", "title": "x", "scope": "department", "department_code": "data-intel"},
    ).json()
    untouched = chen.patch(
        f"/api/v1/harvest-candidates/{case['candidate_id']}",
        json={"meta": {"app": {"demo": {"url": "https://nope"}}}},
    ).json()
    assert "https://nope" not in untouched["files"]["asset.yaml"]


def test_old_data_dir_gets_the_seed_assets_added_later(settings) -> None:
    """十月一日建的演示库里没有应用和客户仓库；沿用它启动时要补上，已有的文件不动。"""
    git = LocalGitRepo(settings.repos)
    department = RepoRef(
        name="dept-data-intel-assets", scope="department", department_code="data-intel"
    )
    # 旧仓库：只有一份部门资产，而且被人改过
    git.commit_files(
        department,
        "main",
        {
            "skills/pg-vacuum-tuning/asset.yaml": b"edited: by hand\n",
            "skills/pg-vacuum-tuning/SKILL.md": b"# mine\n",
        },
        "old",
    )

    added = refresh_seed_assets(settings)

    assert "dept-data-intel-assets:applications/policy-import-console" in added
    assert "dept-data-intel-assets:applications/quick-labeler" in added
    assert "cust-HUAAN-assets（整个仓库）" in added
    assert not any("pg-vacuum-tuning" in item for item in added)
    paths = {entry.path for entry in git.list_tree(department)}
    assert "applications/quick-labeler/asset.yaml" in paths
    assert git.read_file(department, "skills/pg-vacuum-tuning/asset.yaml") == b"edited: by hand\n"

    assert refresh_seed_assets(settings) == [], "补过一次就不该再动"

"""可见范围多项、调整范围（升级/降级/分享）、挂载名、给平台的资产包。"""

from __future__ import annotations

import re
from types import SimpleNamespace

import yaml

from fde_asset.modules.asset import agent_bundle
from fde_asset.modules.asset.manifest import audiences, scope_fields
from fde_asset.modules.asset.snapshot import _mount_names
from tests.conftest import as_user


def _asset(client, name: str) -> dict:
    items = client.get("/api/v1/assets", params={"limit": 200}).json()["items"]
    return next(item for item in items if item["name"] == name)


def test_primary_scope_is_the_widest_audience() -> None:
    assert audiences(["user:chen", "engagement:p1", "department:d1"]) == [
        ("department", "d1"),
        ("engagement", "p1"),
        ("user", "chen"),
    ]
    assert scope_fields(["engagement:p1", "department:d1"]).scope == "department"
    assert scope_fields(["user:chen"]).scope == "user"
    assert scope_fields([]) is None


def test_sharing_with_a_person_makes_it_visible_without_moving_it(client) -> None:
    admin = as_user(client, "admin")
    asset = _asset(admin, "pg-vacuum-tuning")  # 部门级，小赵不在这个部门
    url = f"/api/v1/assets/{asset['asset_id']}"
    assert as_user(client, "zhao").get(url).status_code == 404

    done = as_user(client, "admin").post(
        url + "/visibility", json={"visibility": ["department:data-intel", "user:zhao"]}
    )
    assert done.status_code == 200, done.text
    assert done.json()["changed"] is True and done.json()["commit"]

    seen = as_user(client, "zhao").get(url)
    assert seen.status_code == 200, "点名分享给他之后就看得见了"
    detail = seen.json()
    assert detail["asset_id"] == asset["asset_id"], "资产没搬动，编号不变"
    assert detail["path"] == asset["path"]
    assert detail["visibility"] == ["department:data-intel", "user:zhao"]
    assert detail["scope"] == "department", "作用域取范围最大的那一项"

    same = as_user(client, "admin").post(
        url + "/visibility", json={"visibility": ["user:zhao", "department:data-intel"]}
    )
    assert same.json()["changed"] is False, "只是顺序不同不算改动"


def test_widening_needs_the_reviewer_of_the_target_level(client) -> None:
    wang = as_user(client, "wang")  # 项目负责人：管得了项目自己的资产
    asset = _asset(wang, "policy-date-parse")
    url = f"/api/v1/assets/{asset['asset_id']}/visibility"

    # 2026-10-17 起不再拒绝：没有那一层评审权时转成申请，等资产评审员批
    requested = wang.post(url, json={"visibility": ["engagement:policy-import", "company"]})
    assert requested.status_code == 200, requested.text
    assert requested.json()["changed"] is False
    assert [item["entry"] for item in requested.json()["requested"]] == ["company"]

    allowed = wang.post(url, json={"visibility": ["engagement:policy-import", "user:zhao"]})
    assert allowed.status_code == 200, allowed.text

    empty = wang.post(url, json={"visibility": []})
    assert empty.status_code == 422
    bad = wang.post(url, json={"visibility": ["everyone"]})
    assert bad.status_code == 422


def _row(asset_id: str, name: str, owner: str, kind: str = "Skill") -> SimpleNamespace:
    return SimpleNamespace(asset_id=asset_id, name=name, owner_value=owner, kind=kind)


def test_mount_names_keep_the_first_and_suffix_the_later() -> None:
    rows = [_row("a" * 8, "deploy-check", "chen"), _row("b" * 8, "deploy-check", "Wang.Li")]
    assert _mount_names(rows, {}) == {"a" * 8: "deploy-check", "b" * 8: "deploy-check-wang-li"}
    # 关联时定下的名字优先：哪怕它排在后面
    recorded = {"b" * 8: "deploy-check"}
    assert _mount_names(rows, recorded) == {
        "b" * 8: "deploy-check",
        "a" * 8: "deploy-check-chen",
    }
    # 不同类型之间不算撞名
    mixed = [_row("a" * 8, "x", "chen"), _row("c" * 8, "x", "chen", kind="Case")]
    assert set(_mount_names(mixed, {}).values()) == {"x"}


def test_agent_bundle_is_laid_out_for_the_platform(indexed, tmp_path) -> None:
    from tests.platform.test_agent_integration import _link

    # 上层的技能要关联了才带；项目自己的不用
    for name in ("insurance-policy-import", "pg-vacuum-tuning"):
        _link(indexed, "engagement", "policy-import", name)
    bundle = agent_bundle.build_bundle(
        indexed.engine,
        indexed.repo_port,
        bundle_root=tmp_path / "bundles",
        department_code="data-intel",
        engagement_slug="policy-import",
        target_type="engagement",
        target_id="policy-import",
    )
    assert re.fullmatch(r"[0-9a-f]{40}", bundle.revision)
    paths = set(bundle.files)
    # 公司级、部门级、项目级的技能都在同一个 skills/ 下
    for skill in ("insurance-policy-import", "pg-vacuum-tuning", "policy-date-parse"):
        assert f"skills/{skill}/SKILL.md" in paths
    rules = [path for path in paths if path.startswith("rules/")]
    assert rules and all(re.fullmatch(r"rules/[0-9]{2}-[a-z0-9-]+\.md", path) for path in rules)
    assert all(path.split("/")[0] in {"rules", "skills", "knowledge"} for path in paths)
    assert not any("/attachments/" in path for path in paths), "附件不进资产包"

    document = yaml.safe_load(bundle.files["skills/pg-vacuum-tuning/asset.yaml"])
    assert document["metadata"]["name"] == "pg-vacuum-tuning"
    assert "id" not in document["metadata"]
    assert "visibility" not in document["spec"] and document["spec"]["relations"] == []
    assert {item["mount_name"] for item in bundle.assets} >= {"pg-vacuum-tuning"}

    again = agent_bundle.load_bundle(tmp_path / "bundles", bundle.revision)
    assert again.files == bundle.files, "按版本号取回来的是同一份"
